"""The single "understand" call: stage-2 routing + entities + passage grading + rule extraction + SQL task.

Combining these keeps a hybrid question at 3 generation calls (understand -> SQL -> synthesis),
which fits the free-tier budget.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..config import get_settings
from ..llm import get_llm
from ..prompts import render
from ..retrieval.vector_search import DocHit

TABLE_SUMMARY = ("students(student_id, dept, year_of_study, cgpa, gpa_4, is_hosteller), departments, "
                 "courses(course_code, title, credits, course_type, capacity), enrollments(status, attendance_pct), "
                 "timetable, fee_structure(component, amount), fee_ledger(component, amount_due, amount_paid, "
                 "due_date, status), grants(grant_code, title, amount, application_open, application_deadline, "
                 "status), grant_applications")


class Entities(BaseModel):
    department: str = Field(description="CS, IT, ENTC or empty")
    year_of_study: int = Field(description="1-4, or 0 if not stated")
    cgpa: float = Field(description="10-point CGPA, or 0 if not stated")
    gpa_4: float = Field(description="4-point GPA, or 0 if not stated")
    student_id: str = Field(description="e.g. S2024CS007, or empty")
    grant_codes: list[str]
    reference_date: str = Field(description="ISO date stated in the question, or empty")


class ExtractedRule(BaseModel):
    subject: str
    field: str
    operator: str
    value: str
    effect: str
    citation: str


class Understanding(BaseModel):
    route: Literal["sql", "docs", "hybrid", "out_of_scope"]
    confidence: float
    reason: str
    sub_questions: list[str]
    entities: Entities
    needs_personal_scope: bool
    relevant_passages: list[int]
    rules: list[ExtractedRule]
    sql_task: str


def format_passages(hits: list[DocHit], max_chars: int = 1400) -> str:
    if not hits:
        return "(none retrieved)"
    return "\n\n".join(f"[{i}] {h.content[:max_chars]}" for i, h in enumerate(hits, 1))


def understand(question: str, profile_summary: str, router_hint: str, hits: list[DocHit]) -> tuple[Understanding, str]:
    prompt, pid = render("understand", question=question, profile=profile_summary, router_hint=router_hint,
                         passages=format_passages(hits), tables=TABLE_SUMMARY)
    result: Understanding = get_llm().generate(prompt, model=get_settings().fast_model,
                                               schema=Understanding)  # type: ignore[assignment]
    return result, pid
