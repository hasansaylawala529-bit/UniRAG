You rewrite a follow-up question from a university help-desk chat into ONE standalone question.

Rules:
- Resolve references ("it", "that", "those", "their", "the same") and fill in what the follow-up leaves out
  (topic, department, year of study, course or grant code, date) from the earlier turns.
- Keep everything the follow-up states; when it changes a detail ("what about IT?"), use the new detail and
  drop the old one.
- Do not answer the question, add facts, or add conditions nobody asked about.
- Never add a student ID or a person's name that the conversation does not contain.
- Keep first-person wording ("my fees") when the user is asking about themselves.
- If the follow-up is already standalone, return it unchanged.

## Earlier turns (oldest first)
{history}

## Follow-up
{question}

Return JSON with "standalone_question".
