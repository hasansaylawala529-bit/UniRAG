"""UniRAG orchestration as a LangGraph state machine.

    profile -> route --(confident sql)----------------------------> query_db -> synthesize -> verify
                     --(confident out_of_scope)-------------------> decline
                     --(otherwise)--> retrieve_docs --(confident docs)--> grade_docs -> synthesize
                                                    --(hybrid / unsure)--> understand --sql----> query_db
                                                                                      --docs---> synthesize
                                                                                      --hybrid-> apply_rules -> query_db
                                                                                      --oos----> decline

Generation-call budget (free tier): sql 2 · docs 2 · hybrid 3 (understand, SQL, synthesis);
SQL self-repair adds a call only when a query fails.
"""
from __future__ import annotations

import operator
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from functools import lru_cache
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import get_settings
from .features import FEATURES, UNGRADED_TOP_K
from .llm import get_llm
from .planner.rules import RuleEvaluation, evaluate_rules
from .planner.understand import Understanding, understand
from .profile import Profile, QuestionEntities, build_profile, merge_llm_entities
from .retrieval.grader import grade_hits
from .retrieval.sql_agent import SQLResult, answer_sql, compiled_grant_query
from .retrieval.sql_guard import PERSONAL_TABLES
from .retrieval.vector_search import DocFilters, DocHit, grant_chunks, search_docs
from .router import RouteDecision, semantic_route
from .synthesis.synthesize import passages_for_prompt, synthesize
from .synthesis.formatting import inr
from .synthesis.verifier import VerificationReport, build_pool, database_totals, verify_answer

GRANT_WORDS = re.compile(r"\b(grant|grants|fellowship|scholarship|funding|research|stipend|assistantship)\b", re.I)
WRITE_MESSAGE = ("I can read university records but I can't change them: the assistant has read-only access. "
                 "Fee payments, grade or attendance corrections are handled by the Accounts Office or the "
                 "Examination Cell.")
OWN_RECORDS_MESSAGE = ("You can only view your own records. Questions about another student's fees, attendance or "
                       "results need staff access.")
STAFF_ONLY_MESSAGE = ("Records of an individual student need a logged-in student (their own records) or staff "
                      "access. Without logging in I can share policies and overall figures such as counts and "
                      "averages.")
LOGIN_MESSAGE = ("This question is about your own records (fees, attendance, enrolment). Please choose your "
                 "student ID under \"Logged in as\" so I can show only your data.")
CONTACT_MESSAGE = ("Students' contact details (e-mail addresses and phone numbers) are never shared by the "
                   "assistant, whatever the role. To reach a student, please go through the Academic Office.")
# fixed answers for database outcomes, so the model never improvises a claim about what the data contains
EMAIL_OMITTED_MESSAGE = ("That query would have included students' e-mail addresses, which are never shared by the "
                         "assistant. Please ask for the specific details you need, for example names, departments, "
                         "years or CGPA.")
SCOPED_NOTE = ("As a student you can see only your own records, so this answer covers your own record only; "
               "other students' records need staff access.")
UNANSWERABLE_MESSAGE = ("The university database doesn't hold that information, or it isn't accessible to the "
                        "assistant. I can answer questions about fees and dues, research grants, courses, "
                        "enrolments, attendance, timetables and university policies.")
NOT_ACCESSIBLE_MESSAGE = ("That data isn't accessible to the assistant: it can read only the university's fee, "
                          "grant, course, enrolment, attendance and timetable records, never system tables, "
                          "credentials or contact details.")
DB_FAILED_MESSAGE = ("I couldn't get an answer from the university database for this question, so I won't guess. "
                     "Please try rephrasing it, or ask again in a moment.")
OWN_NO_ROWS_MESSAGE = ("No matching records were found in your own records. As a student you can see only your "
                       "own data; questions about other students' records need staff access.")
OWN_NONE_MESSAGE = "Nothing matched in your records for this question."
NO_ROWS_MESSAGE = "The database query ran but found no matching records."
DECLINE_MESSAGE = ("I can only help with DVUT questions about courses and timetables, fees and refunds, research "
                   "grants and scholarships, exams and attendance, hostel rules and course registration. "
                   "Could you rephrase your question around one of these?")


