# UniRAG – Technology Decision Record

Each decision below has four parts:
- the options I could have chosen;
- why the chosen one fits UniRAG's constraints;
- what it costs;
- what would make me revisit it.

The constraints are: Gemini and Groq free tiers only, local Docker on a Windows laptop, a 24-hour build, and read-only access to university data.

Figures come from `eval/results/` and the ingest log. Where something was not measured, I say so.

| # | Decision | In one line |
|---|---|---|
| 1 | PostgreSQL + pgvector, one database | One engine, one access policy, one SQL query across rows and vectors |
| 2 | Gemini Flash-Lite, with Groq as fallback | The largest free quota, plus a second provider for outages |
| 3 | LangGraph | An explicit graph of steps, with branches and a per-step trace |
| 4 | Retrieved schema cards | No accuracy difference at 9 tables; kept so prompts stay small as the schema grows |
| 5 | Rule engine in code | Worth 31 points of key facts in the ablation |
| 6 | Hybrid vector + keyword search | Exact codes and terms are matched, not just meanings |
| 7 | Streamlit | A working chat UI with a trace panel in hours, not days |

---

## 1. One PostgreSQL database with pgvector

**Options:**
- **Chosen:** PostgreSQL 16 with the pgvector extension.
- Postgres for the relational data, plus a separate vector store (Chroma, Qdrant or Pinecone).
- A document store with vector search.

**Why this fits:**
- **One access policy.** The read-only role `unirag_reader` (read-only transactions, 5 s timeout) protects relational rows and embeddings alike. Two stores would need two sets of credentials and permissions.
- **One query across both kinds of data.** Policy chunks carry `grant_codes` and department metadata, so one SQL statement can filter vectors by those (GIN indexes) and rank them by cosine similarity (HNSW indexes). With a separate store, those filters would have to be kept in sync in two places.
- **Consistency.** Re-ingesting rewrites rows and their embeddings in one transaction.
- **Local and simple.** One `pgvector/pgvector:pg16` container in Docker: no cloud account, no network dependency, nothing extra to secure.
- **Scale fits.** The corpus is small: 61 policy chunks, 9 schema cards, 90 entity texts and 60 router exemplars.

**Cost:**
- Postgres is not a specialised vector store. At millions of vectors, HNSW tuning, memory and rebuild times need attention.
- No managed features such as automatic sharding.

**Revisit when:**
- the document corpus reaches millions of chunks, or
- vector search must scale independently of the transactional database.

## 2. Gemini Flash-Lite as the main model, Groq as fallback

**Options:**
- **Chosen:** Gemini Flash-Lite as primary (3.1, then 3.5, then 3.6 Flash), with Groq `gpt-oss-120b` / `gpt-oss-20b` as cross-provider fallback.
- A single provider.
- A local model (for example through Ollama).
- A paid API.

**Why this fits:**
- **Quota.** Flash-Lite's free tier (15 requests/min, 500/day per model on my key) is the largest available. The evaluation runs need it: the ablation alone made about 310 generation requests across its five variants (2.8 per question × 22 questions × 5), some of them served from cache.
- **Embeddings from the same provider.** `gemini-embedding-001` at 768 dimensions keeps the HNSW indexes small.
- **Groq covers outages.** Its free tier is 30 requests/min but only 8K tokens/min and 200K tokens/day per model, so it can't carry an evaluation run alone, but it is a different provider. When Google returned 503s for hours, answers stayed correct through the fallback chain and the circuit breaker.
- **Structured output from both.** Strict JSON schemas, validated with the same pydantic models.

**Cost:**
- Free tiers are slow when overloaded: median hybrid latency was 72.6 s in the evaluation.
- Data is sent to external providers; that is acceptable only for synthetic data.
- Model names change, which is why they live in `.env`.

**Revisit when:**
- real student data is involved: use a paid tier with a data-processing agreement, or a self-hosted model;
- latency matters more than cost: a paid tier removes most of the overload delays.

I did not choose a local model because a laptop-scale model would be slower and less reliable at structured JSON and SQL, and it would make the demo depend on my hardware.

## 3. LangGraph for orchestration

**Options:**
- **Chosen:** LangGraph.
- Plain Python functions.
- An autonomous agent framework, where the model chooses tools in a loop.

**Why this fits:**
- **Branching is real.** A question can be:
  - refused by a rule (straight to the answer step, 0 model calls);
  - out of scope (declined);
  - routed to documents, SQL or both.

  A state graph with conditional edges states this explicitly.
