"""Final answer: one generation call over SQL rows + policy passages + code-checked rules."""
from __future__ import annotations

import re

from ..config import get_settings
from ..llm import get_llm
from ..prompts import render
from ..retrieval.vector_search import DocHit
from .formatting import fix_inr_grouping, rows_to_prompt_table

CITATION_RE = re.compile(r"\[(SQL|Doc:[^\]]+)\]")


def passages_for_prompt(hits: list[DocHit], max_chars: int = 1500) -> str:
    if not hits:
        return "(no policy passages used)"
    blocks = []
    for h in hits:
        body = h.content.split("\n", 1)[-1].strip()
        blocks.append(f"[Doc: {h.doc_title} {h.section}]\n{body[:max_chars]}")
    return "\n\n".join(blocks)


def synthesize(question: str, *, profile_summary: str, columns: list[str], rows: list[dict],
               sql_note: str, rule_summary: str, hits: list[DocHit]) -> tuple[str, str, list[str]]:
    s = get_settings()
    prompt, pid = render(
        "synthesize", question=question, profile=profile_summary, as_of=s.as_of_date.strftime("%d %b %Y"),
        sql_table=rows_to_prompt_table(columns, rows) if columns else "(no database query for this question)",
        sql_note=sql_note, rule_summary=rule_summary or "(none)", passages=passages_for_prompt(hits))
    answer = get_llm().generate(prompt, model=s.gen_model)
    answer = fix_inr_grouping(str(answer).strip())
    citations = sorted(set(CITATION_RE.findall(answer)))
    return answer, pid, citations
