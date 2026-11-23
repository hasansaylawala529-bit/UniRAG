# UniRAG evaluation

36 questions (SQL 10, Documents 10, Hybrid 12, Out of scope 4). Expected answers are computed from the database and the policy rules (`eval/build_questions.py`); questions are worded differently from the router exemplars.

## 1. Routing

**Accuracy: 35/36 (97%)** · decided by semantic router 8, LLM planner 28, privacy rule 0

| expected ↓ / actual → | SQL | Documents | Hybrid | Out of scope |
|---|---|---|---|---|
| **SQL** | 9 | 0 | 1 | 0 |
| **Documents** | 0 | 10 | 0 | 0 |
| **Hybrid** | 0 | 0 | 12 | 0 |
| **Out of scope** | 0 | 0 | 0 | 4 |

Misrouted: SQL02 (sql → hybrid)

## 2. Answer accuracy (key facts)

| Route | Questions | Key facts found | Fully correct questions |
|---|---|---|---|
| SQL | 10 | 22/22 (100%) | 10/10 |
| Documents | 10 | 11/11 (100%) | 10/10 |
| Hybrid | 12 | 53/59 (90%) | 9/12 |
| Out of scope | 4 | — (declined 4/4) | 4/4 |
| **All** | 36 | **86/92 (93%)** | 33/36 |

## 3. Hybrid questions: UniRAG vs vector-only RAG baseline

Baseline = top-8 policy passages + one generation call (no database, no planner).

| System | Key facts found | Fully correct questions |
|---|---|---|
| Vector-only RAG | 14/59 (24%) | 1/12 |
| **UniRAG** | **53/59 (90%)** | **9/12** |

## 4. Verification and SQL

- Verifier: **36/36 answers fully verified**; 0 figure(s) flagged in total.
- SQL ran for 22 questions: 3 compiled in code from rule results, 19 LLM-generated + guarded.
- LLM SQL success: 19/19 first attempt, 0 after self-repair, 0 failed → **100% overall**.

## 5. Cost and latency per question

| Route | Generation requests (median / max) | Latency median | Latency p90 | Live runs |
|---|---|---|---|---|
| SQL | 3 / 3 | 26.5 s | 97.6 s | 10/10 |
| Documents | 2 / 2 | 18.1 s | 59.1 s | 10/10 |
| Hybrid | 3 / 3 | 72.6 s | 82.4 s | 6/12 |
| Out of scope | 1 / 1 | 2.7 s | 16.8 s | 3/4 |

## 6. Every imperfect answer

- **SQL02** — Which grants are open with a deadline in January 2027? → routed hybrid (expected sql)
- **H1** — I am a third-year CS student with a 3.8 GPA; what research grants am I eligible for, and what is the deadline to apply? → missing ₹90,000, 15 Mar 2027, ₹40,000, 31 Mar 2027
- **H2** — Can student S2026CS003 get the hall ticket for all end-semester exams this term? → missing ₹500
- **H4** — Student S2023ENTC001 wants to add one more 3-credit elective this term. Is that allowed? → missing 28
