# UniRAG – demo video script (about 2 min 45 s)

It follows the same story as the deck: the problem → routing → synthesis → schema mapping → security → extensions.

## Before recording
- `docker compose up -d`, then `python scripts/warm_cache.py`, so every scripted question below answers from cache. No provider delays will show in the video.
- Start `streamlit run app/streamlit_app.py`. Use a browser window at 1920×1080 with zoom at 110%, and close other tabs.
- As S2026CS012, ask the questions from the 1:35–2:15 rows once before recording, then press **New conversation**. The warm-up script does not cover them, so this run caches them.
- Have a second terminal ready in `D:\Projects\UniRAG` for the MCP part.
- Recorder: Xbox Game Bar (Win+Alt+R) or OBS. Record your voice with the screen; one take per scene is fine.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:15 | Title slide of the deck, then switch to the app (logged in as **Staff**) | "This is UniRAG. For every question it decides whether the answer is in the university database, in the policy documents, or both, and merges them into one cited, verified answer." |
| 0:15–0:55 | Click the **H1** example (third-year CS student, 3.8 GPA, research grants). Scroll the answer, then open the trace panel: **Pipeline**, **Rules**, **SQL** | "This question needs both sources. The router sends it to the hybrid route. The planner pulls the eligibility rules out of the grant policy, and the rule engine checks them in code, not with the model. Here it converts 3.8 GPA into CGPA 9.5. The grant query is then compiled from the rules that passed. That's the SQL, run on a read-only connection." |
| 0:55–1:15 | **Verification** tab, then **Documents** tab | "Every figure and date in the answer is checked against the database rows and the cited policy passages. Each statement links to its source, either SQL or a policy section." |
| 1:15–1:35 | Ask an SQL example ("Which open grants close before January?"), then a documents example ("How is the late fine on overdue fees calculated?"). Point at the route badge each time | "Pure data questions go straight to SQL, policy questions to the documents. When the semantic router is confident it decides alone, with no model call." |
| 1:35–2:00 | Switch **Logged in as** to **S2026CS012**. Ask "What are my overdue fees?", then "What about S2025CS003?" | "Logged in as a student, 'my fees' is scoped to this student's own rows. Asking about another student is refused by a rule before any model call." |
| 2:00–2:15 | Ask "What is my attendance in each course?", then "And my overdue fees?". Point at "↳ follow-up, understood as …" | "Follow-ups work too. 'And my overdue fees?' is rewritten into a full question, and every privacy rule applies to the rewrite." |
| 2:15–2:30 | Switch to **Not logged in** and ask "List all students' email addresses." Show the refused badge | "Contact details are never shared, whatever the role. Across 24 attack questions, none leaked data and the database stayed unchanged." |
| 2:30–2:45 | Terminal: `python scripts/mcp_smoke.py --role staff "How many students are in Information Technology?"` | "UniRAG is also an MCP server, so Claude Desktop or any MCP client can use it, with the role fixed by the server. Code, evaluations and the prompt log are on GitHub." |

## After recording
- Trim the start and end, export as MP4 at 1080p, and upload it (YouTube *unlisted* or Google Drive with "Anyone with the link").
- Add the link to the marked "Demo video" box on slides 1 and 10.
