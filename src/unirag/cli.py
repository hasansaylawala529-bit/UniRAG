"""Command-line tester for each retrieval path on its own.

  python -m unirag.cli sql  "Which open grants close before January?"
  python -m unirag.cli sql  "What are my overdue fees?" --student S2026CS012
  python -m unirag.cli docs "What is the minimum attendance for exams?"
  python -m unirag.cli docs "Who can apply for GR-CY-03?" --doc-type grant_policy --no-grade
  python -m unirag.cli guard "DELETE FROM grants"
  python -m unirag.cli ask  "I am a third-year CS student with a 3.8 GPA; which grants can I apply for?"
  python -m unirag.cli ask  "Can I renew my hostel room?" --student S2025CS003 --trace
"""
from __future__ import annotations

import argparse
import json
import sys

from .retrieval.sql_guard import GuardError, guard_sql


def _hr(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


def cmd_sql(args: argparse.Namespace) -> int:
    from .retrieval.sql_agent import answer_sql

    r = answer_sql(args.question, student_id=args.student)
    _hr("Schema cards retrieved")
    print(", ".join(r.cards_used))
    if r.entity_hints:
        _hr("Entity hints")
        print("\n".join(r.entity_hints))
    for i, a in enumerate(r.attempts, 1):
        _hr(f"Attempt {i}")
        print(a.guarded_sql or a.sql)
        if a.guard_notes:
            print("guard:", "; ".join(a.guard_notes))
        if a.error:
            print("ERROR:", a.error)
    _hr("Result" + ("" if r.ok else " (FAILED)"))
    print(r.explanation)
    print(r.as_markdown() if r.ok else r.error)
    print(f"\n{len(r.rows)} rows · {r.latency_ms} ms · prompts: {', '.join(r.prompt_ids)}"
          f" · models: {', '.join(r.models_used)}")
    return 0 if r.ok else 1


def cmd_docs(args: argparse.Namespace) -> int:
    from .retrieval.grader import grade_hits
    from .retrieval.vector_search import DocFilters, search_docs

    f = DocFilters(doc_types=args.doc_type, department=args.dept, year=args.year)
    hits = search_docs(args.question, k=args.k, filters=f)
    if not args.no_grade:
        grade_hits(args.question, hits)
    _hr(f"Top {len(hits)} passages")
    for i, h in enumerate(hits, 1):
        mark = "" if h.relevant is None else ("✔ " if h.relevant else "✘ ")
        print(f"\n[{i}] {mark}{h.citation}")
        print(f"    score {h.score:.3f} (vector {h.vec_score:.3f}, keyword {h.kw_score:.3f})"
              + (f" · codes {', '.join(h.grant_codes)}" if h.grant_codes else ""))
        if h.grade_reason:
            print(f"    grade: {h.grade_reason}")
        body = h.content.split("\n", 1)[-1].strip().replace("\n", " ")
        print("    " + body[:300] + ("…" if len(body) > 300 else ""))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .graph import ask

    r = ask(args.question, student_id=args.student, role="staff" if args.staff else None)
    if args.json:
        print(json.dumps(r.to_dict(), indent=2, ensure_ascii=False, default=str))
        return 0
    _hr("Answer")
    print(r.answer)
    _hr("How it was answered")
    if r.refused_by:
        print(f"refused by: {r.refused_by}")
    print(f"role: {r.role} · route: {r.route} (decided by {r.router_stage} router) · generation requests: {r.gen_requests} "
          f"(API calls {r.llm_calls}) · {r.latency_ms} ms")
    print(f"verification: {r.verification['summary']}")
    if r.citations:
        print("citations: " + "; ".join(r.citations))
    if r.sql:
        print(f"sql: {r.sql}")
    if r.rules:
        passed = [s['subject'] for s in r.rules['subjects'] if s['outcome'] == 'pass']
        failed = [s['subject'] for s in r.rules['subjects'] if s['outcome'] == 'fail']
        print(f"rules: pass {passed} · fail {failed}")
    for n in r.notes:
        print(f"note: {n}")
    if args.trace:
        _hr("Trace")
        for e in r.trace:
            extra = {k: v for k, v in e.items() if k not in ("node", "ms", "llm_calls", "cache_hits", "gen_requests")}
            print(f"- {e['node']:<14}{e['ms']:>6} ms  gen={e.get('gen_requests', 0)}  "
                  + json.dumps(extra, ensure_ascii=False, default=str)[:400])
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    """Interactive multi-turn session; follow-up questions are rewritten using earlier turns."""
    from .conversation import Conversation

    conv = Conversation(role="staff" if args.staff else None, student_id=args.student)
    who = f"student {args.student}" if args.student else ("staff" if args.staff else "anonymous (not logged in)")
    print(f"UniRAG chat as {who}. Commands: /new (forget earlier turns), /quit")
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q.lower() in ("/quit", "/exit", "/q"):
            return 0
        if q.lower() == "/new":
            conv.reset()
            print("(new conversation)")
            continue
        try:
            r = conv.ask(q)
        except Exception as e:  # noqa: BLE001 - keep the session alive
            print(f"error: {type(e).__name__}: {e}")
            continue
        c = r.extra.get("conversation", {})
        if c.get("followup") and c.get("asked") != q:
            print(f"  (follow-up, understood as: {c['asked']})")
        print(f"\nunirag> {r.answer}")
        print(f"  [{r.route}" + (f", refused by {r.refused_by}" if r.refused_by else "")
              + f" · {r.gen_requests} model requests · {r.latency_ms / 1000:.1f} s · {r.verification['summary']}]")


def cmd_guard(args: argparse.Namespace) -> int:
    try:
        g = guard_sql(args.sql, student_id=args.student)
        print(json.dumps({"allowed": True, "sql": g.sql, "tables": g.tables, "notes": g.notes}, indent=2))
        return 0
    except GuardError as e:
        print(json.dumps({"allowed": False, "reason": str(e)}, indent=2))
        return 1


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles: show ₹ and ✔ correctly
    p = argparse.ArgumentParser(prog="unirag.cli", description="Test UniRAG retrieval paths")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("sql", help="Text-to-SQL path")
    ps.add_argument("question")
    ps.add_argument("--student", help="Scope personal tables to this student_id")
    ps.set_defaults(func=cmd_sql)

    pd = sub.add_parser("docs", help="Document path")
    pd.add_argument("question")
    pd.add_argument("--doc-type", action="append", help="Filter by doc_type (repeatable)")
    pd.add_argument("--dept", help="CS / IT / ENTC")
    pd.add_argument("--year", type=int)
    pd.add_argument("-k", type=int, default=6)
    pd.add_argument("--no-grade", action="store_true", help="Skip relevance grading")
    pd.set_defaults(func=cmd_docs)

    pa = sub.add_parser("ask", help="Full pipeline: route, retrieve, plan, answer, verify")
    pa.add_argument("question")
    pa.add_argument("--student", help="Logged-in student_id (enables 'my ...' questions)")
    pa.add_argument("--staff", action="store_true", help="Ask with staff access (named-student records)")
    pa.add_argument("--trace", action="store_true", help="Show the per-node trace")
    pa.add_argument("--json", action="store_true", help="Print the full result as JSON")
    pa.set_defaults(func=cmd_ask)

    pc = sub.add_parser("chat", help="Interactive multi-turn session with follow-up questions")
    who = pc.add_mutually_exclusive_group()
    who.add_argument("--student", help="Logged-in student_id")
    who.add_argument("--staff", action="store_true", help="Staff access")
    pc.set_defaults(func=cmd_chat)

    pg = sub.add_parser("guard", help="Check a SQL statement against the guard (no LLM, no DB)")
    pg.add_argument("sql")
    pg.add_argument("--student")
    pg.set_defaults(func=cmd_guard)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
