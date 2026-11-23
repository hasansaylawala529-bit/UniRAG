"""Evaluate UniRAG on eval/questions.jsonl and write tables + slide-ready charts.

    python eval/build_questions.py        # once: computed test set
    python eval/run_eval.py               # resumable; re-runs only questions without a saved result
    python eval/run_eval.py --fresh       # discard saved results and run everything again

Outputs (eval/results/):
  raw/<id>.json, baseline/<id>.json     one record per question (kept between runs)
  summary.md                            all tables
  charts/*.png                          16:9 charts for slides
Latency is reported only for questions that made at least one live API call (cached answers are
near-instant and would flatter the numbers).
"""
from __future__ import annotations

import argparse
import json
import statistics as stats
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from unirag.evaluation import score_answer  # noqa: E402

RES = ROOT / "eval" / "results"
RAW, BASE, CHARTS = RES / "raw", RES / "baseline", RES / "charts"
ROUTES = ["sql", "docs", "hybrid", "out_of_scope"]
ROUTE_LABEL = {"sql": "SQL", "docs": "Documents", "hybrid": "Hybrid", "out_of_scope": "Out of scope"}
BLUE, ORANGE, INK, MUTED, SURFACE = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#fcfcfb"


def load_items() -> list[dict]:
    return [json.loads(l) for l in (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines() if l]


# ------------------------------------------------------------------ running
def run_item(item: dict) -> dict:
    from unirag.graph import ask

    t0 = time.perf_counter()
    try:
        r = ask(item["question"], student_id=item.get("student"))
    except Exception as e:  # noqa: BLE001 - a failure is a result, not a crash
        return {"id": item["id"], "error": f"{type(e).__name__}: {e}", "expected_route": item["route"]}
    q = next((e for e in r.trace if e["node"] == "query_db"), None)
    score = score_answer(r.answer, item["key_facts"], item.get("must_mention_any"))
    flagged = [f["text"] for f in r.verification.get("figures", []) if not f["verified"]]
    return {
        "id": item["id"], "question": item["question"], "student": item.get("student"),
        "expected_route": item["route"], "route": r.route, "stage": r.router_stage,
        "answer": r.answer, **score, "verified_ok": r.verification["ok"], "flagged": flagged,
        "sql_mode": q.get("mode") if q else None, "sql_ok": q.get("ok") if q else None,
        "sql_attempts": q.get("attempts") if q else None, "sql": r.sql,
        "gen_requests": r.gen_requests, "api_calls": r.llm_calls,
        "latency_ms": r.latency_ms, "wall_s": round(time.perf_counter() - t0, 2),
    }


def run_baseline(item: dict) -> dict:
    """Vector-only RAG: top-8 policy passages + one generation call. No database, no planner."""
    from unirag.config import get_settings
    from unirag.llm import get_llm
    from unirag.prompts import render
    from unirag.retrieval.vector_search import search_docs
    from unirag.synthesis.formatting import fix_inr_grouping
    from unirag.synthesis.synthesize import passages_for_prompt

    hits = search_docs(item["question"], k=8)
    prompt, _ = render("baseline_vector_only", question=item["question"], passages=passages_for_prompt(hits))
    answer = fix_inr_grouping(str(get_llm().generate(prompt, model=get_settings().gen_model)))
    return {"id": item["id"], "answer": answer,
            **score_answer(answer, item["key_facts"], item.get("must_mention_any"))}


def cached_run(path: Path, fn, item: dict, fresh: bool) -> dict:
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if old and not fresh:
        return old
    rec = fn(item)
    # a re-run reuses cached planner/SQL calls, so its latency would flatter the system:
    # keep the latency of the first (fully live) run
    if old and "latency_ms" in old and "latency_ms" in rec:
        rec["latency_ms"], rec["api_calls"] = old["latency_ms"], old.get("api_calls", rec["api_calls"])
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    return rec


# ------------------------------------------------------------------ metrics
def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "—"


def p90(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))] if xs else 0.0


