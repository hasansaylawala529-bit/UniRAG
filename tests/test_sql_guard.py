import pytest

from unirag.retrieval.sql_guard import GuardError, PrivacyError, guard_sql

# ---------------------------------------------------------------- allowed
def test_simple_select_gets_limit():
    r = guard_sql("SELECT grant_code, amount FROM grants WHERE status = 'open'")
    assert "LIMIT 200" in r.sql
    assert r.tables == ["grants"]


def test_join_and_aggregate_allowed():
    r = guard_sql("""SELECT d.code, avg(s.cgpa) FROM students s JOIN departments d ON s.dept_id = d.dept_id
                     GROUP BY d.code""")
    assert r.tables == ["departments", "students"]


def test_cte_allowed_and_cte_name_not_treated_as_table():
    r = guard_sql("""WITH od AS (SELECT student_id FROM fee_ledger WHERE status = 'overdue')
                     SELECT count(*) FROM od""")
    assert r.tables == ["fee_ledger"]


def test_union_allowed():
    r = guard_sql("SELECT grant_code FROM grants WHERE status='open' UNION SELECT grant_code FROM grants WHERE status='upcoming'")
    assert "LIMIT" in r.sql


def test_trailing_semicolon_ok():
    guard_sql("SELECT count(*) FROM students;")


def test_large_limit_is_reduced_small_limit_kept():
    assert "LIMIT 200" in guard_sql("SELECT * FROM grants LIMIT 100000").sql
    assert "LIMIT 5" in guard_sql("SELECT * FROM grants LIMIT 5").sql


# ---------------------------------------------------------------- blocked statements
@pytest.mark.parametrize("sql", [
    "DELETE FROM grants",
    "UPDATE fee_ledger SET amount_paid = amount_due",
    "INSERT INTO grants (grant_id) VALUES (99)",
    "DROP TABLE students",
    "TRUNCATE fee_ledger",
    "CREATE TABLE x (a int)",
    "ALTER TABLE students ADD COLUMN x int",
    "GRANT ALL ON students TO public",
    "COPY students TO '/tmp/out.csv'",
    "SET statement_timeout = 0",
])
def test_non_select_blocked(sql):
    with pytest.raises(GuardError):
        guard_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM students; DROP TABLE students",
    "SELECT 1 FROM students; SELECT 2 FROM grants",
])
def test_multiple_statements_blocked(sql):
    with pytest.raises(GuardError, match="Exactly one statement"):
        guard_sql(sql)


def test_select_into_blocked():
    with pytest.raises(GuardError):
        guard_sql("SELECT * INTO stolen FROM students")


def test_for_update_blocked():
    with pytest.raises(GuardError):
        guard_sql("SELECT * FROM fee_ledger FOR UPDATE")


def test_write_inside_cte_blocked():
    with pytest.raises(GuardError):
        guard_sql("WITH d AS (DELETE FROM grants RETURNING *) SELECT * FROM d")


@pytest.mark.parametrize("sql", [
    "SELECT pg_sleep(10) FROM students",
    "SELECT pg_read_file('/etc/passwd') FROM students",
    "SELECT set_config('statement_timeout','0',false) FROM students",
])
def test_dangerous_functions_blocked(sql):
    with pytest.raises(GuardError, match="Forbidden function"):
        guard_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM pg_shadow",
    "SELECT * FROM doc_chunks",
    "SELECT * FROM salaries",
])
def test_unknown_or_system_tables_blocked(sql):
    with pytest.raises(GuardError, match="Table not allowed"):
        guard_sql(sql)


def test_no_table_blocked():
    with pytest.raises(GuardError):
        guard_sql("SELECT 1")


def test_unparseable_blocked():
    with pytest.raises(GuardError):
        guard_sql("SELEC * FORM students")


# ---------------------------------------------------------------- student scoping
def test_personal_tables_scoped_to_student():
    r = guard_sql("SELECT component, amount_due - amount_paid AS balance FROM fee_ledger WHERE status = 'overdue'",
                  student_id="S2024CS007")
    assert "student_id = 'S2024CS007'" in r.sql
    assert any("Scoped fee_ledger" in n for n in r.notes)


def test_student_asking_for_another_student_is_refused():
    with pytest.raises(PrivacyError):
        guard_sql("SELECT * FROM fee_ledger f WHERE f.student_id = 'S2023IT001'", student_id="S2024CS007")


def test_scoping_holds_whatever_filter_the_model_wrote():
    r = guard_sql("SELECT * FROM fee_ledger f WHERE f.amount_due > 0", student_id="S2024CS007")
    assert "(SELECT * FROM fee_ledger WHERE student_id = 'S2024CS007') AS f" in r.sql


def test_scoping_keeps_join_aliases():
    r = guard_sql("""SELECT c.title, e.attendance_pct FROM enrollments e JOIN courses c ON c.course_id = e.course_id""",
                  student_id="S2024CS007")
    assert "AS e" in r.sql and "JOIN courses AS c" in r.sql


def test_non_personal_tables_not_scoped():
    r = guard_sql("SELECT * FROM grants", student_id="S2024CS007")
    assert "S2024CS007" not in r.sql


def test_invalid_student_id_rejected():
    with pytest.raises(GuardError):
        guard_sql("SELECT * FROM students", student_id="x' OR '1'='1")



# ---------------------------------------------------------------- access policy (roles)
@pytest.mark.parametrize("sql", [
    "SELECT * FROM students",
    "SELECT full_name, cgpa FROM students ORDER BY cgpa DESC",
    "SELECT s.full_name AS who FROM students s JOIN departments d ON s.dept_id = d.dept_id",
    "SELECT student_id, amount_due - amount_paid FROM fee_ledger WHERE status = 'overdue'",
    "SELECT grant_code FROM grants UNION SELECT full_name FROM students",
])
def test_anonymous_cannot_read_individual_records(sql):
    with pytest.raises(PrivacyError):
        guard_sql(sql, role="anonymous")


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM students",
    "SELECT d.code, round(avg(s.cgpa), 2) FROM students s JOIN departments d ON s.dept_id = d.dept_id GROUP BY d.code",
    "SELECT status, count(*), sum(amount_due - amount_paid) FROM fee_ledger GROUP BY status",
])
def test_anonymous_can_read_aggregates(sql):
    assert "Anonymous access" in "; ".join(guard_sql(sql, role="anonymous").notes)


def test_anonymous_cannot_target_one_student_even_with_aggregates():
    with pytest.raises(PrivacyError):
        guard_sql("SELECT sum(amount_due) FROM fee_ledger WHERE student_id = 'S2026CS012'", role="anonymous")


@pytest.mark.parametrize("role,student", [("anonymous", None), ("student", "S2024CS007"), ("staff", None)])
def test_emails_never_returned(role, student):
    with pytest.raises(PrivacyError, match="Email"):
        guard_sql("SELECT full_name, email FROM students", role=role, student_id=student)


def test_student_aggregates_are_not_scoped_to_self():
    r = guard_sql("SELECT count(*) FROM students", student_id="S2024CS007")
    assert "S2024CS007" not in r.sql


def test_staff_can_read_named_student():
    r = guard_sql("SELECT component, amount_due FROM fee_ledger WHERE student_id = 'S2026CS012'", role="staff")
    assert "Staff access" in r.notes[0]
