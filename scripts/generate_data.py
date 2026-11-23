"""Generate the complete, reproducible synthetic dataset for UniRAG.

Outputs
  data/seed/*.csv                         one CSV per relational table
  data/docs/02_research_grant_policy.md   rendered from the same grant definitions as grants.csv
  data/README.md                          dataset summary + hybrid demo questions with expected answers

The other six policy documents in data/docs/ are written by hand. Their numeric
rules are mirrored in the POLICY constants below, which are used to compute the
expected answers.

Usage:  python scripts/generate_data.py
"""
from __future__ import annotations

import csv
import random
from datetime import date, time, timedelta
from pathlib import Path

SEED = 42
ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "data" / "seed"
DOCS_DIR = ROOT / "data" / "docs"

UNIVERSITY = "Deccan Valley University of Technology (DVUT), Pune"
SNAPSHOT = date(2026, 11, 23)          # ledger and grant statuses are computed as of this date
ACADEMIC_YEAR = "2026-27"
TERM = "2026-ODD"
TERM_START = date(2026, 7, 27)
ADD_DROP_DEADLINE = date(2026, 8, 10)   # Course Registration Policy §4

# ---------------------------------------------------------------------------
# Policy constants (must match the hand-written policy documents)
# ---------------------------------------------------------------------------
POLICY = {
    "gpa4_factor": 0.4,                 # Handbook §4: 4-point GPA = CGPA x 0.4
    "late_fine_per_day": 100,           # Fee Policy §4
    "late_fine_cap": 5000,              # per component
    "exam_late_fee_flat": 500,          # Fee Policy §4 / Exam Rules §5
    "attendance_min": 75.0,             # Exam Rules §2
    "attendance_condonable_min": 65.0,  # Exam Rules §3
    "condonation_fee": 500,             # per course
    "credits_min": 16,                  # Course Registration §3
    "credits_max": 24,
    "credits_max_overload": 28,
    "overload_cgpa": 8.5,
    "merit_slabs": [(9.0, 25), (8.5, 15), (8.0, 10)],   # Scholarship Policy §2 (CGPA >=, % of tuition)
    "hostel_renewal_cgpa": 6.0,         # Hostel Rules §3
}

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
DEPARTMENTS = [
    (1, "CS", "Computer Science & Engineering", "Dr. Meera Kulkarni"),
    (2, "IT", "Information Technology", "Dr. Rajesh Iyer"),
    (3, "ENTC", "Electronics & Telecommunication", "Dr. Sunita Deshmukh"),
]
DEPT_ID = {code: did for did, code, _, _ in DEPARTMENTS}
STUDENTS_PER_YEAR = {"CS": 18, "IT": 16, "ENTC": 16}      # x 4 years = 200

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan", "Kabir",
    "Atharva", "Omkar", "Pranav", "Siddharth", "Yash", "Tanmay", "Nikhil", "Varun", "Harsh", "Kunal",
    "Aanya", "Diya", "Ananya", "Saanvi", "Isha", "Pooja", "Sneha", "Riya", "Kavya", "Meera",
    "Shruti", "Tanvi", "Neha", "Aditi", "Gauri", "Mrunal", "Sakshi", "Prachi", "Anushka", "Janhavi",
]
LAST_NAMES = [
    "Patil", "Deshpande", "Kulkarni", "Joshi", "Shinde", "Pawar", "Jadhav", "Gaikwad", "More", "Bhosale",
    "Sharma", "Verma", "Iyer", "Nair", "Reddy", "Rao", "Menon", "Gupta", "Mehta", "Shah",
    "Chavan", "Kale", "Apte", "Gokhale", "Ranade", "Sawant", "Naik", "Desai", "Mishra", "Banerjee",
]
INSTRUCTORS = [
    "Prof. A. Phadke", "Prof. S. Karve", "Prof. R. Bapat", "Prof. N. Damle", "Prof. V. Limaye",
    "Prof. K. Oak", "Prof. M. Tilak", "Prof. P. Sathe", "Prof. G. Ketkar", "Prof. D. Bhide",
    "Prof. H. Mane", "Prof. L. Waghmare", "Prof. T. Salunkhe", "Prof. J. Kamble", "Prof. B. Thorat",
]

FIRST_YEAR = ["Engineering Mathematics I", "Engineering Physics", "Programming in C",
              "Basic Electrical Engineering", "Programming Lab I"]
COURSES = {  # semester -> (core x4 + lab, [dept electives])
    "CS": {
        1: (FIRST_YEAR, []),
        3: (["Data Structures", "Discrete Mathematics", "Computer Organization",
             "Object Oriented Programming", "Data Structures Lab"], []),
        5: (["Database Management Systems", "Theory of Computation", "Computer Networks",
             "Software Engineering", "DBMS Lab"], ["Machine Learning", "Cloud Computing"]),
        7: (["Distributed Systems", "Compiler Design", "Information Security",
             "Project Phase I", "Project Lab"], ["Natural Language Processing", "Blockchain Technology"]),
    },
    "IT": {
        1: (FIRST_YEAR, []),
        3: (["Data Structures & Algorithms", "Digital Logic Design", "Computer Graphics",
             "Java Programming", "DSA Lab"], []),
        5: (["Operating Systems", "Web Technologies", "Design & Analysis of Algorithms",
             "Human Computer Interaction", "Web Technologies Lab"], ["Data Science", "Internet of Things"]),
        7: (["Cyber Security", "Big Data Analytics", "Mobile Computing",
             "Project Phase I", "Project Lab"], ["Deep Learning", "DevOps Engineering"]),
    },
    "ENTC": {
        1: (FIRST_YEAR, []),
        3: (["Electronic Devices & Circuits", "Signals & Systems", "Digital Electronics",
             "Network Theory", "EDC Lab"], []),
        5: (["Digital Communication", "Microcontrollers", "Control Systems",
             "Electromagnetics", "Microcontroller Lab"], ["VLSI Design", "Embedded Systems"]),
        7: (["Wireless Communication", "Digital Signal Processing", "Antenna & Wave Propagation",
             "Project Phase I", "DSP Lab"], ["Robotics", "Computer Vision"]),
    },
}
CORE_CREDITS = [4, 4, 3, 3, 2]
OPEN_ELECTIVES = [("OE301", "Entrepreneurship & Innovation", 2, 5),
                  ("OE302", "Financial Literacy for Engineers", 1, 5),
                  ("OE401", "Technical Writing & Communication", 3, 7)]