def summarise(items: list[dict], recs: dict, base: dict) -> tuple[str, dict]:
    ok = [recs[i["id"]] for i in items if "error" not in recs[i["id"]]]
    errors = [recs[i["id"]] for i in items if "error" in recs[i["id"]]]
    L: list[str] = ["# UniRAG evaluation", "",
                    f"{len(items)} questions ({', '.join(f'{ROUTE_LABEL[r]} {sum(i['route'] == r for i in items)}' for r in ROUTES)}). "
                    "Expected answers are computed from the database and the policy rules (`eval/build_questions.py`); "
                    "questions are worded differently from the router exemplars.", ""]
    if errors:
        L += [f"**Runtime errors:** {len(errors)} — " + "; ".join(f"{e['id']}: {e['error'][:80]}" for e in errors), ""]

    # routing
    cm = {(a, b): 0 for a in ROUTES for b in ROUTES}
    for r in ok:
        cm[(r["expected_route"], r["route"])] += 1
    correct = sum(cm[(r, r)] for r in ROUTES)
    stage = Counter(r["stage"] for r in ok)
    L += ["## 1. Routing", "",
          f"**Accuracy: {correct}/{len(ok)} ({pct(correct, len(ok))})** · decided by semantic router "
          f"{stage.get('semantic', 0)}, LLM planner {stage.get('llm', 0)}, privacy rule {stage.get('rule', 0)}", "",
          "| expected ↓ / actual → | " + " | ".join(ROUTE_LABEL[r] for r in ROUTES) + " |",
          "|---|" + "---|" * len(ROUTES)]
    for a in ROUTES:
        L.append(f"| **{ROUTE_LABEL[a]}** | " + " | ".join(str(cm[(a, b)]) for b in ROUTES) + " |")
    wrong = [r for r in ok if r["route"] != r["expected_route"]]
    if wrong:
        L += ["", "Misrouted: " + "; ".join(f"{r['id']} ({r['expected_route']} → {r['route']})" for r in wrong)]

    # accuracy
    L += ["", "## 2. Answer accuracy (key facts)", "",
          "| Route | Questions | Key facts found | Fully correct questions |", "|---|---|---|---|"]
    acc = {}
    for rt in ROUTES:
        rs = [r for r in ok if r["expected_route"] == rt]
        if not rs:
            continue
        if rt == "out_of_scope":
            dec = sum(r["route"] == "out_of_scope" for r in rs)
            L.append(f"| {ROUTE_LABEL[rt]} | {len(rs)} | — (declined {dec}/{len(rs)}) | {dec}/{len(rs)} |")
            acc[rt] = dec / len(rs)
            continue
        f_found = sum(r["facts_found"] for r in rs)
        f_tot = sum(r["facts_total"] for r in rs)
        full = sum(r["facts_found"] == r["facts_total"] and r["verdict_ok"] for r in rs)
        acc[rt] = f_found / f_tot if f_tot else full / len(rs)
        L.append(f"| {ROUTE_LABEL[rt]} | {len(rs)} | {f_found}/{f_tot} ({pct(f_found, f_tot)}) | {full}/{len(rs)} |")
    tot_f = sum(r["facts_found"] for r in ok)
    tot_t = sum(r["facts_total"] for r in ok)
    L += [f"| **All** | {len(ok)} | **{tot_f}/{tot_t} ({pct(tot_f, tot_t)})** | "
          f"{sum(r['facts_found'] == r['facts_total'] and r['verdict_ok'] for r in ok)}/{len(ok)} |"]

    # baseline
    hyb = [i for i in items if i["route"] == "hybrid"]
    bl = [base[i["id"]] for i in hyb if i["id"] in base]
    hy = [recs[i["id"]] for i in hyb if i["id"] in recs and "error" not in recs[i["id"]]]
    b_found, b_tot = sum(b["facts_found"] for b in bl), sum(b["facts_total"] for b in bl)
    u_found, u_tot = sum(r["facts_found"] for r in hy), sum(r["facts_total"] for r in hy)
    b_full = sum(b["facts_found"] == b["facts_total"] and b["verdict_ok"] for b in bl)
    u_full = sum(r["facts_found"] == r["facts_total"] and r["verdict_ok"] for r in hy)
    acc["baseline_hybrid"] = b_found / b_tot if b_tot else 0
    L += ["", "## 3. Hybrid questions: UniRAG vs vector-only RAG baseline", "",
          "Baseline = top-8 policy passages + one generation call (no database, no planner).", "",
          "| System | Key facts found | Fully correct questions |", "|---|---|---|",
          f"| Vector-only RAG | {b_found}/{b_tot} ({pct(b_found, b_tot)}) | {b_full}/{len(bl)} |",
          f"| **UniRAG** | **{u_found}/{u_tot} ({pct(u_found, u_tot)})** | **{u_full}/{len(hy)}** |"]

    # verification + SQL
    ver_ok = sum(r["verified_ok"] for r in ok)
    flagged = sum(len(r["flagged"]) for r in ok)
    sql_runs = [r for r in ok if r["sql_mode"]]
    compiled = [r for r in sql_runs if r["sql_mode"] == "compiled_from_rules"]
    llm_sql = [r for r in sql_runs if r["sql_mode"] == "text_to_sql"]
    first = sum(1 for r in llm_sql if r["sql_ok"] and r["sql_attempts"] == 1)
    repaired = sum(1 for r in llm_sql if r["sql_ok"] and (r["sql_attempts"] or 0) > 1)
    failed = sum(1 for r in llm_sql if not r["sql_ok"])
    L += ["", "## 4. Verification and SQL", "",
          f"- Verifier: **{ver_ok}/{len(ok)} answers fully verified**; {flagged} figure(s) flagged in total.",
          f"- SQL ran for {len(sql_runs)} questions: {len(compiled)} compiled in code from rule results, "
          f"{len(llm_sql)} LLM-generated + guarded.",
          f"- LLM SQL success: {first}/{len(llm_sql)} first attempt, {repaired} after self-repair, {failed} failed "
          f"→ **{pct(first + repaired, len(llm_sql))} overall**."]

    # cost + latency
    L += ["", "## 5. Cost and latency per question", "",
          "| Route | Generation requests (median / max) | Latency median | Latency p90 | Live runs |",
          "|---|---|---|---|---|"]
    lat = {}
    for rt in ROUTES:
        rs = [r for r in ok if r["expected_route"] == rt]
        if not rs:
            continue
        req = [r["gen_requests"] for r in rs]
        live = [r["latency_ms"] / 1000 for r in rs if r["api_calls"] > 0]
        lat[rt] = (stats.median(live) if live else 0, p90(live))
        L.append(f"| {ROUTE_LABEL[rt]} | {stats.median(req):g} / {max(req)} | "
                 + (f"{lat[rt][0]:.1f} s | {lat[rt][1]:.1f} s" if live else "— | —") + f" | {len(live)}/{len(rs)} |")

    # failures
    bad = [r for r in ok if r["facts_found"] < r["facts_total"] or not r["verdict_ok"] or not r["verified_ok"]
           or r["route"] != r["expected_route"]]
    L += ["", "## 6. Every imperfect answer", ""]
    if not bad:
        L.append("None.")
    for r in bad:
        why = []
        if r["route"] != r["expected_route"]:
            why.append(f"routed {r['route']} (expected {r['expected_route']})")
        if r["missing"]:
            why.append("missing " + ", ".join(r["missing"]))
        if not r["verdict_ok"]:
            why.append("wrong or missing verdict")
        if r["flagged"]:
            why.append("verifier flagged " + ", ".join(r["flagged"]))
        L.append(f"- **{r['id']}** — {r['question']} → " + "; ".join(why))
    metrics = {"cm": cm, "acc": acc, "lat": lat, "routing": (correct, len(ok)),
               "facts": (tot_f, tot_t), "baseline": (b_found, b_tot), "unirag_hybrid": (u_found, u_tot)}
    return "\n".join(L) + "\n", metrics


