"""Stage 1 of the two-stage router: semantic kNN vote over labelled exemplars (no generation call).

The question is embedded once and compared with the 60 labelled exemplars. The k nearest vote with
weight = similarity^4, which sharpens the vote towards the closest examples. Confidence is the
winning route's share of the vote. Stage 1 decides alone only when:
  - confidence >= CONF_THRESHOLD and the nearest example is similar enough (TOP_SIM_MIN), and
  - the hybrid-hint check does not contradict a single-source route.
Otherwise stage 2 (the LLM "understand" call) decides.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .db import connect
from .llm import get_llm

K = 7
POWER = 4
CONF_THRESHOLD = 0.70
TOP_SIM_MIN = 0.62

POLICY_TERMS = re.compile(
    r"\b(eligib\w*|qualif\w*|allowed|can i|am i|rule|polic\w*|fine|penalt\w*|refund\w*|concession|"
    r"scholarship|renew\w*|hall ticket|condon\w*|detain\w*|overload|limit|requirement|criteria|minimum|"
    r"maximum|permitted|entitled)\b", re.I)
DATA_TERMS = re.compile(
    r"\b(my|i am|i'm|how much|how many|deadline|when|amount|rupees|owe|balance|due|registered|"
    r"cgpa|gpa|year|S\d{4}[A-Z]{2,4}\d{3})\b|₹|\d", re.I)


@dataclass
class RouteDecision:
    route: str
    confidence: float
    stage: str                      # "semantic" or "llm"
    hybrid_hint: bool
    neighbours: list[dict] = field(default_factory=list)
    votes: dict[str, float] = field(default_factory=dict)
    needs_llm: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def hybrid_hint(question: str) -> bool:
    return bool(POLICY_TERMS.search(question)) and bool(DATA_TERMS.search(question))


def semantic_route(question: str) -> RouteDecision:
    qv = get_llm().embed_one(question, task_type="SEMANTIC_SIMILARITY")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT question, route, 1 - (embedding <=> %s::vector) AS sim
                       FROM router_exemplars ORDER BY embedding <=> %s::vector LIMIT %s""", (qv, qv, K))
        rows = cur.fetchall()
    votes: dict[str, float] = {}
    for _, route, sim in rows:
        votes[route] = votes.get(route, 0.0) + max(float(sim), 0.0) ** POWER
    total = sum(votes.values()) or 1.0
    votes = {r: round(v / total, 3) for r, v in sorted(votes.items(), key=lambda x: -x[1])}
    best = next(iter(votes)) if votes else "out_of_scope"
    conf = votes.get(best, 0.0)
    top_sim = float(rows[0][2]) if rows else 0.0
    hint = hybrid_hint(question)

    confident = conf >= CONF_THRESHOLD and top_sim >= TOP_SIM_MIN
    reason = f"kNN vote {conf:.2f} for '{best}', nearest example similarity {top_sim:.2f}"
    if hint and best in ("sql", "docs"):
        confident = False
        reason += "; hybrid hint (policy + data terms) contradicts a single-source route"
    return RouteDecision(
        route=best, confidence=conf, stage="semantic", hybrid_hint=hint,
        neighbours=[{"question": q, "route": r, "similarity": round(float(s), 3)} for q, r, s in rows],
        votes=votes, needs_llm=not confident, reason=reason)