class State(TypedDict, total=False):
    question: str
    student_id: str | None
    profile: Profile
    entities: QuestionEntities
    decision: RouteDecision
    route: str
    router_stage: str
    needs_understand: bool
    hits: list[DocHit]
    relevant_hits: list[DocHit]
    understanding: Understanding | None
    rule_eval: RuleEvaluation | None
    constraints: str
    fallback: bool
    role: str                         # anonymous | student | staff
    refusal: str                      # set when a policy layer refuses; answer = this message, no LLM
    refused_by: str
    sql: SQLResult | None
    answer: str
    citations: list[str]
    verification: VerificationReport | None
    notes: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]


def traced(name: str) -> Callable:
    """Record a trace event per node: latency, LLM calls, model, prompt version and a short summary."""
    def deco(fn: Callable[[State], tuple[dict, dict]]) -> Callable[[State], dict]:
        def wrapper(state: State) -> dict:
            llm = get_llm()
            g0, h0, r0 = llm.stats.gen_calls, llm.stats.cache_hits, llm.stats.gen_requests
            k0 = len(getattr(llm, "skip_events", []))
            t0 = time.perf_counter()
            updates, info = fn(state)
            event = {"node": name, "ms": int((time.perf_counter() - t0) * 1000),
                     "gen_requests": llm.stats.gen_requests - r0,
                     "llm_calls": llm.stats.gen_calls - g0,
                     "cache_hits": llm.stats.cache_hits - h0, **info}
            skipped = getattr(llm, "skip_events", [])[k0:]
            if skipped:
                event["skipped_models"] = skipped
            if event["gen_requests"] or info.get("prompt"):
                event.setdefault("model", getattr(llm, "last_model_used", None))
            updates["trace"] = [event]
            return updates
        wrapper.__name__ = name
        return wrapper
    return deco


def refusal_for_block(r, role: str, ents: QuestionEntities) -> str:
    """The message for a query the SQL guard refused, with the reason that fits the question and the role.

    The contact-details message is only for questions that ask for contact data; a query that merely included
    the e-mail column (and could not be regenerated without it) gets the role's own reason.
    """
    if r.hidden_column and ents.contact_request:
        return CONTACT_MESSAGE
    if role == "student":
        return OWN_RECORDS_MESSAGE
    if role == "anonymous":
        return STAFF_ONLY_MESSAGE
    return EMAIL_OMITTED_MESSAGE if r.hidden_column else r.privacy_block


def scoped_to_own_records(state: State) -> bool:
    """A student's query about others (not 'my ...') that the guard narrowed to the student's own rows."""
    r = state.get("sql")
    if state.get("role") != "student" or r is None or not r.ok or not r.rows or not r.attempts:
        return False
    ents = state.get("entities")
    return (any(n.startswith("Scoped") for n in r.attempts[-1].guard_notes)
            and not (ents is not None and ents.first_person))


def policy_refusal(ents: QuestionEntities, role: str, student_id: str | None) -> tuple[str, str] | None:
    """Deterministic access-policy checks on a question: (message, rule) when it must be refused, else None."""
    if ents.write_intent:
        return WRITE_MESSAGE, "write-intent rule"
    if ents.contact_request:
        return CONTACT_MESSAGE, "privacy rule (contact data)"
    if role == "student" and ents.student_id and ents.student_id != student_id:
        return OWN_RECORDS_MESSAGE, "privacy rule (other student)"
    if role == "anonymous" and ents.student_id:
        return STAFF_ONLY_MESSAGE, "privacy rule (named student)"
    if ents.personal_records and not student_id and not ents.student_id:
        return LOGIN_MESSAGE, "privacy rule (not logged in)"
    return None


# ------------------------------------------------------------------ nodes
@traced("profile")
def node_profile(state: State) -> tuple[dict, dict]:
    role = "student" if state.get("student_id") else (state.get("role") or "anonymous")
    profile, ents = build_profile(state["question"], state.get("student_id"))
    base = {"profile": profile, "entities": ents, "role": role, "notes": []}
    # deterministic policy checks: refuse before any model call (0 generation requests)
    refusal = policy_refusal(ents, role, state.get("student_id"))
    if refusal:
        return ({**base, "refusal": refusal[0], "refused_by": refusal[1], "route": "refused",
                 "router_stage": "rule"},
                {"summary": profile.summary(), "role": role, "refused_by": refusal[1]})
    return (base,
            {"summary": profile.summary(), "role": role,
             "entities": {k: v for k, v in asdict(ents).items() if v not in (None, [], False)}})


