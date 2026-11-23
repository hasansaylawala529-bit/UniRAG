"""Split policy documents into heading-based chunks with retrieval metadata.

Strategy
- One chunk per "## N." section. A section with "###" sub-headings (e.g. one per grant) becomes one
  chunk per sub-heading, so each grant's eligibility is retrieved on its own.
- Over-long sections are split on paragraph boundaries with a one-paragraph overlap.
- Every chunk starts with a contextual header ("Doc title > §N Section > Sub-section") so the
  embedding knows where the text came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

DOC_TYPES = {
    "01_student_handbook": "handbook",
    "02_research_grant_policy": "grant_policy",
    "03_fee_payment_refund_policy": "fee_policy",
    "04_scholarship_concession_policy": "scholarship_policy",
    "05_examination_attendance_rules": "exam_policy",
    "06_hostel_rules": "hostel_policy",
    "07_course_registration_policy": "registration_policy",
}
GRANT_CODE_RE = re.compile(r"\bGR-[A-Z0-9]+-\d{2}\b")
EFFECTIVE_RE = re.compile(r"Effective from (\d{1,2} \w+ \d{4})")
DEPT_CODES = ("CS", "IT", "ENTC")
MAX_CHARS = 2200


@dataclass
class Chunk:
    doc_id: str
    doc_title: str
    section: str
    content: str
    doc_type: str
    department: str | None = None
    year_applicability: list[int] | None = None
    effective_date: date | None = None
    grant_codes: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _effective(text: str) -> date | None:
    m = EFFECTIVE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d %B %Y").date()
    except ValueError:
        return None


def _scope(body: str) -> tuple[str | None, list[int] | None, list[str]]:
    """Departments / years a chunk applies to, read from explicit eligibility lines only."""
    depts: list[str] = []
    years: list[int] | None = None
    m = re.search(r"Eligible department\(s\):\s*(.+)", body)
    if m:
        depts = [d for d in DEPT_CODES if re.search(rf"\({d}\)", m.group(1))]
    m = re.search(r"Eligible year\(s\) of study:\s*(.+)", body)
    if m:
        years = sorted({int(y) for y in re.findall(r"Year (\d)", m.group(1))})
    department = depts[0] if len(depts) == 1 else None
    return department, years, depts


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    paras = [p for p in text.split("\n\n") if p.strip()]
    parts, cur = [], []
    for p in paras:
        if cur and len("\n\n".join(cur + [p])) > MAX_CHARS:
            parts.append("\n\n".join(cur))
            cur = [cur[-1]]          # one-paragraph overlap
        cur.append(p)
    if cur:
        parts.append("\n\n".join(cur))
    return parts


def chunk_document(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    doc_id = path.stem
    doc_type = DOC_TYPES.get(doc_id, "other")
    title_m = re.search(r"^# (.+)$", text, re.M)
    doc_title = title_m.group(1).strip() if title_m else doc_id
    eff = _effective(text)

    chunks: list[Chunk] = []
    # split on level-2 headings, keep the heading with its body
    sections = re.split(r"(?m)^(?=## )", text)
    preamble = sections[0]
    for sec in sections[1:]:
        h2 = sec.splitlines()[0][3:].strip()
        m_no = re.match(r"(\d+)\.\s*(.*)", h2)
        sec_no = m_no.group(1) if m_no else None
        sec_label = f"§{sec_no} {m_no.group(2)}" if m_no else h2
        subs = re.split(r"(?m)^(?=### )", sec)
        units: list[tuple[str | None, str]] = []
        if len(subs) == 1:
            units.append((None, sec))
        else:
            if len(subs[0].strip().splitlines()) > 1:      # intro text before the first sub-heading
                units.append((None, subs[0]))
            units += [(s.splitlines()[0][4:].strip(), s) for s in subs[1:]]
        for h3, body in units:
            section = sec_label if not h3 else f"{sec_label} > {h3}"
            for i, part in enumerate(_split_long(body.strip())):
                header = f"{doc_title} > {section}" + (f" (part {i + 1})" if i else "")
                content = f"[{header}]\n{part}"
                department, years, depts = _scope(part)
                codes = sorted(set(GRANT_CODE_RE.findall(part)))
                chunks.append(Chunk(
                    doc_id=doc_id, doc_title=doc_title, section=section, content=content,
                    doc_type=doc_type, department=department, year_applicability=years,
                    effective_date=eff, grant_codes=codes,
                    metadata={"section_no": sec_no, "departments": depts, "source_file": path.name,
                              "chars": len(content)},
                ))
    if not chunks and preamble.strip():
        chunks.append(Chunk(doc_id, doc_title, "Overview", preamble.strip(), doc_type, effective_date=eff))
    return chunks


def chunk_all(docs_dir: Path) -> list[Chunk]:
    out: list[Chunk] = []
    for p in sorted(docs_dir.glob("*.md")):
        out.extend(chunk_document(p))
    return out
