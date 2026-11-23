"""Demo-only admin actions: record a fee payment and reset demo data.

This module is the ONLY code that writes to the database at runtime, and it is completely separate
from the question-answering path:
  - it uses the admin connection; the assistant uses the read-only role;
  - it is called only by an explicit button in the UI, never by an LLM or by ask();
  - no module in the pipeline imports it (enforced by tests/test_admin.py).
Its purpose is to prove live freshness: change a fee in the database, ask again, and see the new answer
with no re-ingestion.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date

from .config import ROOT, get_settings
from .db import connect, jsonable

SEED_LEDGER = ROOT / "data" / "seed" / "fee_ledger.csv"
LEDGER_COLUMNS = ["txn_id", "student_id", "academic_year", "component", "amount_due", "amount_paid",
                  "due_date", "paid_on", "status"]


@dataclass
class FeeItem:
    txn_id: int
    component: str
    amount_due: float
    amount_paid: float
    due_date: str
    status: str

    @property
    def balance(self) -> float:
        return self.amount_due - self.amount_paid


def ledger_status(amount_due: float, amount_paid: float, due_date: date, as_of: date) -> str:
    """Same rule as the data generator and the Fee Policy §3."""
    if amount_paid >= amount_due:
        return "paid"
    if due_date >= as_of:
        return "partial" if amount_paid > 0 else "pending"
    return "overdue"


def unpaid_items(student_id: str) -> list[FeeItem]:
    with connect() as conn, conn.cursor() as cur:       # reading only: the read-only role is enough
        cur.execute("""SELECT txn_id, component, amount_due, amount_paid, due_date, status FROM fee_ledger
                       WHERE student_id = %s AND amount_paid < amount_due ORDER BY due_date""", (student_id,))
        return [FeeItem(*(jsonable(v) for v in r)) for r in cur.fetchall()]


def record_payment(student_id: str, txn_id: int) -> FeeItem:
    """Pay one fee item in full. Touches exactly one row of this student's ledger, or nothing."""
    as_of = get_settings().as_of_date
    with connect(admin=True) as conn, conn.cursor() as cur:
        cur.execute("""SELECT amount_due, amount_paid, due_date FROM fee_ledger
                       WHERE txn_id = %s AND student_id = %s FOR UPDATE""", (txn_id, student_id))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Fee item {txn_id} does not belong to {student_id}.")
        due, _, due_date = row
        status = ledger_status(float(due), float(due), due_date, as_of)
        cur.execute("""UPDATE fee_ledger SET amount_paid = amount_due, paid_on = %s, status = %s
                       WHERE txn_id = %s AND student_id = %s""", (as_of, status, txn_id, student_id))
        if cur.rowcount != 1:
            raise RuntimeError("Payment must update exactly one row; rolled back.")
        cur.execute("""SELECT txn_id, component, amount_due, amount_paid, due_date, status FROM fee_ledger
                       WHERE txn_id = %s""", (txn_id,))
        return FeeItem(*(jsonable(v) for v in cur.fetchone()))


def reset_demo_data() -> int:
    """Restore fee_ledger exactly to the seeded CSV (one transaction). Returns the number of rows."""
    with open(SEED_LEDGER, newline="", encoding="utf-8") as f:
        rows = [[(None if v == "" else v) for v in (r[c] for c in LEDGER_COLUMNS)] for r in csv.DictReader(f)]
    with connect(admin=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM fee_ledger")
        cur.executemany("INSERT INTO fee_ledger (" + ", ".join(LEDGER_COLUMNS) + ") VALUES ("
                        + ", ".join(["%s"] * len(LEDGER_COLUMNS)) + ")", rows)
    return len(rows)
