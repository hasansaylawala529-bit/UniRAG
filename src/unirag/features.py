"""Component switches for the ablation study (all on by default).

    UNIRAG_ABLATE=semantic_router,rule_engine     # disable components through the environment
    with ablate("schema_cards"): ask(...)          # or temporarily, in code (eval/run_ablation.py)

    semantic_router    kNN router over labelled exemplars; off -> every question is routed by the LLM planner
    rule_engine        rules evaluated in code and grant SQL compiled from them; off -> the extracted rules are
                       handed to the LLM-generated SQL as text
    relevance_grading  only passages judged relevant reach the answer; off -> the top passages, ungraded
    schema_cards       SQL step sees the retrieved table cards; off -> it sees the full schema
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, fields
from typing import Iterator

COMPONENTS = ("semantic_router", "rule_engine", "relevance_grading", "schema_cards")
UNGRADED_TOP_K = 5          # passages used when relevance grading is off


@dataclass
class Features:
    semantic_router: bool = True
    rule_engine: bool = True
    relevance_grading: bool = True
    schema_cards: bool = True

    def disabled(self) -> list[str]:
        return [f.name for f in fields(self) if not getattr(self, f.name)]


def _from_env() -> Features:
    off = {x.strip() for x in os.getenv("UNIRAG_ABLATE", "").split(",") if x.strip()}
    unknown = off - set(COMPONENTS)
    if unknown:
        raise ValueError(f"UNIRAG_ABLATE: unknown component(s) {sorted(unknown)}; choose from {COMPONENTS}")
    return Features(**{c: c not in off for c in COMPONENTS})


FEATURES = _from_env()


@contextmanager
def ablate(*names: str) -> Iterator[Features]:
    """Temporarily disable the named components (restored afterwards, even on error)."""
    unknown = set(names) - set(COMPONENTS)
    if unknown:
        raise ValueError(f"unknown component(s) {sorted(unknown)}; choose from {COMPONENTS}")
    saved = {c: getattr(FEATURES, c) for c in COMPONENTS}
    try:
        for n in names:
            setattr(FEATURES, n, False)
        yield FEATURES
    finally:
        for c, v in saved.items():
            setattr(FEATURES, c, v)
