"""Build one natural-language "table card" per relational table.

The mechanical parts (columns, types, keys, row counts, sample values and ranges) are read live from
information_schema and the data on every ingest, so cards never drift from the real schema.
The business meaning (purpose, column notes) is hand-written in DESCRIPTIONS below.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg import sql as q

from ..db import RELATIONAL_TABLES, jsonable

DESCRIPTIONS: dict[str, dict] = {
    "departments": {
        "purpose": "The three academic departments: CS (Computer Science & Engineering), IT (Information "
                   "Technology), ENTC (Electronics & Telecommunication), with the Head of Department.",
        "notes": {"code": "Short code used everywhere in conversation: CS, IT, ENTC."},
    },
    "students": {
        "purpose": "One row per enrolled undergraduate student: department, year of study, CGPA, hostel status.",
        "notes": {
            "student_id": "Format S<admission year><dept code><nnn>, e.g. S2024CS007.",
            "year_of_study": "1-4. 'Third-year' means year_of_study = 3.",
            "cgpa": "10-point scale. NULL for first-year students (no result declared yet).",
            "gpa_4": "4-point equivalent = cgpa * 0.4. Use cgpa for all eligibility comparisons.",
            "is_hosteller": "true if the student currently lives in a University hostel.",
        },
    },
    "courses": {
        "purpose": "Courses offered in the current term (2026-ODD) with credits, type and seat capacity.",
        "notes": {
            "course_type": "core, lab, dept_elective (department electives, years 3-4) or open_elective "
                           "(open to all departments).",
            "semester": "Odd term semesters only: 1, 3, 5, 7 (year = (semester+1)/2).",
            "capacity": "Maximum seats. Seats taken = count of enrollments with status 'enrolled'.",
        },
    },
    "enrollments": {
        "purpose": "Which student is registered in which course this term, with attendance so far.",
        "notes": {
            "status": "'enrolled' counts towards credits and seats; 'dropped' does not.",
            "attendance_pct": "Attendance percentage in that course this term (0-100).",
            "grade": "NULL until results are declared.",
        },
    },
    "timetable": {
        "purpose": "Weekly class schedule for the current term: day, start/end time and room for each course.",
        "notes": {"day_of_week": "Mon, Tue, Wed, Thu or Fri."},
    },
    "fee_structure": {
        "purpose": "Official fee amounts (INR) per department and year of study for the academic year.",
        "notes": {
            "component": "tuition (annual, paid in two instalments 60%/40%), exam (per term), lab (annual), "
                         "hostel (annual, hostellers only).",
        },
    },
    "fee_ledger": {
        "purpose": "Each student's live fee account: what is due, what was paid, due dates and status.",
        "notes": {
            "component": "tuition_inst1 (60% of tuition), tuition_inst2 (40%), exam, lab, hostel.",
            "status": "paid = fully cleared; pending = not yet due and unpaid; partial = part-paid before the "
                      "due date; overdue = balance remaining after the due date.",
            "amount_due": "Balance outstanding = amount_due - amount_paid.",
        },
    },
    "grants": {
        "purpose": "Grants Register: every research grant's amount, number of awards, application window, "
                   "deadline and current status. Eligibility rules are NOT here; they are in the Research "
                   "Grant Policy document, linked by grant_code.",
        "notes": {
            "grant_code": "Unique code such as GR-AI-01; the same code appears in the policy document.",
            "status": "open = accepting applications; upcoming = opens later (see application_open); "
                      "closed = no longer accepting (deadline passed or all awards filled).",
            "amount": "INR per award.",
        },
    },
    "grant_applications": {
        "purpose": "Applications students have submitted for grants, with their review status.",
        "notes": {"status": "submitted, under_review, awarded or rejected."},
    },
}


@dataclass
class SchemaCard:
    table_name: str
    card_text: str


def _columns(cur: psycopg.Cursor, table: str) -> list[tuple[str, str]]:
    cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position""", (table,))
    return cur.fetchall()


def _primary_key(cur: psycopg.Cursor, table: str) -> set[str]:
    cur.execute("""SELECT kcu.column_name FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                   WHERE tc.table_schema = 'public' AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'""",
                (table,))
    return {r[0] for r in cur.fetchall()}


def _foreign_keys(cur: psycopg.Cursor, table: str) -> dict[str, str]:
    cur.execute("""SELECT kcu.column_name, ccu.table_name, ccu.column_name
                   FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                   JOIN information_schema.constraint_column_usage ccu
                     ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
                   WHERE tc.table_schema = 'public' AND tc.table_name = %s AND tc.constraint_type = 'FOREIGN KEY'""",
                (table,))
    return {c: f"{t}.{rc}" for c, t, rc in cur.fetchall()}


def _profile(cur: psycopg.Cursor, table: str, col: str, dtype: str) -> str:
    ident_t, ident_c = q.Identifier(table), q.Identifier(col)
    if dtype in ("numeric", "integer", "smallint", "date", "time without time zone"):
        cur.execute(q.SQL("SELECT min({c}), max({c}), count(*) FILTER (WHERE {c} IS NULL) FROM {t}")
                    .format(c=ident_c, t=ident_t))
        lo, hi, nulls = cur.fetchone()
        s = f"range {jsonable(lo)} to {jsonable(hi)}"
        return s + (f"; {nulls} NULL" if nulls else "")
    cur.execute(q.SQL("SELECT count(DISTINCT {c}) FROM {t}").format(c=ident_c, t=ident_t))
    n = cur.fetchone()[0]
    cur.execute(q.SQL("SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL ORDER BY 1 LIMIT 8")
                .format(c=ident_c, t=ident_t))
    vals = [str(jsonable(r[0])) for r in cur.fetchall()]
    if n <= 8:
        return "values: " + ", ".join(vals)
    return f"{n} distinct, e.g. " + ", ".join(vals[:3])


def build_cards(conn: psycopg.Connection) -> list[SchemaCard]:
    cards = []
    with conn.cursor() as cur:
        for t in RELATIONAL_TABLES:
            desc = DESCRIPTIONS.get(t, {"purpose": "", "notes": {}})
            cur.execute(q.SQL("SELECT count(*) FROM {}").format(q.Identifier(t)))
            rows = cur.fetchone()[0]
            pk, fks = _primary_key(cur, t), _foreign_keys(cur, t)
            lines = [f"Table: {t}", f"Purpose: {desc['purpose']}", f"Rows: {rows}", "Columns:"]
            for col, dtype in _columns(cur, t):
                tags = []
                if col in pk:
                    tags.append("PK")
                if col in fks:
                    tags.append(f"FK -> {fks[col]}")
                tag = f" [{', '.join(tags)}]" if tags else ""
                line = f"- {col} ({dtype}){tag}: {_profile(cur, t, col, dtype)}"
                if col in desc["notes"]:
                    line += f". {desc['notes'][col]}"
                lines.append(line)
            if fks:
                lines.append("Joins: " + "; ".join(f"{t}.{c} = {ref}" for c, ref in fks.items()))
            cards.append(SchemaCard(t, "\n".join(lines)))
    return cards
