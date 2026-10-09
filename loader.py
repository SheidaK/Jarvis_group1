import csv
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation


def to_cents(value):
    """'12.34' -> 1234. None if missing/unparseable (e.g. '12.5O'). No floats."""
    if value is None or str(value).strip() == "":
        return None
    try:
        d = Decimal(str(value).strip().replace("$", "").replace(",", ""))
        return int((d * 100).to_integral_value())
    except InvalidOperation:
        return None


def to_iso(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return datetime.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        return None


def _s(row, key):
    """Stripped string, or None if blank/missing."""
    v = row.get(key)
    v = v.strip() if isinstance(v, str) else v
    return v or None


def load_accounts(conn, path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append((
                _s(r, "accountId"),
                _s(r, "customerName"),
                (_s(r, "accountType") or "").upper() or None,
                (_s(r, "status") or "").upper() or None,
                to_cents(r.get("balance")),
                to_cents(r.get("dailyLimit")),
                _s(r, "currency"),
                _s(r, "openedDate"),
            ))
    # Assumption: if an account ID repeats, the last row wins.
    conn.executemany(
        "INSERT OR REPLACE INTO accounts (account_id, customer_name, account_type, "
        "status, balance_cents, daily_limit_cents, currency, opened_date) "
        "VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def load_transactions(conn, path):
    """Loads EVERY row in file order. Unparseable fields become NULL plus a
    parse_error so the validator can reject with INVALID DATA, not crash."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            amount = to_cents(r.get("amount"))
            ts = to_iso(r.get("timestamp"))
            errors = []
            if _s(r, "transactionId") is None:
                errors.append("missing transactionId")
            if amount is None:
                errors.append("bad amount")
            if ts is None:
                errors.append("bad timestamp")
            rows.append((
                _s(r, "transactionId"),
                ts,
                (_s(r, "type") or "").upper() or None,   # fixes 'purchase'
                _s(r, "fromAccount"),
                _s(r, "toAccount"),
                amount,
                (_s(r, "channel") or "").upper() or None,
                _s(r, "description"),
                json.dumps(r),
                "; ".join(errors) or None,
            ))
    conn.executemany(
        "INSERT INTO transactions (transaction_id, timestamp, type, from_account, "
        "to_account, amount_cents, channel, description, raw_row, parse_error) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)