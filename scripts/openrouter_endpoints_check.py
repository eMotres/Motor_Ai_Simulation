#!/usr/bin/env python
"""Check that the production translation model still has a host under our OpenRouter provider rules (no key, no prompt sent).

    PYTHONPATH=src python scripts/openrouter_endpoints_check.py [model]

Reads OpenRouter's public endpoint list of ``PRODUCTION_TRANSLATE_MODEL`` (default ``deepseek/deepseek-chat``) and prints the hosts that
:func:`motor_ai_sim.production.translate.usable_endpoints` keeps.  Exit 0 when at least one remains, 1 when none does (translations would
stay ``pending``), 2 when the list cannot be read.  ``data_collection: deny`` is applied by OpenRouter at request time and is not visible
in this list; run one real translation after a deploy to confirm.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from motor_ai_sim.production import translate as T     # noqa: E402


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    model = (argv[0] if argv else os.environ.get("PRODUCTION_TRANSLATE_MODEL") or T.DEFAULT_MODEL).strip()
    url = f"https://openrouter.ai/api/v1/models/{model}/endpoints"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            endpoints = json.load(r)["data"]["endpoints"]
    except Exception as e:                                      # noqa: BLE001
        print(f"cannot read {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    for e in endpoints:
        print(f"  {e.get('provider_name')} ({e.get('tag')}): status {e.get('status')}")
    usable = T.usable_endpoints(endpoints)
    print(f"{model}: usable hosts under {T.PROVIDER_PREFS}: {', '.join(usable) or 'NONE'}")
    return 0 if usable else 1


if __name__ == "__main__":
    sys.exit(main())
