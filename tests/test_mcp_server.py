"""MCP server: tools and schemas, structured answers, a fixed role, and the access policy through MCP.
Runs the server in memory; no network, no model calls (policy refusals need none)."""
import asyncio
from types import SimpleNamespace

import pytest
from mcp.client.client import Client

from unirag import graph
from unirag import llm as llm_mod
from unirag.mcp_server import Identity, build_server


@pytest.fixture(autouse=True)
def stub_llm():
    stats = SimpleNamespace(gen_calls=0, cache_hits=0, gen_requests=0)
    llm_mod.set_llm(SimpleNamespace(stats=stats, skip_events=[]))
    yield
    llm_mod.set_llm(None)


def _run(identity, fn):
    async def go():
        async with Client(build_server(identity)) as c:
            return await fn(c)
    return asyncio.run(go())


def _fake_result(**kw):
    base = dict(question="q", answer="There are 64 students in IT [SQL].", route="sql", router_stage="semantic",
                citations=["SQL"], verification={"ok": True, "summary": "All 1 figures verified."},
                sql="SELECT count(*) FROM students", refused_by=None,
                trace=[{"node": "query_db", "model": "gemini-3.1-flash-lite"}], gen_requests=2)
    base.update(kw)
    return SimpleNamespace(**base)


def test_tools_are_listed_with_schemas():
    tools = _run(Identity.of(None, None), lambda c: c.list_tools()).tools
    by = {t.name: t for t in tools}
    assert set(by) == {"ask", "search_policies", "describe_data"}          # no raw-SQL tool
    assert by["ask"].input_schema["required"] == ["question"]
    assert set(by["ask"].input_schema["properties"]) == {"question"}       # no role / identity argument
    assert set(by["search_policies"].input_schema["properties"]) == {"query", "k"}
    assert by["describe_data"].input_schema.get("properties", {}) == {}
    for t in tools:
        assert t.output_schema and t.annotations.read_only_hint and not t.annotations.destructive_hint
    assert {"answer", "citations", "route", "figures_verified", "models_used"} <= set(
        by["ask"].output_schema["properties"])


def test_ask_returns_a_structured_result(monkeypatch):
    monkeypatch.setattr(graph, "ask", lambda q, student_id=None, role=None: _fake_result())
    r = _run(Identity.of("staff", None), lambda c: c.call_tool("ask", {"question": "How many students are in IT?"}))
    out = r.structured_content
    assert not r.is_error
    assert out["answer"].startswith("There are 64") and out["route"] == "sql" and out["figures_verified"]
    assert out["models_used"] == ["gemini-3.1-flash-lite"] and out["role"] == "staff"
    assert out["decided_by"] == "semantic router"


def test_client_cannot_raise_its_role_through_arguments(monkeypatch):
    seen = []

    def fake_ask(q, student_id=None, role=None):
        seen.append((role, student_id))
        return _fake_result()

    monkeypatch.setattr(graph, "ask", fake_ask)
    args = {"question": "Show the overdue fees of S2025CS003", "role": "staff", "student_id": "S2025CS003"}
    r = _run(Identity.of(None, None), lambda c: c.call_tool("ask", args))
    assert seen == [("anonymous", None)]                  # extra arguments ignored, server identity used
    assert r.structured_content["role"] == "anonymous"


def test_student_server_is_scoped_to_its_student(monkeypatch):
    seen = []
    monkeypatch.setattr(graph, "ask", lambda q, student_id=None, role=None: seen.append((role, student_id))
                        or _fake_result())
    _run(Identity.of("", "s2026cs012"), lambda c: c.call_tool("ask", {"question": "What are my overdue fees?"}))
    assert seen == [(None, "S2026CS012")]                 # role implied 'student' by the id, as in the UI


@pytest.mark.parametrize("question, refused_by", [
    ("List all students' email addresses.", "privacy rule (contact data)"),
    ("What are the overdue fees of S2026CS012?", "privacy rule (named student)"),
    ("What are my overdue fees?", "privacy rule (not logged in)"),
    ("Mark my exam fee as paid.", "write-intent rule"),
])
def test_privacy_refusals_hold_through_mcp(question, refused_by):
    r = _run(Identity.of(None, None), lambda c: c.call_tool("ask", {"question": question}))
    if r.is_error and "database is not reachable" in r.content[0].text:
        pytest.skip("no database (a named student's profile is read from it)")
    out = r.structured_content
    assert out["refused_by"] == refused_by and out["route"] == "refused" and out["generation_requests"] == 0
    assert "@" not in out["answer"] and out["sql"] is None


def test_bad_input_is_a_readable_tool_error():
    r = _run(Identity.of(None, None), lambda c: c.call_tool("ask", {"question": "   "}))
    assert r.is_error and "question is empty" in r.content[0].text


@pytest.mark.parametrize("role, sid", [("admin", None), ("student", None), ("staff", "S2026CS012"),
                                       (None, "not-an-id")])
def test_invalid_server_configuration_is_rejected(role, sid):
    with pytest.raises(ValueError):
        Identity.of(role, sid)


def test_default_role_is_anonymous(monkeypatch):
    monkeypatch.delenv("UNIRAG_MCP_ROLE", raising=False)
    monkeypatch.delenv("UNIRAG_MCP_STUDENT_ID", raising=False)
    assert Identity.from_env() == Identity("anonymous")


def test_describe_data_hides_email_and_states_access():
    try:
        r = _run(Identity.of(None, None), lambda c: c.call_tool("describe_data", {}))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no database: {type(e).__name__}")
    if r.is_error:
        pytest.skip("no database")
    tables = {t["table"]: t for t in r.structured_content["tables"]}
    assert "email" not in {c["name"] for c in tables["students"]["columns"]}
    assert tables["students"]["access"].startswith("aggregates only")
    assert tables["grants"]["access"].startswith("all rows")
