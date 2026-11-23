"""Run every demo example once so the live demo answers instantly from the on-disk cache.

    python scripts/warm_cache.py

Personal examples are run both logged out (shows the log-in message) and as a demo student.
Uses roughly 30 model requests the first time and none afterwards.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from unirag.examples import DEMO_STUDENTS, all_examples  # noqa: E402
from unirag.graph import ask  # noqa: E402

if __name__ == "__main__":
    runs = [(q, None) for q in all_examples()] + [("What are my overdue fees?", "S2026CS012"),
                                                   ("Can I renew my hostel room next year?", DEMO_STUDENTS[2])]
    for q, sid in runs:
        t0 = time.perf_counter()
        try:
            r = ask(q, student_id=sid)
            print(f"{time.perf_counter() - t0:5.1f}s  {r.route:<12} req={r.gen_requests}  api={r.llm_calls}  "
                  f"{'ok ' if r.verification['ok'] else 'FLAG'}  {q[:70]}" + (f"  [{sid}]" if sid else ""))
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {type(e).__name__}: {e}  ({q[:60]})")
    print("Done. Demo questions now answer from cache.")