TUITION = {"CS": 145000, "IT": 140000, "ENTC": 130000}
FIRST_YEAR_TUITION_UPLIFT = 5000
LAB_FEE = {"CS": 8000, "IT": 8000, "ENTC": 9500}
EXAM_FEE = 3500
HOSTEL_FEE = 95000
LEDGER_DUE = {
    "hostel": date(2026, 7, 31),
    "tuition_inst1": date(2026, 8, 14),
    "lab": date(2026, 8, 14),
    "exam": date(2026, 10, 31),
    "tuition_inst2": date(2027, 1, 15),
}

# Grants: amounts/dates go to grants.csv; eligibility goes ONLY to the policy document.
GRANTS = [
    dict(code="GR-AI-01", title="AI for Social Good Research Fellowship", sponsor="DVUT Research & Innovation Cell",
         amount=150000, max_awards=5, open=date(2026, 11, 1), deadline=date(2026, 12, 20), quota_closed=False,
         min_cgpa=8.5, years=[3, 4], depts=["CS", "IT"],
         docs=["Research proposal (max 3 pages)", "Faculty mentor consent form", "Latest grade card"],
         extra=None, focus="AI/ML projects with measurable social impact (health, agriculture, accessibility)."),
    dict(code="GR-ML-02", title="Undergraduate Machine Learning Research Assistantship",
         sponsor="Centre for Applied Machine Learning, DVUT",
         amount=60000, max_awards=4, open=date(2026, 11, 10), deadline=date(2027, 1, 10), quota_closed=False,
         min_cgpa=9.0, years=[3], depts=["CS"],
         docs=["Statement of purpose (1 page)", "Latest grade card"],
         extra=None, focus="Paid research assistant role in an ongoing ML lab project for one semester."),
    dict(code="GR-CY-03", title="Cybersecurity Research Scholarship", sponsor="Nexora Secure Systems Foundation",
         amount=75000, max_awards=3, open=date(2026, 11, 15), deadline=date(2027, 1, 31), quota_closed=False,
         min_cgpa=8.0, years=[3, 4], depts=["CS", "IT"],
         docs=["Research proposal (max 3 pages)", "Latest grade card", "No-dues certificate from Accounts"],
         extra="The applicant must have no overdue fee dues in the University fee ledger on the date of application.",
         focus="Applied security research: network defence, secure software, privacy engineering."),
    dict(code="GR-QC-04", title="Quantum Computing Exploratory Grant", sponsor="DVUT Research & Innovation Cell",
         amount=100000, max_awards=2, open=date(2026, 11, 1), deadline=date(2027, 2, 15), quota_closed=False,
         min_cgpa=9.5, years=[4], depts=["CS", "IT", "ENTC"],
         docs=["Research proposal (max 5 pages)", "Two faculty recommendation letters"],
         extra=None, focus="Final-year exploratory projects in quantum algorithms or quantum hardware simulation."),
    dict(code="GR-DS-05", title="Data Science for Governance Fellowship", sponsor="Pune Civic Data Trust",
         amount=50000, max_awards=6, open=date(2026, 11, 5), deadline=date(2026, 12, 31), quota_closed=False,
         min_cgpa=7.5, years=[2, 3], depts=["IT"],
         docs=["Statement of purpose (1 page)", "Latest grade card"],
         extra=None, focus="Analysis of open civic datasets with a municipal partner."),
    dict(code="GR-HW-06", title="Hardware Hackathon Seed Fund", sponsor="DVUT Makerspace",
         amount=30000, max_awards=8, open=date(2026, 10, 1), deadline=date(2026, 12, 15), quota_closed=True,
         min_cgpa=7.0, years=[2, 3, 4], depts=["CS", "IT", "ENTC"],
         docs=["Prototype plan with bill of materials", "Team declaration form"],
         extra=None, focus="Seed money for hardware prototypes built at the University makerspace."),
    dict(code="GR-IOT-07", title="Smart Campus IoT Research Grant", sponsor="Campus Infrastructure Office, DVUT",
         amount=80000, max_awards=3, open=date(2026, 11, 1), deadline=date(2027, 1, 20), quota_closed=False,
         min_cgpa=8.0, years=[3, 4], depts=["ENTC", "IT"],
         docs=["Research proposal (max 3 pages)", "Faculty mentor consent form"],
         extra=None, focus="IoT deployments on campus: energy metering, occupancy sensing, water monitoring."),
    dict(code="GR-SUS-08", title="Sustainability & Green Computing Grant", sponsor="Western Ghats Sustainability Fund",
         amount=70000, max_awards=4, open=date(2026, 11, 1), deadline=date(2027, 2, 28), quota_closed=False,
         min_cgpa=8.0, years=[2, 3, 4], depts=["CS", "IT", "ENTC"],
         docs=["Research proposal (max 3 pages)", "Latest grade card"],
         extra=None, focus="Energy-efficient computing, carbon accounting, and climate data projects."),
    dict(code="GR-VLSI-09", title="VLSI Design Research Fellowship", sponsor="Silicon Deccan Semiconductor Consortium",
         amount=120000, max_awards=2, open=date(2026, 11, 20), deadline=date(2027, 2, 10), quota_closed=False,
         min_cgpa=8.0, years=[3, 4], depts=["ENTC"],
         docs=["Research proposal (max 3 pages)", "Faculty mentor consent form", "Latest grade card"],
         extra=None, focus="Digital and analog IC design projects using University EDA licences."),
    dict(code="GR-HC-10", title="HealthTech Innovation Grant", sponsor="Sahyadri Health Innovation Network",
         amount=90000, max_awards=3, open=date(2027, 1, 5), deadline=date(2027, 3, 15), quota_closed=False,
         min_cgpa=8.5, years=[3, 4], depts=["CS", "ENTC"],
         docs=["Research proposal (max 3 pages)", "Ethics declaration", "Latest grade card"],
         extra=None, focus="Medical devices, diagnostic software and health-data platforms."),
    dict(code="GR-OS-11", title="Open Source Contribution Grant", sponsor="DVUT Open Source Programme Office",
         amount=25000, max_awards=10, open=date(2026, 11, 1), deadline=date(2026, 12, 31), quota_closed=False,
         min_cgpa=7.0, years=[2], depts=["CS", "IT"],
         docs=["Links to at least two merged pull requests", "Mentor contact details"],
         extra=None, focus="Stipend for sustained contributions to recognised open-source projects."),
    dict(code="GR-ROB-12", title="Autonomous Robotics Research Grant", sponsor="DVUT Robotics Society",
         amount=85000, max_awards=2, open=date(2026, 10, 15), deadline=date(2026, 12, 31), quota_closed=True,
         min_cgpa=8.0, years=[3, 4], depts=["ENTC", "CS"],
         docs=["Research proposal (max 3 pages)", "Faculty mentor consent form"],
         extra=None, focus="Autonomous navigation, manipulation and swarm robotics."),
    dict(code="GR-ENT-13", title="Student Startup Seed Grant", sponsor="DVUT Incubation Centre",
         amount=200000, max_awards=2, open=date(2026, 11, 1), deadline=date(2027, 3, 1), quota_closed=False,
         min_cgpa=7.5, years=[4], depts=["CS", "IT", "ENTC"],
         docs=["Business plan (max 10 pages)", "Pitch deck", "Incubation Centre interview"],
         extra=None, focus="Seed funding for final-year student startups admitted to the Incubation Centre."),
    dict(code="GR-SP-14", title="Summer Research Internship Stipend", sponsor="DVUT Research & Innovation Cell",
         amount=40000, max_awards=12, open=date(2026, 12, 1), deadline=date(2027, 3, 31), quota_closed=False,
         min_cgpa=9.0, years=[2, 3], depts=["CS", "IT", "ENTC"],
         docs=["Faculty mentor letter confirming a summer project", "Latest grade card"],
         extra=None, focus="Eight-week summer research internship inside a DVUT laboratory."),
    dict(code="GR-5G-15", title="5G and Next-Gen Networks Fellowship", sponsor="Telecom Research Alliance of India",
         amount=110000, max_awards=2, open=date(2026, 11, 1), deadline=date(2027, 1, 31), quota_closed=False,
         min_cgpa=8.0, years=[4], depts=["ENTC"],
         docs=["Research proposal (max 3 pages)", "Faculty mentor consent form"],
         extra=None, focus="Radio access network, network slicing and spectrum research."),
]
# A grant that opens after the snapshot date is "upcoming" even when SNAPSHOT is inside other windows.
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
PERIODS = [(time(9, 0), time(10, 0)), (time(10, 0), time(11, 0)), (time(11, 15), time(12, 15)),
           (time(12, 15), time(13, 15)), (time(14, 0), time(15, 0)), (time(15, 0), time(16, 0))]
