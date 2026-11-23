"""Single Gemini client wrapper: generation (text or JSON-schema output) and embeddings.

- Retry with exponential backoff and jitter on rate limits (429) and transient server errors.
- On-disk cache keyed by model + full input, so repeated runs (re-ingest, evaluation) cost nothing.
- Embeddings are L2-normalised: Gemini recommends this when output_dimensionality < 3072.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from .config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}


QUOTA_EXHAUSTED_HINTS = ("limit: 0", "per day", "perday", "exceeded your current quota", "free_tier")


def _is_quota_exhausted(exc: BaseException) -> bool:
    """A 429 that won't clear by waiting a few seconds (daily quota used up, or model not in your tier)."""
    return getattr(exc, "code", None) == 429 and any(h in str(exc).lower() for h in QUOTA_EXHAUSTED_HINTS)


def _is_retryable(exc: BaseException) -> bool:
    """Worth retrying the SAME model after a short wait."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code in RETRYABLE_CODES and not _is_quota_exhausted(exc)
    # network-level failures (timeouts, connection resets) have no HTTP code
    return exc.__class__.__name__ in {"ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError",
                                      "ReadError", "TimeoutException", "WriteTimeout", "PoolTimeout"}


def _should_fall_back(exc: BaseException) -> bool:
    """Worth trying the NEXT model: transient overload, quota exhausted, or model not available to this key."""
    code = getattr(exc, "code", None)
    return _is_retryable(exc) or _is_quota_exhausted(exc) or code in (403, 404)


def _log_retry(state) -> None:  # tenacity RetryCallState
    exc = state.outcome.exception() if state.outcome else None
    model = state.args[1] if len(state.args) > 1 else "?"
    wait = getattr(state.next_action, "sleep", 0) or 0
    print(f"[llm] {model}: {getattr(exc, 'code', type(exc).__name__)} - retry {state.attempt_number} "
          f"in {wait:.0f}s", file=sys.stderr, flush=True)


# Generation: a few quick retries per model, then fall back to the next model in the chain.
_retry_gen = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential_jitter(initial=1.5, max=12),
    stop=stop_after_attempt(3),
    before_sleep=_log_retry,
    reraise=True,
)
# Embeddings have no fallback model (vectors must come from one model), so retry longer.
_retry_embed = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential_jitter(initial=2, max=40),
    stop=stop_after_attempt(6),
    before_sleep=_log_retry,
    reraise=True,
)


class RateLimiter:
    """Spaces requests per model so a free-tier RPM limit is never exceeded (sliding 60 s window)."""

    def __init__(self, rpm_for: dict[str, int], default_rpm: int):
        self.rpm_for, self.default_rpm = rpm_for, default_rpm
        self.sent: dict[str, list[float]] = {}
        self.waited_seconds = 0.0

    def acquire(self, model: str) -> None:
        rpm = max(1, self.rpm_for.get(model, self.default_rpm))
        now = time.monotonic()
        window = [t for t in self.sent.get(model, []) if now - t < 60.0]
        if len(window) >= rpm:
            wait = 60.0 - (now - window[0]) + 0.2
            print(f"[llm] {model}: pacing for free-tier limit ({rpm}/min), waiting {wait:.0f}s",
                  file=sys.stderr, flush=True)
            time.sleep(wait)
            self.waited_seconds += wait
            now = time.monotonic()
            window = [t for t in window if now - t < 60.0]
        window.append(now)
        self.sent[model] = window


class DiskCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: Any) -> str:
        raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _path(self, k: str) -> Path:
        return self.root / k[:2] / f"{k}.json"

    def get(self, k: str) -> Any | None:
        p = self._path(k)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def set(self, k: str, value: Any) -> None:
        p = self._path(k)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@dataclass
class LLMStats:
    gen_calls: int = 0          # generation API calls actually sent
    gen_requests: int = 0       # generation requests made by the pipeline (sent or served from cache)
    embed_calls: int = 0
    cache_hits: int = 0
    gen_seconds: float = 0.0
    fallbacks: int = 0
    by_model: dict[str, int] = field(default_factory=dict)


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class LLM:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        if not self.s.gemini_api_key or self.s.gemini_api_key.startswith("your-"):
            raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        from google import genai  # imported lazily so tests without the SDK still run

        self._genai = genai
        from google.genai import types

        # 60 s timeout per request; the SDK's own retry is disabled because we retry and fall back here,
        # and two retry layers multiply the waiting time.
        self.client = genai.Client(api_key=self.s.gemini_api_key, http_options=types.HttpOptions(
            timeout=60_000, retry_options=types.HttpRetryOptions(attempts=1)))
        self.gen_cache = DiskCache(self.s.cache_dir / "gen")
        self.emb_cache = DiskCache(self.s.cache_dir / "emb")
        self.stats = LLMStats()
        self.limiter = RateLimiter(self.s.model_rpm, self.s.default_rpm)
        self.last_model_used: str | None = None
        self.last_fallback_errors: list[str] = []

    # ------------------------------------------------------------------ generation
    def generate(self, prompt: str, *, system: str | None = None, model: str | None = None,
                 schema: type[T] | None = None, temperature: float = 0.0) -> str | T:
        """Generate text, or a validated pydantic object when `schema` is given."""
        from google.genai import types

        model = model or self.s.gen_model
        self.stats.gen_requests += 1
        schema_json = schema.model_json_schema() if schema else None
        k = DiskCache.key("gen", model, system, prompt, schema_json, temperature)
        cached = self.gen_cache.get(k)
        self.last_fallback_errors = []
        if cached is not None:
            self.stats.cache_hits += 1
            text = cached["text"]
            self.last_model_used = cached.get("model", model)
        else:
            cfg: dict[str, Any] = {"temperature": temperature}
            if system:
                cfg["system_instruction"] = system
            if schema:
                cfg["response_mime_type"] = "application/json"
                cfg["response_json_schema"] = schema_json
            if self.s.thinking_level:
                cfg["thinking_config"] = types.ThinkingConfig(thinking_level=self.s.thinking_level)
            config = types.GenerateContentConfig(**cfg)
            text, used = None, None
            t0 = time.perf_counter()
            for candidate in self._model_chain(model):
                try:
                    text, used = self._call_generate(candidate, prompt, config), candidate
                    break
                except Exception as e:  # noqa: BLE001 - decide below whether to fall back
                    if not _should_fall_back(e):
                        raise
                    self.last_fallback_errors.append(f"{candidate}: {getattr(e, 'code', '')} {type(e).__name__}")
                    print(f"[llm] {candidate} unavailable ({getattr(e, 'code', type(e).__name__)}), "
                          f"trying next model", file=sys.stderr, flush=True)
            if text is None:
                raise RuntimeError("All models unavailable: " + "; ".join(self.last_fallback_errors))
            if used != model:
                self.stats.fallbacks += 1
            self.stats.gen_seconds += time.perf_counter() - t0
            self.stats.gen_calls += 1
            self.stats.by_model[used] = self.stats.by_model.get(used, 0) + 1
            self.last_model_used = used
            self.gen_cache.set(k, {"text": text, "model": used})
        if schema:
            return schema.model_validate_json(_strip_fences(text))
        return text

    def _model_chain(self, model: str) -> list[str]:
        if model == self.s.gen_model:
            extra = self.s.gen_model_fallbacks
        elif model == self.s.fast_model:
            extra = self.s.fast_model_fallbacks
        else:
            extra = ()
        chain = [model]
        for m in extra:
            if m not in chain:
                chain.append(m)
        return chain

    @_retry_gen
    def _call_generate(self, model: str, prompt: str, config: Any) -> str:
        self.limiter.acquire(model)
        resp = self.client.models.generate_content(model=model, contents=prompt, config=config)
        if not resp.text:
            raise RuntimeError(f"Empty response from {model}")
        return resp.text

    # ------------------------------------------------------------------ embeddings
    def embed(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT", batch_size: int = 50) -> list[list[float]]:
        """Embed texts (cached per text). task_type: RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY, SEMANTIC_SIMILARITY."""
        out: list[list[float] | None] = [None] * len(texts)
        todo: list[tuple[int, str, str]] = []
        for i, t in enumerate(texts):
            k = DiskCache.key("emb", self.s.embed_model, self.s.embed_dim, task_type, t)
            c = self.emb_cache.get(k)
            if c is not None:
                self.stats.cache_hits += 1
                out[i] = c
            else:
                todo.append((i, t, k))
        for start in range(0, len(todo), batch_size):
            batch = todo[start:start + batch_size]
            vecs = self._call_embed([t for _, t, _ in batch], task_type)
            self.stats.embed_calls += 1
            for (i, _, k), v in zip(batch, vecs):
                v = _normalise(v)
                self.emb_cache.set(k, v)
                out[i] = v
        return [v for v in out]  # type: ignore[misc]

    def embed_one(self, text: str, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
        return self.embed([text], task_type=task_type)[0]

    @_retry_embed
    def _call_embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        self.limiter.acquire(self.s.embed_model)

        resp = self.client.models.embed_content(
            model=self.s.embed_model, contents=texts,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.s.embed_dim))
        vecs = [list(e.values) for e in resp.embeddings]
        if len(vecs) != len(texts) or any(len(v) != self.s.embed_dim for v in vecs):
            raise RuntimeError("Embedding count or dimension mismatch; check EMBED_MODEL / EMBED_DIM.")
        return vecs


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    return t.strip()


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM()
    return _llm


def set_llm(llm: Any) -> None:
    """Swap in another client (used by offline tests)."""
    global _llm
    _llm = llm
