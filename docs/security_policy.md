# UniRAG security & data-access policy

UniRAG answers questions over personal student data (fees, CGPA, attendance, grant applications).
This policy defines what each user may see and how every rule is enforced. It is tested by
`eval/run_adversarial.py` (results: `eval/results/adversarial_summary.md`) and `tests/test_sql_guard.py`.

## 1. Roles

| Role | Who | May see |
|---|---|---|
| **Anonymous** | not logged in | Policies; public data (grants, courses, timetable, fee structure); **aggregates** over personal data (counts, averages, totals by department or year). **No individual student records.** |
| **Student** | logged in as one student | Everything anonymous can, plus **their own** records (fees, attendance, enrolments, applications, CGPA). **Never another student's records.** |
| **Staff** | academic advisor | Individual records of any named student, for advising. |
| **Everyone** | — | **E-mail addresses are never returned** (data minimisation). **No one can change data** through the assistant. |

## 2. Rules and where they are enforced

| # | Rule | Enforced by | Cost |
|---|---|---|---|
| R1 | The assistant is read-only | (a) write-intent rule refuses "mark my fees as paid", "delete…", "set my CGPA to…" before any model call; (b) SQL guard accepts only a single SELECT; (c) the database role `unirag_reader` is read-only with a 5 s timeout | 0 model calls for (a) |
| R2 | Anonymous users get aggregates only | SQL guard: a query on a personal table must return only aggregates or non-identifying attributes (department, year, status); `SELECT *`, names, ids, CGPA, balances or attendance per row are refused, as is filtering on one student's id | refusal is not sent back for "repair" |
| R3 | Students see only their own rows | (a) privacy rule refuses questions that name another student's id; (b) SQL guard rewrites every personal table into a subquery filtered to the logged-in student, whatever WHERE clause the model wrote; (c) a query filtering on another student's id is refused | 0 model calls for (a) |
| R4 | Personal questions need an identity | "my fees / my CGPA …" without a logged-in student → asked to log in, before any model call | 0 model calls |
| R5 | No student contact data | (a) contact-data rule refuses requests for students' e-mail addresses or phone numbers ("list all students' emails", "Riya Shah's email") before any model call, while office contacts and procedures ("who should I e-mail about refunds?") stay allowed; (b) SQL guard refuses any query that would return the `email` column, by name or through `SELECT *` / `s.*` on `students`, for every role. A query blocked only for that reason, when the question did not ask for contact data, is regenerated once without the column (P-025), so staff can list students without their e-mail addresses | 0 model calls for (a) |
| R6 | No system or internal tables | SQL guard allow-lists the 9 application tables; `information_schema`, `pg_catalog`, other schemas and the vector tables are refused | — |
| R7 | Off-topic and jailbreak requests are declined | semantic router / LLM planner → out-of-scope node with a fixed reply (no generation) | 0–1 model calls |
| R8 | Answers never present unverifiable figures as fact | verifier checks every number and date against the evidence and database totals | — |
| R9 | "Nothing found" is never improvised | when a database-only question has no usable rows, the answer is a fixed message chosen in code: data not held (UNANSWERABLE), not accessible (guard rejected every query), none in your own records, other students' records need staff access (scoped to 0 rows), or no matching records plus what was checked | 0 synthesis calls |
| R10 | Other entry points cannot widen access | (a) MCP server: the role and student ID come from the server's configuration (default anonymous); no tool takes an identity argument, unknown arguments are ignored, and there is no raw-SQL tool; (b) follow-up questions: the policy rules run on the follow-up as typed and again on its rewrite; a rewrite that adds a student ID is discarded; a conversation is bound to one identity and is cleared when the identity changes | 0 model calls for refusals |

Personal tables: `students`, `enrollments`, `fee_ledger`, `grant_applications`.
Identifying columns: `student_id`, `full_name`, `email`, `cgpa`, `gpa_4`, `amount_due`, `amount_paid`, `paid_on`,
`attendance_pct`, `grade`, `submitted_on`, `admission_year`, `is_hosteller`.

## 3. Design principles
- **Defence in depth.** Cheap deterministic rules first (no model call), then the SQL guard on the actual query,
  then the database role. No single layer, and never the LLM, is trusted alone.
- **Policy refusals are final.** A query refused for privacy is not sent back to the model to "fix", because
  rewording a forbidden request must not make it allowed.
- **Secure default.** `ask()` without a role is anonymous.

## 4. Known limits (prototype)
- Roles are chosen in the demo UI; a production deployment would take them from university single sign-on.
- Staff access is not further restricted by department or advisee list.
- Aggregates over very small groups could in principle reveal an individual (no minimum group size yet).
