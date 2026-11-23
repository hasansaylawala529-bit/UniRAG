"""Multi-turn conversations: follow-up questions are rewritten into standalone questions.

    conv = Conversation(role="staff")
    conv.ask("How many students are enrolled in Computer Science?")
    conv.ask("And in Information Technology?")    # -> "How many students are enrolled in Information Technology?"

1. Detection is deterministic (no model call): an opener ("and …", "what about …"), a very short question
   without a topic word, or a reference ("it", "those", "their") without a topic word, AND an earlier turn.
2. A detected follow-up is rewritten with one fast-model call (prompt conversation_rewrite.v1); the whole
   pipeline then runs on the rewritten question, so every policy check applies to it.
3. The access-policy rules are also applied to the follow-up as typed: "what about S2025CS003?" from a student
   is refused at once, without a rewrite.
4. A conversation belongs to one identity (role + student ID). Changing identity starts a new conversation,
   so nothing from one identity's answers reaches another identity's prompts.
5. A rewrite that introduces a student ID not present in the conversation is discarded.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from pydantic import BaseModel

from .config import get_settings
from .llm import get_llm
from .profile import STUDENT_ID_RE, extract_entities
from .prompts import render

MAX_TURNS = 3                  # earlier turns given to the rewriter
ANSWER_CHARS = 400             # of each earlier answer
OPENER_RE = re.compile(r"^\s*(and|also|plus|but|so|then|ok(ay)?[, ]+(and|so|what)|what\s+about|how\s+about|"
                       r"what\s+if|same\s+(for|question))\b", re.I)
# "For IT?", "In second year?": follow-up openers only in short questions ("In which semester is CS304 taught?")
SHORT_OPENER_RE = re.compile(r"^\s*(for|in|only|except|just)\b", re.I)
REFERENCE_RE = re.compile(r"\b(it|its|that|those|these|them|they|their|theirs|this\s+one|the\s+same|there|"
                          r"which\s+of\s+(them|those|these)|he|she|his|her)\b", re.I)
TOPIC_RE = re.compile(r"\b(grants?|scholarships?|fellowships?|fees?|fines?|tuition|refunds?|courses?|electives?|"
                      r"timetables?|exams?|examinations?|attendance|condonation|hostels?|departments?|cgpa|gpa|"
                      r"credits?|enrol\w*|registration|students?|policy|policies|dues|ledger|seats?)\b", re.I)


class Rewrite(BaseModel):
    standalone_question: str


@dataclass
class Turn:
    question: str              # as typed
    asked: str                 # what the pipeline answered (rewritten for a follow-up)
    answer: str
    route: str


def followup_reason(question: str, has_history: bool) -> str | None:
    """Why the question looks like a follow-up, or None. No model call."""
    if not has_history:
        return None
    q = question.strip()
    words = re.findall(r"[\w'-]+", q)
    topic = bool(TOPIC_RE.search(q))
    if OPENER_RE.search(q) or (SHORT_OPENER_RE.search(q) and len(words) <= 6):
        return "opener"
    if len(words) <= 4 and not topic:
        return "short, no topic"
    if REFERENCE_RE.search(q) and not topic:
        return "reference to an earlier turn"
    return None


def rewrite_followup(question: str, turns: list[Turn]) -> tuple[str, str]:
    """(standalone question, prompt id) with one fast-model call."""
    hist = "\n\n".join(f"User: {t.asked}\nAssistant: {t.answer[:ANSWER_CHARS]}" for t in turns[-MAX_TURNS:])
    prompt, pid = render("conversation_rewrite", history=hist, question=question)
    out = get_llm().generate(prompt, model=get_settings().fast_model, schema=Rewrite)
    return out.standalone_question.strip(), pid


@dataclass
class Conversation:
    role: str | None = None            # anonymous | staff; implied "student" when student_id is set
    student_id: str | None = None
    turns: list[Turn] = field(default_factory=list)

    @property
    def identity(self) -> tuple[str, str | None]:
        return ("student" if self.student_id else (self.role or "anonymous"), self.student_id)

    def reset(self) -> None:
        self.turns.clear()

    def switch_identity(self, role: str | None, student_id: str | None) -> bool:
        """Start a new conversation if the identity changes. Returns True when it did."""
        new = ("student" if student_id else (role or "anonymous"), student_id)
        if new == self.identity:
            return False
        self.role, self.student_id = role, student_id
        self.reset()
        return True

    def ask(self, question: str):
        from .graph import ask, policy_refusal

        role, sid = self.identity
        reason = followup_reason(question, bool(self.turns))
        asked, info = question, None
        if reason:
            info = {"node": "rewrite", "original": question, "detected_by": reason}
            # the access policy is checked on the follow-up as typed first: refused at once, no rewrite
            refusal = policy_refusal(extract_entities(question), role, sid)
            if refusal:
                info.update({"ms": 0, "gen_requests": 0, "llm_calls": 0, "cache_hits": 0,
                             "rewritten": question, "skipped": f"refused as typed ({refusal[1]})"})
            else:
                llm = get_llm()
                g0, h0, r0 = llm.stats.gen_calls, llm.stats.cache_hits, llm.stats.gen_requests
                t0 = time.perf_counter()
                rewritten, pid = rewrite_followup(question, self.turns)
                known = set(STUDENT_ID_RE.findall(" ".join([question] + [t.asked + " " + t.answer
                                                                        for t in self.turns]).upper()))
                new_ids = set(STUDENT_ID_RE.findall(rewritten.upper())) - known
                if not rewritten or new_ids:
                    info["discarded"] = rewritten
                    rewritten = question
                info.update({"ms": int((time.perf_counter() - t0) * 1000), "prompt": pid,
                             "gen_requests": llm.stats.gen_requests - r0, "llm_calls": llm.stats.gen_calls - g0,
                             "cache_hits": llm.stats.cache_hits - h0,
                             "model": getattr(llm, "last_model_used", None) or "", "rewritten": rewritten})
                asked = rewritten
        r = ask(asked, student_id=sid, role=None if sid else role)
        if info:
            r.trace = [info] + r.trace
            r.gen_requests += info["gen_requests"]
            r.llm_calls += info["llm_calls"]
            r.cache_hits += info["cache_hits"]
            r.latency_ms += info["ms"]
        r.extra["conversation"] = {"original": question, "asked": asked, "followup": bool(reason),
                                   "detected_by": reason, "turn": len(self.turns) + 1}
        r.question = question
        self.turns.append(Turn(question=question, asked=asked, answer=r.answer, route=r.route))
        del self.turns[:-MAX_TURNS]
        return r
