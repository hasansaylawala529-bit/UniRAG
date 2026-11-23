"""Build the evaluation set eval/questions.jsonl with COMPUTED expected answers.

    python eval/build_questions.py

Expected key facts come from SQL queries on the seeded database and from the same grant rules /
policy constants the data generator used, never typed by hand. Questions are worded differently from
the router exemplars, so routing is measured on unseen phrasings.

Each item: id, route (expected), question, student (logged-in student or null), key_facts,
must_mention_any (optional verdict words), source.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unirag.db import connect, jsonable  # noqa: E402
from unirag.ingest.exemplars import EXEMPLARS  # noqa: E402
from unirag.synthesis.formatting import human_date, inr  # noqa: E402

spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "generate_data.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)          # GRANTS, POLICY, grant_status (main() is not run)


def q1(sql: str, params: tuple | None = None):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
        return jsonable(r[0]) if r and len(r) == 1 else tuple(jsonable(v) for v in r)


def qall(sql: str, params: tuple | None = None):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [tuple(jsonable(v) for v in r) for r in cur.fetchall()]


def d(iso: str) -> str:
    return human_date(iso)


def eligible_grants(dept: str, year: int, cgpa: float, has_dues: bool | None = None) -> list[dict]:
    out = []
    for g in gen.GRANTS:
        if cgpa >= g["min_cgpa"] and year in g["years"] and dept in g["depts"]:
            if g["extra"] and has_dues:
                continue
            out.append({**g, "status": gen.grant_status(g)})
    return out


def grant_facts(gs: list[dict]) -> list[str]:
    facts = []
    for g in gs:
        if g["status"] in ("open", "upcoming"):
            facts += [g["code"], inr(g["amount"]), gen.fmt_date(g["deadline"])]
    return facts


def build() -> list[dict]:
    items: list[dict] = []

    def add(route, question, facts, student=None, must=None, source=""):
        items.append({"id": f"{route[:3].upper()}{sum(1 for i in items if i['route'] == route) + 1:02d}",
                      "route": route, "question": question, "student": student, "key_facts": facts,
                      "must_mention_any": must or [], "source": source})

    # ------------------------------------------------------------- SQL-only (10)
    n_it = q1("SELECT count(*) FROM students s JOIN departments d USING (dept_id) WHERE d.code='IT'")
    add("sql", "What is the total number of students in Information Technology?", [str(n_it)], source="students")
    jan = qall("""SELECT grant_code, application_deadline::text FROM grants WHERE status='open'
                  AND application_deadline BETWEEN '2027-01-01' AND '2027-01-31' ORDER BY 2""")
    add("sql", "Which grants are open with a deadline in January 2027?",
        [c for c, _ in jan] + sorted({d(x) for _, x in jan}), source="grants")
    t = q1("""SELECT amount FROM fee_structure fs JOIN departments d USING (dept_id)
              WHERE d.code='ENTC' AND year_of_study=3 AND component='tuition'""")
    add("sql", "What is the annual tuition for a third-year ENTC student?", [inr(t)], source="fee_structure")
    n_od = q1("SELECT count(*) FROM fee_ledger WHERE status='overdue'")
    add("sql", "How many fee ledger entries are currently overdue across the university?", [str(n_od)],
        source="fee_ledger")
    hod = q1("SELECT hod_name FROM departments WHERE code='IT'")
    add("sql", "Who heads the Information Technology department?", [hod.replace("Dr. ", "")], source="departments")
    instr = "Prof. A. Phadke"
    codes = [c for (c,) in qall("SELECT course_code FROM courses WHERE instructor=%s ORDER BY 1", (instr,))]
    add("sql", f"List the course codes taught by {instr} this term.", codes, source="courses")
    avg = q1("""SELECT round(avg(cgpa), 2) FROM students s JOIN departments d USING (dept_id)
                WHERE d.code='CS' AND year_of_study=3""")
    add("sql", "What is the average CGPA of third-year CS students, rounded to two decimals?", [f"{avg:.2f}"],
        source="students")
    cap, taken = q1("""SELECT c.capacity, count(e.*) FILTER (WHERE e.status='enrolled') FROM courses c
                       LEFT JOIN enrollments e USING (course_id) WHERE c.course_code='CS3E1' GROUP BY c.capacity""")
    add("sql", "How many seats are still free in the CS3E1 Machine Learning elective?", [str(cap - taken)],
        source="courses+enrollments")
    od = qall("""SELECT component, (amount_due - amount_paid)::numeric FROM fee_ledger
                 WHERE student_id='S2026CS012' AND status='overdue' ORDER BY due_date""")
    add("sql", "Show me my overdue fee items and how much is outstanding on each.", [inr(b) for _, b in od],
        student="S2026CS012", source="fee_ledger (scoped)")
    n_app = q1("SELECT count(*) FROM grant_applications a JOIN grants g USING (grant_id) WHERE g.grant_code='GR-SUS-08'")
    add("sql", "How many applications has the GR-SUS-08 grant received so far?", [str(n_app)],
        source="grant_applications")

    # ------------------------------------------------------------- documents-only (10)
    P = gen.POLICY
    add("docs", "Below what attendance percentage am I barred from a course's end-semester exam?",
        [f"{int(P['attendance_min'])}%"], source="Exam Rules §2")
    add("docs", "What does attendance condonation cost per course?", [inr(P["condonation_fee"])],
        source="Exam Rules §3")
    add("docs", "How is the late fine on an overdue tuition instalment calculated, and is there a maximum?",
        [inr(P["late_fine_per_day"]), inr(P["late_fine_cap"])], source="Fee Policy §4")
    add("docs", "If I withdraw ten days after classes begin, what share of the tuition I paid is refunded?",
        ["75%"], source="Fee Policy §6")
    add("docs", "What is the formula to turn a 10-point CGPA into a 4-point GPA?", ["0.4"], source="Handbook §4")
    add("docs", "Until what date can hostel residents renew their rooms for next year?", ["30 Apr 2027"],
        source="Hostel Rules §3")
    add("docs", "What percentage merit concession does a CGPA of 9.2 earn?", ["25%"], source="Scholarship §2")
    add("docs", "What is the highest credit load allowed with an approved overload?",
        [str(P["credits_max_overload"])], source="Registration §3")
    vlsi = next(g for g in gen.GRANTS if g["code"] == "GR-VLSI-09")
    add("docs", "What minimum CGPA does the VLSI Design Research Fellowship require?", [f"{vlsi['min_cgpa']:.1f}"],
        source="Grant Policy §3")
    add("docs", "Within how many working days are grant applications screened after the deadline?", ["10"],
        source="Grant Policy §4")

    # ------------------------------------------------------------- hybrid: 6 generator cases + 6 new
    for h in json.loads((ROOT / "data" / "hybrid_questions.json").read_text(encoding="utf-8")):
        items.append({"id": h["id"], "route": "hybrid", "question": h["question"], "student": None,
                      "key_facts": h["key_facts"], "must_mention_any": [], "source": "data/hybrid_questions.json"})
    g7 = eligible_grants("ENTC", 4, 8.2)
    add("hybrid", "I'm a final-year ENTC student with CGPA 8.2. Which research grants can I go for, and by when?",
        grant_facts(g7), source="grant rules x grants")
    g8 = eligible_grants("IT", 2, round(3.1 / 0.4, 2))
    add("hybrid", "Second-year IT student here, GPA 3.1 on a 4-point scale. Which grants am I eligible for?",
        grant_facts(g8), source="grant rules x grants")
    # cybersecurity scholarship for a real CS/IT year-3/4 student with overdue dues -> not eligible
    # (the dues rule alone rules them out, whatever their CGPA)
    cy = qall("""SELECT s.student_id FROM students s JOIN departments d USING (dept_id)
                 WHERE d.code IN ('CS','IT') AND s.year_of_study IN (3,4)
                 AND EXISTS (SELECT 1 FROM fee_ledger f WHERE f.student_id=s.student_id AND f.status='overdue')
                 ORDER BY 1 LIMIT 1""")
    if cy:
        add("hybrid", "Can I apply for the Cybersecurity Research Scholarship GR-CY-03?", ["GR-CY-03"],
            student=cy[0][0], must=["not eligible", "not currently eligible", "cannot", "can't", "ineligible", "no,"],
            source="grant rule (no overdue dues) x fee_ledger")
    # refund now (> 30 days after term start) -> no refund
    paid = qall("""SELECT student_id FROM fee_ledger WHERE component='tuition_inst1' AND status='paid'
                   AND student_id LIKE 'S2025%' ORDER BY 1 LIMIT 1""")
    add("hybrid", "If I withdraw from the university today, how much of my tuition will be refunded?", [],
        student=paid[0][0], must=["no refund", "not eligible for a refund", "₹0", "nothing", "not be refunded",
                                  "no tuition refund"], source="Fee Policy §6 x term dates")
    # attendance below 75 but >= 65 in exactly one course, logged in
    low = qall("""SELECT e.student_id, c.course_code, e.attendance_pct FROM enrollments e JOIN courses c USING (course_id)
                  WHERE e.status='enrolled' AND e.attendance_pct >= 65 AND e.attendance_pct < 75
                  AND e.student_id LIKE 'S2024%' AND (SELECT count(*) FROM enrollments e2 WHERE e2.student_id=e.student_id
                  AND e2.status='enrolled' AND e2.attendance_pct < 75) = 1 ORDER BY 1 LIMIT 1""")
    if low:
        sid, code, pct = low[0]
        add("hybrid", "Which of my courses are below the attendance requirement, and can the shortfall be condoned?",
            [code, f"{pct}%", inr(P["condonation_fee"])], student=sid, source="enrollments x Exam Rules §2-3")
    # merit concession for a 9.0+ student with no overdue dues
    m = qall("""SELECT s.student_id, d.code, s.year_of_study FROM students s JOIN departments d USING (dept_id)
                WHERE s.cgpa >= 9.0 AND s.year_of_study IN (2,3)
                AND NOT EXISTS (SELECT 1 FROM fee_ledger f WHERE f.student_id=s.student_id AND f.status='overdue')
                ORDER BY 1 LIMIT 1""")
    if m:
        sid, dept, yr = m[0]
        tu = q1("""SELECT amount FROM fee_structure fs JOIN departments d USING (dept_id)
                   WHERE d.code=%s AND year_of_study=%s AND component='tuition'""", (dept, yr))
        add("hybrid", "How much merit concession will I get on next year's tuition?",
            ["25%", inr(tu), inr(tu * 25 / 100)], student=sid, source="students x Scholarship §2-4 x fee_structure")

    # ------------------------------------------------------------- out of scope (4)
    for q in ["What's the forecast for Mumbai this weekend?", "Compose a short poem about the monsoon.",
              "Which country won the last football world cup?", "What is Infosys trading at today?"]:
        add("out_of_scope", q, [], source="—")

    exemplar_texts = {q.strip().lower() for q, _ in EXEMPLARS}
    clash = [i["question"] for i in items if i["question"].strip().lower() in exemplar_texts]
    if clash:
        raise SystemExit(f"Evaluation questions duplicate router exemplars: {clash}")
    return items


if __name__ == "__main__":
    items = build()
    out = ROOT / "eval" / "questions.jsonl"
    out.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
    from collections import Counter
    print(f"Wrote {len(items)} questions to {out}: {dict(Counter(i['route'] for i in items))}")
