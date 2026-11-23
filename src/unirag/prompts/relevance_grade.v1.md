You are grading retrieved policy passages for a university question-answering system.
For each passage, decide whether it contains information that helps answer the question
(a rule, condition, procedure, definition or eligibility criterion that applies). A passage that
is only on the same general topic but would not change the answer is NOT relevant.

## Question
{question}

## Passages
{passages}

Return JSON: a list "grades" with one item per passage: "id" (the passage number), "relevant"
(true/false) and "reason" (at most 12 words).
