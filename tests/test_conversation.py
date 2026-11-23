"""Multi-turn follow-ups: detection without a model call, rewriting, and the access policy across turns.
The pipeline and the rewriter are replaced by stubs; no model or database calls."""
from types import SimpleNamespace

import pytest

from unirag import conversation as conv_mod
from unirag import graph
from unirag import llm as llm_mod
from unirag.conversation import Conversation, followup_reason
from unirag.profile import extract_entities


@pytest.fixture(autouse=True)
def stub_llm():
    llm_mod.set_llm(SimpleNamespace(stats=SimpleNamespace(gen_calls=0, cache_hits=0, gen_requests=0),
                                    skip_events=[], last_model_used="fake-lite"))
    yield
    llm_mod.set_llm(None)


@pytest.fixture
def pipeline(monkeypatch):
    """Record what the pipeline was asked, as whom; answer with a fixed text."""
    calls = []

    def fake_ask(question, student_id=None, role=None):
        calls.append({"question": question, "student_id": student_id, "role": role})
        return SimpleNamespace(question=question, answer=f"answer to: {question}", route="sql", trace=[],
                               gen_requests=2, llm_calls=2, cache_hits=0, latency_ms=10, extra={})

    monkeypatch.setattr(graph, "ask", fake_ask)
    return calls


def _rewriter(monkeypatch, text):
    seen = []

    def fake(question, turns):
        seen.append((question, [t.asked for t in turns]))
        llm_mod.get_llm().stats.gen_requests += 1
        return text, "conversation_rewrite.v1"

    monkeypatch.setattr(conv_mod, "rewrite_followup", fake)
    return seen


@pytest.mark.parametrize("q", ["And in Information Technology?", "What about IT?", "And for a third-year ENTC student?",
                               "And my overdue fees?", "List their names.", "Second year?", "When is it due?",
                               "For IT?", "How about the hostel?"])
def test_followups_are_detected(q):
    assert followup_reason(q, has_history=True)


@pytest.mark.parametrize("q", ["Which grants are open?", "How many students are in IT?",
                               "Which grants can I apply for and what are their deadlines?",
                               "Is the hostel fee refundable if I leave it early?",
                               "In which semester is CS304 taught by Prof. A. Phadke?"])
def test_standalone_questions_are_not_followups(q):
    assert followup_reason(q, has_history=True) is None


def test_first_turn_is_never_a_followup():
    assert followup_reason("And in IT?", has_history=False) is None


def test_standalone_question_needs_no_rewrite(monkeypatch, pipeline):
    monkeypatch.setattr(conv_mod, "rewrite_followup", lambda *a: pytest.fail("rewriter called"))
    c = Conversation(role="staff")
    c.ask("How many students are enrolled in Computer Science?")
    r = c.ask("Which grants are open?")
    assert pipeline[-1]["question"] == "Which grants are open?" and not r.extra["conversation"]["followup"]


def test_followup_is_rewritten_and_traced(monkeypatch, pipeline):
    seen = _rewriter(monkeypatch, "How many students are enrolled in Information Technology?")
    c = Conversation(role="staff")
    c.ask("How many students are enrolled in Computer Science?")
    r = c.ask("And in Information Technology?")
    assert seen == [("And in Information Technology?", ["How many students are enrolled in Computer Science?"])]
    assert pipeline[-1] == {"question": "How many students are enrolled in Information Technology?",
                            "student_id": None, "role": "staff"}
    ev = r.trace[0]
    assert ev["node"] == "rewrite" and ev["original"] == "And in Information Technology?"
    assert ev["rewritten"].endswith("Information Technology?") and ev["gen_requests"] == 1
    assert r.gen_requests == 3 and r.question == "And in Information Technology?"
    assert r.extra["conversation"]["asked"] == ev["rewritten"]


def test_followup_naming_another_student_is_refused_without_rewrite(monkeypatch, pipeline):
    monkeypatch.setattr(conv_mod, "rewrite_followup", lambda *a: pytest.fail("rewriter called"))
    c = Conversation(student_id="S2026CS012")
    c.ask("What are my overdue fees?")
    r = c.ask("What about S2025CS003?")
    assert pipeline[-1]["question"] == "What about S2025CS003?"      # the pipeline's privacy rule refuses it
    assert "refused as typed" in r.trace[0]["skipped"]
    assert graph.policy_refusal(extract_entities("What about S2025CS003?"), "student", "S2026CS012")[1] \
        == "privacy rule (other student)"


def test_rewrite_cannot_introduce_a_student(monkeypatch, pipeline):
    _rewriter(monkeypatch, "What are the overdue fees of S2025CS003?")
    c = Conversation(student_id="S2026CS012")
    c.ask("What are my overdue fees?")
    r = c.ask("And the late fine?")
    assert pipeline[-1]["question"] == "And the late fine?"           # rewrite discarded, original used
    assert r.trace[0]["discarded"] == "What are the overdue fees of S2025CS003?"


def test_identity_is_fixed_per_conversation(monkeypatch, pipeline):
    _rewriter(monkeypatch, "What are my overdue fees?")
    c = Conversation(student_id="S2026CS012")
    c.ask("What is my attendance in each course?")
    assert c.switch_identity(None, "S2024CS006") and c.turns == []    # new student: history gone
    c.ask("And my overdue fees?")                                     # no history now -> not a follow-up
    assert pipeline[-1] == {"question": "And my overdue fees?", "student_id": "S2024CS006", "role": None}
    assert not c.switch_identity(None, "S2024CS006")


def test_rewriter_only_sees_recent_turns(monkeypatch, pipeline):
    seen = _rewriter(monkeypatch, "rewritten")
    c = Conversation(role="staff")
    for i in range(5):
        c.ask(f"How many students are in department number {i}?")
    c.ask("And their average CGPA?")
    assert len(seen[0][1]) == conv_mod.MAX_TURNS and len(c.turns) == conv_mod.MAX_TURNS
