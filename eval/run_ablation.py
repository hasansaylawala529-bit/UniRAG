"""Ablation study: switch off one component at a time and measure what it was contributing.

    python eval/run_ablation.py                              # all variants, resumable
    python eval/run_ablation.py --variant no_rule_engine     # one variant at a time (free-tier friendly)
    python eval/run_ablation.py --report                     # only rebuild the report from saved results
    python eval/run_ablation.py --fresh --variant full       # re-run a variant from scratch

Variants (src/unirag/features.py): full system, and the full system without
  semantic_router · rule_engine · relevance_grading · schema_cards.
Questions: the SQL and hybrid questions of eval/questions.jsonl (same roles as run_eval.py).
Every variant shares the disk cache, so calls whose prompt is unchanged by the switch are not repeated.
Latency is the median over questions that made at least one live model call; for the full system, a question
answered entirely from cache uses its first live latency from eval/results/raw/ (run_eval.py), if present.
Outputs: eval/results/ablation/<variant>/<id>.json, eval/results/ablation_summary.md,
         eval/results/charts/ablation.png
"""
from __future__ import annotations

import argparse
import json
import statistics as stats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from unirag.evaluation import score_answer  # noqa: E402
from unirag.features import ablate  # noqa: E402

RES = ROOT / "eval" / "results"
OUT = RES / "ablation"
CHARTS = RES / "charts"
RAW = RES / "raw"
VARIANTS: dict[str, tuple[str, ...]] = {
    "full": (),
    "no_semantic_router": ("semantic_router",),
    "no_rule_engine": ("rule_engine",),
    "no_relevance_grading": ("relevance_grading",),
    "no_schema_cards": ("schema_cards",),
}
LABEL = {"full": "Full system", "no_semantic_router": "− semantic router", "no_rule_engine": "− rule engine",
         "no_relevance_grading": "− relevance grading", "no_schema_cards": "− schema-card retrieval"}
WHAT = {"no_semantic_router": "every question routed by the LLM planner",
        "no_rule_engine": "LLM-written SQL applies the eligibility rules",
        "no_relevance_grading": "top passages used ungraded",
        "no_schema_cards": "SQL step sees the full schema"}
BLUE, GREY, INK, MUTED, SURFACE = "#2a78d6", "#a3a29e", "#0b0b0b", "#52514e", "#fcfcfb"


