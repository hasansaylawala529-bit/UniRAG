"""UniRAG as a Model Context Protocol (MCP) server (stdio), for Claude Desktop or any MCP client.

    python -m unirag.mcp_server                       # anonymous (default)
    UNIRAG_MCP_ROLE=staff python -m unirag.mcp_server
    UNIRAG_MCP_STUDENT_ID=S2026CS012 python -m unirag.mcp_server   # a logged-in student

Tools (all read-only):
    ask              question -> cited answer, route, verification, models used (the full pipeline)
    search_policies  semantic search over the policy documents -> cited passages
    describe_data    tables and columns the assistant can read, and what this role may see of each

Security: the role (and student ID) is fixed when the server starts, from its configuration. No tool takes a
role or identity argument, unknown arguments are ignored, and there is no tool that runs raw SQL. Every answer goes
through the same pipeline as the UI: contact-data and write-intent rules, SQL guard, row scoping, read-only
database role. Nothing is written to stdout except the protocol (logs go to stderr).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .profile import STUDENT_ID_RE
from .retrieval.sql_guard import NEVER_RETURNED, PERSONAL_TABLES, ROLES

MAX_QUESTION_CHARS = 1000
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)


@dataclass(frozen=True)
class Identity:
    role: str                       # anonymous | student | staff
    student_id: str | None = None

    @classmethod
    def from_env(cls) -> "Identity":
        return cls.of(os.getenv("UNIRAG_MCP_ROLE", ""), os.getenv("UNIRAG_MCP_STUDENT_ID", ""))

    @classmethod
    def of(cls, role: str | None, student_id: str | None) -> "Identity":
        role = (role or "").strip().lower()
        sid = (student_id or "").strip().upper() or None
        if sid:
            if not STUDENT_ID_RE.fullmatch(sid):
                raise ValueError(f"UNIRAG_MCP_STUDENT_ID {sid!r} is not a student id (e.g. S2026CS012)")
            if role not in ("", "student"):
                raise ValueError("UNIRAG_MCP_STUDENT_ID is set, so UNIRAG_MCP_ROLE must be 'student' or empty")
            return cls("student", sid)
        role = role or "anonymous"                       # secure default
        if role not in ROLES or role == "student":
            raise ValueError(f"UNIRAG_MCP_ROLE must be anonymous or staff (or set UNIRAG_MCP_STUDENT_ID); got {role!r}")
        return cls(role)

    def describe(self) -> str:
        return f"student {self.student_id}" if self.role == "student" else self.role


# ------------------------------------------------------------------ output schemas
class AskOutput(BaseModel):
    answer: str = Field(description="Final answer with [SQL] and [Doc: ...] citations")
    citations: list[str] = Field(description="Sources cited in the answer")
    route: str = Field(description="sql | docs | hybrid | out_of_scope | refused")
    decided_by: str = Field(description="semantic router, LLM planner, or policy rule")
    figures_verified: bool = Field(description="Every number and date in the answer was found in the evidence")
    verification: str
    refused_by: str | None = Field(description="Policy layer that refused the question, if any")
    models_used: list[str]
    sql: str | None = Field(description="The read-only query that was run, if any")
    role: str = Field(description="Role this server runs as (fixed at start-up)")
    generation_requests: int


class Passage(BaseModel):
    citation: str
    score: float
    excerpt: str


class PolicySearchOutput(BaseModel):
    query: str
    passages: list[Passage]


class Column(BaseModel):
    name: str
    type: str


class TableInfo(BaseModel):
    table: str
    access: str = Field(description="What this server's role may see of the table")
    columns: list[Column]


class DataDescription(BaseModel):
    role: str
    tables: list[TableInfo]
    rules: list[str]


# ------------------------------------------------------------------ tool logic (plain functions, testable)
def _models_used(trace: list[dict]) -> list[str]:
    return sorted({m.strip() for e in trace for m in (e.get("model") or "").split(",") if m.strip()})


def ask_tool(identity: Identity, question: str) -> AskOutput:
    from .graph import ask

    question = (question or "").strip()
    if not question:
        raise ToolError("question is empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise ToolError(f"question is longer than {MAX_QUESTION_CHARS} characters")
    r = ask(question, student_id=identity.student_id,
            role=None if identity.role == "student" else identity.role)
    stage = {"semantic": "semantic router", "llm": "LLM planner", "rule": "policy rule"}.get(r.router_stage,
                                                                                         r.router_stage)
    return AskOutput(answer=r.answer, citations=r.citations, route=r.route, decided_by=stage,
                     figures_verified=bool(r.verification.get("ok")),
                     verification=r.verification.get("summary", ""), refused_by=r.refused_by,
                     models_used=_models_used(r.trace), sql=r.sql, role=identity.describe(),
                     generation_requests=r.gen_requests)


def search_policies_tool(query: str, k: int = 5) -> PolicySearchOutput:
    from .retrieval.vector_search import search_docs

    query = (query or "").strip()
    if not query:
        raise ToolError("query is empty")
    k = max(1, min(int(k), 10))
    hits = search_docs(query[:MAX_QUESTION_CHARS], k=k)
    return PolicySearchOutput(query=query, passages=[
        Passage(citation=h.citation, score=round(h.score, 3), excerpt=h.content.split("\n", 1)[-1].strip()[:800])
        for h in hits])


ACCESS = {
    "anonymous": "aggregates only (counts, averages, totals); no individual records",
    "student": "your own rows only; aggregates over everyone",
    "staff": "individual records of any student",
}


def describe_data_tool(identity: Identity) -> DataDescription:
    from .db import RELATIONAL_TABLES, connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT table_name, column_name, data_type FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = ANY(%s)
                       ORDER BY table_name, ordinal_position""", (list(RELATIONAL_TABLES),))
        rows = cur.fetchall()
    cols: dict[str, list[Column]] = {t: [] for t in RELATIONAL_TABLES}
    for t, c, typ in rows:
        if c not in NEVER_RETURNED:
            cols[t].append(Column(name=c, type=typ))
    tables = [TableInfo(table=t, access=ACCESS[identity.role] if t in PERSONAL_TABLES else "all rows (public data)",
                        columns=cols[t]) for t in RELATIONAL_TABLES]
    return DataDescription(role=identity.describe(), tables=tables, rules=[
        "Read-only: nothing can be changed through the assistant.",
        "Students' e-mail addresses and phone numbers are never returned, for any role.",
        "System tables, credentials and the vector index are not readable.",
        "Policy documents are searchable with search_policies; ask combines them with the database.",
    ])


