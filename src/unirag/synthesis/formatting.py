"""Deterministic formatting: INR with Indian digit grouping, human dates, SQL rows as a prompt table."""
from __future__ import annotations

import re
from datetime import date

MONEY_COLUMNS = re.compile(r"amount|fee|balance|fine|tuition|concession|refund|paid|due$|outstanding|total_inr|inr",
                           re.I)
NOT_MONEY = re.compile(r"date|_on$|count|pct|percent|attendance|credits|cgpa|gpa|year|days|id$|code", re.I)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INR_IN_TEXT = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)")


def inr(x: float | int | str) -> str:
    """145000 -> ₹1,45,000 ; 1234.5 -> ₹1,234.50"""
    v = float(str(x).replace(",", ""))
    neg, v = v < 0, abs(v)
    whole = int(v)
    frac = round(v - whole, 2)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    out = "₹" + s + (f"{frac:.2f}"[1:] if frac else "")
    return ("-" if neg else "") + out


def human_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d %b %Y")


def is_money_column(col: str) -> bool:
    return bool(MONEY_COLUMNS.search(col)) and not NOT_MONEY.search(col)


def rows_to_prompt_table(columns: list[str], rows: list[dict], max_rows: int = 60) -> str:
    """Markdown table with INR and dates pre-formatted, so the model copies values instead of reformatting."""
    if not rows:
        return "(the database query returned no rows)"
    out = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for r in rows[:max_rows]:
        cells = []
        for c in columns:
            v = r.get(c)
            if v is None:
                cells.append("—")
            elif isinstance(v, bool):                  # bool is a subclass of int: check it first
                cells.append("yes" if v else "no")
            elif isinstance(v, (int, float)) and is_money_column(c):
                cells.append(inr(v))
            elif isinstance(v, str) and ISO_DATE.match(v):
                cells.append(human_date(v))
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    if len(rows) > max_rows:
        out.append(f"({len(rows) - max_rows} more rows not shown)")
    return "\n".join(out)


def fix_inr_grouping(text: str) -> str:
    """Rewrite any ₹ amount in the answer to Indian digit grouping (₹150,000 -> ₹1,50,000)."""
    return INR_IN_TEXT.sub(lambda m: inr(m.group(1)), text)
