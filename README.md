# CBOJ Transaction Processing Engine

A Python and SQLite batch application for the Canadian Bank of Jarvis hackathon. It loads account and transaction CSVs, validates each transaction, calculates final balances, flags approved transactions for manual review, and writes operational reports.

## Requirements

- Python 3.9 or later.
- No third-party packages. All imports use the Python standard library.
- An accounts CSV and a transactions CSV in the same input folder.

## Project structure

```text
project/
    main.py
    db.py
    loader.py
    processor.py
    report.py
    README.md
    data/
        accounts.csv
        transactions.csv
```

This README assumes the command-line entry point is named `main.py`. Substitute its actual filename if different.

| File | Responsibility |
|---|---|
| `main.py` | Parse command-line options, identify inputs, and run loading, processing, and reporting |
| `db.py` | Connect to SQLite, initialize tables, and clear earlier processing outputs |
| `loader.py` | Normalize input and store transaction rows, original JSON, and parse errors |
| `processor.py` | Validate transactions, calculate balances, handle refunds, and generate review flags |
| `report.py` | Query the database and write text reports |

## Run

From the project folder:

```bash
python main.py ./data
```

Use `python3` if required by your environment.

Specify the database and report folder:

```bash
python main.py ./data --db cboj.db --out reports
```

For an input path containing spaces:

```bash
python main.py "C:/Users/YourName/Jarvis Hackathon"
```

| Argument | Default | Meaning |
|---|---|---|
| `folder` | Required | Folder containing the input CSVs |
| `--db` | `cboj.db` | SQLite database path |
| `--out` | `reports` | Output folder |

Relative paths resolve from the terminal's current working directory.

**Each command-line run resets the selected database.** It drops existing application tables and reloads the input files. Existing report files with the same names are overwritten. Use a dedicated database path for this application.

## Input files

The application identifies CSVs by their headers, so filenames can differ from the examples.

- A header containing `accountId` identifies account data.
- A header containing `transactionId` identifies transaction data.
- Unrecognized CSVs are skipped.
- CSV discovery scans the specified folder directly, without scanning subfolders.

Provide exactly one recognized file of each type. If several files have the same recognized type, the last filename in sorted order wins. Files with both identifying headers are classified as account files.

### Account columns

```csv
accountId,customerName,accountType,status,balance,dailyLimit,currency,openedDate
```

### Transaction columns

```csv
transactionId,timestamp,type,fromAccount,toAccount,amount,channel,description
```

Use ISO timestamps such as `2026-10-15T14:44:00`. Use consistent timezone conventions throughout the file.

| Type | Required account fields | Balance effect |
|---|---|---|
| `DEPOSIT` | `toAccount` | Credit destination |
| `PURCHASE` | `fromAccount` | Debit source |
| `WITHDRAWAL` | `fromAccount` | Debit source |
| `TRANSFER` | Both | Debit source and credit destination |
| `REVERSAL` | `fromAccount` | Credit source as a refund |

The validator checks the required account fields for each type. It does not reject unexpected extra account fields for single-account transactions.

## Processing workflow

1. Identify the two input CSVs from their headers.
2. Reset and initialize the database.
3. Load accounts and every transaction row.
4. Preserve original transaction values as JSON. Store parse errors with affected rows.
5. Copy opening balances into Python memory.
6. Process transactions in original file order.
7. Record a rejection reason or apply approved balance changes.
8. Track refund allowances and review rules.
9. Calculate velocity flags across approved transactions.
10. Save results, flags, and opening/final balance snapshots.
11. Generate reports from the saved database.

The processor leaves loaded `accounts` and `transactions` unchanged. Final balances live in `account_balances`.

## Validation rules

The first failed check determines the rejection reason.

| Order | Check | Rejection |
|---|---|---|
| 1 | Missing ID, unparseable amount/timestamp, or amount precision | `INVALID DATA (...)` |
| 2 | Earlier approved transaction has the same ID | `DUPLICATE TRANSACTION` |
| 3 | Unsupported or missing type | `UNSUPPORTED TYPE (...)` |
| 4 | Required account is missing or nonexistent | `INVALID ACCOUNT` |
| 5 | Transfer source and destination are the same | `INVALID TRANSFER (same account)` |
| 6 | Required account is not `ACTIVE` | `INACTIVE ACCOUNT` |
| 7 | Amount is zero or negative | `INVALID AMOUNT` |
| 8 | Debit would leave a negative balance | `INSUFFICIENT FUNDS` |

Rejected transactions change no balances and receive no review flags.

### Duplicate handling

Only approved transaction IDs enter the duplicate set. A rejected first attempt does not prevent a later valid retry with that ID. If the earlier attempt was approved, the later row is rejected as a duplicate.

### Money handling

The loader uses `Decimal` to convert monetary strings to integer cents. It strips dollar signs and commas. The processor rejects transaction amounts that cannot be represented exactly to two decimal places. Extra trailing zeros, such as `1.230`, do not change the numeric value and pass this check.

## Refund reversals

`REVERSAL` represents a refund credited to `fromAccount`.

After normal validation and crediting the account, the processor searches previously approved purchases, withdrawals, and outgoing transfers from that account. It searches newest first by processing order and selects one debit with enough remaining reversible amount.

- With a match, reduce that debit's remaining refundable amount.
- Without a match, keep the refund approved and add `NO ORIGINAL TRANSACTION`.
- A matched refund can still receive other review flags.

For example, a $100 debit can cover a $30 refund, leaving $70 available for a later matched refund.

**Matching is a heuristic.** It uses the account and remaining amount, without an original transaction ID or description match. It does not combine several smaller debits. A refund of an outgoing transfer credits the source without debiting the original recipient.

