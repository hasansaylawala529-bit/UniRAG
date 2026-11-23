"""Who is asking, and about whom: deterministic entity extraction and student profiles (no LLM calls).

A profile combines (a) facts stated in the question ("third-year CS student with a 3.8 GPA") and
(b) the logged-in or mentioned student's record from the database. Database facts win over stated
ones for a real student; stated facts are used for hypothetical questions.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from .db import connect, jsonable

GPA4_FACTOR = 0.4   # Student Handbook §4: 4-point GPA = CGPA x 0.4

YEAR_WORDS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
              "final": 4, "last": 4}
STUDENT_ID_RE = re.compile(r"\bS\d{4}(?:CS|IT|ENTC)\d{3}\b", re.I)
GRANT_CODE_RE = re.compile(r"\bGR-[A-Z0-9]+-\d{2}\b", re.I)
FIRST_PERSON_RE = re.compile(r"\b(my|me|mine|i am|i'm|am i|can i|do i|i have|i owe|have i|should i)\b", re.I)
# questions about the asker's OWN stored records (not "am I eligible", which can use stated facts)
PERSONAL_RECORD_RE = re.compile(
    r"\b(my|mine)\s+(\w+\s+)?(fee|fees|dues|balance|ledger|payment|payments|cgpa|gpa|grade|grades|"
    r"attendance|course|courses|enrol\w*|timetable|schedule|credits|hostel|application|applications|result|"
    r"results|record|records|profile)\b", re.I)
# requests to change data: refused before any model call (the assistant is read-only)
WRITE_INTENT_RE = re.compile(
    r"\b(delete|truncate|insert)\b|\bdrop\s+(the|table|all|every|my|his|her|their)\b"
    r"|\bupdate\s+(the|my|his|her|their|all|\w+'s)\b"
    r"|\bmark\b.{0,40}\bas\s+(paid|cleared|present|passed)\b"
    r"|\bset\s+(my|the|his|her|their|\w+'s)\s+[\w\s]{0,20}\bto\b"
    r"|\b(change|modify|edit|alter|increase|raise|waive|erase)\s+(my|the|his|her|their|all|\w+'s)\s+"
    r"[\w\s]{0,20}(cgpa|gpa|grades?|fees?|dues|fine|attendance|records?|marks?|ledger|balance|status)\b", re.I)
# "how do I / can I ..." asks about a procedure the student performs, not a command to the assistant
SELF_PROCEDURE_RE = re.compile(r"^\s*(how\s+(do|can|should)\s+i|can\s+i|may\s+i|what\s+happens|is\s+it\s+possible"
                               r"\s+(for\s+me\s+)?to|when\s+can\s+i)\b", re.I)
# "my attendance is 70 percent" states the value: a hypothetical policy question, not a record lookup
STATED_VALUE_RE = re.compile(r"\b(my|mine)\s+(\w+\s+){0,2}(is|was|are|of|=|at)\s+\d", re.I)
DATE_IN_TEXT_RE = re.compile(r"\b(?:as of|on|by)\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2})", re.I)


@dataclass
class Profile:
    student_id: str | None = None
    full_name: str | None = None
    department: str | None = None
    year_of_study: int | None = None
    cgpa: float | None = None
    gpa_4: float | None = None
    is_hosteller: bool | None = None
    has_overdue_dues: bool | None = None
    credits_registered: int | None = None
    source: dict = field(default_factory=dict)       # field -> "question" | "database" | "derived"

    def known(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None and k != "source"}

    def summary(self) -> str:
        k = self.known()
        if not k:
            return "No student profile (general question)."
        parts = [f"{key}={val}" + (f" ({self.source.get(key)})" if self.source.get(key) else "")
                 for key, val in k.items()]
        return "; ".join(parts)


@dataclass
class QuestionEntities:
    department: str | None = None
    year_of_study: int | None = None
    cgpa: float | None = None
    gpa_4: float | None = None
    student_id: str | None = None
    grant_codes: list[str] = field(default_factory=list)
    reference_date: date | None = None
    first_person: bool = False
    personal_records: bool = False
    write_intent: bool = False


def extract_entities(question: str) -> QuestionEntities:
    q = question.strip()
    ql = q.lower()
    e = QuestionEntities()
    # "IT" only in capitals, so the English word "it" never matches
    if re.search(r"\b(ENTC|E&TC|E\s*&\s*TC)\b", q, re.I) or re.search(r"electronics\s*(&|and)\s*telecom", ql):
        e.department = "ENTC"
    elif re.search(r"\bCSE?\b", q) or re.search(r"computer\s+science|comp\s*sci", ql):
        e.department = "CS"
    elif re.search(r"\bIT\b", q) or "information technology" in ql:
        e.department = "IT"
    m = re.search(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|final|last)[\s-]*year\b", ql)
    if m:
        e.year_of_study = YEAR_WORDS[m.group(1)]
    else:
        m = re.search(r"\byear\s*([1-4])\b", ql)
        if m:
            e.year_of_study = int(m.group(1))
    m = re.search(r"\bcgpa\s*(?:of|is|=|:)?\s*(\d{1,2}(?:\.\d{1,2})?)\b|\b(\d{1,2}(?:\.\d{1,2})?)\s*cgpa\b", ql)
    if m:
        e.cgpa = float(m.group(1) or m.group(2))
    else:
        m = re.search(r"\b(\d(?:\.\d{1,2})?)\s*(?:/\s*4(?:\.0)?\s*)?gpa\b|\bgpa\s*(?:of|is|=|:)?\s*(\d(?:\.\d{1,2})?)\b", ql)
        if m:
            val = float(m.group(1) or m.group(2))
            if val <= 4.0:
                e.gpa_4 = val
            else:
                e.cgpa = val
    m = STUDENT_ID_RE.search(q)
    if m:
        e.student_id = m.group(0).upper()
    e.grant_codes = sorted({c.upper() for c in GRANT_CODE_RE.findall(q)})
    m = DATE_IN_TEXT_RE.search(q)
    if m:
        e.reference_date = _parse_date(m.group(1))
    e.first_person = bool(FIRST_PERSON_RE.search(q))
    e.personal_records = bool(PERSONAL_RECORD_RE.search(q)) and not STATED_VALUE_RE.search(q)
    e.write_intent = bool(WRITE_INTENT_RE.search(q)) and not SELF_PROCEDURE_RE.search(q)
    return e


def _parse_date(text: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def load_student_profile(student_id: str) -> Profile | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT s.student_id, s.full_name, d.code, s.year_of_study, s.cgpa, s.gpa_4, s.is_hosteller,
                   EXISTS (SELECT 1 FROM fee_ledger f WHERE f.student_id = s.student_id AND f.status = 'overdue'),
                   COALESCE((SELECT sum(c.credits) FROM enrollments e JOIN courses c USING (course_id)
                             WHERE e.student_id = s.student_id AND e.status = 'enrolled'), 0)
            FROM students s JOIN departments d USING (dept_id) WHERE s.student_id = %s""", (student_id,))
        r = cur.fetchone()
    if not r:
        return None
    sid, name, dept, year, cgpa, gpa4, hostel, overdue, credits = (jsonable(v) for v in r)
    p = Profile(sid, name, dept, year, cgpa, gpa4, hostel, overdue, int(credits))
    p.source = {k: "database" for k in p.known()}
    return p


