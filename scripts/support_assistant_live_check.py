"""Ask the live model the example questions and apply the string rules.

    GEMINI_API_KEY=... python scripts/support_assistant_live_check.py [exchange_id ...]

Uses the same prompt the route builds (``build_system_prompt``) and the same
rules as ``tests/test_support_assistant.py``: no tab the account does not have,
the required words, no marker in the text, and a ticket draft of the right type
where the fixture expects one.  Prints every answer; exits 1 on a problem.  A
model reply varies from run to run, so a failure here is a prompt to read the
answer, not proof of a regression.  It makes one provider call per exchange
(about nine) and writes nothing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from motor_ai_sim import support_context as C                      # noqa: E402
from motor_ai_sim.routes import support                            # noqa: E402
from tests import support_assistant_rules as R                     # noqa: E402


def main(argv: list[str]) -> int:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    if not key:
        print("GEMINI_API_KEY is not set - nothing to check against.")
        return 2
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    wanted = set(argv)
    failed = 0
    for ex in R.exchanges():
        if wanted and ex["id"] not in wanted:
            continue
        role = "admin" if ex["role"] == "admin" else "user"
        sp = support.build_system_prompt(role, anon=False,
                                         context=C.sanitize_context(ex["context"]))
        raw = support._call_provider(lambda: support._gemini_reply(
            [{"role": "user", "content": ex["question"]}], key, model, sp))
        clean, draft = support.parse_ticket_draft(raw)
        bad = R.check_answer(ex, clean)
        want = ex.get("draft")
        if want and (draft is None or draft["type"] != want["type"]):
            bad.append(f"expected a {want['type']} draft, got {draft}")
        if not want and draft is not None:
            bad.append(f"unexpected draft: {draft}")
        print(f"\n=== {ex['id']} ({role}) {'FAIL' if bad else 'ok'}")
        print(f"Q: {ex['question']}")
        print(f"A: {clean}")
        if draft:
            print(f"DRAFT: {draft}")
        for b in bad:
            print(f"  ! {b}")
        failed += bool(bad)
    print(f"\n{failed} of the exchanges had problems (model {model}).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
