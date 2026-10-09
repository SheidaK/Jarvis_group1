import argparse
import csv
from pathlib import Path

from db import get_connection, init_db, DB_PATH
from loader import load_accounts, load_transactions
from processor import process_all
from report import write_reports


def identify(csv_file):
    """Classify a CSV by its header row instead of trusting the filename."""
    with open(csv_file, newline="", encoding="utf-8-sig") as f:
        header = next(csv.reader(f), [])
    if "accountId" in header:
        return "accounts"
    if "transactionId" in header:
        return "transactions"
    return None


def main():
    parser = argparse.ArgumentParser(description="CBOJ transaction processing engine")
    parser.add_argument("folder", help="Folder containing the accounts and transactions CSVs")
    parser.add_argument("--db", default=DB_PATH, help="SQLite file to write to")
    parser.add_argument("--out", default="reports", help="Folder for report files")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    files = {}
    for csv_file in sorted(folder.glob("*.csv")):
        kind = identify(csv_file)
        if kind:
            files[kind] = csv_file
        else:
            print(f"Skipping unrecognized CSV: {csv_file.name}")

    missing = {"accounts", "transactions"} - files.keys()
    if missing:
        raise SystemExit(f"Missing CSV(s) in {folder}: {', '.join(sorted(missing))}")

    conn = get_connection(args.db)
    init_db(conn)
    n_acc = load_accounts(conn, files["accounts"])
    n_tx = load_transactions(conn, files["transactions"])
    print(f"Loaded {n_acc} accounts from {files['accounts'].name}")
    print(f"Loaded {n_tx} transactions from {files['transactions'].name}")

    summary = process_all(conn)
    print(write_reports(conn, args.out))
    print(f"\nReports written to {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
