from unirag.planner.rules import evaluate_rules
from unirag.profile import Profile

DOC = "[Doc: Research Grant Policy 2026-27 §3 Grant-specific eligibility]"


def R(subject, field, op, value):
    return {"subject": subject, "field": field, "operator": op, "value": value, "effect": "eligible", "citation": DOC}


RULES = [
    R("GR-AI-01", "cgpa", ">=", "8.5"), R("GR-AI-01", "year_of_study", "in", "3,4"),
    R("GR-AI-01", "department", "in", "Computer Science & Engineering (CS), Information Technology (IT)"),
    R("GR-QC-04", "cgpa", ">=", "9.5"), R("GR-QC-04", "year_of_study", "in", "4"),
    R("GR-DS-05", "cgpa", ">=", "7.5"), R("GR-DS-05", "department", "in", "IT"),
    R("GR-CY-03", "cgpa", ">=", "8.0"), R("GR-CY-03", "has_overdue_dues", "==", "false"),
]


def test_h1_profile_gets_correct_eligibility():
    p = Profile(department="CS", year_of_study=3, cgpa=9.5)
    ev = evaluate_rules(RULES, p)
    assert ev.passing == ["GR-AI-01", "GR-CY-03"]          # CY-03 passes on CGPA; dues unknown, not failed
    fails = {s.subject: s.reasons for s in ev.failing}
    assert set(fails) == {"GR-QC-04", "GR-DS-05"}
    assert "year_of_study 3 not in [4]" in fails["GR-QC-04"][0]
    text = ev.constraints_text(p)
    assert "grant_code IN ('GR-AI-01', 'GR-CY-03')" in text


def test_overdue_dues_fail_when_known():
    p = Profile(department="CS", year_of_study=3, cgpa=9.5, has_overdue_dues=True)
    ev = evaluate_rules(RULES, p)
    assert "GR-CY-03" in {s.subject for s in ev.failing}


def test_non_profile_fields_are_left_for_database():
    rules = [R("late_fine", "days_overdue", ">", "0")]
    ev = evaluate_rules(rules, Profile(student_id="S2026CS012"))
    assert ev.subjects[0].outcome == "unknown"