def load_items(routes: list[str]) -> list[dict]:
    items = [json.loads(l) for l in (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines() if l]
    return [i for i in items if i["route"] in routes]


def run_one(item: dict, variant: str) -> dict:
    from run_eval import item_role
    from unirag.graph import ask

    t0 = time.perf_counter()
    try:
        with ablate(*VARIANTS[variant]):
            r = ask(item["question"], student_id=item.get("student"), role=item_role(item))
    except Exception as e:  # noqa: BLE001 - a failure is a result
        return {"id": item["id"], "variant": variant, "error": f"{type(e).__name__}: {e}",
                "expected_route": item["route"]}
    score = score_answer(r.answer, item["key_facts"], item.get("must_mention_any"))
    q = next((e for e in r.trace if e["node"] == "query_db"), None)
    return {"id": item["id"], "variant": variant, "expected_route": item["route"], "route": r.route,
            "stage": r.router_stage, "answer": r.answer, **score,
            "fully_correct": score["facts_found"] == score["facts_total"] and score["verdict_ok"],
            "verified_ok": r.verification["ok"], "sql_mode": q.get("mode") if q else None,
            "sql_ok": q.get("ok") if q else None, "sql_attempts": q.get("attempts") if q else None,
            "sql": r.sql, "gen_requests": r.gen_requests, "api_calls": r.llm_calls,
            "latency_ms": r.latency_ms, "wall_s": round(time.perf_counter() - t0, 2)}


def latency_s(rec: dict) -> float | None:
    """Live latency in seconds, or None when every call came from cache."""
    if rec.get("api_calls"):
        return rec["latency_ms"] / 1000
    if rec["variant"] == "full":
        raw = RAW / f"{rec['id']}.json"
        if raw.exists():
            old = json.loads(raw.read_text(encoding="utf-8"))
            if old.get("api_calls") and old.get("latency_ms"):
                return old["latency_ms"] / 1000
    return None


def metrics(recs: list[dict]) -> dict:
    ok = [r for r in recs if "error" not in r]
    lat = [x for x in (latency_s(r) for r in ok) if x is not None]
    by = {rt: [r for r in ok if r["expected_route"] == rt] for rt in ("sql", "hybrid")}
    return {
        "n": len(recs), "errors": len(recs) - len(ok),
        "routing": sum(r["route"] == r["expected_route"] for r in ok),
        "facts": sum(r["facts_found"] for r in ok), "facts_total": sum(r["facts_total"] for r in ok),
        "fully": sum(r["fully_correct"] for r in ok),
        "verified": sum(r["verified_ok"] for r in ok),
        "requests": (sum(r["gen_requests"] for r in ok) / len(ok)) if ok else 0.0,
        "latency": stats.median(lat) if lat else None, "latency_n": len(lat),
        "facts_by_route": {rt: (sum(r["facts_found"] for r in rs), sum(r["facts_total"] for r in rs))
                           for rt, rs in by.items()},
        "sql_failed": sum(1 for r in ok if r.get("sql_ok") is False),
    }


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "—"


def report(all_recs: dict[str, list[dict]], items: list[dict]) -> tuple[str, dict]:
    m = {v: metrics(rs) for v, rs in all_recs.items() if rs}
    n_sql = sum(i["route"] == "sql" for i in items)
    n_hyb = sum(i["route"] == "hybrid" for i in items)
    L = ["# Ablation study", "",
         f"{len(items)} questions (SQL {n_sql}, hybrid {n_hyb}) from `eval/questions.jsonl`. Each variant switches "
         "off one component (`src/unirag/features.py`); everything else is identical, including the model chain "
         "and the cache. Expected answers are computed from the database and the policy rules.", "",
         "| Variant | What changes | Routing | Key facts | SQL facts | Hybrid facts | Fully correct | Verified | "
         "Requests / q | Median latency |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        if v not in m:
            L.append(f"| {LABEL[v]} | {WHAT.get(v, '—')} | not run yet | | | | | | | |")
            continue
        x = m[v]
        n = x["n"] - x["errors"]
        (sf, st), (hf, ht) = x["facts_by_route"]["sql"], x["facts_by_route"]["hybrid"]
        lat = f"{x['latency']:.1f} s" if x["latency"] is not None else "cached"
        L.append(f"| {'**' + LABEL[v] + '**' if v == 'full' else LABEL[v]} | {WHAT.get(v, 'all components on')} | "
                 f"{x['routing']}/{n} | {x['facts']}/{x['facts_total']} ({pct(x['facts'], x['facts_total'])}) | "
                 f"{pct(sf, st)} | {pct(hf, ht)} | {x['fully']}/{n} | {x['verified']}/{n} | "
                 f"{x['requests']:.2f} | {lat} |")
    if "full" in m:
        f = m["full"]
        same, compared = 0, 0
        for r in all_recs["full"]:
            raw = RAW / f"{r['id']}.json"
            if "error" in r or not raw.exists():
                continue
            old = json.loads(raw.read_text(encoding="utf-8"))
            if "error" in old:
                continue
            compared += 1
            same += (old.get("facts_found"), old.get("route")) == (r["facts_found"], r["route"])
        if compared:
            L += ["", f"Consistency check: with every switch on, {same}/{compared} questions have the same route and "
                      "key facts as the last `run_eval.py` run (eval/results/raw/)."]
        L += ["", "## Effect of removing each component (vs the full system)", ""]
        for v in VARIANTS:
            if v == "full" or v not in m:
                continue
            x = m[v]
            d_facts = 100 * (x["facts"] / x["facts_total"] - f["facts"] / f["facts_total"]) if x["facts_total"] else 0
            parts = [f"key facts {d_facts:+.0f} pts", f"fully correct {x['fully'] - f['fully']:+d}",
                     f"routing {x['routing'] - f['routing']:+d}", f"verified {x['verified'] - f['verified']:+d}",
                     f"requests/q {x['requests'] - f['requests']:+.2f}"]
            if x["latency"] is not None and f["latency"] is not None:
                parts.append(f"median latency {x['latency'] - f['latency']:+.1f} s")
            if x["sql_failed"] != f["sql_failed"]:
                parts.append(f"failed SQL {x['sql_failed'] - f['sql_failed']:+d}")
            L.append(f"- **{LABEL[v]}** ({WHAT[v]}): " + ", ".join(parts))
        L += ["", "## Questions that changed", ""]
        full_by = {r["id"]: r for r in all_recs["full"] if "error" not in r}
        for v in VARIANTS:
            if v == "full" or v not in m:
                continue
            changes = []
            for r in all_recs[v]:
                b = full_by.get(r["id"])
                if "error" in r:
                    changes.append(f"{r['id']} error")
                elif b and (r["facts_found"], r["route"], r["verified_ok"]) != (b["facts_found"], b["route"], b["verified_ok"]):
                    bits = []
                    if r["facts_found"] != b["facts_found"]:
                        bits.append(f"facts {b['facts_found']}→{r['facts_found']}/{r['facts_total']}")
                    if r["route"] != b["route"]:
                        bits.append(f"route {b['route']}→{r['route']}")
                    if r["verified_ok"] != b["verified_ok"]:
                        bits.append("verifier " + ("✓→⚠" if b["verified_ok"] else "⚠→✓"))
                    changes.append(f"{r['id']} ({', '.join(bits)})")
            L.append(f"- {LABEL[v]}: " + ("; ".join(changes) if changes else "no question changed"))
    L += ["", "Latency: median over questions with at least one live model call (free tier; dominated by provider "
          "load). Requests / q counts generation requests including cached ones, so it reflects the design, "
          "not the cache."]
    return "\n".join(L) + "\n", m


def chart(m: dict) -> Path | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vs = [v for v in VARIANTS if v in m]
    if not vs:
        return None
    plt.rcParams.update({"font.size": 13, "axes.edgecolor": MUTED, "xtick.color": MUTED, "ytick.color": INK,
                         "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 6.2), dpi=200, sharey=True,
                                 gridspec_kw={"width_ratios": [1.6, 1]})
    labels = [LABEL[v] for v in vs]
    colours = [BLUE if v == "full" else GREY for v in vs]
    acc = [100 * m[v]["facts"] / m[v]["facts_total"] if m[v]["facts_total"] else 0 for v in vs]
    req = [m[v]["requests"] for v in vs]
    a1.barh(labels, acc, color=colours, height=0.6, edgecolor=SURFACE, linewidth=2)
    for i, v in enumerate(vs):
        n = m[v]["n"] - m[v]["errors"]
        a1.text(acc[i] + 1, i, f"{acc[i]:.0f}%  ·  {m[v]['fully']}/{n} fully correct", va="center",
                color=INK, fontsize=12)
    a1.set_xlim(0, 135)
    a1.set_xticks([0, 25, 50, 75, 100])
    a1.set_xlabel("Key facts found (%)", color=INK)
    a1.invert_yaxis()
    a2.barh(labels, req, color=colours, height=0.6, edgecolor=SURFACE, linewidth=2)
    for i, v in enumerate(vs):
        a2.text(req[i] + 0.05, i, f"{req[i]:.2f}", va="center", color=INK, fontsize=12)
    a2.set_xlim(0, max(req) * 1.3 if max(req) else 1)
    a2.set_xlabel("Generation requests per question", color=INK)
    a2.tick_params(axis="y", length=0)
    for ax in (a1, a2):
        ax.grid(axis="x", color="#e6e5e1", linewidth=0.8)
        ax.set_axisbelow(True)
    total = m[vs[0]]["n"]
    fig.suptitle(f"Ablation: what each component contributes ({total} SQL + hybrid questions)",
                 x=0.01, ha="left", fontsize=16, color=INK)
    fig.subplots_adjust(left=0.2, right=0.98, top=0.88, bottom=0.12, wspace=0.08)
    CHARTS.mkdir(parents=True, exist_ok=True)
    p = CHARTS / "ablation.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", nargs="+", choices=list(VARIANTS), help="run only these variants")
    ap.add_argument("--fresh", action="store_true", help="re-run the selected variants from scratch")
    ap.add_argument("--report", action="store_true", help="do not run anything; rebuild the report")
    ap.add_argument("--routes", nargs="+", default=["sql", "hybrid"], choices=["sql", "docs", "hybrid"])
    args = ap.parse_args()
    items = load_items(args.routes)
    selected = [] if args.report else (args.variant or list(VARIANTS))
    all_recs: dict[str, list[dict]] = {}
    for v in VARIANTS:
        d = OUT / v
        d.mkdir(parents=True, exist_ok=True)
        recs = []
        for n, it in enumerate(items, 1):
            path = d / f"{it['id']}.json"
            if path.exists() and not (args.fresh and v in selected):
                recs.append(json.loads(path.read_text(encoding="utf-8")))
                continue
            if v not in selected:
                continue
            rec = run_one(it, v)
            path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
            recs.append(rec)
            status = rec.get("error") or (f"{rec['route']:<7} facts {rec['facts_found']}/{rec['facts_total']} "
                                          f"req={rec['gen_requests']} {'✓' if rec['verified_ok'] else '⚠'}")
            print(f"[{v} {n:>2}/{len(items)}] {it['id']:<6} {status}")
        if len(recs) == len(items):
            all_recs[v] = recs
        elif recs:
            print(f"({v}: {len(recs)}/{len(items)} questions saved; left out of the report until complete)")
    md, m = report(all_recs, items)
    (RES / "ablation_summary.md").write_text(md, encoding="utf-8")
    p = chart(m)
    print("\n" + md)
    print("Report: eval/results/ablation_summary.md" + (f" · Chart: {p.relative_to(ROOT)}" if p else ""))


if __name__ == "__main__":
    main()
