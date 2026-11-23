# Ablation study

22 questions (SQL 10, hybrid 12) from `eval/questions.jsonl`. Each variant switches off one component (`src/unirag/features.py`); everything else is identical, including the model chain and the cache. Expected answers are computed from the database and the policy rules.

| Variant | What changes | Routing | Key facts | SQL facts | Hybrid facts | Fully correct | Verified | Requests / q | Median latency |
|---|---|---|---|---|---|---|---|---|---|
| **Full system** | all components on | 21/22 | 75/81 (93%) | 100% | 90% | 19/22 | 22/22 | 2.82 | 43.7 s |
| − semantic router | every question routed by the LLM planner | 20/22 | 67/81 (83%) | 95% | 78% | 18/22 | 20/22 | 2.77 | 72.8 s |
| − rule engine | LLM-written SQL applies the eligibility rules | 21/22 | 50/81 (62%) | 100% | 47% | 18/22 | 20/22 | 2.95 | 47.5 s |
| − relevance grading | top passages used ungraded | 21/22 | 61/81 (75%) | 100% | 66% | 18/22 | 19/22 | 2.82 | 4.1 s |
| − schema-card retrieval | SQL step sees the full schema | 21/22 | 75/81 (93%) | 100% | 90% | 19/22 | 21/22 | 2.82 | 12.1 s |

Consistency check: with every switch on, 22/22 questions have the same route and key facts as the last `run_eval.py` run (eval/results/raw/).

## Effect of removing each component (vs the full system)

- **− semantic router** (every question routed by the LLM planner): key facts -10 pts, fully correct -1, routing -1, verified -2, requests/q -0.05, median latency +29.1 s
- **− rule engine** (LLM-written SQL applies the eligibility rules): key facts -31 pts, fully correct -1, routing +0, verified -2, requests/q +0.14, median latency +3.8 s
- **− relevance grading** (top passages used ungraded): key facts -17 pts, fully correct -1, routing +0, verified -3, requests/q +0.00, median latency -39.7 s
- **− schema-card retrieval** (SQL step sees the full schema): key facts +0 pts, fully correct +0, routing +0, verified -1, requests/q +0.00, median latency -31.6 s

## Questions that changed

- − semantic router: SQL02 (route hybrid→sql, verifier ✓→⚠); SQL04 (facts 1→0/1); SQL08 (route sql→hybrid); SQL09 (route sql→hybrid); H1 (facts 14→8/18, verifier ✓→⚠); H3 (facts 3→1/3); H4 (facts 2→3/3)
- − rule engine: H1 (facts 14→0/18); H4 (facts 2→3/3); HYB07 (facts 15→5/15, verifier ✓→⚠); HYB08 (facts 5→3/5); HYB10 (verifier ✓→⚠)
- − relevance grading: H1 (facts 14→6/18, verifier ✓→⚠); H4 (facts 2→3/3); HYB07 (facts 15→10/15, verifier ✓→⚠); HYB08 (facts 5→3/5, verifier ✓→⚠)
- − schema-card retrieval: HYB10 (verifier ✓→⚠)

Latency: median over questions with at least one live model call (free tier; dominated by provider load). Requests / q counts generation requests including cached ones, so it reflects the design, not the cache.
