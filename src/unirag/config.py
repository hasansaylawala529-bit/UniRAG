"""Settings loaded from .env (project root) with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _csv(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


def _rpm_map(value: str) -> dict[str, int]:
    out = {}
    for item in _csv(value):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = int(v)
    return out


@dataclass(frozen=True, eq=False)
class Settings:
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    # Free-tier configuration: Flash-Lite does the work, 3.6 Flash is the last-resort backup.
    gen_model: str = field(default_factory=lambda: os.getenv("GEN_MODEL", "gemini-3.1-flash-lite"))
    fast_model: str = field(default_factory=lambda: os.getenv("FAST_MODEL", "gemini-3.1-flash-lite"))
    embed_model: str = field(default_factory=lambda: os.getenv("EMBED_MODEL", "gemini-embedding-001"))
    embed_dim: int = field(default_factory=lambda: int(os.getenv("EMBED_DIM", "768")))
    # Tried in order when a model stays overloaded (503) or rate-limited (429) after retries.
    gen_model_fallbacks: tuple[str, ...] = field(default_factory=lambda: _csv(
        os.getenv("GEN_MODEL_FALLBACKS", "gemini-3.5-flash-lite,gemini-3.6-flash")))
    fast_model_fallbacks: tuple[str, ...] = field(default_factory=lambda: _csv(
        os.getenv("FAST_MODEL_FALLBACKS", "gemini-3.5-flash-lite")))
    # Requests-per-minute budget per model; calls are spaced to stay under it (free-tier friendly).
    model_rpm: dict[str, int] = field(default_factory=lambda: _rpm_map(os.getenv(
        "MODEL_RPM", "gemini-3.1-flash-lite=14,gemini-3.5-flash-lite=14,gemini-3.6-flash=4,"
                     "gemini-3.8-flash=4,gemini-embedding-001=90")))
    default_rpm: int = field(default_factory=lambda: int(os.getenv("DEFAULT_RPM", "4")))
    thinking_level: str = field(default_factory=lambda: os.getenv("THINKING_LEVEL", ""))  # optional, e.g. "low"

    db_admin_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL_ADMIN", "postgresql://unirag_admin:unirag_admin_pw@localhost:5433/unirag"))
    db_reader_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL_READER", "postgresql://unirag_reader:unirag_reader_pw@localhost:5433/unirag"))

    # Reference date used instead of CURRENT_DATE so answers and evaluation are reproducible.
    as_of_date: date = field(default_factory=lambda: date.fromisoformat(os.getenv("AS_OF_DATE", "2026-11-23")))

    cache_dir: Path = field(default_factory=lambda: ROOT / os.getenv("CACHE_DIR", ".cache"))
    docs_dir: Path = ROOT / "data" / "docs"
    sql_row_limit: int = 200


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