def build_profile(question: str, logged_in_student: str | None = None) -> tuple[Profile, QuestionEntities]:
    """Profile for this question. Logged-in student > student named in the question > stated facts."""
    ents = extract_entities(question)
    sid = logged_in_student or ents.student_id
    profile = (load_student_profile(sid) if sid else None) or Profile()
    stated = {"department": ents.department, "year_of_study": ents.year_of_study,
              "cgpa": ents.cgpa, "gpa_4": ents.gpa_4}
    for key, val in stated.items():
        if val is not None and getattr(profile, key) is None:
            setattr(profile, key, val)
            profile.source[key] = "question"
    if profile.cgpa is None and profile.gpa_4 is not None:
        profile.cgpa = round(profile.gpa_4 / GPA4_FACTOR, 2)
        profile.source["cgpa"] = "derived: 4-point GPA ÷ 0.4 (Handbook §4)"
    if profile.gpa_4 is None and profile.cgpa is not None:
        profile.gpa_4 = round(profile.cgpa * GPA4_FACTOR, 2)
        profile.source["gpa_4"] = "derived: CGPA × 0.4 (Handbook §4)"
    return profile, ents


def merge_llm_entities(profile: Profile, llm_entities: dict) -> Profile:
    """Fill gaps from the understand call's entities (never overrides database or regex facts)."""
    mapping = {"department": "department", "year_of_study": "year_of_study", "cgpa": "cgpa", "gpa_4": "gpa_4"}
    for src, dst in mapping.items():
        val = llm_entities.get(src)
        if val in (None, "", 0, 0.0):
            continue
        if getattr(profile, dst) is None:
            if dst == "department":
                val = str(val).upper().replace("E&TC", "ENTC")
                if val not in ("CS", "IT", "ENTC"):
                    continue
            setattr(profile, dst, val)
            profile.source[dst] = "question (LLM-extracted)"
    if profile.cgpa is None and profile.gpa_4:
        profile.cgpa = round(float(profile.gpa_4) / GPA4_FACTOR, 2)
        profile.source["cgpa"] = "derived: 4-point GPA ÷ 0.4 (Handbook §4)"
    return profile
