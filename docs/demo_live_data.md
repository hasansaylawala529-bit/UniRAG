# Demo: answers come from live data (≈ 30 seconds)

**Point to make:** UniRAG embeds only what is stable (policy text, table *descriptions*, grant and course names).
Every amount, date and status is read from PostgreSQL with SQL at question time, so a change in the database
shows up in the very next answer, with no re-ingestion. Tabular RAG that embeds rows would still show the old value.

| Step | Action | What the panel sees |
|---|---|---|
| 1 | Sidebar → **Logged in as** `S2026CS012 · Siddharth Ranade` | Profile switches to this student |
| 2 | Ask **"What are my overdue fees?"** | tuition_inst1 ₹45,000 and exam ₹3,500, both overdue |
| 3 | Sidebar → **Demo tools (admin)** → pick `exam · ₹3,500` → **Record payment** | "Recorded full payment of exam … status now paid" |
| 4 | Ask **the same question again** | Only tuition_inst1 ₹45,000 remains |
| 5 | Right panel → **SQL** tab | Same SQL, "Executed live against PostgreSQL at hh:mm:ss", one row fewer |
| 6 | **Reset demo data** | Ledger restored to the seed (874 rows), ready to repeat |

**What to say at step 3:** "This button is a separate admin tool with its own connection, standing in for the
accounts office. The assistant itself runs as a read-only database role: nothing a model generates can write data."

**At step 5:** "The SQL text was reused from cache, but it was executed again against the live database;
only the final answer was regenerated, from the new rows."
