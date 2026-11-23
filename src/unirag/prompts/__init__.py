"""Versioned runtime prompts. Files are named <name>.v<N>.md; the highest N is used unless pinned.

Each prompt uses {placeholders} filled with str.format. The returned id (e.g. "sql_generate.v1")
is recorded in every trace, so each answer can be tied to the exact prompt version that produced it.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_prompt(name: str, version: int | None = None) -> tuple[str, str]:
    files = {}
    for p in PROMPT_DIR.glob(f"{name}.v*.md"):
        m = re.fullmatch(rf"{re.escape(name)}\.v(\d+)\.md", p.name)
        if m:
            files[int(m.group(1))] = p
    if not files:
        raise FileNotFoundError(f"No prompt named {name!r} in {PROMPT_DIR}")
    v = version if version is not None else max(files)
    return files[v].read_text(encoding="utf-8"), f"{name}.v{v}"


def render(name: str, version: int | None = None, **kwargs: object) -> tuple[str, str]:
    template, pid = load_prompt(name, version)
    return template.format(**kwargs), pid
