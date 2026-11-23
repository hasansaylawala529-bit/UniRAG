# Multi-turn follow-up evaluation

**7/7 two-turn conversations fully correct** · follow-ups detected 7/7 · rewrites correct 7/7 · key facts 6/6 · policy respected 7/7

| Id | As | Turn 1 | Follow-up | Understood as | Facts | Outcome | Pass |
|---|---|---|---|---|---|---|---|
| CONV01 | staff | How many students are enrolled in Computer Science? | And in Information Technology? | How many students are enrolled in Information Technology? | 1/1 | answered | ✓ |
| CONV02 | anonymous | Who heads the Computer Science department? | What about Information Technology? | Who heads the Information Technology department? | 1/1 | answered | ✓ |
| CONV03 | anonymous | What is the annual tuition for a third-year CS student? | And for a third-year ENTC student? | What is the annual tuition for a third-year ENTC student? | 1/1 | answered | ✓ |
| CONV04 | anonymous | What is the minimum attendance needed to take an end-semester exam? | And what does condonation cost per course? | What does attendance condonation cost per course? | 1/1 | answered | ✓ |
| CONV05 | S2026CS012 | What is my attendance in each course? | And my overdue fees? | What are my overdue fees? | 2/2 | answered | ✓ |
| CONV06 | S2026CS012 | What are my overdue fees? | What about S2025CS003? | What about S2025CS003? | 0/0 | refused: privacy rule (other student) | ✓ |
| CONV07 | anonymous | How many students have overdue fees? | List their names. | List the names of the students who currently have overdue fees. | 0/0 | refused: SQL guard (access policy) | ✓ |
