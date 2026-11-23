"""Check which Gemini models your API key can actually use, and how fast.

    python scripts/check_models.py

One request per model, no retries, 30 s timeout. Prints status, latency and the reason for any
failure, then suggests .env settings. Uses ~10 requests of your quota.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from unirag.config import get_settings  # noqa: E402

GEN_CANDIDATES = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                  "gemini-2.5-flash"]
FAST_CANDIDATES = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]


def short(e: Exception) -> str:
    msg = str(e).replace("\n", " ")
    return msg[:160] + ("…" if len(msg) > 160 else "")


def main() -> None:
    s = get_settings()
    if not s.gemini_api_key or s.gemini_api_key.startswith("your-"):
        sys.exit("GEMINI_API_KEY is not set in .env")
    client = genai.Client(api_key=s.gemini_api_key, http_options=types.HttpOptions(
        timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)))

    ok_gen, ok_fast = [], []
    print(f"{'model':<26}{'result':<8}{'seconds':>8}  detail")
    print("-" * 90)
    for group, models, ok in (("gen", GEN_CANDIDATES, ok_gen), ("fast", FAST_CANDIDATES, ok_fast)):
        for m in models:
            t0 = time.perf_counter()
            try:
                r = client.models.generate_content(model=m, contents="Reply with the single word: OK",
                                                   config=types.GenerateContentConfig(temperature=0))
                dt = time.perf_counter() - t0
                print(f"{m:<26}{'OK':<8}{dt:>8.1f}  {(r.text or '').strip()[:30]}")
                ok.append((dt, m))
            except Exception as e:  # noqa: BLE001
                dt = time.perf_counter() - t0
                code = getattr(e, "code", type(e).__name__)
                print(f"{m:<26}{str(code):<8}{dt:>8.1f}  {short(e)}")

    t0 = time.perf_counter()
    try:
        r = client.models.embed_content(model=s.embed_model, contents=["test"],
                                        config=types.EmbedContentConfig(output_dimensionality=s.embed_dim))
        print(f"{s.embed_model:<26}{'OK':<8}{time.perf_counter() - t0:>8.1f}  dim={len(r.embeddings[0].values)}")
    except Exception as e:  # noqa: BLE001
        print(f"{s.embed_model:<26}{str(getattr(e, 'code', '?')):<8}{time.perf_counter() - t0:>8.1f}  {short(e)}")

    if s.groq_api_key and not s.groq_api_key.startswith("your-"):
        try:
            from groq import Groq
            gclient = Groq(api_key=s.groq_api_key, max_retries=0, timeout=30.0)
            for m in s.groq_fallback_models:
                t0 = time.perf_counter()
                try:
                    r = gclient.chat.completions.create(model=m.removeprefix("groq:"), temperature=0, messages=[
                        {"role": "user", "content": "Reply with the single word: OK"}])
                    print(f"{m:<26}{'OK':<8}{time.perf_counter() - t0:>8.1f}  {(r.choices[0].message.content or '').strip()[:30]}")
                except Exception as e:  # noqa: BLE001
                    print(f"{m:<26}{str(getattr(e, 'status_code', '?')):<8}{time.perf_counter() - t0:>8.1f}  {short(e)}")
        except ImportError:
            print("groq package not installed: pip install groq")
    else:
        print("(GROQ_API_KEY not set: Groq fallback disabled)")

    print("\nSuggested .env (fastest working models first):")
    # any working Gemini text model can serve either role; fastest first
    working = [m for _, m in sorted(ok_gen + ok_fast)]
    if working:
        print(f"GEN_MODEL={working[0]}\nGEN_MODEL_FALLBACKS={','.join(working[1:])}")
        print(f"FAST_MODEL={working[0]}\nFAST_MODEL_FALLBACKS={','.join(working[1:])}")
    else:
        print("# No Gemini generation model worked right now - the Groq fallback (if configured) will answer")
    print("\nYour limits per model: https://aistudio.google.com/rate-limit")


if __name__ == "__main__":
    main()
