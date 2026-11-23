"""UniRAG demo interface.

    streamlit run app/streamlit_app.py

Left: chat. Right: "How it was answered", the trace of the selected answer (route, pipeline steps,
profile, rule checks, SQL, policy passages, figure verification). The UI only displays what ask()
returns; all logic lives in src/unirag.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unirag.config import get_settings  # noqa: E402
from unirag.synthesis.formatting import inr  # noqa: E402
from unirag.examples import (DEMO_STUDENTS, DOCS_EXAMPLES, OUT_OF_SCOPE_EXAMPLES,  # noqa: E402
                             SQL_EXAMPLES, hybrid_examples)

st.set_page_config(page_title="UniRAG · DVUT assistant", page_icon="🎓", layout="wide")

ROUTE_STYLE = {   # route -> (label, colour)
    "sql": ("DATABASE", "#2563eb"),
    "docs": ("POLICY DOCUMENTS", "#059669"),
    "hybrid": ("HYBRID · DATABASE + POLICY", "#7c3aed"),
    "out_of_scope": ("OUT OF SCOPE", "#6b7280"),
    "refused": ("REFUSED BY POLICY", "#b45309"),
}
STEP_NAMES = {
    "profile": "Read who is asking",
    "route": "Chose the data source",
    "retrieve_docs": "Searched policy documents",
    "understand": "Planned: rules + data request",
    "grade_docs": "Checked passage relevance",
    "apply_rules": "Applied policy rules in code",
    "query_db": "Queried the database",
    "synthesize": "Wrote the cited answer",
    "verify": "Checked every number and date",
    "decline": "Declined politely",
}

st.markdown("""
<style>
.badge {display:inline-block;padding:4px 12px;border-radius:999px;color:#fff;font-weight:600;font-size:0.85rem}
.cite-sql {background:#dbeafe;color:#1e3a8a;border-radius:4px;padding:0 4px;font-size:0.85em;white-space:nowrap}
.cite-doc {background:#d1fae5;color:#064e3b;border-radius:4px;padding:0 4px;font-size:0.85em}
.footer {color:#6b7280;font-size:0.8rem;margin-top:4px}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ helpers
@st.cache_data(ttl=600, show_spinner=False)
def load_students(ids: tuple[str, ...]) -> dict[str, str]:
    """Labels for the 'Logged in as' selector, read from the database."""
    from unirag.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT s.student_id, s.full_name, d.code, s.year_of_study
                       FROM students s JOIN departments d USING (dept_id)
                       WHERE s.student_id = ANY(%s) ORDER BY s.student_id""", (list(ids),))
        return {sid: f"{sid} · {name} · {dept} year {year}" for sid, name, dept, year in cur.fetchall()}


def highlight_citations(text: str) -> str:
    text = re.sub(r"\[SQL\]", '<span class="cite-sql">SQL</span>', text)
    return re.sub(r"\[Doc:\s*([^\]]+)\]", r'<span class="cite-doc">📄 \1</span>', text)


def friendly_error(e: Exception) -> str:
    name, msg = type(e).__name__, str(e)
    if "OperationalError" in name or ("connection" in msg.lower() and "5433" in msg):
        return "The database is not reachable. Start it with `docker compose up -d` and try again."
    if "GEMINI_API_KEY" in msg:
        return "The Gemini API key is missing. Add GEMINI_API_KEY to the .env file."
    if "All models unavailable" in msg:
        return "All Gemini models are busy or out of quota right now. Please try again in a minute."
    return f"Something went wrong ({name}). The details are below."


def run_question(question: str, student: str | None, role: str | None) -> None:
    from unirag.graph import ask

    entry = {"question": question, "student": student, "role": role or "student", "result": None, "error": None}
    try:
        with st.spinner("Routing → retrieving → planning → answering → verifying…"):
            entry["result"] = ask(question, student_id=student, role=role).to_dict()
    except Exception as e:  # noqa: BLE001 - shown to the user, never a raw stack trace
        entry["error"] = {"message": friendly_error(e), "detail": f"{type(e).__name__}: {e}"}
    st.session_state.history.append(entry)
    st.session_state.selected = len(st.session_state.history) - 1


def models_used(r: dict) -> list[str]:
    """Distinct models that produced this answer (a step may list several, comma-separated)."""
    found = set()
    for e in r["trace"]:
        for m in (e.get("model") or "").split(","):
            if m.strip():
                found.add(m.strip())
    return sorted(found)


def footer(r: dict) -> str:
    v = r["verification"]
    used = models_used(r)
    provider = ""
    if used:
        names = ", ".join(("Groq " + m.removeprefix("groq:")) if m.startswith("groq:") else ("Gemini " + m)
                          for m in used)
        provider = f" · {names}"
    figs = len(v.get("figures", []))
    check = ("✅ all figures verified" if v["ok"] and figs else "✅ no figures to check" if v["ok"]
             else f"⚠️ {len([f for f in v['figures'] if not f['verified']])} figure(s) flagged")
    return (f'<div class="footer">{r["route"]} · {r["gen_requests"]} model requests · '
            f'{r["latency_ms"] / 1000:.1f} s{provider} · {check}</div>')


# ------------------------------------------------------------------ state
st.session_state.setdefault("history", [])
st.session_state.setdefault("selected", None)

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🎓 UniRAG")
    st.caption("Deccan Valley University of Technology · hybrid assistant over the database and policy documents")

    try:
        labels = load_students(tuple(DEMO_STUDENTS))
        db_ok = True
    except Exception:  # noqa: BLE001
        labels, db_ok = {}, False
    STAFF, ANON = "staff", "(not logged in)"
    role_labels = {STAFF: "Staff · academic advisor", ANON: "Not logged in (public)", **labels}
    options = [STAFF, ANON] + list(labels)
    choice = st.selectbox("Logged in as", options, format_func=lambda o: role_labels.get(o, o),
                          help="Staff: any named student's records. Student: only their own records. "
                               "Not logged in: policies and overall figures only.")
    student = choice if choice in labels else None
    role = "staff" if choice == STAFF else ("anonymous" if choice == ANON else None)

    if student:
        with st.expander("🛠️ Demo tools (admin)", expanded=False):
            st.caption("Proves answers come from live data: record a payment, then ask the same question "
                       "again. This is a separate admin action; the assistant itself is read-only.")
            from unirag import admin

            try:
                items = admin.unpaid_items(student)
            except Exception:  # noqa: BLE001
                items = []
            if items:
                pick = st.selectbox("Unpaid fee item", items, key="pay-item",
                                    format_func=lambda i: f"{i.component} · {inr(i.balance)} · due {i.due_date} · {i.status}")
                if st.button("Record payment", width="stretch"):
                    paid = admin.record_payment(student, pick.txn_id)
                    st.session_state.admin_msg = (f"Recorded full payment of {paid.component} for {student} "
                                                  f"(status now {paid.status}). Ask your question again.")
                    st.rerun()
            else:
                st.caption("No unpaid fee items for this student.")
            if st.button("Reset demo data", width="stretch"):
                n = admin.reset_demo_data()
                st.session_state.admin_msg = f"Fee ledger restored to the seeded data ({n} rows)."
                st.rerun()
            if st.session_state.get("admin_msg"):
                st.success(st.session_state.pop("admin_msg"))

    st.subheader("Try an example")
    groups = [("🗄️ Database", SQL_EXAMPLES), ("📄 Policy documents", DOCS_EXAMPLES),
              ("🔀 Hybrid (verified cases)", [f"{i}: {q}" for i, q in hybrid_examples()]),
              ("🚫 Out of scope", OUT_OF_SCOPE_EXAMPLES)]
    for title, items in groups:
        with st.expander(title, expanded=title.startswith("🔀")):
            for k, q in enumerate(items):
                if st.button(q, key=f"ex-{title}-{k}", width="stretch"):
                    st.session_state.pending = re.sub(r"^H\d+: ", "", q)

    st.divider()
    s = get_settings()
    st.caption(f"{'🟢' if db_ok else '🔴'} Database {'connected' if db_ok else 'not reachable'}  \n"
               f"Model: `{s.gen_model}` (backups: {', '.join(s.gen_model_fallbacks)}"
               + (f"; Groq: {', '.join(m.removeprefix('groq:') for m in s.groq_fallback_models)}" if s.groq_api_key else "")
               + ")  \n"
               f"Reference date: {s.as_of_date:%d %b %Y}")
    if st.button("Clear chat"):
        st.session_state.history, st.session_state.selected = [], None
        st.rerun()

# ------------------------------------------------------------------ main area
chat_col, trace_col = st.columns([3, 2], gap="large")

with chat_col:
    st.subheader("Ask about fees, grants, exams, hostel or courses")
    prompt = st.chat_input("e.g. I am a third-year CS student with a 3.8 GPA; which grants can I apply for?")
    pending = st.session_state.pop("pending", None) or prompt
    if pending:
        run_question(pending, student, role)

    if not st.session_state.history:
        st.info("Pick an example on the left or type a question. Each answer shows its route, the SQL, "
                "the policy passages and a check of every number in the panel on the right.")
    # newest first, so a new answer appears right under the input box during the demo
    for idx, entry in reversed(list(enumerate(st.session_state.history))):
        with st.chat_message("user"):
            who = (f" _(logged in as {entry['student']})_" if entry["student"]
                   else " _(staff)_" if entry.get("role") == "staff" else " _(not logged in)_")
            st.markdown(entry["question"] + who)
        with st.chat_message("assistant"):
            if entry["error"]:
                st.error(entry["error"]["message"])
                with st.expander("Technical detail"):
                    st.code(entry["error"]["detail"])
            else:
                r = entry["result"]
                st.markdown(highlight_citations(r["answer"]), unsafe_allow_html=True)
                st.markdown(footer(r), unsafe_allow_html=True)
                if st.button("Show how this was answered", key=f"sel-{idx}"):
                    st.session_state.selected = idx

# ------------------------------------------------------------------ trace panel
with trace_col:
    st.subheader("How it was answered")
    sel = st.session_state.selected
    entry = st.session_state.history[sel] if sel is not None and sel < len(st.session_state.history) else None
    if entry is None or entry["result"] is None:
        st.caption("The trace of the selected answer appears here.")
    else:
        r = entry["result"]
        trace = {e["node"]: e for e in r["trace"]}
        label, colour = ROUTE_STYLE.get(r["route"], (r["route"].upper(), "#374151"))
        route_ev = trace.get("route", {})
        conf = route_ev.get("confidence")
        if r["router_stage"] == "semantic":
            decided = f"decided by the **semantic router** (embeddings, no LLM, vote {conf:.0%})"
        elif r["router_stage"] == "rule":
            decided = f"stopped by a **{r.get('refused_by') or 'policy rule'}**, before any model call"
        else:
            decided = (f"semantic router unsure ({conf:.0%}) → decided by the **LLM planner**"
                       if conf is not None else "decided by the **LLM planner**")
        st.markdown(f'<span class="badge" style="background:{colour}">{label}</span>&nbsp; {decided}',
                    unsafe_allow_html=True)
        st.caption(f"Q: {r['question']}")

        t_over, t_rules, t_sql, t_docs, t_ver = st.tabs(["Pipeline", "Rules", "SQL", "Documents", "Verification"])

        with t_over:
            steps = pd.DataFrame([{
                "Step": f"{i}. {STEP_NAMES.get(e['node'], e['node'])}", "Time (ms)": e["ms"],
                "Model requests": e.get("gen_requests", 0)} for i, e in enumerate(r["trace"], 1)])
            st.dataframe(steps, hide_index=True, width="stretch")
            models = models_used(r)
            if models:
                st.caption("Model used: " + ", ".join(models)
                           + ("  ·  ⚠️ fallback provider (Groq) answered" if any(m.startswith("groq:") for m in models) else ""))
            c1, c2, c3 = st.columns(3)
            c1.metric("Model requests", r["gen_requests"])
            c2.metric("Latency", f"{r['latency_ms'] / 1000:.1f} s")
            c3.metric("Figures verified", "✓" if r["verification"]["ok"] else "⚠")
            if r["profile"]:
                st.markdown("**Profile used**")
                st.dataframe(pd.DataFrame([{"field": k, "value": str(v)} for k, v in r["profile"].items()]),
                             hide_index=True, width="stretch")
            if "understand" in trace and trace["understand"].get("sql_task"):
                st.markdown("**Planner's data request**")
                st.caption(trace["understand"]["sql_task"])
            if r.get("refused_by"):
                st.warning(f"Refused by: {r['refused_by']} · role: {r.get('role')}")
            fixed = trace.get("synthesize", {}).get("fixed_message")
            if fixed:
                st.info(f"Fixed message, no model call: {fixed}")
            skipped = [s for e in r["trace"] for s in e.get("skipped_models", [])]
            if skipped:
                st.caption("Skipped (overloaded recently, circuit open): " + ", ".join(dict.fromkeys(skipped)))
            for n in r["notes"]:
                st.warning(n)

        with t_rules:
            if not r["rules"]:
                st.caption("No policy rules were evaluated for this question.")
            else:
                icon = {"pass": "✅ pass", "fail": "❌ fail", "unknown": "❔ needs data"}
                st.dataframe(pd.DataFrame([{
                    "Subject": c["subject"], "Condition": f"{c['field']} {c['operator']} {c['value']}",
                    "Result": icon.get(c["outcome"], c["outcome"]), "Why": c["detail"], "Source": c["citation"]}
                    for c in r["rules"]["checks"]]), hide_index=True, width="stretch")
                st.caption("The LLM only *read* these rules from the policy; each was checked against the "
                           "profile in code.")

        with t_sql:
            q_ev = trace.get("query_db")
            if not r["sql"]:
                st.caption("No database query for this question." if not q_ev or not q_ev.get("withheld")
                           else "Query withheld: personal records need a logged-in student.")
            else:
                mode = q_ev.get("mode") if q_ev else None
                if mode == "compiled_from_rules":
                    st.success("Compiled in code from the rule results: no LLM, parameterised, read-only.")
                else:
                    st.info(f"LLM-generated, then validated by the SQL guard and run as a read-only role "
                            f"(attempts: {q_ev.get('attempts', 1) if q_ev else 1}).")
                st.code(r["sql"], language="sql")
                if q_ev and q_ev.get("executed_at"):
                    st.caption(f"⏱ Executed live against PostgreSQL at {q_ev['executed_at']} · "
                               f"{len(r['sql_rows'])} row(s). Answers are never served from embedded copies of data.")
                if q_ev and q_ev.get("guard"):
                    st.caption("Guard: " + "; ".join(q_ev["guard"]))
                if r["sql_rows"]:
                    st.dataframe(pd.DataFrame(r["sql_rows"], columns=r["sql_columns"]), hide_index=True,
                                 width="stretch")

        with t_docs:
            if not r["passages"]:
                st.caption("No policy passages were used.")
            for p in r["passages"]:
                with st.expander(f"📄 {p['citation']}"):
                    st.markdown(p.get("excerpt", ""))

        with t_ver:
            v = r["verification"]
            (st.success if v["ok"] else st.warning)(v["summary"])
            if v.get("figures"):
                st.dataframe(pd.DataFrame([{
                    "Figure": f["text"], "Type": f["kind"], "Status": "✅ verified" if f["verified"] else "⚠️ flagged",
                    "Found in": f["basis"] or "not found in the evidence"} for f in v["figures"]]),
                    hide_index=True, width="stretch")
            st.caption("Every number and date in the answer must come from the SQL rows or the policy text, "
                       "or be a simple derivation of them.")
