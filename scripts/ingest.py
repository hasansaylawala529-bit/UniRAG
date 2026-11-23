"""Ingest everything into pgvector in one command:  python scripts/ingest.py

Prerequisites: `docker compose up -d` (database with seed data) and GEMINI_API_KEY in .env.
Safe to re-run: vector tables are rebuilt and embeddings come from the on-disk cache.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unirag.ingest.pipeline import run_ingest  # noqa: E402

if __name__ == "__main__":
    run_ingest()
