"""Assemble the full passport records and HTML cards from solved results.

    python scripts/passport_card.py --spec CIANO28_85_20SW1200 --work <dir with out/<M>, out/full>
                                    [--html-dir <dir>] [--records-dir <dir>] [--machines L13]

Post-processing only (no FEM): stage-1 records (``passport_v1.card``) + 3-D
factors + mechanics + coupled thermal + per-cooling thermal limits + demag limit
+ PWM / controller (``passport_v1.stage3``) -> ``passport_<M>.json`` and
``<M>.html`` (+ ``compare.html`` when the spec has more than one machine).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.passport_v1 import fullcard as FC  # noqa: E402
from motor_ai_sim.passport_v1.spec import default_spec_path, load_spec  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--html-dir", default=None)
    ap.add_argument("--records-dir", default=None)
    ap.add_argument("--machines", default=None, help="comma list (default: every spec machine)")
    a = ap.parse_args(argv)
    spec = load_spec(default_spec_path(ROOT, a.spec))
    work = Path(a.work)
    Ms = a.machines.split(",") if a.machines else list(spec["machines"])
    recs = {}
    for M in Ms:
        snap = json.loads((work / "out" / M / "snapshot.json").read_text(encoding="utf-8"))
        recs[M] = FC.build_full(work, ROOT, M,
                                {"version": (snap.get("owner_inputs") or {}).get("version")},
                                spec_m=spec["machines"][M])
        recs[M]["spec"] = {"file": Path(spec["_path"]).name, "title": spec.get("title")}
        rd = Path(a.records_dir) if a.records_dir else work / "out" / M
        rd.mkdir(parents=True, exist_ok=True)
        (rd / f"passport_{M}.json").write_text(json.dumps(recs[M], indent=1, default=str),
                                               encoding="utf-8")
        print(M, "record written", flush=True)
    if a.html_dir:
        hd = Path(a.html_dir)
        hd.mkdir(parents=True, exist_ok=True)
        for M, r in recs.items():
            (hd / f"{M}.html").write_text(FC.machine_html(r, M, str(spec.get("title") or "")),
                                          encoding="utf-8")
        if len(recs) > 1:
            (hd / "compare.html").write_text(FC.compare_html(recs), encoding="utf-8")
        print("html written to", hd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
