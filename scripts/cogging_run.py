#!/usr/bin/env python
"""Headless no-load cogging torque of a catalogued configuration (owner 2026-10-26).

Two ways, one result (the ``cogging:`` block of the configuration yaml, see ``motor_ai_sim.cogging``):

  # in-process: the process owns the catalog files (this PC, a compute container, the server shell).
  # The workspace is the one MOTOR_AI_SIM_CONFIG / WORKSPACES_ROOT name; no login, no HTTP.
  python scripts/cogging_run.py --die "<die>" --config <cfg> --save

  # through a running API (a verified account that may write the die; the run is queued like any solve):
  python scripts/cogging_run.py --api https://aerostator.com --token "$TOKEN" --die "<die>" --config <cfg> --save

Without ``--save`` nothing is written: the pk-pk, half-amplitude, share of the continuous torque and the spectrum line are
printed.  ``--json FILE`` also writes the full answer (waveform included).  ``--mesh-size`` runs a deliberately different
mesh (a coarse check); the default is the configuration's own Mesh settings.  ``--frozen-nu`` repeats the run with frozen
permeability for comparison (not the default: it breaks the slot symmetry, see ``motor_ai_sim/cogging.py``).

On a Windows workstation the geometry mesher needs ``MOTOR_AI_SIM_GEO_CDT=gmsh`` (netgen is blocked by App Control); on the
server and under WSL2 the default is right.  One solve, ~1-6 min for a small motor: do not start it while the owner works.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _print(out: dict) -> None:
    s = out["summary"]
    print("cogging torque (peak-to-peak): %.5g N·m   half-amplitude %.5g N·m" % (s["pkpk_Nm"], s["half_amp_Nm"]))
    if s.get("pct_of_continuous") is not None:
        print("  = %.3g %% of the continuous torque %.4g N·m" % (s["pct_of_continuous"], s["continuous_torque_Nm"]))
    print("  fundamental cogging order %s (amplitude %.4g N·m), dominant order %s, %s samples (%.1f per cycle)"
          % (s["fundamental_order"], s.get("fundamental_amp_Nm") or 0.0, s.get("dominant_order"), s["samples"],
             s["samples_per_cycle"]))
    print("  magnets %s at %s °C (%s); %s; runtime %.0f s%s"
          % (out.get("magnet_card"), out.get("magnet_temp_c"), out.get("magnet_temp_source"), out.get("method"),
             out.get("runtime_s") or 0.0, "; SAVED on the configuration" if out.get("saved") else ""))


def _local(a) -> dict:
    from motor_ai_sim import cogging as C
    from motor_ai_sim.routes import cogging as RC
    from motor_ai_sim.routes import family as F
    die_doc = F._load_yaml(F._die_file(a.die), "die")
    cfg_doc = F._load_yaml(F._cfg_file(a.die, a.config), "configuration")
    machine = C.machine_from_config(die_doc, cfg_doc, {"mesh_size_mm": a.mesh_size})
    ct, _ = C.continuous_torque(cfg_doc, RC._stored(a.die, a.config))
    run = C.run(machine, magnet_temp_c=a.magnet_temp, frozen_nu=a.frozen_nu, samples_per_cycle=a.samples_per_cycle,
                continuous_torque_nm=ct)
    run["build_sig"] = C.current_build_sig(die_doc, cfg_doc)
    run["saved"] = False
    if a.save:
        RC._save(a.die, a.config, run, {"is_admin": True})
        run["saved"] = True
    return run


def _remote(a) -> dict:
    body = {"die": a.die, "config": a.config, "save": bool(a.save), "frozen_nu": bool(a.frozen_nu),
            "samples_per_cycle": a.samples_per_cycle}
    if a.magnet_temp is not None:
        body["magnet_temp_c"] = a.magnet_temp
    if a.mesh_size is not None:
        body["mesh_size_mm"] = a.mesh_size
    req = urllib.request.Request(a.api.rstrip("/") + "/api/simulation/cogging/run", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + a.token},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=a.timeout) as r:
            if r.status == 202:
                print("queued (HTTP 202): %s\nread the result with GET /api/simulation/cogging/%s/%s once the job is done"
                      % (r.read().decode()[:300], a.die, a.config), file=sys.stderr)
                sys.exit(2)
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("HTTP %s: %s" % (e.code, e.read().decode()[:500]), file=sys.stderr)
        sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--die", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--save", action="store_true", help="file the result on the configuration yaml")
    ap.add_argument("--mesh-size", type=float, default=None, help="mesh size [mm] (default: the configuration's own)")
    ap.add_argument("--magnet-temp", type=float, default=None, help="magnet temperature [°C] (default: the card as quoted)")
    ap.add_argument("--frozen-nu", action="store_true", help="frozen permeability (comparison only)")
    ap.add_argument("--samples-per-cycle", type=int, default=12)
    ap.add_argument("--json", default=None, help="write the full answer here")
    ap.add_argument("--api", default=None, help="base URL of a running API (omit for in-process)")
    ap.add_argument("--token", default=None, help="bearer token for --api")
    ap.add_argument("--timeout", type=float, default=3600.0)
    a = ap.parse_args()
    if a.api and not a.token:
        ap.error("--api needs --token")
    out = _remote(a) if a.api else _local(a)
    _print(out)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
