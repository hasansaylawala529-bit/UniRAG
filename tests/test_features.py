"""Ablation switches: each one disables exactly its component, and everything is on by default.
No model calls; the one database test is skipped without a database."""
from types import SimpleNamespace

import pytest

from unirag import features, graph
from unirag import llm as llm_mod
from unirag.features import COMPONENTS, FEATURES, Features, ablate
from unirag.planner.understand import ExtractedRule
from unirag.profile import build_profile
from unirag.router import RouteDecision


@pytest.fixture(autouse=True)
def stub_llm():
    stats = SimpleNamespace(gen_calls=0, cache_hits=0, gen_requests=0)
    llm_mod.set_llm(SimpleNamespace(stats=stats, skip_events=[]))
    yield
    llm_mod.set_llm(None)


def _boom(*a, **k):
    raise AssertionError("this component should have been skipped")


def test_everything_is_on_by_default():
    assert Features().disabled() == []
    assert all(getattr(FEATURES, c) for c in COMPONENTS)


def test_ablate_restores_switches_even_on_error():
    with pytest.raises(RuntimeError):
        with ablate("rule_engine", "schema_cards"):
            assert FEATURES.disabled() == ["rule_engine", "schema_cards"]
            raise RuntimeError
    assert FEATURES.disabled() == []


def test_unknown_component_is_rejected(monkeypatch):
    with pytest.raises(ValueError):
        with ablate("verifier"):
            pass
    monkeypatch.setenv("UNIRAG_ABLATE", "rule_engine, nonsense")
    with pytest.raises(ValueError):
        features._from_env()


def test_environment_switches(monkeypatch):
    monkeypatch.setenv("UNIRAG_ABLATE", "semantic_router,relevance_grading")
    assert features._from_env().disabled() == ["semantic_router", "relevance_grading"]


def test_with_router_on_the_semantic_router_decides(monkeypatch):
    d = RouteDecision(route="sql", confidence=0.9, stage="semantic", hybrid_hint=False)
    monkeypatch.setattr(graph, "semantic_route", lambda q: d)
    out = graph.node_route({"question": "How many students are in IT?", "trace": []})
    assert out["router_stage"] == "semantic" and out["route"] == "sql" and not out["needs_understand"]


def test_without_router_every_question_goes_to_the_planner(monkeypatch):
    monkeypatch.setattr(graph, "semantic_route", _boom)
    with ablate("semantic_router"):
        out = graph.node_route({"question": "How many students are in IT?", "trace": []})
    assert out["needs_understand"] and out["decision"].needs_llm
    assert graph.after_route(out) == "retrieve_docs" and graph.after_retrieve(out) == "understand"


def _hits(n):
    return [SimpleNamespace(citation=f"[Doc {i}]", relevant=False, chunk_id=i) for i in range(n)]


def test_without_grading_top_passages_are_used_ungraded(monkeypatch):
    monkeypatch.setattr(graph, "grade_hits", _boom)
    hits = _hits(8)
    with ablate("relevance_grading"):
        out = graph.node_grade({"question": "q", "hits": hits, "trace": []})
    assert out["relevant_hits"] == hits[:features.UNGRADED_TOP_K]


def test_with_grading_the_grader_decides(monkeypatch):
    hits = _hits(8)
    monkeypatch.setattr(graph, "grade_hits", lambda q, h: ([h[6]], "relevance_grade.v2"))
    out = graph.node_grade({"question": "q", "hits": hits, "trace": []})
    assert out["relevant_hits"] == [hits[6]]


def _rule_state():
    profile, _ = build_profile("I'm a third-year CS student with CGPA 8.4. Which grants can I apply for?", None)
    rule = ExtractedRule(subject="GR-AI-01", field="cgpa", operator=">=", value="8.0", effect="eligible",
                         citation="[Doc: Research Grant Policy §2]")
    return {"understanding": SimpleNamespace(rules=[rule]), "profile": profile, "trace": []}


def test_without_rule_engine_rules_are_passed_to_sql_unchecked(monkeypatch):
    monkeypatch.setattr(graph, "evaluate_rules", _boom)
    with ablate("rule_engine"):
        out = graph.node_apply_rules(_rule_state())
    assert out["rule_eval"] is None                 # so node_query cannot compile the grant query
    assert "GR-AI-01: cgpa >= 8.0" in out["constraints"] and "NOT been checked" in out["constraints"]


def test_with_rule_engine_rules_are_evaluated_in_code():
    out = graph.node_apply_rules(_rule_state())
    assert out["rule_eval"] is not None and "GR-AI-01" in out["rule_eval"].passing


def test_without_schema_cards_sql_sees_every_table():
    from unirag.retrieval import sql_agent
    try:
        with ablate("schema_cards"):
            cards = sql_agent.retrieve_schema_cards("anything")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no database: {type(e).__name__}")
    assert len(cards) >= 9 and len({t for t, _ in cards}) == len(cards)
