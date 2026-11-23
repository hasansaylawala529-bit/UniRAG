"""Post-generation check: every number, amount and date in the answer must come from the evidence.

1. Strip citations and identifiers (grant codes, student IDs, course codes, section numbers, terms).
2. Extract dates (several formats -> ISO), ₹ amounts and other numbers (with Indian or Western grouping).
3. Build the evidence pool: SQL row values, numbers/dates in the passages and rule summary, profile
   values, the question itself and the reference date.
4. Allow simple derivations of evidence: row-wise differences and sums, column totals, percentages of
   amounts, GPA/CGPA conversion, day counts between evidence dates, and products with rule rates
   (e.g. 108 days x ₹100), plus their caps.
5. Anything left over is reported as unverified, so a possibly hallucinated figure is flagged.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|" \
         "august|september|october|november|december"
DATE_PATTERNS = [
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), ("%Y-%m-%d",)),
    (re.compile(rf"\b(\d{{1,2}}\s+(?:{MONTHS})\.?,?\s+\d{{4}})\b", re.I), ("%d %b %Y", "%d %B %Y")),
    (re.compile(rf"\b((?:{MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}})\b", re.I), ("%b %d %Y", "%B %d %Y")),
]
IGNORE_PATTERNS = [
    re.compile(r"\[(?:SQL|Doc:[^\]]*)\]"),                 # citations
    re.compile(r"\bGR-[A-Z0-9]+-\d{2}\b", re.I),           # grant codes
    re.compile(r"\bS\d{4}(?:CS|IT|ENTC)\d{3}\b", re.I),    # student ids
    re.compile(r"\b(?:CS|IT|ENTC|OE)\d{1,2}E?\d{0,2}\b"),  # course codes
    re.compile(r"§\s?\d+"),                                # section numbers
    re.compile(r"\b\d{4}-(?:\d{2}(?!-\d)|ODD|EVEN)\b", re.I),  # academic year / term (not ISO dates)
    re.compile(r"\b(?:year|semester|sem)\s*\d\b", re.I),   # "Year 3"
    re.compile(r"\b\d(?:st|nd|rd|th)\b", re.I),
]
MONEY_RE = re.compile(r"(?:₹|rs\.?|inr)\s?(\d[\d,]*(?:\.\d+)?)", re.I)
NUM_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(\s?%)?")
SMALL_INT_IGNORE = 4     # bare integers up to this value (counts, list positions) are not checked


@dataclass
class Figure:
    text: str
    kind: str          # date | money | percent | number
    value: str
    verified: bool = False
    basis: str = ""


@dataclass
class VerificationReport:
    figures: list[Figure] = field(default_factory=list)

    @property
    def unverified(self) -> list[Figure]:
        return [f for f in self.figures if not f.verified]

    @property
    def ok(self) -> bool:
        return not self.unverified

    def summary(self) -> str:
        n, bad = len(self.figures), len(self.unverified)
        if n == 0:
            return "No figures to verify."
        if bad == 0:
            return f"All {n} figures verified against the evidence."
        return f"{n - bad}/{n} figures verified; unverified: " + ", ".join(f.text for f in self.unverified)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "summary": self.summary(), "figures": [asdict(f) for f in self.figures]}


def _plain(v: Decimal) -> str:
    return format(v.normalize(), "f")


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _parse_date(s: str, fmts: tuple[str, ...]) -> date | None:
    clean = re.sub(r"[.,]", "", s).strip()
    clean = re.sub(r"\bsept\b", "sep", clean, flags=re.I)
    clean = re.sub(r"\s+", " ", clean)
    for f in fmts:
        try:
            return datetime.strptime(clean, f).date()
        except ValueError:
            continue
    return None


def extract_figures(text: str) -> list[Figure]:
    t = text
    for p in IGNORE_PATTERNS:
        t = p.sub(" ", t)
    figs: list[Figure] = []
    for pat, fmts in DATE_PATTERNS:
        def _take(m: re.Match) -> str:
            d = _parse_date(m.group(1), fmts)
            if d:
                figs.append(Figure(m.group(1), "date", d.isoformat()))
                return " "
            return m.group(0)
        t = pat.sub(_take, t)

    def _money(m: re.Match) -> str:
        v = _dec(m.group(1))
        if v is not None:
            figs.append(Figure(m.group(0).strip(), "money", _plain(v)))
        return " "
    t = MONEY_RE.sub(_money, t)
    for m in NUM_RE.finditer(t):
        v = _dec(m.group(1))
        if v is None:
            continue
        kind = "percent" if m.group(2) else "number"
        if kind == "number" and v == v.to_integral_value() and abs(v) <= SMALL_INT_IGNORE:
            continue
        if kind == "number" and 1900 <= v <= 2100 and v == v.to_integral_value():
            continue        # a bare year
        figs.append(Figure(m.group(0).strip(), kind, _plain(v)))
    return figs


Q2 = Decimal("0.01")


class EvidencePool:
    def __init__(self) -> None:
        self.numbers: dict[Decimal, str] = {}        # keys quantised to 2 decimals
        self.dates: dict[str, str] = {}
        self.pct_rates: set[Decimal] = set()          # percentages stated in policies (e.g. 15%)
        self.money_rates: set[Decimal] = set()        # ₹ amounts stated in policies (e.g. ₹100 per day)

    def add_num(self, v, basis: str) -> None:
        d = v if isinstance(v, Decimal) else _dec(str(v))
        if d is not None:
            self.numbers.setdefault(d.quantize(Q2), basis)

    def add_text(self, text: str, basis: str, rates: bool = False) -> None:
        for f in extract_figures(text):
            if f.kind == "date":
                self.dates.setdefault(f.value, basis)
                continue
            self.add_num(f.value, basis)
            if rates and f.kind == "percent":
                self.pct_rates.add(Decimal(f.value))
            if rates and f.kind == "money":
                self.money_rates.add(Decimal(f.value))

    def has_num(self, v: Decimal) -> str | None:
        q = v.quantize(Q2)
        for cand in (q, q - Q2, q + Q2):
            if cand in self.numbers:
                return self.numbers[cand]
        return None


def build_pool(*, rows: list[dict], passages_text: str, rule_text: str, profile: dict, question: str,
               as_of: date, extra_dates: list[date] | None = None) -> EvidencePool:
    pool = EvidencePool()
    numeric_rows: list[list[Decimal]] = []
    columns: dict[str, list[Decimal]] = {}
    row_dates: list[date] = []
    for r in rows:
        nums = []
        for c, v in r.items():
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float, Decimal)):
                d = Decimal(str(v))
                pool.add_num(d, f"SQL {c}")
                nums.append(d)
                columns.setdefault(c, []).append(d)
            elif isinstance(v, str):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    pool.dates.setdefault(v, f"SQL {c}")
                    row_dates.append(date.fromisoformat(v))
                elif _dec(v) is not None:
                    pool.add_num(_dec(v), f"SQL {c}")
        numeric_rows.append(nums)
    pool.add_text(passages_text, "policy passage", rates=True)
    pool.add_text(rule_text, "rule check", rates=True)
    pool.add_text(question, "question")
    for k, v in profile.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            pool.add_num(v, f"profile {k}")
    pool.dates.setdefault(as_of.isoformat(), "reference date")
    for d in extra_dates or []:
        pool.dates.setdefault(d.isoformat(), "question date")

    # ---- derivations (kept narrow so a made-up figure is unlikely to match by coincidence)
    base = list(pool.numbers)
    for nums in numeric_rows:
        for x, y in itertools.permutations(nums, 2):
            pool.add_num(x - y, "derived: row difference")
            pool.add_num(x + y, "derived: row sum")
    for c, vals in columns.items():
        pool.add_num(sum(vals), f"derived: total of {c}")
    for v in base:
        for p in pool.pct_rates:
            pool.add_num(v * p / 100, f"derived: {p}% of {v}")
        pool.add_num(v / Decimal("0.4"), "derived: GPA -> CGPA (Handbook §4)")
        pool.add_num(v * Decimal("0.4"), "derived: CGPA -> GPA (Handbook §4)")
    anchors = [as_of] + list(extra_dates or [])
    for d in row_dates:
        for a in anchors:
            days = Decimal(abs((a - d).days))
            pool.add_num(days, "derived: days between dates")
            for r in pool.money_rates:
                pool.add_num(days * r, f"derived: {days} days x ₹{r}")
    return pool


def verify_answer(answer: str, pool: EvidencePool) -> VerificationReport:
    report = VerificationReport()
    for f in extract_figures(answer):
        if f.kind == "date":
            basis = pool.dates.get(f.value)
        else:
            basis = pool.has_num(Decimal(f.value))
        f.verified, f.basis = basis is not None, basis or ""
        report.figures.append(f)
    # A total may combine figures from different sources (a database fine + a policy flat fee).
    # Accept an amount that equals the sum of 2-3 OTHER amounts already verified in this answer.
    # (Up to 4 was too permissive: a wrong total of 4 of 5 items passed in the UI test, P-012.)
    verified_money = [Decimal(f.value) for f in report.figures if f.verified and f.kind == "money"]
    for f in report.unverified:
        if f.kind != "money":
            continue
        target = Decimal(f.value)
        for n in range(2, min(3, len(verified_money)) + 1):
            if any(sum(c) == target for c in itertools.combinations(verified_money, n)):
                f.verified, f.basis = True, "derived: sum of verified figures in the answer"
                break
    return report