@traced("route")
def node_route(state: State) -> tuple[dict, dict]:
    if not FEATURES.semantic_router:        # ablation: the LLM planner routes every question
        d = RouteDecision(route="hybrid", confidence=0.0, stage="llm", hybrid_hint=False, needs_llm=True,
                          reason="semantic router disabled (ablation)")
        return ({"decision": d, "route": d.route, "router_stage": "llm", "needs_understand": True},
                {"route": None, "ablated": "semantic_router", "reason": d.reason})
    d = semantic_route(state["question"])
    needs = d.needs_llm or d.route == "hybrid"
    return ({"decision": d, "route": d.route, "router_stage": "semantic", "needs_understand": needs},
            {"route": d.route, "confidence": d.confidence, "confident": not d.needs_llm,
             "hybrid_hint": d.hybrid_hint, "votes": d.votes, "reason": d.reason,
             "nearest": d.neighbours[:3]})


@traced("retrieve_docs")
def node_retrieve(state: State) -> tuple[dict, dict]:
    p, q = state["profile"], state["question"]
    filters = DocFilters(department=p.department, year=p.year_of_study)
    hits = search_docs(q, k=8, filters=filters)
    grant_mode = bool(GRANT_WORDS.search(q))
    if grant_mode:
        # all grants that pass the department/year metadata filter; CGPA is checked by the rule engine
        seen = {h.chunk_id for h in hits}
        hits += [h for h in grant_chunks(department=p.department, year=p.year_of_study) if h.chunk_id not in seen]
    return ({"hits": hits},
            {"filters": {k: v for k, v in asdict(filters).items() if v}, "passages": len(hits),
             "grant_metadata_filter": grant_mode,
             "top": [f"{h.citation} ({h.score:.2f})" for h in hits[:5]]})


@traced("understand")
def node_understand(state: State) -> tuple[dict, dict]:
    d = state["decision"]
    hint = (f"route '{d.route}' with confidence {d.confidence:.2f}" + (" (hybrid hint)" if d.hybrid_hint else "")
            if FEATURES.semantic_router else "none (decide the route yourself)")
    u, pid = understand(state["question"], state["profile"].summary(), hint, state.get("hits", []))
    route = d.route if (not d.needs_llm and d.route == "hybrid") else u.route
    stage = "semantic" if route == d.route and not d.needs_llm else "llm"
    profile = merge_llm_entities(state["profile"], u.entities.model_dump())
    hits = state.get("hits", [])
    if FEATURES.relevance_grading:
        relevant = [hits[i - 1] for i in u.relevant_passages if 0 < i <= len(hits)]
    else:                                   # ablation: top passages, ungraded
        relevant = hits[:UNGRADED_TOP_K]
    chosen = {id(h) for h in relevant}
    for h in hits:
        h.relevant = id(h) in chosen
    return ({"understanding": u, "route": route, "router_stage": stage, "profile": profile,
             "relevant_hits": relevant},
            {"prompt": pid, "route": route, "decided_by": stage, "llm_route": u.route,
             "llm_confidence": u.confidence, "reason": u.reason, "rules_extracted": len(u.rules),
             "relevant_passages": [h.citation for h in relevant], "sql_task": u.sql_task,
             "personal_scope": u.needs_personal_scope})


@traced("grade_docs")
def node_grade(state: State) -> tuple[dict, dict]:
    if not FEATURES.relevance_grading:      # ablation: no grading call, top passages as retrieved
        top = state.get("hits", [])[:UNGRADED_TOP_K]
        for h in top:
            h.relevant = True
        return ({"relevant_hits": top}, {"ablated": "relevance_grading", "relevant": [h.citation for h in top]})
    relevant, pid = grade_hits(state["question"], state.get("hits", []))
    if not relevant:
        relevant = state.get("hits", [])[:3]
        note = ["No passage was graded relevant; the top 3 were used and the answer may be incomplete."]
    else:
        note = []
    return ({"relevant_hits": relevant, "notes": note},
            {"prompt": pid, "relevant": [h.citation for h in relevant]})


