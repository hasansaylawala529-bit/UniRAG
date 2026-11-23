"""Database connections. The app reads through the read-only role; only ingestion uses admin."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterator

import psycopg
from pgvector.psycopg import register_vector

from .config import get_settings

RELATIONAL_TABLES = [
    "departments", "students", "courses", "enrollments", "timetable",
    "fee_structure", "fee_ledger", "grants", "grant_applications",
]
VECTOR_TABLES = ["doc_chunks", "schema_cards", "entity_texts", "router_exemplars"]


@contextmanager
def connect(admin: bool = False) -> Iterator[psycopg.Connection]:
    s = get_settings()
    conn = psycopg.connect(s.db_admin_url if admin else s.db_reader_url, autocommit=not admin)
    try:
        register_vector(conn)
        yield conn
        if admin:
            conn.commit()
    except Exception:
        if admin:
            conn.rollback()
        raise
    finally:
        conn.close()


def jsonable(v: Any) -> Any:
    """Convert DB values to JSON-friendly values without losing precision."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (date, datetime, time)):
        return v.isoformat()
    return v
