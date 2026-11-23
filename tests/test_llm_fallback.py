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
    s = Settings(gemini_api_key="test-key", groq_api_key="", cache_dir=tmp_path)   # Gemini only
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



# ------------------------------------------------------------------ cross-provider fallback (Groq)
import httpx  # noqa: E402
import groq as groq_sdk  # noqa: E402


def _groq_error(code):
    resp = httpx.Response(code, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    cls = groq_sdk.InternalServerError if code >= 500 else groq_sdk.RateLimitError
    return cls(f"error {code}", response=resp, body=None)


def _make_two_providers(tmp_path, gemini_behaviour, groq_behaviour):
    s = Settings(gemini_api_key="test-key", groq_api_key="test-groq", cache_dir=tmp_path)
    llm = llm_mod.LLM(s)
    calls = []

    def gemini(model, contents, config):
        calls.append(model)
        return gemini_behaviour(model)

    def groq_create(**kw):
        calls.append("groq:" + kw["model"])
        return groq_behaviour(kw)

    llm.client = SimpleNamespace(models=SimpleNamespace(generate_content=gemini))
    llm.groq = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=groq_create)))
    return llm, calls, s


def _groq_reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_gemini_down_groq_answers(tmp_path):
    def gemini(model):
        raise _overloaded()

    llm, calls, s = _make_two_providers(tmp_path, gemini, lambda kw: _groq_reply('{"sql": "SELECT 1"}'))
    out = llm.generate("q", schema=Out)
    assert out.sql == "SELECT 1"
    assert llm.last_model_used == "groq:openai/gpt-oss-120b"
    assert not any(c.startswith("groq:") for c in calls[:-1])          # every Gemini model tried first
    assert calls[-1] == "groq:openai/gpt-oss-120b"


def test_groq_request_uses_strict_closed_schema(tmp_path):
    seen = {}

    def groq_create(kw):
        seen.update(kw)
        return _groq_reply('{"sql": "SELECT 1"}')

    llm, calls, s = _make_two_providers(tmp_path, lambda m: (_ for _ in ()).throw(_overloaded()), groq_create)
    llm.generate("q", schema=Out)
    js = seen["response_format"]["json_schema"]
    assert js["strict"] is True and js["schema"]["additionalProperties"] is False
    assert js["schema"]["required"] == ["sql"] and "$defs" not in js["schema"]


def test_invalid_json_from_groq_moves_to_next_model(tmp_path):
    def groq(kw):
        if kw["model"] == "openai/gpt-oss-120b":
            return _groq_reply("not json at all")
        return _groq_reply('{"sql": "SELECT 2"}')

    llm, calls, s = _make_two_providers(tmp_path, lambda m: (_ for _ in ()).throw(_overloaded()), groq)
    assert llm.generate("q", schema=Out).sql == "SELECT 2"
    assert llm.last_model_used == "groq:openai/gpt-oss-20b"


def test_all_providers_down_gives_clear_error(tmp_path):
    def groq(kw):
        raise _groq_error(503)

    llm, calls, s = _make_two_providers(tmp_path, lambda m: (_ for _ in ()).throw(_overloaded()), groq)
    with pytest.raises(RuntimeError, match="All models unavailable"):
        llm.generate("q", schema=Out)
    assert "groq:openai/gpt-oss-20b" in calls


def test_without_groq_key_chain_is_gemini_only(tmp_path):
    llm, calls, s = _make(tmp_path, lambda m: SimpleNamespace(text="x"))
    assert not any(m.startswith("groq:") for m in llm._model_chain(s.gen_model))


def test_token_budget_paces_groq(monkeypatch):
    clock = {"t": 0.0}
    slept = []
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(llm_mod.time, "sleep", lambda x: (slept.append(x), clock.__setitem__("t", clock["t"] + x)))
    rl = llm_mod.RateLimiter({"g": 30}, default_rpm=30, tpm_for={"g": 8000})
    rl.acquire("g", tokens=5000)
    rl.acquire("g", tokens=5000)          # 10,000 > 8,000 tokens in the minute -> waits
    assert len(slept) == 1