OPEN_ELECTIVE_SLOTS = [("Wed", 5), ("Fri", 5)]           # reserved for open electives in every timetable
LAB_SLOT = (time(14, 0), time(16, 0))


def inr(x: float) -> str:
    """Format a number in Indian digit grouping, e.g. 145000 -> ₹1,45,000."""
    neg = x < 0
    n = int(round(abs(x)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + "₹" + s


def fmt_date(d: date) -> str:
    return d.strftime("%d %b %Y")


def write_csv(name: str, header: list[str], rows: list[list]) -> None:
    with open(SEED_DIR / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def grant_status(g: dict) -> str:
    if g["quota_closed"]:
        return "closed"
    if SNAPSHOT < g["open"]:
        return "upcoming"
    if SNAPSHOT > g["deadline"]:
        return "closed"
    return "open"


def main() -> None:
    rng = random.Random(SEED)
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------- departments ----------------
    write_csv("departments", ["dept_id", "code", "name", "hod_name"], [list(d) for d in DEPARTMENTS])

    # ---------------- students ----------------
    students = []
    used_names, used_emails = set(), set()
    for code in ["CS", "IT", "ENTC"]:
        for year in [1, 2, 3, 4]:
            adm = 2027 - year
            for i in range(1, STUDENTS_PER_YEAR[code] + 1):
                while True:
                    fn, ln = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
                    if (fn, ln) not in used_names:
                        used_names.add((fn, ln))
                        break
                email = f"{fn.lower()}.{ln.lower()}@dvut.edu.in"
                k = 2
                while email in used_emails:
                    email = f"{fn.lower()}.{ln.lower()}{k}@dvut.edu.in"
                    k += 1
                used_emails.add(email)
                if year == 1:
                    cgpa = None          # first CGPA is declared after Semester 1
                else:
                    cgpa = round(min(9.9, max(5.2, rng.gauss(7.7, 1.0))), 2)
                gpa4 = None if cgpa is None else round(cgpa * POLICY["gpa4_factor"], 2)
                students.append(dict(
                    student_id=f"S{adm}{code}{i:03d}", full_name=f"{fn} {ln}", dept=code,
                    dept_id=DEPT_ID[code], year=year, cgpa=cgpa, gpa_4=gpa4, admission_year=adm,
                    is_hosteller=rng.random() < 0.4, email=email,
                ))
    write_csv("students", ["student_id", "full_name", "dept_id", "year_of_study", "cgpa", "gpa_4",
                           "admission_year", "is_hosteller", "email"],
              [[s["student_id"], s["full_name"], s["dept_id"], s["year"], s["cgpa"], s["gpa_4"],
                s["admission_year"], str(s["is_hosteller"]).lower(), s["email"]] for s in students])

    # ---------------- courses ----------------
    courses = []
    cid = 100
    for code, sems in COURSES.items():
        for sem, (core, electives) in sems.items():
            yr = (sem + 1) // 2
            for idx, title in enumerate(core):
                cid += 1
                courses.append(dict(course_id=cid, course_code=f"{code}{yr}{idx + 1:02d}", title=title,
                                    dept=code, dept_id=DEPT_ID[code], semester=sem, year=yr,
                                    credits=CORE_CREDITS[idx], course_type="lab" if idx == 4 else "core",
                                    capacity=STUDENTS_PER_YEAR[code] + 12, instructor=rng.choice(INSTRUCTORS)))
            for e_idx, title in enumerate(electives):
                cid += 1
                courses.append(dict(course_id=cid, course_code=f"{code}{yr}E{e_idx + 1}", title=title,
                                    dept=code, dept_id=DEPT_ID[code], semester=sem, year=yr, credits=3,
                                    course_type="dept_elective", capacity=15, instructor=rng.choice(INSTRUCTORS)))
    for oe_code, title, did, sem in OPEN_ELECTIVES:
        cid += 1
        courses.append(dict(course_id=cid, course_code=oe_code, title=title,
                            dept=[c for d, c, _, _ in DEPARTMENTS if d == did][0], dept_id=did,
                            semester=sem, year=(sem + 1) // 2, credits=3, course_type="open_elective",
                            capacity=40, instructor=rng.choice(INSTRUCTORS)))
    write_csv("courses", ["course_id", "course_code", "title", "dept_id", "semester", "credits",
                          "course_type", "capacity", "instructor"],
              [[c["course_id"], c["course_code"], c["title"], c["dept_id"], c["semester"], c["credits"],
                c["course_type"], c["capacity"], c["instructor"]] for c in courses])

    # ---------------- enrollments ----------------
    enrollments = []
    eid = 0
    seats = {c["course_id"]: 0 for c in courses}

    def enroll(s, c, status="enrolled"):
        nonlocal eid
        eid += 1
        base = s["_att_base"]
        att = round(min(100.0, max(40.0, rng.gauss(base, 5.0))), 1)
        enrollments.append(dict(enrollment_id=eid, student_id=s["student_id"], course=c,
                                status=status, attendance_pct=att))
        if status == "enrolled":
            seats[c["course_id"]] += 1

    for s in students:
        s["_att_base"] = rng.choice([rng.gauss(86, 5)] * 4 + [rng.gauss(74, 6)])   # ~20% weak attendance
        core = [c for c in courses if c["dept"] == s["dept"] and c["year"] == s["year"]
                and c["course_type"] in ("core", "lab")]
        for c in core:
            enroll(s, c)
        if s["year"] >= 3:
            dept_el = [c for c in courses if c["dept"] == s["dept"] and c["year"] == s["year"]
                       and c["course_type"] == "dept_elective"]
            rng.shuffle(dept_el)
            picks = dept_el[:2] if (s["cgpa"] or 0) >= POLICY["overload_cgpa"] and rng.random() < 0.4 else dept_el[:1]
            for c in picks:
                if seats[c["course_id"]] < c["capacity"]:
                    enroll(s, c)
            oes = [c for c in courses if c["course_type"] == "open_elective" and c["year"] == s["year"]]
            if oes and rng.random() < 0.55:
                c = rng.choice(oes)
                enroll(s, c, status="dropped" if rng.random() < 0.08 else "enrolled")
    write_csv("enrollments", ["enrollment_id", "student_id", "course_id", "term", "status", "attendance_pct", "grade"],
              [[e["enrollment_id"], e["student_id"], e["course"]["course_id"], TERM, e["status"],
                e["attendance_pct"], None] for e in enrollments])

    # ---------------- timetable ----------------
    slots = []
    sid = 0
    reserved = set(OPEN_ELECTIVE_SLOTS)
    for code in ["CS", "IT", "ENTC"]:
        for yr in [1, 2, 3, 4]:
            group = [c for c in courses if c["dept"] == code and c["year"] == yr
                     and c["course_type"] != "open_elective"]
            free = [(d, p) for d in DAYS for p in range(len(PERIODS)) if (d, p) not in reserved]
            rng.shuffle(free)
            lab_days = ["Mon", "Tue", "Thu"]        # Wed/Fri afternoons hold open electives
            rng.shuffle(lab_days)
            room = f"{code[0]}-{yr}0{DEPT_ID[code]}"
            for c in group:
                if c["course_type"] == "lab":
                    d = lab_days.pop()
                    free = [(fd, fp) for fd, fp in free if not (fd == d and fp in (4, 5))]
                    sid += 1
                    slots.append([sid, c["course_id"], d, LAB_SLOT[0], LAB_SLOT[1], f"Lab-{code}{yr}", TERM])
            for c in group:
                if c["course_type"] == "lab":
                    continue
                n = c["credits"]
                used_days = set()
                for (d, p) in list(free):
                    if n == 0:
                        break
                    if d in used_days:
                        continue
                    free.remove((d, p))
                    used_days.add(d)
                    n -= 1
                    sid += 1
                    r = room if c["course_type"] == "core" else f"B-{c['course_code'][-2:]}{DEPT_ID[code]}"
                    slots.append([sid, c["course_id"], d, PERIODS[p][0], PERIODS[p][1], r, TERM])
    for c in [c for c in courses if c["course_type"] == "open_elective"]:
        for (d, p) in OPEN_ELECTIVE_SLOTS:
            sid += 1
            slots.append([sid, c["course_id"], d, PERIODS[p][0], PERIODS[p][1], f"Auditorium-{c['course_code'][-1]}", TERM])
    write_csv("timetable", ["slot_id", "course_id", "day_of_week", "start_time", "end_time", "room", "term"],
              [[r[0], r[1], r[2], r[3].strftime("%H:%M"), r[4].strftime("%H:%M"), r[5], r[6]] for r in slots])

    # ---------------- fee structure ----------------
    fs_rows = []
    fee = {}
    for did, code, _, _ in DEPARTMENTS:
        for yr in [1, 2, 3, 4]:
            comps = {"tuition": TUITION[code] + (FIRST_YEAR_TUITION_UPLIFT if yr == 1 else 0),
                     "exam": EXAM_FEE, "lab": LAB_FEE[code], "hostel": HOSTEL_FEE}
            for comp, amt in comps.items():
                fs_rows.append([did, yr, comp, f"{amt:.2f}", ACADEMIC_YEAR])
                fee[(code, yr, comp)] = amt
    write_csv("fee_structure", ["dept_id", "year_of_study", "component", "amount", "academic_year"], fs_rows)

    # ---------------- fee ledger ----------------
    ledger = []
    tid = 0
    for s in students:
        profile = rng.choices(["on_time", "late", "defaulter"], weights=[72, 16, 12])[0]
        tuition = fee[(s["dept"], s["year"], "tuition")]
        items = {"tuition_inst1": round(tuition * 0.6), "tuition_inst2": round(tuition * 0.4),
                 "exam": EXAM_FEE, "lab": fee[(s["dept"], s["year"], "lab")]}
        if s["is_hosteller"]:
            items["hostel"] = HOSTEL_FEE
        defaulted = set()
        if profile == "defaulter":
            past = [k for k in items if LEDGER_DUE[k] < SNAPSHOT]
            defaulted = set(rng.sample(past, k=rng.choice([1, 1, 2])))
        for comp, due_amt in items.items():
            due = LEDGER_DUE[comp]
            if due >= SNAPSHOT:                       # not yet due
                paid = due_amt if rng.random() < 0.3 else 0
                paid_on = due - timedelta(days=rng.randint(10, 60)) if paid else None
                if paid_on and paid_on > SNAPSHOT:
                    paid_on = SNAPSHOT - timedelta(days=rng.randint(1, 20))
            elif comp in defaulted:
                paid = 0 if rng.random() < 0.6 else round(due_amt * rng.choice([0.25, 0.5]))
                paid_on = due + timedelta(days=rng.randint(5, 40)) if paid else None
            elif profile == "late" and rng.random() < 0.5:
                paid = due_amt
                paid_on = due + timedelta(days=rng.randint(3, 25))
            else:
                paid = due_amt
                paid_on = due - timedelta(days=rng.randint(0, 20))
            if paid_on and paid_on > SNAPSHOT:          # no payment can be dated after the snapshot
                paid_on = SNAPSHOT
            balance = due_amt - paid
            if balance == 0:
                status = "paid"
            elif due >= SNAPSHOT:
                status = "partial" if paid else "pending"
            else:
                status = "overdue"
            tid += 1
            ledger.append(dict(txn_id=tid, student_id=s["student_id"], component=comp, amount_due=due_amt,
                               amount_paid=paid, due_date=due, paid_on=paid_on, status=status))
    write_csv("fee_ledger", ["txn_id", "student_id", "academic_year", "component", "amount_due", "amount_paid",
                             "due_date", "paid_on", "status"],
              [[l["txn_id"], l["student_id"], ACADEMIC_YEAR, l["component"], f"{l['amount_due']:.2f}",
                f"{l['amount_paid']:.2f}", l["due_date"].isoformat(),
                l["paid_on"].isoformat() if l["paid_on"] else None, l["status"]] for l in ledger])

    # ---------------- grants ----------------
    grant_rows = []
    for i, g in enumerate(GRANTS, start=1):
        g["grant_id"] = i
        g["status"] = grant_status(g)
        grant_rows.append([i, g["code"], g["title"], g["sponsor"], f"{g['amount']:.2f}", g["max_awards"],
                           g["open"].isoformat(), g["deadline"].isoformat(), g["status"],
                           "Research Grant Policy 2026-27"])
    write_csv("grants", ["grant_id", "grant_code", "title", "sponsor", "amount", "max_awards",
                         "application_open", "application_deadline", "status", "policy_doc"], grant_rows)

    # ---------------- grant applications ----------------
    overdue_ids = {l["student_id"] for l in ledger if l["status"] == "overdue"}

    def eligible(s, g, check_dues=True):
        if s["cgpa"] is None or s["cgpa"] < g["min_cgpa"]:
            return False
        if s["year"] not in g["years"] or s["dept"] not in g["depts"]:
            return False
        if check_dues and g["extra"] and s["student_id"] in overdue_ids:
            return False
        return True

    apps = []
    aid = 0
    for g in GRANTS:
        if SNAPSHOT < g["open"]:
            continue
        pool = [s for s in students if eligible(s, g)]
        rng.shuffle(pool)
        n = min(len(pool), g["max_awards"] if g["quota_closed"] else rng.randint(1, max(1, g["max_awards"])))
        for j, s in enumerate(pool[:n]):
            aid += 1
            submitted = g["open"] + timedelta(days=rng.randint(0, max(0, min((SNAPSHOT - g["open"]).days, 30))))
            st = "awarded" if g["quota_closed"] else rng.choice(["submitted", "submitted", "under_review"])
            apps.append([aid, g["grant_id"], s["student_id"], submitted.isoformat(), st])
    write_csv("grant_applications", ["application_id", "grant_id", "student_id", "submitted_on", "status"], apps)

    # ---------------- grant policy document ----------------
    render_grant_policy()

    # ---------------- expected answers ----------------
    write_data_readme(students, courses, enrollments, ledger, fee, eligible, overdue_ids, apps)
    print(f"Generated: {len(students)} students, {len(courses)} courses, {len(enrollments)} enrollments, "
          f"{len(slots)} timetable slots, {len(ledger)} ledger rows, {len(GRANTS)} grants, {len(apps)} applications.")


def render_grant_policy() -> None:
    dept_names = {code: name for _, code, name, _ in DEPARTMENTS}
    lines = [
        "# Research Grant Policy 2026-27",
        "",
        f"**{UNIVERSITY}** · Office of Research & Innovation · Effective from 1 October 2026",
        "",
        "## 1. Purpose and scope",
        "This policy defines who may apply for each undergraduate research grant offered at DVUT, the documents "
        "required, and how applications are selected. It applies to all undergraduate students of the "
        "Departments of Computer Science & Engineering (CS), Information Technology (IT) and Electronics & "
        "Telecommunication (ENTC).",
        "",
        "The award amount, number of awards, application window, deadline and current status (open, upcoming or "
        "closed) of every grant are maintained in the University Grants Register (live system) and are not "
        "repeated in this policy. Where this policy and the Register differ on a date or amount, the Register "
        "prevails.",
        "",
        "## 2. General eligibility rules (apply to every grant)",
        "1. Eligibility is assessed on the applicant's CGPA on the 10-point scale as recorded in the latest "
        "declared result. Students who state a 4-point GPA are assessed using the conversion in the Student "
        "Handbook, Section 4 (CGPA = 4-point GPA ÷ 0.4).",
        "2. First-year students do not yet have a CGPA and are not eligible for any grant in this policy.",
        "3. A student may hold at most **two** research grants at the same time.",
        "4. A student must apply before the deadline shown in the Grants Register; late applications are not "
        "considered.",
        "5. Applications are accepted only while the grant's status in the Register is **open**. Grants marked "
        "**closed** have stopped accepting applications (deadline passed or all awards filled); grants marked "
        "**upcoming** will accept applications from their opening date.",
        "6. Research grants may be held together with a merit fee concession (see Scholarship & Fee Concession "
        "Policy).",
        "",
        "## 3. Grant-specific eligibility",
        "",
    ]
    for g in GRANTS:
        years = ", ".join(f"Year {y}" for y in g["years"])
        depts = ", ".join(f"{dept_names[d]} ({d})" for d in g["depts"])
        lines += [
            f"### {g['code']} — {g['title']}",
            f"**Grant code:** {g['code']}  ",
            f"**Sponsor:** {g['sponsor']}",
            "",
            f"**Focus:** {g['focus']}",
            "",
            "**Eligibility:**",
            f"- Minimum CGPA: {g['min_cgpa']:.1f} (10-point scale)",
            f"- Eligible year(s) of study: {years}",
            f"- Eligible department(s): {depts}",
        ]
        if g["extra"]:
            lines.append(f"- Additional condition: {g['extra']}")
        lines += ["", "**Required documents:**"] + [f"- {d}" for d in g["docs"]] + [""]
    lines += [
        "## 4. Selection process",
        "1. The Office of Research & Innovation screens applications for eligibility within 10 working days of "
        "the deadline.",
        "2. Eligible applications are reviewed by a panel of three faculty members, scoring originality (40%), "
        "feasibility (30%), impact (20%) and academic record (10%).",
        "3. Results are published in the Grants Register. Awarded students must sign the grant agreement within "
        "7 days.",
        "",
        "## 5. Disbursement and reporting",
        "- 50% of the award is released on signing the agreement and 50% after an approved mid-term report.",
        "- Grant holders submit a final report and a poster at the Annual Research Day.",
        "- A student whose CGPA falls below the grant's minimum during the grant period receives no further "
        "instalments.",
        "",
        "## 6. Contact",
        "Office of Research & Innovation, Admin Building, Room 204 · research@dvut.edu.in",
        "",
    ]
    (DOCS_DIR / "02_research_grant_policy.md").write_text("\n".join(lines), encoding="utf-8")


def write_data_readme(students, courses, enrollments, ledger, fee, eligible, overdue_ids, apps) -> None:
    by_id = {s["student_id"]: s for s in students}
    led = {}
    for l in ledger:
        led.setdefault(l["student_id"], []).append(l)
    enr = {}
    for e in enrollments:
        enr.setdefault(e["student_id"], []).append(e)
    rng = random.Random(SEED + 1)
    qa = []

    # Q1 – the problem statement's own question
    profile = dict(dept="CS", year=3, cgpa=round(3.8 / POLICY["gpa4_factor"], 2))
    fake = dict(student_id="(anonymous)", dept="CS", year=3, cgpa=profile["cgpa"])
    elig = [g for g in GRANTS if eligible(fake, g, check_dues=False)]
    lines = []
    order = {"open": 0, "upcoming": 1, "closed": 2}
    for g in sorted(elig, key=lambda g: (order[g["status"]], g["deadline"])):
        cond = " — only if the student has no overdue fee dues" if g["extra"] else ""
        if g["status"] == "open":
            lines.append(f"  - **{g['code']} {g['title']}**: {inr(g['amount'])}, apply by "
                         f"**{fmt_date(g['deadline'])}** (open){cond}")
        elif g["status"] == "upcoming":
            lines.append(f"  - **{g['code']} {g['title']}**: {inr(g['amount'])}, not open yet — applications "
                         f"from {fmt_date(g['open'])}, deadline {fmt_date(g['deadline'])}")
        else:
            lines.append(f"  - {g['code']} {g['title']}: eligible but **closed** (all awards filled)")
    qa.append(dict(
        q="I am a third-year CS student with a 3.8 GPA; what research grants am I eligible for, and what is "
          "the deadline to apply?",
        a=f"A 3.8 GPA on the 4-point scale converts to CGPA {profile['cgpa']:.2f} (Handbook §4). Matching the "
          "Research Grant Policy rules (CGPA, year, department) against grant status, amounts and deadlines in the "
          "Grants Register:\n" + "\n".join(lines),
        needs="Docs: Handbook §4 (conversion), Grant Policy §3 (eligibility) · SQL: grants (status, amount, deadline)",
        facts=[x for g in elig if g["status"] in ("open", "upcoming")
               for x in (g["code"], inr(g["amount"]), fmt_date(g["deadline"]))],
    ))

    # Q2 – hall ticket eligibility (attendance rules + exam-fee ledger)
    cands = []
    for s in students:
        es = [e for e in enr.get(s["student_id"], []) if e["status"] == "enrolled"]
        lows = [e for e in es if e["attendance_pct"] < POLICY["attendance_min"]]
        exam_paid = any(l["component"] == "exam" and l["status"] == "paid" for l in led[s["student_id"]])
        if len(lows) == 1 and POLICY["attendance_condonable_min"] <= lows[0]["attendance_pct"] and exam_paid:
            cands.append((s, lows[0]))
    s, low = cands[0]
    qa.append(dict(
        q=f"Can student {s['student_id']} get the hall ticket for all end-semester exams this term?",
        a=f"Not automatically. {s['full_name']} has paid the exam fee in full and meets the 75% attendance rule in "
          f"every course except **{low['course']['course_code']} {low['course']['title']}** "
          f"({low['attendance_pct']}%). Because that is between 65% and 75%, attendance can be condoned with a "
          f"medical certificate and a condonation fee of {inr(POLICY['condonation_fee'])} (Examination & "
          "Attendance Rules §3); otherwise the student is detained from that course's exam only.",
        needs="Docs: Exam & Attendance Rules §2–§5 · SQL: enrollments.attendance_pct, fee_ledger (exam)",
        facts=[low["course"]["course_code"], f"{low['attendance_pct']}%", inr(POLICY["condonation_fee"])],
    ))

    # Q3 – late fine as of a fixed date
    as_of = date(2026, 11, 30)
    cands = [s for s in students
             if sum(1 for l in led[s["student_id"]] if l["status"] == "overdue") >= 2]
    s = cands[0]
    parts, total, fines = [], 0, []
    for l in sorted(led[s["student_id"]], key=lambda l: l["due_date"]):
        if l["status"] != "overdue":
            continue
        days = (as_of - l["due_date"]).days
        if l["component"] == "exam":
            fine = POLICY["exam_late_fee_flat"]
            how = f"flat exam late fee {inr(fine)}"
        else:
            fine = min(days * POLICY["late_fine_per_day"], POLICY["late_fine_cap"])
            how = f"{days} days × ₹100 = {inr(days * 100)}" + (f", capped at {inr(POLICY['late_fine_cap'])}"
                                                               if days * 100 > POLICY["late_fine_cap"] else "")
        total += fine
        fines.append(inr(fine))
        parts.append(f"  - {l['component']}: balance {inr(l['amount_due'] - l['amount_paid'])}, due "
                     f"{fmt_date(l['due_date'])} → {how} → **{inr(fine)}**")
    qa.append(dict(
        q=f"As of 30 November 2026, how much late fine has student {s['student_id']} accumulated on overdue fees?",
        a=f"{s['full_name']} has these overdue items (Fee Payment & Refund Policy §4: ₹100 per day per component, "
          f"capped at ₹5,000; flat ₹500 for the exam fee):\n" + "\n".join(parts) + f"\n  - **Total late fine: {inr(total)}**",
        needs="Docs: Fee Policy §4 · SQL: fee_ledger (status, due_date, balances)",
        facts=sorted(set(fines)) + [inr(total)],
    ))

    # Q4 – remaining credits (registration policy + enrollments + CGPA)
    cands = []
    for s in students:
        if s["year"] >= 3 and s["cgpa"] and s["cgpa"] >= POLICY["overload_cgpa"]:
            cr = sum(e["course"]["credits"] for e in enr[s["student_id"]] if e["status"] == "enrolled")
            cands.append((s, cr))
    cands.sort(key=lambda t: (-t[1], t[0]["student_id"]))
    s, cr = cands[0]
    qa.append(dict(
        q=f"Student {s['student_id']} wants to add one more 3-credit elective this term. Is that allowed?",
        a=f"Not this term. On credits alone it would be allowed: {s['full_name']} (CGPA {s['cgpa']:.2f}) is "
          f"registered for **{cr} credits** in {TERM}, and with CGPA ≥ 8.5 the limit is "
          f"{POLICY['credits_max_overload']} credits with HoD approval, so {cr} + 3 = {cr + 3} fits "
          f"(Course Registration & Credit Policy §3). But the add/drop period for {TERM} ended on "
          f"**{fmt_date(ADD_DROP_DEADLINE)}** (§4), so no course can be added now; the student can plan it for "
          "the next term.",
        needs="Docs: Course Registration Policy §3–§4 · SQL: students.cgpa, enrollments + courses.credits",
        facts=[str(cr), str(POLICY["credits_max_overload"]), fmt_date(ADD_DROP_DEADLINE)],
    ))

    # Q5 – merit concession (scholarship policy + CGPA + dues + fee structure)
    cands = [s for s in students if s["cgpa"] and 8.5 <= s["cgpa"] < 9.0 and s["student_id"] not in overdue_ids
             and s["year"] in (2, 3)]
    s = cands[0]
    pct = next(p for c, p in POLICY["merit_slabs"] if s["cgpa"] >= c)
    tuition = fee[(s["dept"], s["year"], "tuition")]
    conc = tuition * pct / 100
    qa.append(dict(
        q=f"Is student {s['student_id']} eligible for a merit fee concession, and how much would it be?",
        a=f"Yes. {s['full_name']} has CGPA {s['cgpa']:.2f} (slab 8.50–8.99 → {pct}%) and no overdue fees "
          f"(Scholarship & Fee Concession Policy §2–§3). The concession is {pct}% of the annual tuition of "
          f"{inr(tuition)} in the {ACADEMIC_YEAR} fee structure for {s['dept']} Year {s['year']} = "
          f"**{inr(conc)}**, applied to next year's tuition.",
        needs="Docs: Scholarship Policy §2–§3 · SQL: students.cgpa, fee_ledger (overdue check), fee_structure",
        facts=[f"{pct}%", inr(tuition), inr(conc)],
    ))

    # Q6 – hostel renewal (hostel rules + CGPA + dues)
    cands = [s for s in students if s["is_hosteller"] and s["year"] in (2, 3) and s["cgpa"]
             and s["cgpa"] >= POLICY["hostel_renewal_cgpa"] and s["student_id"] in overdue_ids]
    s = cands[0]
    od = [l for l in led[s["student_id"]] if l["status"] == "overdue"]
    od_txt = ", ".join(f"{l['component']} ({inr(l['amount_due'] - l['amount_paid'])} due since "
                       f"{fmt_date(l['due_date'])})" for l in od)
    qa.append(dict(
        q=f"Can student {s['student_id']} renew their hostel room for next year?",
        a=f"Not at present. {s['full_name']} meets the academic condition (CGPA {s['cgpa']:.2f} ≥ 6.0) but has "
          f"overdue fees: {od_txt}. Hostel Rules §3 require no overdue dues for renewal, so the room can be "
          "renewed once these are cleared before the renewal window closes.",
        needs="Docs: Hostel Rules §3 · SQL: students (cgpa, is_hosteller), fee_ledger (overdue)",
        facts=[inr(l["amount_due"] - l["amount_paid"]) for l in od] + [f"{s['cgpa']:.2f}"],
    ))

    n_over = sum(1 for l in ledger if l["status"] == "overdue")
    out = [
        "# UniRAG dataset",
        "",
        f"Synthetic, fictional data for **{UNIVERSITY}**. Generated by `python scripts/generate_data.py` "
        f"(fixed seed {SEED}). Ledger and grant statuses are computed as of **{fmt_date(SNAPSHOT)}**.",
        "",
        "## Contents",
        "| Source | Size |",
        "|---|---|",
        f"| students | {len(students)} (CS 72, IT 64, ENTC 64; Years 1–4; first-years have no CGPA yet) |",
        f"| courses | {len(courses)} (core, lab, department and open electives, {TERM}) |",
        f"| enrollments | {len(enrollments)} with attendance % |",
        f"| fee_ledger | {len(ledger)} rows ({n_over} overdue) |",
        f"| grants | {len(GRANTS)} (open / upcoming / closed) · grant_applications {len(apps)} |",
        "| policy documents | 7 Markdown files in `data/docs/` |",
        "",
        "**By design:**",
        "- Eligibility rules for grants exist **only** in `02_research_grant_policy.md`.",
        "- Amounts, deadlines and statuses exist **only** in the `grants` table.",
        "- Both are linked by `grant_code`.",
        "",
        "## Hybrid demo & evaluation questions",
        "Each question has a single correct answer that needs **both** the database and the policy documents.",
        "",
    ]
    for i, item in enumerate(qa, start=1):
        out += [f"### H{i}. {item['q']}", "", f"**Expected answer:** {item['a']}", "",
                f"*Sources needed:* {item['needs']}", ""]
    (ROOT / "data" / "README.md").write_text("\n".join(out), encoding="utf-8")
    import json
    (ROOT / "data" / "hybrid_questions.json").write_text(json.dumps(
        [{"id": f"H{i}", "question": it["q"], "expected": it["a"], "sources": it["needs"], "key_facts": it["facts"]}
         for i, it in enumerate(qa, start=1)], indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
