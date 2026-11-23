You are grading retrieved policy passages for a university question-answering system.
For each passage, decide whether it contains information that helps answer the question: a rule,
condition, procedure, definition, eligibility criterion or consequence that applies.

Count a passage as relevant when it describes a consequence or rule that MAY apply to the asker under a
condition the question does not rule out. Example: for "my attendance is 70%", a passage on detention
below 65% OR after a rejected condonation request is relevant, because the condonation could be rejected.

A passage that is only on the same general topic, but would not change or qualify the answer, is NOT relevant.

## Question
{question}

## Passages
{passages}

Return JSON: a list "grades" with one item per passage: "id" (the passage number), "relevant"
(true/false) and "reason" (at most 12 words).
