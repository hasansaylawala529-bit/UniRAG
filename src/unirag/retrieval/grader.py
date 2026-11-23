"""Batched relevance grading (Self-RAG style) with the fast model: one call grades all passages."""
from __future__ import annotations

from pydantic import BaseModel

from ..config import get_settings
from ..llm import get_llm
from ..prompts import render
from .vector_search import DocHit


class Grade(BaseModel):
    id: int
    relevant: bool
    reason: str


class Grades(BaseModel):
    grades: list[Grade]


def grade_hits(question: str, hits: list[DocHit], max_chars: int = 1200) -> tuple[list[DocHit], str]:
    """Annotate hits with relevance. Returns (relevant hits in rank order, prompt id)."""
    if not hits:
        return [], "relevance_grade.v2"
    passages = "\n\n".join(f"[{i}] ({h.citation})\n{h.content[:max_chars]}" for i, h in enumerate(hits, 1))
    prompt, pid = render("relevance_grade", question=question, passages=passages)
    result: Grades = get_llm().generate(prompt, model=get_settings().fast_model, schema=Grades)  # type: ignore
    by_id = {g.id: g for g in result.grades}
    for i, h in enumerate(hits, 1):
        g = by_id.get(i)
        # if the grader skipped a passage, keep it rather than silently losing evidence
        h.relevant = True if g is None else g.relevant
        h.grade_reason = "not graded" if g is None else g.reason
    return [h for h in hits if h.relevant], pid