def _friendly(fn, *args):
    """Turn infrastructure failures into a readable tool error (the client otherwise sees a generic one)."""
    try:
        return fn(*args)
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        import psycopg

        if isinstance(e, psycopg.OperationalError):
            raise ToolError("the university database is not reachable (is the Docker container running?)") from e
        if type(e).__name__ in {"ServerError", "ClientError", "ProviderError", "RuntimeError"}:
            raise ToolError(f"the model provider is unavailable right now ({type(e).__name__}); "
                            "please try again in a minute") from e
        raise


# ------------------------------------------------------------------ server
def build_server(identity: Identity | None = None) -> MCPServer:
    ident = identity or Identity.from_env()
    server = MCPServer(
        name="unirag",
        title="UniRAG – DVUT university assistant",
        instructions=(f"Answers questions about DVUT (fees, grants, courses, timetables, enrolments, attendance and "
                      f"university policies) by combining the university database with the policy documents. "
                      f"This server runs as: {ident.describe()}. Its access is fixed and cannot be changed by a "
                      f"tool call. Answers cite [SQL] and [Doc: ...] sources; keep the citations when you quote."),
    )

    @server.tool(name="ask", title="Ask UniRAG", annotations=READ_ONLY,
                 description="Answer a question about DVUT from the university database and policy documents, "
                             "with citations and figure verification. Access is fixed by the server's role.")
    def ask(question: str) -> AskOutput:
        return _friendly(ask_tool, ident, question)

    @server.tool(name="search_policies", title="Search policies", annotations=READ_ONLY,
                 description="Semantic search over DVUT policy documents; returns cited passages (k: 1-10).")
    def search_policies(query: str, k: int = 5) -> PolicySearchOutput:
        return _friendly(search_policies_tool, query, k)

    @server.tool(name="describe_data", title="Describe data", annotations=READ_ONLY,
                 description="List the database tables and columns the assistant can read, and what this "
                             "server's role may see of each.")
    def describe_data() -> DataDescription:
        return _friendly(describe_data_tool, ident)

    return server


def main() -> None:
    import sys

    ident = Identity.from_env()
    print(f"[mcp] UniRAG MCP server (stdio) running as {ident.describe()}", file=sys.stderr, flush=True)
    build_server(ident).run("stdio")


if __name__ == "__main__":
    main()
