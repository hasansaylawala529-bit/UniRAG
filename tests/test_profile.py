from unirag.profile import extract_entities


def test_record_lookups_need_login():
    for q in ["What are my overdue fees?", "what is my cgpa", "Show my attendance", "Which of my courses are below 75%?"]:
        assert extract_entities(q).personal_records, q


def test_stated_values_are_not_record_lookups():
    for q in ["What happens if my attendance is 70 percent?", "My CGPA is 8.2, which grants can I get?",
              "I am a third-year CS student with a 3.8 GPA; what grants am I eligible for?"]:
        assert not extract_entities(q).personal_records, q


def test_entities():
    e = extract_entities("I am a third-year CS student with a 3.8 GPA")
    assert (e.department, e.year_of_study, e.gpa_4, e.cgpa) == ("CS", 3, 3.8, None)
    e = extract_entities("final-year ENTC, CGPA 8.2, as of 30 November 2026")
    assert (e.department, e.year_of_study, e.cgpa, str(e.reference_date)) == ("ENTC", 4, 8.2, "2026-11-30")
