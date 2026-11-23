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


class ProviderError(Exception):
    """Uniform error for non-Gemini providers: `code` is the HTTP status (503 for network failures)."""

    def __init__(self, code: int, message: str, fallback: bool = False):
        super().__init__(f"{code} {message}")
        self.code, self.fallback = code, fallback


def _should_fall_back(exc: BaseException) -> bool:
    """Worth trying the NEXT model: transient overload, quota exhausted, model not available to this key,
    or a provider-specific request problem (e.g. a schema feature one provider does not support)."""
    code = getattr(exc, "code", None)
    return (_is_retryable(exc) or _is_quota_exhausted(exc) or code in (403, 404)
            or bool(getattr(exc, "fallback", False)))


def strict_json_schema(schema: dict) -> dict:
    """Pydantic schema -> self-contained schema for strict structured output: $refs inlined, titles and
    defaults removed, every object closed (additionalProperties false) with all properties required."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            out = {k: walk(v) for k, v in node.items() if k not in ("$defs", "title", "default")}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


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

    def __init__(self, rpm_for: dict[str, int], default_rpm: int, tpm_for: dict[str, int] | None = None):
        self.rpm_for, self.default_rpm, self.tpm_for = rpm_for, default_rpm, tpm_for or {}
        self.sent: dict[str, list[tuple[float, int]]] = {}
        self.waited_seconds = 0.0

    def acquire(self, model: str, tokens: int = 0) -> None:
        rpm = max(1, self.rpm_for.get(model, self.default_rpm))
        tpm = self.tpm_for.get(model)
        while True:
            now = time.monotonic()
            window = [(t, n) for t, n in self.sent.get(model, []) if now - t < 60.0]
            over_rpm = len(window) >= rpm
            over_tpm = bool(tpm) and window and sum(n for _, n in window) + tokens > tpm
            if not (over_rpm or over_tpm):
                break
            wait = 60.0 - (now - window[0][0]) + 0.2
            why = f"{rpm}/min" if over_rpm else f"{tpm} tokens/min"
            print(f"[llm] {model}: pacing for free-tier limit ({why}), waiting {wait:.0f}s",
                  file=sys.stderr, flush=True)
            time.sleep(wait)
            self.waited_seconds += wait
            self.sent[model] = window
        window.append((now, tokens))
        self.sent[model] = window


class CircuitBreaker:
    """Skip a model for a while after it fails with an overload error, instead of retrying it on every call.

    Without it, every call during a provider outage pays the full retry cost (about 5-15 s) on each overloaded
    model before reaching one that works. With it, that cost is paid once per model per window.
    """

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.open_until: dict[str, float] = {}

    def is_open(self, model: str) -> float:
        """Seconds left while the circuit is open (model being skipped); 0 when the model may be tried."""
        left = self.open_until.get(model, 0.0) - time.monotonic()
        return left if left > 0 else 0.0

    def trip(self, model: str) -> None:
        if self.seconds > 0:
            self.open_until[model] = time.monotonic() + self.seconds

    def reset(self, model: str) -> None:
        self.open_until.pop(model, None)


def _trips_breaker(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code in (500, 502, 503, 504) or _is_quota_exhausted(exc)
    return exc.__class__.__name__ in {"ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError",
                                      "ReadError", "TimeoutException", "WriteTimeout", "PoolTimeout"}


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
        self.limiter = RateLimiter(self.s.model_rpm, self.s.default_rpm, self.s.model_tpm)
        self.groq = None
        if self.s.groq_api_key and not self.s.groq_api_key.startswith("your-"):
            try:
                from groq import Groq

                self.groq = Groq(api_key=self.s.groq_api_key, max_retries=0, timeout=60.0)
            except ImportError:
                print("[llm] GROQ_API_KEY is set but the groq package is missing (pip install groq)",
                      file=sys.stderr)
        self.last_model_used: str | None = None
        self.last_fallback_errors: list[str] = []
        self.breaker = CircuitBreaker(self.s.circuit_breaker_seconds)
        self.skip_events: list[str] = []          # "model (circuit open, Ns left)", appended per skip

    # ------------------------------------------------------------------ generation
    def generate(self, prompt: str, *, system: str | None = None, model: str | None = None,
                 schema: type[T] | None = None, temperature: float = 0.0) -> str | T:
        """Generate text, or a validated pydantic object when `schema` is given.

        Tries the requested model, then its fallback chain (other Gemini models, then Groq). A reply that
        does not validate against the schema counts as a failure of that model and moves on to the next.
        """
        model = model or self.s.gen_model
        self.stats.gen_requests += 1
        schema_json = schema.model_json_schema() if schema else None
        k = DiskCache.key("gen", model, system, prompt, schema_json, temperature)
        self.last_fallback_errors = []
        cached = self.gen_cache.get(k)
        if cached is not None:
            try:
                out = self._parse(cached["text"], schema)
                self.stats.cache_hits += 1
                self.last_model_used = cached.get("model", model)
                return out
            except ValueError:
                pass                                   # an invalid cached reply is regenerated
        request = {"system": system, "temperature": temperature, "schema": schema_json}
        t0 = time.perf_counter()
        chain = self._model_chain(model)
        available = [m for m in chain if not self.breaker.is_open(m)]
        for m in chain:
            if m not in available:
                ev = f"{m} (circuit open, {self.breaker.is_open(m):.0f}s left)"
                self.skip_events.append(ev)
                print(f"[llm] skipping {ev}", file=sys.stderr, flush=True)
        # if every model is in its cool-down window, try them all anyway rather than fail without trying
        for candidate in (available or chain):
            try:
                text = self._call_generate(candidate, prompt, request)
                out = self._parse(text, schema)
            except ValueError as e:                    # pydantic ValidationError / bad JSON
                reason = f"{candidate}: invalid JSON for {schema.__name__ if schema else 'text'}"
                self.last_fallback_errors.append(reason)
                print(f"[llm] {reason}, trying next model", file=sys.stderr, flush=True)
                continue
            except Exception as e:  # noqa: BLE001 - decide whether to fall back
                if not _should_fall_back(e):
                    raise
                if _trips_breaker(e):
                    self.breaker.trip(candidate)
                self.last_fallback_errors.append(f"{candidate}: {getattr(e, 'code', '')} {type(e).__name__}")
                print(f"[llm] {candidate} unavailable ({getattr(e, 'code', type(e).__name__)}), "
                      f"trying next model", file=sys.stderr, flush=True)
                continue
            self.breaker.reset(candidate)
            if candidate != model:
                self.stats.fallbacks += 1
            self.stats.gen_seconds += time.perf_counter() - t0
            self.stats.gen_calls += 1
            self.stats.by_model[candidate] = self.stats.by_model.get(candidate, 0) + 1
            self.last_model_used = candidate
            self.gen_cache.set(k, {"text": text, "model": candidate})
            return out
        raise RuntimeError("All models unavailable: " + "; ".join(self.last_fallback_errors))

    @staticmethod
    def _parse(text: str, schema: type[T] | None) -> str | T:
        if schema:
            return schema.model_validate_json(_strip_fences(text))
        if not text or not text.strip():
            raise ValueError("empty reply")
        return text

    def _model_chain(self, model: str) -> list[str]:
        if model == self.s.gen_model:
            extra = self.s.gen_model_fallbacks
        elif model == self.s.fast_model:
            extra = self.s.fast_model_fallbacks
        else:
            extra = ()
        chain = [model]
        for m in list(extra) + list(self.s.groq_fallback_models):
            if m not in chain:
                chain.append(m)
        # Groq entries are used only when a Groq key is configured
        return [m for m in chain if not m.startswith("groq:") or self.groq is not None]

    @_retry_gen
    def _call_generate(self, model: str, prompt: str, request: dict) -> str:
        if model.startswith("groq:"):
            return self._groq_generate(model, prompt, request)
        return self._gemini_generate(model, prompt, request)

    def _gemini_generate(self, model: str, prompt: str, request: dict) -> str:
        from google.genai import types

        cfg: dict[str, Any] = {"temperature": request["temperature"]}
        if request["system"]:
            cfg["system_instruction"] = request["system"]
        if request["schema"]:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = request["schema"]
        if self.s.thinking_level:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=self.s.thinking_level)
        model = model.removeprefix("gemini:")
        self.limiter.acquire(model)
        resp = self.client.models.generate_content(model=model, contents=prompt,
                                                   config=types.GenerateContentConfig(**cfg))
        if not resp.text:
            raise RuntimeError(f"Empty response from {model}")
        return resp.text

    def _groq_generate(self, model: str, prompt: str, request: dict) -> str:
        import groq

        messages = ([{"role": "system", "content": request["system"]}] if request["system"] else []) \
            + [{"role": "user", "content": prompt}]
        kwargs: dict[str, Any] = {"model": model.removeprefix("groq:"), "messages": messages,
                                  "temperature": request["temperature"]}
        if request["schema"]:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "response", "strict": True, "schema": strict_json_schema(request["schema"])}}
        self.limiter.acquire(model, tokens=len(prompt) // 4 + 1500)   # prompt estimate + reply allowance
        try:
            resp = self.groq.chat.completions.create(**kwargs)
        except groq.APIStatusError as e:
            raise ProviderError(e.status_code, str(e)[:200], fallback=e.status_code in (400, 413, 422)) from e
        except (groq.APIConnectionError, groq.APITimeoutError) as e:
            raise ProviderError(503, f"connection: {e}") from e
        text = resp.choices[0].message.content if resp.choices else None
        if not text:
            raise ProviderError(502, "empty response", fallback=True)
        return text

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
