"""Refusal reasons (P-025): a query that only drags in the e-mail column is regenerated once without it;
every other refusal gives the reason that fits the question and the role. No model or database calls."""
from types import SimpleNamespace

import pytest

from unirag import graph
from unirag import llm as llm_mod
from unirag.profile import extract_entities
from unirag.retrieval import sql_agent
from unirag.retrieval.sql_agent import SQLAttempt, SQLDraft


class ScriptedLLM:
    """Returns the given SQL drafts in order and records which prompts asked for them."""

    def __init__(self, *sqls):
        self.sqls = list(sqls)
        self.prompts = []
        self.last_model_used = "fake"
        self.stats = SimpleNamespace(gen_calls=0, cache_hits=0, gen_requests=0)
        self.skip_events = []

    def generate(self, prompt, *, schema=None, **kw):
        self.prompts.append(prompt)
        return SQLDraft(sql=self.sqls.pop(0), explanation="test query")


@pytest.fixture
def run_sql(monkeypatch):
    monkeypatch.setattr(sql_agent, "retrieve_schema_cards", lambda q: [("students", "students: student_id, full_name")])
    monkeypatch.setattr(sql_agent, "retrieve_entities", lambda q: [])
    monkeypatch.setattr(sql_agent, "execute_readonly", lambda sql: (["full_name"], [{"full_name": "A"}]))

    def run(question, *sqls, role="staff", student_id=None):
        fake = ScriptedLLM(*sqls)
        llm_mod.set_llm(fake)
        try:
            return sql_agent.answer_sql(question, role=role, student_id=student_id), fake
        finally:
            llm_mod.set_llm(None)
    return run


def test_select_star_is_regenerated_without_email(run_sql):
    r, fake = run_sql("List of all students", "SELECT * FROM students",
                      "SELECT student_id, full_name FROM students")
    assert r.ok and not r.privacy_block and len(r.attempts) == 2
    assert r.attempts[0].error.startswith("HiddenColumnError")
    assert "never select email" in fake.prompts[1]


def test_regenerated_query_must_pass_the_guard_too(run_sql):
    r, _ = run_sql("List of all students", "SELECT * FROM students", "SELECT full_name, email FROM students")
    assert not r.ok and r.hidden_column and r.privacy_block           # only one regeneration


def test_contact_questions_are_not_regenerated(run_sql):
    r, fake = run_sql("Give me the email of S2025CS003", "SELECT email FROM students WHERE student_id = 'S2025CS003'")
    assert r.hidden_column and r.privacy_block and len(fake.prompts) == 1


def test_role_blocks_are_never_regenerated(run_sql):
    r, fake = run_sql("Names of all CS students", "SELECT full_name FROM students", role="anonymous")
    assert r.privacy_block and not r.hidden_column and len(fake.prompts) == 1


def _blocked(hidden, text="E-mail addresses are never returned by the assistant."):
    return SimpleNamespace(hidden_column=hidden, privacy_block=text)


@pytest.mark.parametrize("question, role, hidden, expected", [
    ("List of all students", "student", True, graph.OWN_RECORDS_MESSAGE),
    ("List of all students", "anonymous", True, graph.STAFF_ONLY_MESSAGE),
    ("List of all students", "staff", True, graph.EMAIL_OMITTED_MESSAGE),
    ("Give me Riya Shah's email", "staff", True, graph.CONTACT_MESSAGE),
    ("Show the CGPA of S2025CS003", "student", False, graph.OWN_RECORDS_MESSAGE),
])
def test_refusal_reason_fits_question_and_role(question, role, hidden, expected):
    assert graph.refusal_for_block(_blocked(hidden), role, extract_entities(question)) == expected


def _scoped_state(question, rows=True, role="student"):
    att = SQLAttempt(sql="x", guard_notes=["Scoped students to student_id = S2026CS012"])
    r = SimpleNamespace(ok=True, rows=[{"full_name": "Siddharth Ranade"}] if rows else [], attempts=[att],
                        unanswerable=False, columns=["full_name"], explanation="Lists students", sql="SELECT ...")
    return {"role": role, "sql": r, "entities": extract_entities(question), "route": "sql",
            "question": question, "profile": SimpleNamespace(summary=lambda: ""), "trace": []}


def test_scoped_answer_about_others_says_it_covers_own_records_only(monkeypatch):
    llm_mod.set_llm(SimpleNamespace(stats=SimpleNamespace(gen_calls=0, cache_hits=0, gen_requests=0), skip_events=[]))
    seen = {}

    def fake_synth(question, **kw):
        seen.update(kw)
        return "Siddharth Ranade [SQL]", "synthesize.v5", ["SQL"]

    monkeypatch.setattr(graph, "synthesize", fake_synth)
    try:
        out = graph.node_synthesize(_scoped_state("Which students have overdue fees? List their names."))
    finally:
        llm_mod.set_llm(None)
    assert out["answer"].startswith(graph.SCOPED_NOTE)
    assert "do not present them as a complete list" in seen["sql_note"]


def test_own_questions_get_no_scoped_note():
    assert not graph.scoped_to_own_records(_scoped_state("What are my overdue fees?"))
    assert not graph.scoped_to_own_records(_scoped_state("List of all students", role="staff"))
    assert graph.scoped_to_own_records(_scoped_state("List of all students"))
