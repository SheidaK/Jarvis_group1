"""Operational reports, generated from the database (not from in-memory state)."""
from collections import Counter
from pathlib import Path


def _label(row):
    return row["transaction_id"] or f"(row {row['row_num']})"


def _money(cents):
    return f"${cents / 100:,.2f}" if cents is not None else "n/a"


def write_reports(conn, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = conn.execute(
        "SELECT t.*, r.status, r.reject_reason FROM transactions t "
        "JOIN results r USING (row_num) ORDER BY t.row_num").fetchall()

    # 1. per-transaction results
    lines = []
    for r in rows:
        if r["status"] == "APPROVED":
            lines.append(f"{_label(r)} APPROVED")
        else:
            lines.append(f"{_label(r)} REJECTED - {r['reject_reason']}")
    (out / "results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 2. processing summary
    n_ok = sum(1 for r in rows if r["status"] == "APPROVED")
    n_flagged = conn.execute("SELECT COUNT(DISTINCT row_num) FROM flags").fetchone()[0]
    reject_counts = Counter(r["reject_reason"].split(" (")[0]
                            for r in rows if r["status"] == "REJECTED")
    summary = [
        f"Transactions Processed: {len(rows)}",
        f"Approved: {n_ok}",
        f"Rejected: {len(rows) - n_ok}",
        f"Flagged For Review: {n_flagged}",
        "",
        "Rejections by reason:",
        *[f"  {k}: {v}" for k, v in reject_counts.most_common()],
    ]
    (out / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")

    # 3. flagged-transaction report
    flagged = conn.execute(
        "SELECT t.*, GROUP_CONCAT(f.reason, ', ') AS reasons "
        "FROM flags f JOIN transactions t USING (row_num) "
        "GROUP BY f.row_num ORDER BY f.row_num").fetchall()
    by_reason = Counter(r[0] for r in conn.execute("SELECT reason FROM flags"))
    rep = ["FLAGGED TRANSACTIONS (approved, pending manual review)",
           f"Total flagged: {len(flagged)}", "", "By reason:",
           *[f"  {k}: {v}" for k, v in by_reason.most_common()], "",
           f"{'ID':<9}{'Timestamp':<21}{'Type':<11}{'From':<9}{'To':<9}"
           f"{'Amount':>12}  Reasons"]
    for r in flagged:
        rep.append(f"{_label(r):<9}{r['timestamp']:<21}{r['type']:<11}"
                   f"{r['from_account'] or '-':<9}{r['to_account'] or '-':<9}"
                   f"{_money(r['amount_cents']):>12}  {r['reasons']}")
    (out / "flagged_report.txt").write_text("\n".join(rep) + "\n", encoding="utf-8")

    # 4. balance changes
    bal = conn.execute(
        "SELECT * FROM account_balances WHERE opening_balance_cents != final_balance_cents "
        "ORDER BY account_id").fetchall()
    b = [f"{'Account':<9}{'Opening':>14}{'Final':>14}{'Change':>14}"]
    for r in bal:
        o, f = r["opening_balance_cents"], r["final_balance_cents"]
        b.append(f"{r['account_id']:<9}{_money(o):>14}{_money(f):>14}{_money(f - o):>14}")
    (out / "balances.txt").write_text("\n".join(b) + "\n", encoding="utf-8")

    return "\n".join(summary)
