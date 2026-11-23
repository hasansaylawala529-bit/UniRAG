"""Row-to-text for entity tables, used only to *find* which grant or course a question is about.

Changing values (amounts, deadlines, status, seats taken) are deliberately left out: the answer
always reads those from SQL at question time, so embedded text can never serve stale numbers.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass
class EntityText:
    entity_type: str
    entity_key: str
    text: str


def build_entity_texts(conn: psycopg.Connection) -> list[EntityText]:
    out: list[EntityText] = []
    with conn.cursor() as cur:
        cur.execute("SELECT grant_code, title, sponsor FROM grants ORDER BY grant_code")
        for code, title, sponsor in cur.fetchall():
            out.append(EntityText("grant", code,
                                  f"Research grant {code}: {title}. Sponsored by {sponsor}. "
                                  f"Eligibility rules are in the Research Grant Policy under {code}."))
        cur.execute("""SELECT c.course_code, c.title, d.code, d.name, c.semester, c.course_type, c.credits,
                              c.instructor
                       FROM courses c JOIN departments d USING (dept_id) ORDER BY c.course_code""")
        for code, title, dcode, dname, sem, ctype, credits, instr in cur.fetchall():
            kind = ctype.replace("_", " ")
            out.append(EntityText("course", code,
                                  f"Course {code}: {title}. {kind.capitalize()} offered by {dname} ({dcode}), "
                                  f"semester {sem} (year {(sem + 1) // 2}), {credits} credits, taught by {instr}."))
    return out
