"""Text-to-SQL path: schema-card retrieval -> generation -> guard -> read-only execution -> self-correction."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import connect, jsonable
from ..llm import get_llm
from ..prompts import render
from .sql_guard import GuardError, guard_sql

HUB_TABLES = ("students", "departments", "courses", "grants")


class SQLDraft(BaseModel):
    sql: str = Field(description="A single read-only PostgreSQL SELECT query")
    explanation: str = Field(description="One sentence on what the query computes")


@dataclass
class SQLAttempt:
    sql: str
    error: str | None = None
    guarded_sql: str | None = None
    guard_notes: list[str] = field(default_factory=list)


@dataclass
class SQLResult:
    question: str
    ok: bool
    sql: str | None
    columns: list[str]
    rows: list[dict[str, Any]]
    explanation: str
    tables_used: list[str]
    cards_used: list[str]
    entity_hints: list[str]
    attempts: list[SQLAttempt]
    prompt_ids: list[str]
    latency_ms: int
    error: str | None = None
    models_used: list[str] = field(default_factory=list)

    @property
    def unanswerable(self) -> bool:
        return bool(self.rows) and self.columns == ["reason"] and self.rows[0].get("reason") == "UNANSWERABLE"

    def as_markdown(self, max_rows: int = 50) -> str:
        if not self.rows:
            return "_(query returned no rows)_"
        head = "| " + " | ".join(self.columns) + " |\n|" + "---|" * len(self.columns) + "\n"
        body = "\n".join("| " + " | ".join("" if r[c] is None else str(r[c]) for c in self.columns) + " |"
                         for r in self.rows[:max_rows])
        more = f"\n_({len(self.rows) - max_rows} more rows)_" if len(self.rows) > max_rows else ""
        return head + body + more

    def to_dict(self) -> dict:
        return asdict(self)


def retrieve_schema_cards(question: str, k: int = 4) -> list[tuple[str, str]]:
    """Top-k cards by similarity, plus hub tables that the chosen cards join to."""
    qv = get_llm().embed_one(question, task_type="RETRIEVAL_QUERY")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name, card_text FROM schema_cards ORDER BY embedding <=> %s::vector LIMIT %s",
                    (qv, k))
        top = cur.fetchall()
        names = {t for t, _ in top}
        needed = {h for h in HUB_TABLES if h not in names and any(f"-> {h}." in c for _, c in top)}
        if needed:
            cur.execute("SELECT table_name, card_text FROM schema_cards WHERE table_name = ANY(%s)", (list(needed),))
            top += cur.fetchall()
    return top


def retrieve_entities(question: str, k: int = 5, min_score: float = 0.55) -> list[str]:
    qv = get_llm().embed_one(question, task_type="RETRIEVAL_QUERY")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT text, 1 - (embedding <=> %s::vector) AS score FROM entity_texts
                       ORDER BY embedding <=> %s::vector LIMIT %s""", (qv, qv, k))
        return [t for t, score in cur.fetchall() if score >= min_score]


def execute_readonly(sql: str) -> tuple[list[str], list[dict[str, Any]]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description] if cur.description else []
        rows = [{c: jsonable(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
    return cols, rows


def answer_sql(question: str, *, student_id: str | None = None, extra_context: str = "",
               max_retries: int = 2) -> SQLResult:
    s = get_settings()
    llm = get_llm()
    t0 = time.perf_counter()

    cards = retrieve_schema_cards(question)
    entities = retrieve_entities(question)
    card_text = "\n\n".join(c for _, c in cards)
    student_ctx = (f"- The user is logged in as student_id '{student_id}'. Questions about 'me/my' refer to "
                   f"this student." if student_id else "")
    common = dict(schema_cards=card_text, as_of=s.as_of_date.isoformat(), student_context=student_ctx,
                  extra_context=extra_context or "", question=question)

    prompt, pid = render("sql_generate", entity_hints="\n".join(f"- {e}" for e in entities) or "- (none)", **common)
    prompt_ids = [pid]
    attempts: list[SQLAttempt] = []
    draft: SQLDraft = llm.generate(prompt, schema=SQLDraft)  # type: ignore[assignment]
    models = [getattr(llm, "last_model_used", None) or s.gen_model]

    for attempt_no in range(max_retries + 1):
        att = SQLAttempt(sql=draft.sql)
        attempts.append(att)
        try:
            g = guard_sql(draft.sql, student_id=student_id, max_limit=s.sql_row_limit)
            att.guarded_sql, att.guard_notes = g.sql, g.notes
            cols, rows = execute_readonly(g.sql)
            return SQLResult(question, True, g.sql, cols, rows, draft.explanation, g.tables,
                             [t for t, _ in cards], entities, attempts, prompt_ids,
                             int((time.perf_counter() - t0) * 1000), models_used=models)
        except (GuardError, psycopg.Error) as e:
            att.error = f"{type(e).__name__}: {str(e).strip().splitlines()[0]}"
            if attempt_no == max_retries:
                break
            prompt, pid = render("sql_repair", failed_sql=draft.sql, error=att.error, **common)
            prompt_ids.append(pid)
            draft = llm.generate(prompt, schema=SQLDraft)  # type: ignore[assignment]
            models.append(getattr(llm, "last_model_used", None) or s.gen_model)

    return SQLResult(question, False, None, [], [], draft.explanation, [], [t for t, _ in cards], entities,
                     attempts, prompt_ids, int((time.perf_counter() - t0) * 1000),
                     error=attempts[-1].error, models_used=models)


GRANT_COLUMNS = ["grant_code", "title", "amount", "application_open", "application_deadline", "status"]


def compiled_grant_query(eligible_codes: list[str]) -> SQLResult:
    """Grant eligibility, compiled from the rule engine's result: no LLM, parameterised, read-only.

    Once the planner has decided which grants the profile is eligible for, the database question is
    fully determined ("live amount, dates and status for these codes"). Generating it with a model
    only adds a way to get it wrong (live run P-010: the model dropped the code filter and added an
    unscoped fee check), so it is compiled in code instead. Every eligible grant is returned with its
    status; the answer step groups them into open / upcoming / closed.
    """
    t0 = time.perf_counter()
    sql = ("SELECT " + ", ".join(GRANT_COLUMNS) + " FROM grants WHERE grant_code = ANY(%s) "
           "ORDER BY application_deadline")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (list(eligible_codes),))
        rows = [dict(zip(GRANT_COLUMNS, (jsonable(v) for v in r))) for r in cur.fetchall()]
    shown = sql.replace("%s", "ARRAY[" + ", ".join(repr(c) for c in eligible_codes) + "]")
    return SQLResult("compiled from planner constraints", True, shown, list(GRANT_COLUMNS), rows,
                     "Live amount, dates and status for every grant the rule engine found eligible.",
                     ["grants"], [], [], [SQLAttempt(sql=shown, guarded_sql=shown,
                                                     guard_notes=["compiled in code (no LLM)"])],
                     [], int((time.perf_counter() - t0) * 1000), models_used=[])