The allowance limits matched refunds against each debit. It does not cap all refunds, because unmatched refunds still process with a review flag.

## Manual review rules

Flags apply only to approved transactions and never prevent processing.

| Flag | Trigger |
|---|---|
| `LARGE AMOUNT` | Amount at least $10,000 |
| `NEAR LARGE-AMOUNT THRESHOLD` | Amount from $9,000 to below $10,000 |
| `DAILY LIMIT` | Approved outgoing amount for the calendar day exceeds the account's daily limit |
| `VELOCITY` | Transaction belongs to a window containing at least five approved events for one actor within ten minutes |
| `NO ORIGINAL TRANSACTION` | Refund has no eligible previously processed debit match |

These thresholds are project assumptions for manual review. They do not establish fraud or demonstrate regulatory compliance.

The amount flags are mutually exclusive. Other flags can coexist on the same transaction.

### Daily limits

Purchases, withdrawals, and outgoing transfers increase daily outgoing totals. Deposits, incoming transfers, reversals, and rejected rows do not. A refund does not reduce the earlier outgoing total.

The day comes from the transaction timestamp's date portion. The rule triggers only when the total is greater than the limit. A missing limit disables that check.

### Velocity

The actor is `fromAccount` when present, otherwise `toAccount`.

All approved transaction types can count, including deposits and reversals. For a transfer, only its source actor counts.

For each actor, the processor sorts event timestamps and checks windows:

```text
current timestamp - 10 minutes < event timestamp <= current timestamp
```

A transaction exactly ten minutes earlier is excluded. When a window contains five or more events, every event in that window receives the velocity flag, including earlier events. Events at the same timestamp are counted together.

## Reports

By default, open the `reports` folder after running the application.

| File | Contents |
|---|---|
| `results.txt` | Every transaction's approved/rejected status and rejection reason |
| `summary.txt` | Processed, approved, rejected, distinct flagged counts, and rejection breakdown |
| `flagged_report.txt` | Approved flagged transactions, their details, and combined review reasons |
| `balances.txt` | Opening balance, final balance, and change for accounts whose balances changed |

The summary also prints in the terminal.

Illustrative result format:

```text
TX001 APPROVED
TX002 REJECTED - INVALID ACCOUNT
```

These are format examples, not observed run results.

One transaction can have several flags but contributes only one to the summary's flagged count. Counts by flag reason can therefore sum to more than the number of distinct flagged transactions.

The rejection breakdown groups detailed `INVALID DATA` reasons under the common category. Open `results.txt` for the detailed reason.

## SQLite tables

| Table | Purpose |
|---|---|
| `accounts` | Loaded account information and opening balances |
| `transactions` | Every loaded transaction, original JSON, normalized fields, and parse errors |
| `results` | One outcome per transaction row |
| `flags` | Zero or more review reasons per transaction |
| `account_balances` | Opening and final balances |

Transaction IDs are deliberately not unique in the raw transactions table. `row_num` distinguishes duplicate input records and preserves file order.

The schema declares references from results and flags to transaction rows, but the connection does not explicitly enable SQLite foreign-key enforcement.

### Example queries

Rejected transactions:

```sql
SELECT t.row_num, t.transaction_id, r.reject_reason
FROM transactions AS t
JOIN results AS r USING (row_num)
WHERE r.status = 'REJECTED'
ORDER BY t.row_num;
```

Review reasons:

```sql
SELECT t.transaction_id, f.reason
FROM transactions AS t
JOIN flags AS f USING (row_num)
ORDER BY t.row_num;
```

Opening and final balances:

```sql
SELECT account_id,
       opening_balance_cents / 100.0 AS opening_balance,
       final_balance_cents / 100.0 AS final_balance
FROM account_balances
ORDER BY account_id;
```

## Assumptions and limitations

- Process transactions in file order, assuming the input is approximately chronological. The processor does not reorder balance updates by timestamp.
- Each command-line run resets application tables. Processing history does not persist across runs.
- Calling `process_all()` again clears output tables and recalculates from loaded opening balances.
- Repeated account IDs use the last loaded account row.
- Missing or unparseable opening balances become zero during processing.
- The loader does not fully validate account data or required header sets.
- Currency is stored but the processor does not validate or convert it.
- Input should use consistent timestamp timezone conventions. Mixed aware and naive timestamps can break velocity sorting.
- Nonfinite values such as `NaN` and `Infinity`, extreme amounts, and SQLite integer bounds need additional validation.
- The CLI does not close its database connection explicitly. Add a `try/finally` or context-managed cleanup for a stronger implementation.
- Processing saves output at batch end. It clears earlier outputs first, so a failed rerun can leave no replacement outputs.
- The velocity implementation repeatedly scans each actor's events. A sliding window would improve performance for larger inputs.
- Reports format money using division by 100, which uses floating-point formatting. Balance arithmetic itself uses integer cents.
- The supplied implementation has no automated test suite. Validate actual run results before presenting.

## Suggested verification

- Confirm approved plus rejected equals processed.
- Check that flagged rows are approved and counted once in the flagged total.
- Confirm rejected transactions leave balances unchanged.
- Check that an approved transfer debits and credits equal amounts.
- Try a duplicate following approval and a retry following rejection.
- Try matched, partially matched across repeated refunds, and unmatched reversals.
- Check $9,000, $10,000, exact daily limits, and the exclusive ten-minute velocity boundary.

## Team workflow

Share source files and example input data through Git. Keep generated databases and reports local.

Suggested `.gitignore` entries:

```gitignore
*.db
*.db-journal
*.db-wal
*.db-shm
reports/
__pycache__/
.venv/
```

Each teammate can run the same inputs to regenerate their own database and reports.
