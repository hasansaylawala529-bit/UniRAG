"""One-command, re-runnable ingestion: chunk -> cards -> entities -> exemplars -> embed -> load.

Re-running replaces the contents of the four vector tables inside one transaction. Embeddings are
cached on disk, so a re-run after a small document edit only embeds the changed chunks.
"""
from __future__ import annotations

import json
import time

from psycopg.types.json import Jsonb

from ..config import get_settings
from ..db import VECTOR_TABLES, connect
from ..llm import get_llm
from .chunk_docs import chunk_all
from .entity_text import build_entity_texts
from .exemplars import EXEMPLARS
from .schema_cards import build_cards


def run_ingest(verbose: bool = True) -> dict:
    s = get_settings()
    llm = get_llm()
    t0 = time.perf_counter()
    log = print if verbose else (lambda *a, **k: None)

    chunks = chunk_all(s.docs_dir)
    log(f"[1/5] Chunked {len(chunks)} sections from {len({c.doc_id for c in chunks})} documents")

    with connect(admin=True) as conn:
        cards = build_cards(conn)
        log(f"[2/5] Built {len(cards)} schema cards from information_schema + data profiles")
        entities = build_entity_texts(conn)
        log(f"[3/5] Built {len(entities)} entity texts (grants, courses)")
        log(f"[4/5] Loaded {len(EXEMPLARS)} router exemplars")

        log("[5/5] Embedding ...")
        chunk_vecs = llm.embed([c.content for c in chunks], task_type="RETRIEVAL_DOCUMENT")
        card_vecs = llm.embed([c.card_text for c in cards], task_type="RETRIEVAL_DOCUMENT")
        ent_vecs = llm.embed([e.text for e in entities], task_type="RETRIEVAL_DOCUMENT")
        ex_vecs = llm.embed([q for q, _ in EXEMPLARS], task_type="SEMANTIC_SIMILARITY")

        with conn.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(VECTOR_TABLES) + " RESTART IDENTITY")
            cur.executemany(
                """INSERT INTO doc_chunks (doc_id, doc_title, section, content, embedding, doc_type, department,
                                          year_applicability, effective_date, grant_codes, metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(c.doc_id, c.doc_title, c.section, c.content, v, c.doc_type, c.department,
                  c.year_applicability, c.effective_date, c.grant_codes, Jsonb(c.metadata))
                 for c, v in zip(chunks, chunk_vecs)])
            cur.executemany("INSERT INTO schema_cards (table_name, card_text, embedding) VALUES (%s,%s,%s)",
                            [(c.table_name, c.card_text, v) for c, v in zip(cards, card_vecs)])
            cur.executemany("INSERT INTO entity_texts (entity_type, entity_key, text, embedding) VALUES (%s,%s,%s,%s)",
                            [(e.entity_type, e.entity_key, e.text, v) for e, v in zip(entities, ent_vecs)])
            cur.executemany("INSERT INTO router_exemplars (question, route, embedding) VALUES (%s,%s,%s)",
                            [(q, r, v) for (q, r), v in zip(EXEMPLARS, ex_vecs)])

    summary = {
        "chunks": len(chunks), "schema_cards": len(cards), "entities": len(entities),
        "exemplars": len(EXEMPLARS), "embed_api_calls": llm.stats.embed_calls,
        "cache_hits": llm.stats.cache_hits, "seconds": round(time.perf_counter() - t0, 1),
    }
    log("Done: " + json.dumps(summary))
    return summary
