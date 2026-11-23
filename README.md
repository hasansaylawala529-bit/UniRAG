# UniRAG

**Unified Hybrid RAG for Structured and Unstructured University Intelligence.** UniRAG is a conversational engine that decides for each question whether to query the relational database, search the policy documents, or combine both. It then merges the results into one cited, numerically exact answer.

Design: see [`docs/architecture.md`](docs/architecture.md). Dataset: see [`data/README.md`](data/README.md).

## Quick start (Windows / macOS / Linux)

```bash
# 1. Python environment
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .                  # makes `python -m unirag.cli` available

# 2. Configuration
copy .env.example .env            # macOS/Linux: cp .env.example .env
#    then put your GEMINI_API_KEY in .env

# 3. Generate the dataset (reproducible, fixed seed)
python scripts/generate_data.py

# 4. Start PostgreSQL + pgvector (schema, read-only role and seed data load automatically)
docker compose up -d
```

```bash
# 5. Ingest: chunk documents, build schema cards, embed everything into pgvector (re-runnable)
python scripts/ingest.py
```

To reload after regenerating data: `docker compose down -v`, `docker compose up -d`, then `python scripts/ingest.py`.

## Test each retrieval path
```bash
python -m unirag.cli sql  "Which open grants close before January?"
python -m unirag.cli sql  "What are my overdue fees?" --student S2026CS012
python -m unirag.cli docs "What is the minimum attendance for exams?"
python -m unirag.cli guard "DELETE FROM grants"        # shows the guard rejecting it
pytest -q                                               # unit tests (guard, chunking, rules, verifier, fallback)
```

## Demo interface
```bash
python scripts/warm_cache.py               # once: pre-answers every example so the demo is instant
streamlit run app/streamlit_app.py         # opens http://localhost:8501
```
Chat on the left; on the right, "How it was answered" shows the route, pipeline steps, rule checks,
the SQL (compiled in code or LLM-generated + guarded), the policy passages and the figure verification.
Choose a student under "Logged in as" to ask about "my" fees or attendance.

## Evaluation
```bash
python eval/build_questions.py             # 36 questions; expected answers computed from the DB + policy rules
python eval/run_eval.py                    # resumable; writes eval/results/summary.md and charts/*.png
```
Measures routing accuracy (confusion matrix), key-fact accuracy per route, a vector-only RAG baseline on the
hybrid questions, verifier pass rate, SQL success (compiled vs LLM-generated, first attempt vs self-repair)
and generation requests / latency per route.

## Ask questions (full pipeline)
```bash
python -m unirag.cli ask "I am a third-year CS student with a 3.8 GPA; what research grants am I eligible for, and what is the deadline to apply?" --trace
python -m unirag.cli ask "Can I renew my hostel room next year?" --student S2025CS003 --trace
python scripts/run_hybrid_demo.py          # six hybrid questions vs expected answers -> eval/results/hybrid_demo.md
```

## Check the database
```bash
docker exec -it unirag-db psql -U unirag_admin -d unirag -c "SELECT grant_code, amount, application_deadline, status FROM grants ORDER BY application_deadline;"
```

## Repository layout
| Path | Contents |
|---|---|
| `db/init/` | Extensions, schema (relational + vector), read-only role, seed loader |
| `data/seed/` | Generated CSVs (one per table) |
| `data/docs/` | 7 policy documents (Markdown) |
| `scripts/generate_data.py` | Synthetic data generator |
| `scripts/ingest.py` | One-command ingestion into pgvector |
| `src/unirag/` | Application code (llm, db, ingest, retrieval, prompts, cli) |
| `src/unirag/prompts/` | Versioned runtime prompts (`name.vN.md`) |
| `scripts/run_hybrid_demo.py` | Runs the six hybrid questions and checks key facts |
| `app/streamlit_app.py` | Demo interface (chat + trace panel) |
| `scripts/warm_cache.py` | Pre-answers all demo examples |
| `tests/` | Unit tests |
| `docs/architecture.md` | Architecture and design decisions |
| `prompts.md` | Prompt engineering log |
