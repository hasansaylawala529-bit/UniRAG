"""Scoring helpers shared by the demo runner and the evaluation.

A key fact passes if it appears in the answer after the verifier's normalisation:
  - codes (GR-AI-01, CS102) and other words: case-insensitive substring match;
  - amounts, dates, percentages and numbers: compared as values (₹1,50,000 = 150000; 20 Dec 2026 = 2026-12-20).
"""
from __future__ import annotations

import re

from .synthesis.verifier import extract_figures

CODE_RE = re.compile(r"^(GR-[A-Z0-9]+-\d{2}|(?:CS|IT|ENTC|OE)\d{1,2}E?\d{0,2}|S\d{4}[A-Z]{2,4}\d{3})$")


def answer_values(answer: str) -> set[str]:
    return {f.value for f in extract_figures(answer)}


def fact_present(fact: str, answer: str, values: set[str] | None = None) -> bool:
    values = answer_values(answer) if values is None else values
    if CODE_RE.match(fact):
        return fact.upper() in answer.upper()
    figs = extract_figures(fact if not re.fullmatch(r"\d+", fact) else f"x {fact} x")
    if not figs:
        if re.fullmatch(r"[\d.]+", fact):      # small integers are ignored by the extractor: match literally
            return re.search(rf"(?<![\d.]){re.escape(fact)}(?![\d])", answer) is not None
        return fact.lower() in answer.lower()   # plain words / names
    return all(f.value in values for f in figs)


def score_answer(answer: str, key_facts: list[str], must_mention_any: list[str] | None = None) -> dict:
    vals = answer_values(answer)
    found = {f: fact_present(f, answer, vals) for f in key_facts}
    verdict_ok = True
    if must_mention_any:
        verdict_ok = any(p.lower() in answer.lower() for p in must_mention_any)
    return {"facts_found": sum(found.values()), "facts_total": len(found),
            "missing": [f for f, ok in found.items() if not ok], "verdict_ok": verdict_ok}
