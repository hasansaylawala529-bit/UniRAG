"""Model fallback: an overloaded primary model must not break an answer. No network calls."""
from types import SimpleNamespace

import pytest
import tenacity
from google.genai import errors
from pydantic import BaseModel

from unirag import llm as llm_mod
from unirag.config import Settings


class Out(BaseModel):
    sql: str


def _make(tmp_path, behaviour):
    s = Settings(gemini_api_key="test-key", cache_dir=tmp_path)
    llm = llm_mod.LLM(s)
    calls = []

    def fake_generate(model, contents, config):
        calls.append(model)
        return behaviour(model)

    llm.client = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate))
    return llm, calls, s


@pytest.fixture(autouse=True)
def no_sleep():
    llm_mod.LLM._call_generate.retry.wait = tenacity.wait_none()
    yield


def _overloaded(code=503):
    return errors.ServerError(code, {"error": {"code": code, "message": "high demand", "status": "UNAVAILABLE"}})


def test_falls_back_when_primary_overloaded(tmp_path):
    primary = Settings().gen_model

    def behaviour(model):
        if model == primary:
            raise _overloaded()
        return SimpleNamespace(text='{"sql": "SELECT 1"}')

    llm, calls, s = _make(tmp_path, behaviour)
    backup = s.gen_model_fallbacks[0]
    out = llm.generate("q", model=s.gen_model, schema=Out)
    assert out.sql == "SELECT 1"
    assert calls == [primary] * 3 + [backup]   # 3 retries, then fallback
    assert llm.last_model_used == backup
    assert llm.stats.fallbacks == 1


def test_cached_answer_needs_no_call(tmp_path):
    llm, calls, s = _make(tmp_path, lambda m: SimpleNamespace(text="hello"))
    assert llm.generate("same prompt") == "hello"
    assert llm.generate("same prompt") == "hello"
    assert len(calls) == 1 and llm.stats.cache_hits == 1


def test_non_retryable_error_is_raised_immediately(tmp_path):
    def behaviour(model):
        raise errors.ClientError(400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}})

    llm, calls, s = _make(tmp_path, behaviour)
    with pytest.raises(errors.ClientError):
        llm.generate("q")
    assert calls == [s.gen_model]


def test_all_models_down_gives_clear_error(tmp_path):
    def behaviour(model):
        raise _overloaded(429)

    llm, calls, s = _make(tmp_path, behaviour)
    with pytest.raises(RuntimeError, match="All models unavailable"):
        llm.generate("q")
    assert len(set(calls)) == 3


def test_model_not_available_to_key_falls_back_without_retrying(tmp_path):
    primary = Settings().gen_model

    def behaviour(model):
        if model == primary:
            raise errors.ClientError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
        return SimpleNamespace(text="ok")

    llm, calls, s = _make(tmp_path, behaviour)
    assert llm.generate("q") == "ok"
    assert calls == [primary, s.gen_model_fallbacks[0]]


def test_exhausted_quota_falls_back_without_retrying(tmp_path):
    primary = Settings().gen_model

    def behaviour(model):
        if model == primary:
            raise errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                     "message": "Quota exceeded for metric: requests per day, limit: 0"}})
        return SimpleNamespace(text="ok")

    llm, calls, s = _make(tmp_path, behaviour)
    assert llm.generate("q") == "ok"
    assert calls == [primary, s.gen_model_fallbacks[0]]


def test_rate_limiter_paces_calls(monkeypatch):
    clock = {"t": 1000.0}
    slept = []
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s)))
    rl = llm_mod.RateLimiter({"m": 2}, default_rpm=2)
    rl.acquire("m")
    rl.acquire("m")
    assert slept == []                 # first two calls in the minute go straight through
    rl.acquire("m")
    assert len(slept) == 1 and 59 < slept[0] <= 61   # third waits for the window to clear
