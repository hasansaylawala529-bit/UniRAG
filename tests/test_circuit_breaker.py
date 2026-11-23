"""Circuit breaker: a model that just failed with an overload error is skipped by the following calls.
No network calls."""
from types import SimpleNamespace

import pytest
import tenacity
from google.genai import errors

from unirag import llm as llm_mod
from unirag.config import Settings


def _make(tmp_path, behaviour, seconds=60.0):
    s = Settings(gemini_api_key="test-key", groq_api_key="", cache_dir=tmp_path, circuit_breaker_seconds=seconds)
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


def _primary_down(primary):
    def behaviour(model):
        if model == primary:
            raise _overloaded()
        return SimpleNamespace(text="ok")
    return behaviour


def test_overloaded_model_is_skipped_on_the_next_call(tmp_path):
    s0 = Settings()
    llm, calls, s = _make(tmp_path, _primary_down(s0.gen_model))
    backup = s.gen_model_fallbacks[0]
    assert llm.generate("first") == "ok"
    assert calls == [s.gen_model] * 3 + [backup]          # retried, then fell back, circuit now open
    calls.clear()
    assert llm.generate("second") == "ok"                 # different prompt, so no cache hit
    assert calls == [backup]                              # primary skipped: no retries paid again
    assert len(llm.skip_events) == 1 and llm.skip_events[0].startswith(s.gen_model)
    assert "circuit open" in llm.skip_events[0]


def test_model_is_tried_again_after_the_window(tmp_path):
    s0 = Settings()
    llm, calls, s = _make(tmp_path, _primary_down(s0.gen_model))
    llm.generate("first")
    llm.breaker.open_until[s.gen_model] = 0.0             # window elapsed
    calls.clear()
    llm.generate("second")
    assert calls[0] == s.gen_model                        # primary is tried (and fails) again


def test_success_closes_the_circuit(tmp_path):
    llm, calls, s = _make(tmp_path, lambda m: SimpleNamespace(text="ok"))
    llm.breaker.trip(s.gen_model)
    llm.breaker.open_until[s.gen_model] = 0.0
    llm.generate("q")
    assert s.gen_model not in llm.breaker.open_until


def test_all_circuits_open_still_tries_every_model(tmp_path):
    llm, calls, s = _make(tmp_path, lambda m: SimpleNamespace(text="ok"))
    for m in llm._model_chain(s.gen_model):
        llm.breaker.trip(m)
    assert llm.generate("q") == "ok"                      # never fail without trying
    assert calls == [s.gen_model]


def test_client_errors_do_not_open_the_circuit(tmp_path):
    def behaviour(model):
        raise errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})

    llm, calls, s = _make(tmp_path, behaviour)
    with pytest.raises(errors.ClientError):
        llm.generate("q")
    assert not llm.breaker.is_open(s.gen_model)


def test_breaker_can_be_disabled(tmp_path):
    s0 = Settings()
    llm, calls, s = _make(tmp_path, _primary_down(s0.gen_model), seconds=0)
    llm.generate("first")
    calls.clear()
    llm.generate("second")
    assert calls[0] == s.gen_model and not llm.skip_events


def test_trips_on_overload_and_network_errors_only():
    assert llm_mod._trips_breaker(_overloaded(503))
    assert llm_mod._trips_breaker(_overloaded(504))
    assert not llm_mod._trips_breaker(errors.ClientError(404, {"error": {"code": 404, "message": "not found"}}))

    class ReadTimeout(Exception):
        pass
    assert llm_mod._trips_breaker(ReadTimeout())
    assert not llm_mod._trips_breaker(ValueError("bad json"))