# ------------------------------------------------------------------ charts
def charts(m: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    CHARTS.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 13, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                         "ytick.color": MUTED, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
                         "axes.spines.top": False, "axes.spines.right": False})
    out = []

    # 1. confusion matrix (sequential blue, one hue)
    fig, ax = plt.subplots(figsize=(8, 6.2), dpi=200)
    data = [[m["cm"][(a, b)] for b in ROUTES] for a in ROUTES]
    vmax = max(max(r) for r in data) or 1
    ax.imshow(data, cmap="Blues", vmin=0, vmax=vmax * 1.15)
    for i, row in enumerate(data):
        for j, v in enumerate(row):
            ax.text(j, i, str(v), ha="center", va="center", fontsize=18, fontweight="bold",
                    color="white" if v > vmax * 0.55 else INK)
    labels = [ROUTE_LABEL[r] for r in ROUTES]
    ax.set_xticks(range(4), labels)
    ax.set_yticks(range(4), labels)
    ax.set_xlabel("Route chosen by UniRAG")
    ax.set_ylabel("Expected route")
    c, n = m["routing"]
    ax.set_title(f"Routing accuracy {c}/{n} ({100 * c / n:.0f}%)", loc="left", fontsize=16, color=INK, pad=12)
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    p = CHARTS / "routing_confusion_matrix.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    out.append(p)

    # 2. key-fact accuracy by route + baseline on hybrid
    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=200)
    cats = [r for r in ["sql", "docs", "hybrid"] if r in m["acc"]]
    xs = list(range(len(cats)))
    w = 0.36
    uni = [100 * m["acc"][r] for r in cats]
    bars = ax.bar([x - w / 2 for x in xs], uni, w, color=BLUE, label="UniRAG", zorder=3)
    hi = cats.index("hybrid") if "hybrid" in cats else None
    if hi is not None:
        bb = ax.bar([hi + w / 2], [100 * m["acc"]["baseline_hybrid"]], w, color=ORANGE,
                    label="Vector-only RAG baseline", zorder=3)
        for b in bb:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5, f"{b.get_height():.0f}%", ha="center",
                    color=INK, fontsize=13)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5, f"{b.get_height():.0f}%", ha="center",
                color=INK, fontsize=13, fontweight="bold")
    ax.set_xticks(xs, [ROUTE_LABEL[r] + " questions" for r in cats])
    ax.set_ylim(0, 110)
    ax.set_ylabel("Key facts found (%)")
    ax.yaxis.grid(True, color="#e6e5e0", zorder=0)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0, 1.02), ncol=2)
    ax.set_title("Answer accuracy by question type", loc="left", fontsize=16, color=INK, pad=28)
    fig.tight_layout()
    p = CHARTS / "accuracy_by_route_vs_baseline.png"
    fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)
    out.append(p)

    # 3. latency median / p90 by route (live runs only)
    cats = [r for r in ROUTES if r in m["lat"] and m["lat"][r][0] > 0]
    if cats:
        fig, ax = plt.subplots(figsize=(11, 6.2), dpi=200)
        xs = list(range(len(cats)))
        med = [m["lat"][r][0] for r in cats]
        p9 = [m["lat"][r][1] for r in cats]
        b1 = ax.bar([x - w / 2 for x in xs], med, w, color=BLUE, label="Median", zorder=3)
        b2 = ax.bar([x + w / 2 for x in xs], p9, w, color=ORANGE, label="90th percentile", zorder=3)
        for b in list(b1) + list(b2):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + max(p9) * 0.02, f"{b.get_height():.1f}s",
                    ha="center", color=INK, fontsize=12)
        ax.set_xticks(xs, [ROUTE_LABEL[r] for r in cats])
        ax.set_ylabel("Seconds per question (live API calls)")
        ax.yaxis.grid(True, color="#e6e5e0", zorder=0)
        ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0, 1.02), ncol=2)
        ax.set_title("Latency by route (Gemini free tier, Flash-Lite)", loc="left", fontsize=16, color=INK, pad=28)
        fig.tight_layout()
        p = CHARTS / "latency_by_route.png"
        fig.savefig(p, facecolor=SURFACE)
        plt.close(fig)
        out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="ignore saved results")
    ap.add_argument("--skip-baseline", action="store_true")
    args = ap.parse_args()
    for d in (RAW, BASE, CHARTS):
        d.mkdir(parents=True, exist_ok=True)
    items = load_items()
    recs, base = {}, {}
    for n, it in enumerate(items, 1):
        r = cached_run(RAW / f"{it['id']}.json", run_item, it, args.fresh)
        recs[it["id"]] = r
        status = r.get("error") or (f"{r['route']:<12} facts {r['facts_found']}/{r['facts_total']}"
                                    f"{'' if r['verdict_ok'] else ' VERDICT✗'} req={r['gen_requests']} "
                                    f"{'✓' if r['verified_ok'] else '⚠'}")
        print(f"[{n:>2}/{len(items)}] {it['id']:<6} {status}")
        if it["route"] == "hybrid" and not args.skip_baseline:
            base[it["id"]] = cached_run(BASE / f"{it['id']}.json", run_baseline, it, args.fresh)
    md, m = summarise(items, recs, base)
    (RES / "summary.md").write_text(md, encoding="utf-8")
    paths = charts(m)
    print("\n" + md.split("## 6.")[0])
    print("Charts: " + ", ".join(str(p.relative_to(ROOT)) for p in paths))


if __name__ == "__main__":
    main()