@traced("apply_rules")
def node_apply_rules(state: State) -> tuple[dict, dict]:
    u = state.get("understanding")
    rules = u.rules if u else []
    if not rules:
        return ({"rule_eval": None, "constraints": "", "fallback": True,
                 "notes": ["No rules could be extracted; fell back to parallel retrieval (SQL + documents)."]},
                {"rules": 0, "fallback": True})
    if not FEATURES.rule_engine:            # ablation: the SQL step must apply the rules itself
        text = ("Policy rules extracted from the documents (apply them in the query; they have NOT been "
                f"checked):\nStudent profile: {state['profile'].summary()}\n"
                + "\n".join(f"- {r.subject}: {r.field} {r.operator} {r.value} -> {r.effect} {r.citation}"
                            for r in rules))
        return ({"rule_eval": None, "constraints": text, "fallback": False},
                {"rules": len(rules), "ablated": "rule_engine", "constraints": text})
    ev = evaluate_rules(rules, state["profile"])
    text = ev.constraints_text(state["profile"])
    return ({"rule_eval": ev, "constraints": text, "fallback": False},
            {"rules": len(rules), "pass": ev.passing, "fail": [s.subject for s in ev.failing],
             "unknown": [s.subject for s in ev.subjects if s.outcome == "unknown"], "constraints": text})


@traced("query_db")
def node_query(state: State) -> tuple[dict, dict]:
    u = state.get("understanding")
    ents = state["entities"]
    question = (u.sql_task if u and u.sql_task.strip() else state["question"])
    role = state.get("role", "anonymous")
    scope = state.get("student_id") if role == "student" else None      # the guard decides own-rows vs aggregate
    ref = ents.reference_date or (_iso(u.entities.reference_date) if u else None)
    context = "\n".join(x for x in [
        state.get("constraints", ""),
        f"- Reference date stated in the question: DATE '{ref.isoformat()}' (use it for day counts)." if ref else "",
        f"- Original user question: {state['question']}" if question != state["question"] else "",
    ] if x)
    ev = state.get("rule_eval")
    eligible = [g for g in (ev.passing if ev else []) if g.startswith("GR-")]
    if eligible and state.get("route") == "hybrid":
        # grant eligibility is fully determined by the rule engine: compile the query, no LLM
        r, mode = compiled_grant_query(eligible), "compiled_from_rules"
    else:
        r, mode = answer_sql(question, student_id=scope, extra_context=context, role=role), "text_to_sql"
    executed_at = datetime.now().strftime("%H:%M:%S")      # SQL text may be cached; execution never is
    notes = [] if r.ok else [f"The database step failed ({r.error}); the answer uses policy documents only."]
    if r.privacy_block:
        msg = refusal_for_block(r, role, state["entities"])
        return ({"sql": None, "refusal": msg, "refused_by": "SQL guard (access policy)"},
                {"mode": mode, "executed_at": executed_at, "blocked": r.privacy_block, "role": role})
    return ({"sql": r, "notes": notes},
            {"prompt": ",".join(r.prompt_ids), "sql_question": question, "sql": r.sql, "ok": r.ok,
             "rows": len(r.rows), "attempts": len(r.attempts), "scoped_to": scope,
             "guard": r.attempts[-1].guard_notes if r.attempts else [], "model": ",".join(r.models_used),
             "attempt_errors": [a.error for a in r.attempts if a.error],
             "mode": mode, "executed_at": executed_at, "role": role})


