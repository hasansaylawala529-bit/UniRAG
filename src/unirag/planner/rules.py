"""Deterministic rule evaluation: apply extracted policy rules to the profile in code, not in the LLM.

The LLM reads the policy and writes each condition in a fixed form (subject, field, operator, value).
This module checks every condition it can against the profile, then groups the results by subject:
  pass    - every checkable condition holds
  fail    - at least one condition fails (with the failing reason)
  unknown - nothing could be checked (e.g. the profile lacks that field)
The outcome becomes explicit constraints for the SQL step, e.g. "grant_code IN ('GR-AI-01', ...)".
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..profile import Profile

CHECKABLE_FIELDS = {"cgpa", "gpa_4", "year_of_study", "department", "has_overdue_dues", "is_hosteller",
                    "credits_registered"}
DEPT_CODES = ("ENTC", "CS", "IT")
GRANT_CODE_RE = re.compile(r"^GR-[A-Z0-9]+-\d{2}$")


@dataclass
class RuleCheck:
    subject: str
    field: str
    operator: str
    value: str
    effect: str
    citation: str
    outcome: str            # pass | fail | unknown
    detail: str


@dataclass
class SubjectResult:
    subject: str
    outcome: str
    reasons: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)


@dataclass
class RuleEvaluation:
    checks: list[RuleCheck]
    subjects: list[SubjectResult]

    @property
    def passing(self) -> list[str]:
        return [s.subject for s in self.subjects if s.outcome == "pass"]

    @property
    def failing(self) -> list[SubjectResult]:
        return [s for s in self.subjects if s.outcome == "fail"]

    def to_dict(self) -> dict:
        return {"checks": [asdict(c) for c in self.checks], "subjects": [asdict(s) for s in self.subjects]}

    def constraints_text(self, profile: Profile) -> str:
        """Context handed to the SQL step and to synthesis."""
        if not self.checks:
            return ""
        lines = [f"Profile used: {profile.summary()}"]
        grants_pass = [s for s in self.passing if GRANT_CODE_RE.match(s)]
        if grants_pass:
            lines.append("Policy check (done in code): the profile meets every checkable eligibility rule for "
                         + ", ".join(grants_pass)
                         + f". Use grant_code IN ({', '.join(repr(g) for g in grants_pass)}) and return live "
                           "amount, application_open, application_deadline and status for each (do not filter by status).")
        fails = self.failing
        if fails:
            lines.append("Not eligible / rule not met: " + "; ".join(
                f"{s.subject} ({'; '.join(s.reasons)})" for s in fails))
        other = [s for s in self.subjects if s.outcome == "pass" and not GRANT_CODE_RE.match(s.subject)]
        if other:
            lines.append("Rules met: " + ", ".join(s.subject for s in other))
        unknown = [s.subject for s in self.subjects if s.outcome == "unknown"]
        if unknown:
            lines.append("Rules needing database values: " + ", ".join(unknown))
        return "\n".join(lines)


def _num(v: str) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except ValueError:
        return None


def _list(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,/;]|\band\b|\bor\b", str(v)) if x.strip()]


def _dept_codes(v: str) -> list[str]:
    found = []
    up = str(v).upper().replace("E&TC", "ENTC")
    for code in DEPT_CODES:
        if re.search(rf"\b{code}\b", up) and code not in found:
            found.append(code)
    if "COMPUTER SCIENCE" in up and "CS" not in found:
        found.append("CS")
    if "INFORMATION TECHNOLOGY" in up and "IT" not in found:
        found.append("IT")
    if "ELECTRONICS" in up and "ENTC" not in found:
        found.append("ENTC")
    return found


def _check(field_name: str, op: str, value: str, actual: Any) -> tuple[str, str]:
    if actual is None:
        return "unknown", f"{field_name} not known for this profile"
    op = op.strip().lower()
    if field_name == "department":
        allowed = _dept_codes(value)
        if not allowed:
            return "unknown", f"could not read departments from '{value}'"
        hit = actual in allowed
        ok = hit if op in ("in", "==") else (not hit if op in ("not_in", "!=") else None)
        if ok is None:
            return "unknown", f"operator {op} not supported for department"
        return ("pass" if ok else "fail"), f"department {actual} {'in' if hit else 'not in'} {allowed}"
    if field_name in ("has_overdue_dues", "is_hosteller"):
        want = str(value).strip().lower() in ("true", "yes", "1")
        ok = (bool(actual) == want) if op in ("==", "in") else (bool(actual) != want)
        return ("pass" if ok else "fail"), f"{field_name} is {bool(actual)}"
    if op in ("in", "not_in"):
        opts = [_num(x) for x in _list(value)]
        opts = [o for o in opts if o is not None]
        if not opts:
            return "unknown", f"could not read list '{value}'"
        hit = float(actual) in opts
        ok = hit if op == "in" else not hit
        return ("pass" if ok else "fail"), f"{field_name} {actual} {'in' if hit else 'not in'} {[int(o) if o.is_integer() else o for o in opts]}"
    threshold = _num(value)
    if threshold is None:
        return "unknown", f"could not read threshold '{value}'"
    a = float(actual)
    ok = {">=": a >= threshold, ">": a > threshold, "<=": a <= threshold, "<": a < threshold,
          "==": a == threshold, "!=": a != threshold}.get(op)
    if ok is None:
        return "unknown", f"operator {op} not supported"
    return ("pass" if ok else "fail"), f"{field_name} {actual} {op} {value}: {'yes' if ok else 'no'}"


def evaluate_rules(rules: list, profile: Profile) -> RuleEvaluation:
    checks: list[RuleCheck] = []
    for r in rules:
        rd = r.model_dump() if hasattr(r, "model_dump") else dict(r)
        subject = rd["subject"].strip()
        if GRANT_CODE_RE.match(subject.upper()):
            subject = subject.upper()
        fld = rd["field"].strip().lower()
        if fld in CHECKABLE_FIELDS:
            outcome, detail = _check(fld, rd["operator"], rd["value"], getattr(profile, fld, None))
        else:
            outcome, detail = "unknown", f"'{fld}' is checked against database values, not the profile"
        checks.append(RuleCheck(subject, fld, rd["operator"], rd["value"], rd.get("effect", ""),
                                rd.get("citation", ""), outcome, detail))

    subjects: dict[str, SubjectResult] = {}
    for c in checks:
        s = subjects.setdefault(c.subject, SubjectResult(c.subject, "unknown"))
        if c.citation and c.citation not in s.citations:
            s.citations.append(c.citation)
        if c.outcome == "fail":
            s.outcome = "fail"
            s.reasons.append(c.detail)
        elif c.outcome == "pass" and s.outcome == "unknown":
            s.outcome = "pass"
    return RuleEvaluation(checks, list(subjects.values()))
