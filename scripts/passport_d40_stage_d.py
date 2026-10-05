"""Ø40 Stage D k_T at one stack length, with the axial mesh scaled to the stack.

    python scripts/passport_d40_stage_d.py --n-stack auto --shifts=-1,0,1 --out <json>

Runs ``scripts/stage_d_kt_passport.py`` unchanged except for the number of
axial element layers inside the stack (``BandedModel.n_stack``, fixed at 5 in
that script).  ``auto`` keeps the axial element no longer than the 12 mm
reference's (12 / 5 = 2.4 mm): n_stack = max(5, ceil(L / 2.4)).

Why (2026-10-05): at 24 and 30 mm the 5-layer stack gives 4.8–6 mm thick tets
against a 0.55 mm air-gap size; the first linear solve (CG with the
regularised PARDISO preconditioner) then ran 75–100 min without finishing one
Picard sweep, where the 12 mm run needs 20 CG iterations.  A finer axial mesh
is the same physics, better conditioned.  The layer count is written into the
output JSON (``mesh.n_stack``).
"""
from __future__ import annotations

import functools
import json
import math
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AXIAL_MM = 12.0 / 5.0


def main() -> int:
    argv = list(sys.argv[1:])
    n_arg = "auto"
    if "--n-stack" in argv:
        i = argv.index("--n-stack")
        n_arg = argv[i + 1]
        del argv[i:i + 2]
    out = next((a.split("=", 1)[1] for a in argv if a.startswith("--out=")), None)
    if out is None and "--out" in argv:
        out = argv[argv.index("--out") + 1]
    from motor_ai_sim.config import get_config
    L = float((get_config().get("geometry") or {}).get("motor_length"))
    n = max(5, math.ceil(L / AXIAL_MM - 1e-9)) if n_arg == "auto" else int(n_arg)
    print("stack %.1f mm -> n_stack %d (axial element %.2f mm)" % (L, n, L / n), flush=True)
    from motor_ai_sim.simulation.static3d import torque3d
    torque3d.BandedModel = functools.partial(torque3d.BandedModel, n_stack=n)
    sys.argv = [str(ROOT / "scripts" / "stage_d_kt_passport.py")] + argv
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    except SystemExit as e:
        if e.code not in (0, None):
            raise
    if out and Path(out).exists():
        d = json.loads(Path(out).read_text(encoding="utf-8"))
        d.setdefault("mesh", {})["n_stack"] = n
        d["mesh"]["axial_rule"] = "n_stack = max(5, ceil(L / 2.4 mm))" if n_arg == "auto" \
            else "given"
        Path(out).write_text(json.dumps(d, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
