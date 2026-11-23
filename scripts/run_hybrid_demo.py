"""Run the six hybrid questions (data/hybrid_questions.json) through the full pipeline and compare
each answer with the expected answer computed by the data generator.

    python scripts/run_hybrid_demo.py            # all six
    python scripts/run_hybrid_demo.py H1 H3      # selected

A key fact (grant code, amount, date, percentage, count) passes if it appears in the answer, after the
same normalisation the verifier uses (₹1,50,000 = 150000, "20 Dec 2026" = 2026-12-20).
Writes eval/results/hybrid_demo.md and .json. Answers are cached, so re-runs cost no API calls.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from unirag.graph import ask  # noqa: E402
from unirag.evaluation import answer_values, fact_present  # noqa: E402

def main(selected: list[str]) -> None:
    items = json.loads((ROOT / "data" / "hybrid_questions.json").read_text(encoding="utf-8"))
    if selected:
        items = [i for i in items if i["id"] in selected]
    results, lines = [], ["# Hybrid demo questions: actual vs expected", ""]
    for it in items:
        print(f"\n=== {it['id']}: {it['question']}")
        t0 = time.perf_counter()
        r = ask(it["question"])
        vals = answer_values(r.answer)
        facts = {f: fact_present(f, r.answer, vals) for f in it["key_facts"]}
        hit = sum(facts.values())
        res = {"id": it["id"], "route": r.route, "stage": r.router_stage, "gen_requests": r.gen_requests,
               "api_calls": r.llm_calls, "latency_ms": r.latency_ms, "facts_found": hit,
               "facts_total": len(facts), "missing": [f for f, ok in facts.items() if not ok],
               "verification_ok": r.verification["ok"], "verification": r.verification["summary"],
               "answer": r.answer, "sql": r.sql, "sql_columns": r.sql_columns, "sql_rows": r.sql_rows[:10],
               "notes": r.notes}
        results.append(res)
        print(f"route={r.route} ({r.router_stage}) · gen requests={r.gen_requests} · "
              f"facts {hit}/{len(facts)} · verified={r.verification['ok']} · {time.perf_counter() - t0:.1f}s")
        if res["missing"]:
            print("missing:", ", ".join(res["missing"]))
        lines += [f"## {it['id']}. {it['question']}", "",
                  f"**Route:** {r.route} (stage: {r.router_stage}) · **generation requests:** {r.gen_requests} · "
                  f"**key facts:** {hit}/{len(facts)} · **verifier:** {r.verification['summary']}", "",
                  "**UniRAG answer:**", "", r.answer, "",
                  f"**SQL:** `{r.sql}`", "", f"**Columns:** {', '.join(r.sql_columns)}", "", "**Expected (from the data generator):**", "",
                  it["expected"], ""]
        if res["missing"]:
            lines += [f"**Missing facts:** {', '.join(res['missing'])}", ""]
    tot_f = sum(r["facts_found"] for r in results)
    tot = sum(r["facts_total"] for r in results)
    summary = (f"{len(results)} questions · key facts {tot_f}/{tot} · routed hybrid "
               f"{sum(r['route'] == 'hybrid' for r in results)}/{len(results)} · max generation requests "
               f"{max((r['gen_requests'] for r in results), default=0)} · verifier clean "
               f"{sum(r['verification_ok'] for r in results)}/{len(results)}")
    lines.insert(2, f"**Summary:** {summary}\n")
    out = ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "hybrid_demo.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "hybrid_demo.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{summary}\nReport: eval/results/hybrid_demo.md")


if __name__ == "__main__":
    main(sys.argv[1:])
