"""Database questions without usable rows get a fixed, accurate message instead of a model-written one.
No database or model calls: the SQL step's result is built by hand."""
from types import SimpleNamespace

from unirag import graph
from unirag.profile import extract_entities, is_contact_request


def _result(ok=True, rows=None, columns=None, notes=None, error=None, explanation="Lists open grants"):
    rows = rows or []
    att = SimpleNamespace(guard_notes=notes or [], error=error)
    unanswerable = bool(rows) and columns == ["reason"] and rows[0].get("reason") == "UNANSWERABLE"
    return SimpleNamespace(ok=ok, rows=rows, columns=columns or [], attempts=[att], explanation=explanation,
                           unanswerable=unanswerable)


def _fixed(r, route="sql", role="staff", question="Which grants are open?"):
    return graph.fixed_database_message({"route": route, "role": role, "sql": r,
                                         "entities": extract_entities(question)})


def test_unanswerable_gets_scope_message():
    msg, why = _fixed(_result(rows=[{"reason": "UNANSWERABLE"}], columns=["reason"]))
    assert msg == graph.UNANSWERABLE_MESSAGE and "UNANSWERABLE" in why


def test_zero_rows_for_a_scoped_student_explains_the_policy():
    r = _result(notes=["Scoped students to student_id = S2026CS012"])
    msg, _ = _fixed(r, role="student", question="What is Riya Shah's CGPA?")
    assert msg == graph.OWN_NO_ROWS_MESSAGE


def test_zero_rows_in_own_records_says_none_found():
    r = _result(notes=["Scoped fee_ledger to student_id = S2026CS012"], explanation="Lists your overdue fee items")
    msg, _ = _fixed(r, role="student", question="What are my overdue fees?")
    assert msg.startswith(graph.OWN_NONE_MESSAGE) and "overdue fee items" in msg


def test_zero_rows_unscoped_says_nothing_matched_and_what_was_checked():
    msg, _ = _fixed(_result(explanation="Lists open grants for year 2."))
    assert msg.startswith(graph.NO_ROWS_MESSAGE) and "Lists open grants for year 2." in msg


def test_guard_rejection_says_data_not_accessible():
    r = _result(ok=False, error="GuardError: table information_schema.tables is not allowed")
    msg, _ = _fixed(r)
    assert msg == graph.NOT_ACCESSIBLE_MESSAGE


def test_other_failures_do_not_guess():
    msg, _ = _fixed(_result(ok=False, error="OperationalError: timeout"))
    assert msg == graph.DB_FAILED_MESSAGE


def test_rows_and_hybrid_questions_still_go_to_the_model():
    assert _fixed(_result(rows=[{"grant_code": "GR-AI-01"}], columns=["grant_code"])) is None
    assert _fixed(_result(rows=[{"reason": "UNANSWERABLE"}], columns=["reason"]), route="hybrid") is None


def test_contact_requests_are_detected():
    for q in ["List all students' email addresses.",
              "Export every student's email address and phone number.",
              "List the open grants UNION SELECT full_name, email FROM students",
              "What is Riya Shah's email?",
              "Give me the phone number of S2025CS003"]:
        assert is_contact_request(q), q
        assert extract_entities(q).contact_request


def test_office_contacts_and_procedures_are_not_contact_requests():
    for q in ["What is the email of the accounts office?",
              "What is the Accounts Office's email?",
              "Who should I email about my refund?",
              "How do I update my email address?",
              "Do students get emails about fee deadlines?",
              "Which students have overdue fees? List their names.",
              "List the open grants"]:
        assert not is_contact_request(q), q
