"""Validation, business rules, balance updates and review flagging.

Flow: the loader has already stored every raw row. process_all() walks them in
file order, writes one row per transaction to `results`, any review reasons to
`flags`, and the before/after balances to `account_balances`. The raw
`transactions` and `accounts` tables are never modified.

Assumptions (also listed in README notes / the summary report):
  * Rows are processed in file order (the file is already ~chronological).
  * Check order: INVALID DATA -> DUPLICATE -> type -> accounts -> status ->
    amount -> balance. First failure wins and is the reject reason.
  * "Already processed" means an APPROVED transaction with that ID came
    earlier. A rejected first attempt does not block a later retry.
  * Every account on a transaction (both sides of a transfer) must exist and
    be ACTIVE. Self-transfers are rejected.
  * Amounts with more than 2 decimal places are rejected rather than rounded.
  * REVERSAL credits the account in `fromAccount` (a refund). It needs an
    earlier approved debit (purchase / withdrawal / outgoing transfer) from
    the same account whose remaining reversible amount is >= the reversal
    amount. Match found -> approved, no flag. No match -> still approved
    (it is a valid transaction) but flagged NO ORIGINAL TRANSACTION. Each
    original can only be reversed up to its own amount in total.
    Matching does not compare descriptions.
  * Flags are only raised for APPROVED transactions; rejected ones are
    already in front of someone.
  * Daily limit counts money going OUT of an account (purchase, withdrawal,
    outgoing transfer) per calendar day; deposits don't count.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from db import clear_results

# --- tunable thresholds -----------------------------------------------------
LARGE_AMOUNT_CENTS = 1_000_000        # >= $10,000 (matches Canada's LCTR line)
NEAR_LARGE_CENTS = 900_000            # $9,000-$9,999.99: possible structuring
VELOCITY_COUNT = 5                    # this many txns ...
VELOCITY_WINDOW = timedelta(minutes=10)   # ... inside this window

DEBIT_TYPES = {"PURCHASE", "WITHDRAWAL", "TRANSFER"}
SUPPORTED_TYPES = {"DEPOSIT", "WITHDRAWAL", "PURCHASE", "TRANSFER", "REVERSAL"}

FLAG_LARGE = "LARGE AMOUNT"
FLAG_NEAR_LARGE = "NEAR LARGE-AMOUNT THRESHOLD"
FLAG_DAILY = "DAILY LIMIT"
FLAG_VELOCITY = "VELOCITY"
FLAG_NO_ORIGINAL = "NO ORIGINAL TRANSACTION"


def _too_precise(raw_row_json):
    """True if the raw amount has more than 2 decimal places (to_cents would
    have silently rounded it)."""
    try:
        raw = json.loads(raw_row_json).get("amount") or ""
        d = Decimal(raw.strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError, AttributeError):
        return False
    return d != d.quantize(Decimal("0.01"))


def _required_accounts(tx):
    t = tx["type"]
    if t == "DEPOSIT":
        return [tx["to_account"]]
    if t in ("PURCHASE", "WITHDRAWAL", "REVERSAL"):
        return [tx["from_account"]]
    return [tx["from_account"], tx["to_account"]]          # TRANSFER


def _validate(tx, accounts, balances, approved_ids):
    """Return a reject reason string, or None if the transaction is valid."""
    if tx["parse_error"]:
        return f"INVALID DATA ({tx['parse_error']})"
    if _too_precise(tx["raw_row"]):
        return "INVALID DATA (amount has more than 2 decimals)"

    if tx["transaction_id"] in approved_ids:
        return "DUPLICATE TRANSACTION"

    if tx["type"] not in SUPPORTED_TYPES:
        return f"UNSUPPORTED TYPE ({tx['type'] or 'missing'})"

    needed = _required_accounts(tx)
    if any(a is None or a not in accounts for a in needed):
        return "INVALID ACCOUNT"
    if tx["type"] == "TRANSFER" and tx["from_account"] == tx["to_account"]:
        return "INVALID TRANSFER (same account)"
    if any(accounts[a]["status"] != "ACTIVE" for a in needed):
        return "INACTIVE ACCOUNT"

    if tx["amount_cents"] <= 0:
        return "INVALID AMOUNT"

    if tx["type"] in DEBIT_TYPES:
        if balances[tx["from_account"]] - tx["amount_cents"] < 0:
            return "INSUFFICIENT FUNDS"
    return None


def _velocity_flags(approved):
    """approved: list of (row_num, actor, datetime). Returns set of row_nums
    that sit inside any window with >= VELOCITY_COUNT txns by one account."""
    by_actor = defaultdict(list)
    for row_num, actor, ts in approved:
        by_actor[actor].append((ts, row_num))
    hits = set()
    for events in by_actor.values():
        events.sort()
        for i, (ts, _) in enumerate(events):
            window = [e for e in events if ts - VELOCITY_WINDOW < e[0] <= ts]
            if len(window) >= VELOCITY_COUNT:
                hits.update(r for _, r in window)
    return hits


def process_all(conn):
    """Process every loaded transaction. Safe to rerun. Returns a summary dict."""
    clear_results(conn)

    accounts = {r["account_id"]: r for r in conn.execute("SELECT * FROM accounts")}
    opening = {a: (r["balance_cents"] or 0) for a, r in accounts.items()}
    balances = dict(opening)

    approved_ids = set()
    daily_out = defaultdict(int)        # (account, date) -> cents going out
    originals = defaultdict(list)       # account -> [[remaining_cents], ...] reversible debits
    results, flags, approved_for_velocity = [], [], []

    for tx in conn.execute("SELECT * FROM transactions ORDER BY row_num"):
        row = tx["row_num"]
        reason = _validate(tx, accounts, balances, approved_ids)
        if reason:
            results.append((row, "REJECTED", reason))
            continue

        # ---- approve: apply to balances -----------------------------------
        amt, t = tx["amount_cents"], tx["type"]
        if t in DEBIT_TYPES:
            balances[tx["from_account"]] -= amt
        if t in ("DEPOSIT", "TRANSFER"):
            balances[tx["to_account"]] += amt
        if t == "REVERSAL":
            balances[tx["from_account"]] += amt
        approved_ids.add(tx["transaction_id"])
        results.append((row, "APPROVED", None))

        # ---- reversal matching / tracking ----------------------------------
        if t in DEBIT_TYPES:
            originals[tx["from_account"]].append([amt])
        elif t == "REVERSAL":
            match = next((o for o in reversed(originals[tx["from_account"]])
                          if o[0] >= amt), None)
            if match:
                match[0] -= amt              # consume reversible amount
            else:
                flags.append((row, FLAG_NO_ORIGINAL))

        # ---- review flags (do not block) -----------------------------------
        if amt >= LARGE_AMOUNT_CENTS:
            flags.append((row, FLAG_LARGE))
        elif amt >= NEAR_LARGE_CENTS:
            flags.append((row, FLAG_NEAR_LARGE))

        if t in DEBIT_TYPES:
            acct = tx["from_account"]
            day = tx["timestamp"][:10]
            daily_out[(acct, day)] += amt
            limit = accounts[acct]["daily_limit_cents"]
            if limit is not None and daily_out[(acct, day)] > limit:
                flags.append((row, FLAG_DAILY))

        actor = tx["from_account"] or tx["to_account"]
        approved_for_velocity.append(
            (row, actor, datetime.fromisoformat(tx["timestamp"])))

    flags += [(r, FLAG_VELOCITY) for r in sorted(_velocity_flags(approved_for_velocity))]

    conn.executemany(
        "INSERT INTO results (row_num, status, reject_reason) VALUES (?,?,?)", results)
    conn.executemany("INSERT INTO flags (row_num, reason) VALUES (?,?)", flags)
    conn.executemany(
        "INSERT INTO account_balances (account_id, opening_balance_cents, "
        "final_balance_cents) VALUES (?,?,?)",
        [(a, opening[a], balances[a]) for a in accounts])
    conn.commit()

    n_ok = sum(1 for r in results if r[1] == "APPROVED")
    return {
        "processed": len(results),
        "approved": n_ok,
        "rejected": len(results) - n_ok,
        "flagged": len({f[0] for f in flags}),
    }
