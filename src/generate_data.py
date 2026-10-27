"""Generate synthetic customers, accounts and transactions with injected errors.

Deterministic for a given seed. Besides the three CSV files it writes
``injected_errors.json``: the ground truth (source line + expected reason) that
lets tests and the final run measure a real detection rate.

Usage:  python -m src.generate_data [--transactions 50000] [--seed 42] [--out data]
"""
import argparse
import csv
import json
import os
import random
from datetime import date, timedelta

COUNTRIES = ["Germany", "France", "Spain", "Italy", "Netherlands", "Austria"]
TYPES = ["DEPOSIT", "WITHDRAWAL", "PAYMENT", "TRANSFER", "REFUND", "REVERSAL"]
FIRST_NAMES = ["Ali", "Sara", "Reza", "Anna", "Lukas", "Maria", "Jonas", "Lea", "Omid", "Nina"]

# Share of transactions that receive each injected error (applied to distinct rows).
ERROR_RATES = {
    "MISSING_AMOUNT": 0.010,
    "INVALID_AMOUNT": 0.010,
    "INVALID_DATE": 0.010,
    "ACCOUNT_NOT_FOUND": 0.010,
    "AMOUNT_NOT_ALLOWED_FOR_TYPE": 0.005,
    "INVALID_CURRENCY": 0.005,
    "DUPLICATE_TRANSACTION_ID": 0.020,
}

SOURCE_SYSTEMS = ["CORE_BANKING", "CARDS", "ONLINE_BANKING"]
SOURCE_WEIGHTS = [60, 25, 15]
HEADER_TX = ["transaction_id", "account_id", "amount", "currency", "transaction_type", "transaction_date",
             "source_system"]


def build_reference_data(rng, n_customers):
    customers = [
        {"customer_id": f"C{i:05d}", "name": rng.choice(FIRST_NAMES), "country": rng.choice(COUNTRIES)}
        for i in range(1, n_customers + 1)
    ]
    accounts = []
    for i, c in enumerate(customers, start=1):
        accounts.append({"account_id": f"A{i:05d}", "customer_id": c["customer_id"],
                         "status": "Closed" if rng.random() < 0.05 else "Active"})
    return customers, accounts


def build_transactions(rng, accounts, n):
    start = date(2026, 1, 1)
    span = (date(2026, 10, 15) - start).days
    rows = []
    for i in range(1, n + 1):
        ttype = rng.choice(TYPES)
        amount = round(rng.uniform(1, 5000), 2)
        if ttype in ("REFUND", "REVERSAL") and rng.random() < 0.5:
            amount = -amount  # negative amounts are legitimate for these types
        rows.append({
            "transaction_id": f"T{i:07d}",
            "account_id": rng.choice(accounts)["account_id"],
            "amount": f"{amount:.2f}",
            "currency": rng.choice(["EUR", "EUR", "EUR", "USD"]),
            "transaction_type": ttype,
            "transaction_date": (start + timedelta(days=rng.randint(0, span))).isoformat(),
            "source_system": rng.choices(SOURCE_SYSTEMS, weights=SOURCE_WEIGHTS)[0],
        })
    return rows


def inject_errors(rng, rows):
    """Corrupt distinct rows in place, append duplicates, return the ground truth."""
    n = len(rows)
    indices = list(range(n))
    rng.shuffle(indices)
    cursor = 0
    truth = []  # (row index, reason)

    def take(count):
        nonlocal cursor
        chosen = indices[cursor:cursor + count]
        cursor += count
        return chosen

    for reason, rate in ERROR_RATES.items():
        if reason == "DUPLICATE_TRANSACTION_ID":
            continue
        for idx in take(round(n * rate)):
            row = rows[idx]
            if reason == "MISSING_AMOUNT":
                row["amount"] = rng.choice(["", "NULL"])
            elif reason == "INVALID_AMOUNT":
                row["amount"] = rng.choice(["abc", "12,5x", "--"])
            elif reason == "INVALID_DATE":
                row["transaction_date"] = rng.choice(["invalid_date", "2026-13-45", "31.10.2026"])
            elif reason == "ACCOUNT_NOT_FOUND":
                # Generated accounts always start with A; this namespace cannot collide,
                # even once the reference data reaches A90000 and beyond.
                row["account_id"] = "UNKNOWN_ACCOUNT_" + row["account_id"]
            elif reason == "AMOUNT_NOT_ALLOWED_FOR_TYPE":
                row["transaction_type"] = "PAYMENT"
                row["amount"] = f"-{abs(float(row['amount'])):.2f}"
            elif reason == "INVALID_CURRENCY":
                row["currency"] = "XXX"
            truth.append((idx, reason))

    # Duplicates are copies of clean rows appended at the end, so the original
    # (lower line number) is always the one that is kept.
    clean = indices[cursor:]
    n_dupes = round(n * ERROR_RATES["DUPLICATE_TRANSACTION_ID"])
    dupes = []
    for k, idx in enumerate(rng.sample(clean, n_dupes)):
        copy = dict(rows[idx])
        conflicting = k % 4 == 0  # every 4th duplicate differs in content
        if conflicting:
            # Change content without creating an unrelated amount error
            # (for example, adding 1 to a -1.00 refund would produce zero).
            copy["currency"] = "USD" if copy["currency"] == "EUR" else "EUR"
        dupes.append((copy, conflicting))
    for copy, conflicting in dupes:
        rows.append(copy)
        reasons = ["DUPLICATE_TRANSACTION_ID"] + (["DUPLICATE_CONFLICTING_CONTENT"] if conflicting else [])
        for reason in reasons:
            truth.append((len(rows) - 1, reason))

    # line number = index + 2 (header is line 1)
    return [{"line": idx + 2, "expected_reason": reason} for idx, reason in sorted(truth)]


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def generate(out_dir="data", n_transactions=50000, seed=42):
    if n_transactions < 0:
        raise ValueError("transactions must be zero or greater")
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    n_customers = max(10, n_transactions // 20)
    customers, accounts = build_reference_data(rng, n_customers)
    transactions = build_transactions(rng, accounts, n_transactions)
    truth = inject_errors(rng, transactions)
    write_csv(os.path.join(out_dir, "customers.csv"), ["customer_id", "name", "country"], customers)
    write_csv(os.path.join(out_dir, "accounts.csv"), ["account_id", "customer_id", "status"], accounts)
    write_csv(os.path.join(out_dir, "transactions.csv"), HEADER_TX, transactions)
    with open(os.path.join(out_dir, "injected_errors.json"), "w", encoding="utf-8") as f:
        json.dump(truth, f, indent=1)
    return {"customers": len(customers), "accounts": len(accounts),
            "transactions": len(transactions), "injected_errors": len(truth)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--transactions", type=int, default=50000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="data")
    a = p.parse_args()
    if a.transactions < 0:
        p.error("--transactions must be zero or greater")
    print(generate(a.out, a.transactions, a.seed))
