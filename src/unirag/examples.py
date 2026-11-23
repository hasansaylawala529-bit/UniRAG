"""Example questions for the demo UI and the cache warm-up script, one group per route."""
from __future__ import annotations

import json

from .config import ROOT

DEMO_STUDENTS = ["S2026CS003", "S2026CS012", "S2025CS003", "S2025CS017", "S2023ENTC001"]

SQL_EXAMPLES = [
    "Which open grants close before January?",
    "How many students are there in each department and year?",
    "What are my overdue fees?",                       # personal: needs a logged-in student
]
DOCS_EXAMPLES = [
    "What happens if my attendance is 70 percent?",
    "How is the late fine on overdue fees calculated?",
    "Can I hold a research grant and a merit concession together?",
]
OUT_OF_SCOPE_EXAMPLES = [
    "What is the weather in Pune tomorrow?",
]


def hybrid_examples() -> list[tuple[str, str]]:
    """(id, question) for the six generator-verified hybrid questions."""
    path = ROOT / "data" / "hybrid_questions.json"
    try:
        return [(q["id"], q["question"]) for q in json.loads(path.read_text(encoding="utf-8"))]
    except FileNotFoundError:
        return []


def all_examples() -> list[str]:
    return SQL_EXAMPLES + DOCS_EXAMPLES + [q for _, q in hybrid_examples()] + OUT_OF_SCOPE_EXAMPLES
