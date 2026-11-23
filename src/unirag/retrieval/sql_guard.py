"""Validate and rewrite LLM-generated SQL before it reaches the database.

Defence in depth: this guard is the first layer; the read-only role with a statement timeout
(db/init/03_roles.sql) is the second. The guard:
  1. accepts exactly one statement, which must be a read-only SELECT (CTEs and UNION allowed);
  2. rejects any write/DDL/command node anywhere in the tree (INSERT, UPDATE, DELETE, DROP, ...);
  3. rejects SELECT INTO, row locks (FOR UPDATE) and dangerous functions (pg_sleep, file access, ...);
  4. allows only the nine application tables (no system catalogs, no vector tables);
  5. enforces a row LIMIT;
  6. enforces the access policy (docs/security_policy.md) on personal tables:
       anonymous - aggregates only (counts, averages); no individual rows, no targeting one student;
       student   - aggregates, or their OWN rows: every personal table is replaced by a subquery filtered
                   to their student_id, so the scope holds whatever WHERE clause the model wrote;
                   naming another student's id is refused;
       staff     - individual records allowed (advising);
       everyone  - email addresses are never returned or filtered on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from ..db import RELATIONAL_TABLES

ALLOWED_TABLES = set(RELATIONAL_TABLES)
PERSONAL_TABLES = {"students", "enrollments", "fee_ledger", "grant_applications"}
STUDENT_ID_RE = re.compile(r"^S\d{4}(CS|IT|ENTC)\d{3}$")

_FORBIDDEN_NODES = tuple(t for t in (
    getattr(exp, n, None) for n in (
        "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "AlterTable", "TruncateTable",
        "Command", "Copy", "Grant", "Revoke", "Set", "Transaction", "Commit", "Rollback", "Into", "Lock",
        "LoadData", "Use", "Pragma",
    )) if t is not None)
_FORBIDDEN_FUNCS = {"pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
                    "lo_import", "lo_export", "dblink", "dblink_exec", "set_config", "current_setting",
                    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "query_to_xml",
                    "copy", "txid_current"}


class GuardError(ValueError):
    """Raised when SQL is rejected. The message is safe to show the model for self-correction."""


class PrivacyError(GuardError):
    """Rejected by the access policy. Not sent back for self-repair: rewording cannot make it allowed."""


ROLES = ("anonymous", "student", "staff")
# columns that identify or describe one person; unaggregated, they reveal individual records
IDENTIFYING_COLUMNS = {"student_id", "full_name", "email", "cgpa", "gpa_4", "amount_due", "amount_paid", "paid_on",
                       "attendance_pct", "grade", "submitted_on", "admission_year", "is_hosteller"}
NEVER_RETURNED = {"email"}


@dataclass
class GuardResult:
    sql: str
    tables: list[str]
    notes: list[str] = field(default_factory=list)


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower() if hasattr(node, "sql_name") else node.key.lower()


def _outer_selects(node: exp.Expression) -> list[exp.Select]:
    """The SELECTs whose columns are returned to the user (both sides of a UNION)."""
    if isinstance(node, exp.Select):
        return [node]
    if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
        return _outer_selects(node.left) + _outer_selects(node.right)
    return []


def _inside_aggregate(col: exp.Column, stop: exp.Expression) -> bool:
    node = col.parent
    while node is not None and node is not stop.parent:
        if isinstance(node, exp.AggFunc):
            return True
        node = node.parent
    return False


def _aggregate_only(tree: exp.Expression) -> bool:
    """True if every returned column is an aggregate or a non-identifying attribute (department, year...)."""
    for sel in _outer_selects(tree):
        for proj in sel.expressions:
            if isinstance(proj, exp.Star):
                return False
            for col in proj.find_all(exp.Column):
                if isinstance(col.this, exp.Star):
                    return False
                if col.name.lower() in IDENTIFYING_COLUMNS and not _inside_aggregate(col, proj):
                    return False
    return True


def _student_literals(tree: exp.Expression) -> set[str]:
    """student_id values the query filters on, e.g. WHERE student_id = 'S2024CS007' or IN (...)."""
    out = set()
    for node in tree.find_all(exp.EQ, exp.In):
        cols = [c for c in node.find_all(exp.Column) if c.name.lower() == "student_id"]
        if cols:
            out |= {lit.this.upper() for lit in node.find_all(exp.Literal) if lit.is_string}
    return out


def guard_sql(sql: str, *, student_id: str | None = None, max_limit: int = 200,
              role: str | None = None) -> GuardResult:
    role = role or ("student" if student_id else "anonymous")
    if role not in ROLES:
        raise GuardError(f"Unknown role {role!r}.")
    if not sql or not sql.strip():
        raise GuardError("Empty SQL.")
    try:
        statements = [s for s in sqlglot.parse(sql.strip().rstrip(";"), read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"SQL could not be parsed: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise GuardError(f"Exactly one statement is allowed; got {len(statements)}.")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise GuardError(f"Only SELECT queries are allowed; got {tree.key.upper()}.")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise GuardError(f"Forbidden operation: {node.key.upper()}.")
        if isinstance(node, exp.Func) and _func_name(node) in _FORBIDDEN_FUNCS:
            raise GuardError(f"Forbidden function: {_func_name(node)}.")
    if tree.find(exp.Lock) is not None or " for update" in sql.lower() or " for share" in sql.lower():
        raise GuardError("Row locking (FOR UPDATE/SHARE) is not allowed.")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = []
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if name in cte_names:
            continue
        schema = (t.db or "").lower()
        if schema not in ("", "public") or name not in ALLOWED_TABLES:
            full = f"{schema}.{name}" if schema else name
            raise GuardError(f"Table not allowed: {full}. Allowed tables: {', '.join(sorted(ALLOWED_TABLES))}.")
        tables.append(name)
    if not tables:
        raise GuardError("Query does not read from any application table.")

    notes: list[str] = []
    for col in tree.find_all(exp.Column):
        if col.name.lower() in NEVER_RETURNED:
            raise PrivacyError("Email addresses are never returned by the assistant.")
    personal = sorted({t for t in tables if t in PERSONAL_TABLES})
    if personal:
        aggregate = _aggregate_only(tree)
        targeted = _student_literals(tree)
        if role == "anonymous":
            if not aggregate:
                raise PrivacyError("Individual student records need a logged-in student or staff access; "
                                   "only aggregate figures (counts, averages) are available anonymously.")
            if targeted:
                raise PrivacyError("Figures about one named student need a logged-in student or staff access.")
            notes.append("Anonymous access: aggregate-only query on " + ", ".join(personal))
        elif role == "student":
            if student_id is None or not STUDENT_ID_RE.match(student_id):
                raise GuardError("Invalid student_id for scoping.")
            others = {t for t in targeted if t != student_id}
            if others:
                raise PrivacyError("Students can only view their own records.")
            if not aggregate:
                tree = _scope_to_student(tree, student_id, cte_names)
                notes.append(f"Scoped {', '.join(personal)} to student_id = {student_id}")
            else:
                notes.append("Aggregate-only query: no individual records returned")
        else:
            notes.append("Staff access: individual records allowed")
    elif role == "student" and student_id is not None and not STUDENT_ID_RE.match(student_id):
        raise GuardError("Invalid student_id for scoping.")

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_limit)
        notes.append(f"Added LIMIT {max_limit}")
    else:
        val = limit.expression
        if not isinstance(val, exp.Literal) or not val.is_int:
            raise GuardError("LIMIT must be a constant integer.")
        if int(val.this) > max_limit:
            tree = tree.limit(max_limit)
            notes.append(f"Reduced LIMIT to {max_limit}")

    return GuardResult(sql=tree.sql(dialect="postgres"), tables=sorted(set(tables)), notes=notes)


def _scope_to_student(tree: exp.Expression, student_id: str, cte_names: set[str]) -> exp.Expression:
    def rewrite(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Table) and node.name.lower() in PERSONAL_TABLES \
                and node.name.lower() not in cte_names and not isinstance(node.parent, exp.Subquery):
            alias = node.alias or node.name
            inner = sqlglot.select("*").from_(exp.to_table(node.name)).where(
                exp.EQ(this=exp.column("student_id"), expression=exp.Literal.string(student_id)))
            return exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias)))
        return node

    return tree.transform(rewrite, copy=True)
