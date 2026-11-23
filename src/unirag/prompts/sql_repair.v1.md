The previous SQL query for this question failed. Fix it.

## Question
{question}

## Relevant tables
{schema_cards}

## Context
- Reference date: DATE '{as_of}'
{student_context}
{extra_context}

## Failed query
```sql
{failed_sql}
```

## Error
{error}

Write a corrected single read-only PostgreSQL SELECT that avoids this error. Use only the listed tables
and columns. Return JSON with "sql" and "explanation" (one sentence, including what you changed).
