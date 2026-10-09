import sqlite3

DB_PATH = "cboj.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id         TEXT PRIMARY KEY,
    customer_name      TEXT,
    account_type       TEXT,
    status             TEXT,
    balance_cents      INTEGER,
    daily_limit_cents  INTEGER,
    currency           TEXT,
    opened_date        TEXT
);

-- Raw input, never modified after loading.
-- transaction_id is NOT unique (the data has duplicates); row_num is the key
-- and preserves file order. from_account / to_account are deliberately NOT
-- foreign keys: the data contains accounts that don't exist, and those rows
-- must load so the validator can reject them as INVALID ACCOUNT.
CREATE TABLE IF NOT EXISTS transactions (
    row_num         INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  TEXT,
    timestamp       TEXT,      -- ISO 8601, NULL if unparseable
    type            TEXT,      -- normalized to UPPERCASE
    from_account    TEXT,      -- NULL for DEPOSIT
    to_account      TEXT,      -- NULL for PURCHASE / WITHDRAWAL
    amount_cents    INTEGER,   -- NULL if missing/unparseable
    channel         TEXT,
    description     TEXT,
    raw_row         TEXT,      -- original CSV row as JSON
    parse_error     TEXT       -- NULL if the row parsed cleanly
);
CREATE INDEX IF NOT EXISTS idx_tx_txid ON transactions(transaction_id);

-- One row per transaction, written by the processor.
CREATE TABLE IF NOT EXISTS results (
    row_num        INTEGER PRIMARY KEY REFERENCES transactions(row_num),
    status         TEXT NOT NULL,   -- APPROVED / REJECTED
    reject_reason  TEXT             -- required when REJECTED
);

-- Zero or more rows per transaction (a transaction can be flagged for several reasons).
CREATE TABLE IF NOT EXISTS flags (
    flag_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    row_num   INTEGER NOT NULL REFERENCES transactions(row_num),
    reason    TEXT NOT NULL         -- e.g. LARGE AMOUNT, DAILY LIMIT, VELOCITY
);
CREATE INDEX IF NOT EXISTS idx_flags_row ON flags(row_num);

-- Written by the processor. `accounts.balance_cents` stays as the opening
-- balance (raw input); the updated balance lives here.
CREATE TABLE IF NOT EXISTS account_balances (
    account_id             TEXT PRIMARY KEY,
    opening_balance_cents  INTEGER,
    final_balance_cents    INTEGER
);
"""


def get_connection(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection, reset: bool = True) -> None:
    """Create tables. reset=True drops existing data so reruns start clean."""
    if reset:
        conn.executescript(
            "DROP TABLE IF EXISTS account_balances; "
            "DROP TABLE IF EXISTS flags; DROP TABLE IF EXISTS results; "
            "DROP TABLE IF EXISTS transactions; DROP TABLE IF EXISTS accounts;"
        )
    conn.executescript(SCHEMA)
    conn.commit()


def clear_results(conn: sqlite3.Connection) -> None:
    """Wipe processing output so the processor can be rerun without reloading CSVs."""
    conn.execute("DELETE FROM flags")
    conn.execute("DELETE FROM results")
    conn.execute("DELETE FROM account_balances")
    conn.commit()