- **Traceable.** Every node records its time, model requests and inputs, and that is exactly what the trace panel shows.
- **Deterministic.** The model never decides which tool to call next, so cost is bounded (at most 3 generation requests per question) and the security layers can't be skipped. A free-roaming agent would be harder to bound and to secure.

**Cost:**
- A dependency and a learning curve. The same flow could be written as plain functions with if-statements.

**Revisit when:**
- the flow becomes a simple straight line (plain functions would do), or
- questions need open-ended multi-step tool use. Even then I would keep the policy checks outside the agent.

## 4. Retrieved schema cards for text-to-SQL

**Options:**
- **Chosen:** retrieve the relevant table cards by similarity (plus the hub tables they join to).
- Pass the full schema every time.
- Fine-tune a text-to-SQL model.

**Why this fits:**
- Each card describes a table in plain language: its columns, joins and what the values mean, for example that `cgpa` is on a 10-point scale. That is how a question's words get mapped to real columns.
- Retrieval keeps the prompt to the tables that matter, which matters on token-limited free tiers (Groq: 8K tokens/min).

**What the ablation showed:**
- Passing the full schema gave the **same** results: key facts 93% (75/81) in both variants, and 19/22 fully correct in both.
- With only 9 tables, the full schema fits easily, so this component is **not proven** on this database.

**Cost:**
- One embedding lookup per database question.
- Cards must be rebuilt when the schema changes; ingest does this.

**Revisit when:** the claim is tested on a larger schema. With dozens or hundreds of tables, the full schema stops fitting in the prompt and retrieval becomes necessary. That is the reason to keep it, but it is a design argument, not a measured result.

## 5. A rule engine in code for eligibility

**Options:**
- **Chosen:** the planner extracts rules into a fixed schema (`subject, field, operator, value, effect, citation`). Code evaluates them against the student profile, and the grant query is compiled from the result.
- Let the model apply the rules inside its SQL or its answer.

**Why this fits:**
- Eligibility is logic, for example "CGPA ≥ 8.5, year 3 or 4, CS or IT". Code evaluates it the same way every time and can show which rule passed or failed (the Rules tab).
- The SQL for grant questions is compiled, not written by a model, so a model can't drop a filter. Early live runs did exactly that (P-010).

**What the ablation showed:** this is the component that matters most. Without it, with the model applying the rules:
- key facts fall from **93% to 62%**, and hybrid facts from **90% to 47%**;
- H1 drops from 14/18 facts to 0/18.

**Cost:**
- The rule schema covers comparisons and set membership. Rules that don't fit, such as unusual exceptions, fall back to text constraints for the model.

**Revisit when:** the policies need richer logic, such as dates relative to other events or combined conditions. I would then extend the rule schema, not hand the logic back to the model.

## 6. Hybrid vector + keyword search over the policy documents

**Options:**
- **Chosen:** score = 0.75 × vector similarity + 0.25 × trigram keyword similarity + 0.20 bonus when the chunk is tagged with a grant code named in the question.
- Pure vector search.
- Pure keyword search (BM25).

**Why this fits:**
- Policy questions mix meaning ("can I still apply") with exact tokens: grant codes like `GR-AI-01`, section names, terms like "condonation". Embeddings blur exact codes; keywords catch them.
- The `pg_trgm` index lives in the same database, so this costs no extra service.
- Grant questions additionally fetch every grant chunk that matches the department and year metadata, so recall doesn't depend on a top-k cut-off.

**Cost:**
- The weights were set by hand and **not tuned or ablated**. I don't have a measured gain over pure vector search.

**Revisit when:** there is a larger document set. Then I would ablate the keyword component, tune the weights on a labelled retrieval set, or try a reranker.

## 7. Streamlit for the interface

**Options:**
- **Chosen:** Streamlit.
- A custom front end (React plus an API).
- A notebook or the command line only.

**Why this fits:**
- A chat interface with a five-tab trace panel, a role selector and admin demo tools took hours, all in Python, in the same process as the pipeline.
- The evaluation criteria are about the engine. The UI's job is to make the engine's decisions visible, and the trace panel does that.

**Cost:**
- Not built for production. It has no real authentication (the role is chosen from a list), it reruns on every interaction, and it offers limited layout control.

**Revisit when:** UniRAG moves towards real users. I would then add an API layer with single sign-on and a proper front end. The MCP server already shows the engine can sit behind a different interface without changes.