def fixed_database_message(state: State) -> tuple[str, str] | None:
    """A deterministic answer when a database-only question has no usable rows, else None.

    The model is not asked to phrase "nothing found": in the security run it turned an UNANSWERABLE result into
    "there are no open grants" (false) and "the database contains no records for e-mail addresses" (misleading).
    Hybrid questions keep the model, because the policy passages can still answer part of them.
    """
    r = state.get("sql")
    if state.get("route") != "sql" or r is None:
        return None
    attempts = getattr(r, "attempts", []) or []
    if not r.ok:
        guard_blocked = any((a.error or "").startswith("GuardError") for a in attempts)
        return ((NOT_ACCESSIBLE_MESSAGE, "guard rejected every query") if guard_blocked
                else (DB_FAILED_MESSAGE, "database step failed"))
    if r.unanswerable:
        return UNANSWERABLE_MESSAGE, "SQL step returned UNANSWERABLE"
    if not r.rows:
        notes = attempts[-1].guard_notes if attempts else []
        what = (r.explanation or "").strip().rstrip(".")
        checked = f" What was checked: {what}." if what else ""
        if state.get("role") == "student" and any(n.startswith("Scoped") for n in notes):
            ents = state.get("entities")
            if ents is not None and ents.first_person:           # "what are my overdue fees?" -> none
                return OWN_NONE_MESSAGE + checked, "0 rows in the student's own records"
            return OWN_NO_ROWS_MESSAGE, "0 rows after scoping to the logged-in student"
        return NO_ROWS_MESSAGE + checked, "query returned 0 rows"
    return None


@traced("synthesize")
def node_synthesize(state: State) -> tuple[dict, dict]:
    if state.get("refusal"):
        return ({"answer": state["refusal"], "citations": [], "relevant_hits": []},
                {"refused_by": state.get("refused_by")})
    fixed = fixed_database_message(state)
    if fixed:
        return ({"answer": fixed[0], "citations": [], "relevant_hits": []},
                {"fixed_message": fixed[1], "llm": "none (fixed message)"})
    r = state.get("sql")
    hits = state.get("relevant_hits") or ([] if state.get("route") == "sql" else state.get("hits", [])[:4])
    ev = state.get("rule_eval")
    rule_summary = state.get("constraints", "")
    if ev:
        rule_summary += "\nChecks:\n" + "\n".join(
            f"- {c.subject}: {c.field} {c.operator} {c.value} -> {c.outcome} ({c.detail}) {c.citation}"
            for c in ev.checks)
    sql_note = ""
    if r is not None and not r.ok:
        sql_note = "(The database query failed; answer from the policy passages and say live values are unavailable.)"
    if r is not None and r.ok and r.unanswerable:
        sql_note = "(The database has no data for this question.)"
    elif r is not None and r.ok:
        # tell the answer step what the rows ARE (eval SQL06: rows without the filter column were misread
        # as "no information"), and make database-computed totals authoritative (eval H3)
        sql_note = f"These rows are the result of: {r.explanation}\nQuery: {r.sql}"
        if scoped_to_own_records(state):
            sql_note += ("\nThe access policy limited these rows to the logged-in student's own records: do not "
                         "present them as a complete list or as information about other students.")
        totals = database_totals(r.rows)
        if totals:
            sql_note += ("\nDatabase-computed total(s): " + ", ".join(inr(t) for t in totals)
                         + ". If you state a total, use exactly this figure.")
    answer, pid, cites = synthesize(
        state["question"], profile_summary=state["profile"].summary(),
        columns=r.columns if (r and r.ok and not r.unanswerable) else [],
        rows=r.rows if (r and r.ok and not r.unanswerable) else [],
        sql_note=sql_note, rule_summary=rule_summary.strip(), hits=hits)
    info = {"prompt": pid, "citations": cites, "chars": len(answer)}
    if scoped_to_own_records(state):                 # stated in code, so it cannot be left out
        answer = SCOPED_NOTE + "\n\n" + answer
        info["scoped_note"] = True
    return ({"answer": answer, "citations": cites, "relevant_hits": hits}, info)


@traced("verify")
def node_verify(state: State) -> tuple[dict, dict]:
    r = state.get("sql")
    ents = state["entities"]
    u = state.get("understanding")
    extra = [d for d in [ents.reference_date, _iso(u.entities.reference_date) if u else None] if d]
    pool = build_pool(rows=r.rows if (r and r.ok) else [],
                      passages_text=passages_for_prompt(state.get("relevant_hits", [])),
                      rule_text=state.get("constraints", ""), profile=state["profile"].known(),
                      question=state["question"], as_of=get_settings().as_of_date, extra_dates=extra)
    rep = verify_answer(state.get("answer", ""), pool,
                        db_totals=database_totals(r.rows) if (r and r.ok) else None)
    return ({"verification": rep},
            {"ok": rep.ok, "summary": rep.summary(),
             "unverified": [f.text for f in rep.unverified]})


