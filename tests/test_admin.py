"""Demo admin actions: payment touches one row, status is recomputed, reset is exact, assistant stays read-only.

Database tests are skipped when PostgreSQL is not reachable; each restores the seeded data afterwards.
"""
import csv
import re
from pathlib import Path

import psycopg
import pytest

from unirag import admin
from unirag.db import connect

SRC = Path(__file__).resolve().parents[1] / "src" / "unirag"
STUDENT = "S2026CS012"


def _db_available() -> bool:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM fee_ledger LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


needs_db = pytest.mark.skipif(not _db_available(), reason="PostgreSQL not reachable")


def _ledger() -> dict[int, tuple]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT txn_id, student_id, component, amount_due, amount_paid, due_date, paid_on, status "
                    "FROM fee_ledger ORDER BY txn_id")
        return {r[0]: r for r in cur.fetchall()}


@pytest.fixture
def clean_ledger():
    admin.reset_demo_data()
    yield
    admin.reset_demo_data()


# ------------------------------------------------------------------ separation (no database needed)
def test_pipeline_never_imports_admin():
    for path in SRC.rglob("*.py"):
        if path.name == "admin.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bimport\s+admin\b|from\s+\.+\s*import\s+admin|from\s+\.?admin\s+import|unirag\.admin",
                             text), f"{path} imports the admin module"


def test_status_rule():
    from datetime import date
    as_of = date(2026, 11, 23)
    assert admin.ledger_status(100, 100, date(2026, 8, 14), as_of) == "paid"
    assert admin.ledger_status(100, 0, date(2026, 8, 14), as_of) == "overdue"
    assert admin.ledger_status(100, 0, date(2027, 1, 15), as_of) == "pending"
    assert admin.ledger_status(100, 40, date(2027, 1, 15), as_of) == "partial"


# ------------------------------------------------------------------ database behaviour
@needs_db
def test_payment_updates_only_the_target_row(clean_ledger):
    before = _ledger()
    item = next(i for i in admin.unpaid_items(STUDENT) if i.status == "overdue")
    paid = admin.record_payment(STUDENT, item.txn_id)
    after = _ledger()
    changed = [t for t in before if before[t] != after[t]]
    assert changed == [item.txn_id]
    assert paid.status == "paid" and paid.amount_paid == paid.amount_due


@needs_db
def test_cannot_pay_another_students_item(clean_ledger):
    other = next(t for t, r in _ledger().items() if r[1] != STUDENT)
    with pytest.raises(ValueError):
        admin.record_payment(STUDENT, other)


@needs_db
def test_reset_restores_seed_exactly(clean_ledger):
    for i in admin.unpaid_items(STUDENT):
        admin.record_payment(STUDENT, i.txn_id)
    admin.reset_demo_data()
    with open(admin.SEED_LEDGER, newline="", encoding="utf-8") as f:
        seed = {int(r["txn_id"]): r for r in csv.DictReader(f)}
    now = _ledger()
    assert len(now) == len(seed)
    for t, r in now.items():
        s = seed[t]
        assert (r[1], r[2], float(r[3]), float(r[4]), str(r[5]), str(r[6]) if r[6] else "", r[7]) == \
               (s["student_id"], s["component"], float(s["amount_due"]), float(s["amount_paid"]),
                s["due_date"], s["paid_on"], s["status"])


@needs_db
def test_assistant_role_cannot_write():
    with connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.Error):
            cur.execute("UPDATE fee_ledger SET amount_paid = amount_due WHERE txn_id = 1")