@traced("decline")
def node_decline(state: State) -> tuple[dict, dict]:
    return ({"answer": DECLINE_MESSAGE, "citations": [], "verification": VerificationReport()},
            {"reason": "out_of_scope"})


def _iso(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


# ------------------------------------------------------------------ edges
def after_profile(state: State) -> str:
    return "synthesize" if state.get("refusal") else "route"


def after_route(state: State) -> str:
    d = state["decision"]
    if not d.needs_llm and d.route == "out_of_scope":
        return "decline"
    if not d.needs_llm and d.route == "sql":
        return "query_db"
    return "retrieve_docs"


def after_retrieve(state: State) -> str:
    return "understand" if state.get("needs_understand") else "grade_docs"


def after_understand(state: State) -> str:
    return {"out_of_scope": "decline", "sql": "query_db", "docs": "synthesize",
            "hybrid": "apply_rules"}.get(state["route"], "synthesize")


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(State)
    for name, fn in [("profile", node_profile), ("route", node_route), ("retrieve_docs", node_retrieve),
                     ("understand", node_understand), ("grade_docs", node_grade), ("apply_rules", node_apply_rules),
                     ("query_db", node_query), ("synthesize", node_synthesize), ("verify", node_verify),
                     ("decline", node_decline)]:
        g.add_node(name, fn)
    g.add_edge(START, "profile")
    g.add_conditional_edges("profile", after_profile, ["route", "synthesize"])
    g.add_conditional_edges("route", after_route, ["decline", "query_db", "retrieve_docs"])
    g.add_conditional_edges("retrieve_docs", after_retrieve, ["understand", "grade_docs"])
    g.add_conditional_edges("understand", after_understand, ["decline", "query_db", "synthesize", "apply_rules"])
    g.add_edge("grade_docs", "synthesize")
    g.add_edge("apply_rules", "query_db")
    g.add_edge("query_db", "synthesize")
    g.add_edge("synthesize", "verify")
    g.add_edge("verify", END)
    g.add_edge("decline", END)
    return g.compile()


# ------------------------------------------------------------------ public API
@dataclass
class AskResult:
    question: str
    answer: str
    route: str
    router_stage: str
    citations: list[str]
    verification: dict
    sql: str | None
    sql_columns: list[str]
    sql_rows: list[dict]
    passages: list[dict]
    rules: dict | None
    profile: dict
    notes: list[str]
    trace: list[dict]
    role: str
    refused_by: str | None
    gen_requests: int
    llm_calls: int
    cache_hits: int
    latency_ms: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def ask(question: str, student_id: str | None = None, role: str | None = None) -> AskResult:
    """role: 'anonymous' (default), 'staff', or implied 'student' when student_id is given."""
    t0 = time.perf_counter()
    final: State = build_graph().invoke({"question": question, "student_id": student_id, "role": role,
                                         "notes": [], "trace": []})
    r = final.get("sql")
    ver = final.get("verification")
    ev = final.get("rule_eval")
    trace = final.get("trace", [])
    return AskResult(
        question=question, answer=final.get("answer", ""), route=final.get("route", "?"),
        router_stage=final.get("router_stage", "?"), citations=final.get("citations", []),
        verification=ver.to_dict() if ver else {"ok": True, "summary": "n/a", "figures": []},
        sql=r.sql if r else None, sql_columns=r.columns if r else [], sql_rows=r.rows if r else [],
        passages=[{"citation": h.citation, "score": h.score, "relevant": h.relevant,
                   "excerpt": h.content.split("\n", 1)[-1].strip()[:700]}
                  for h in final.get("relevant_hits", [])],
        rules=ev.to_dict() if ev else None, profile=final["profile"].known() if final.get("profile") else {},
        notes=final.get("notes", []), trace=trace,
        role=final.get("role", "anonymous"), refused_by=final.get("refused_by"),
        gen_requests=sum(e.get("gen_requests", 0) for e in trace),
        llm_calls=sum(e.get("llm_calls", 0) for e in trace), cache_hits=sum(e.get("cache_hits", 0) for e in trace),
        latency_ms=int((time.perf_counter() - t0) * 1000))
