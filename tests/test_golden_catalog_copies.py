"""Golden check over COPIES of the live catalog (owner 2026-11-15: nothing existing may change).

Reads a bundle made with `sudo motres-agent-catalog get <tag> <die> <config>` (`<bundle>/<die>/die.yaml` + `<config>.yaml`) and
compares, per die/configuration, a digest of the 2-D polygons the builder returns (stator, rotor, coils, enamel, liner, magnets,
shaft, winding fit), the turns and conductors per slot and the end-winding factor with a golden JSON written by the BASE code:

    python tests/test_golden_catalog_copies.py write  <bundle> <golden.json>     (run with the base code on PYTHONPATH)
    MOTRES_GOLDEN_BUNDLE=<bundle> MOTRES_GOLDEN_JSON=<golden.json> pytest tests/test_golden_catalog_copies.py

No catalog data is committed: without the two variables the test is skipped.  The opt-in keys of the 12_40 series
(`wire_bore_margin`, `wire_omit`, `k_end_by_turns`, ...) are absent from every catalog machine, so every digest must be equal.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys


class _P:
    pass


def digest_all(bundle: str) -> dict:
    import yaml
    from motor_ai_sim import winding
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.masses import end_winding_factor
    out = {}
    for d in sorted(glob.glob(os.path.join(bundle, "*"))):
        die = os.path.basename(d)
        dg = (yaml.safe_load(open(os.path.join(d, "die.yaml"))) or {}).get("geometry") or {}
        for f in sorted(glob.glob(os.path.join(d, "*.yaml"))):
            if f.endswith("die.yaml"):
                continue
            c = yaml.safe_load(open(f)) or {}
            g = {**dg, **(c.get("geometry_overrides") or {})}
            key = die + " | " + os.path.basename(f)[:-5]
            try:
                m = CadQueryMotor()
                m.set_parameters(dict(g))
                P = m.get_2d_polygons()
                h = hashlib.sha256()
                for k in ("stator", "rotor", "coils", "wire_insulation", "slot_insulation", "magnets", "shaft"):
                    v = P.get(k)
                    v = v if isinstance(v, (list, tuple)) and not (len(v) == 2 and isinstance(v[1], int)) else [v]
                    for x in v:
                        if x is None:
                            continue
                        x = x[0] if isinstance(x, tuple) else x
                        if hasattr(x, "geoms"):
                            x = max(x.geoms, key=lambda q: q.area)
                        h.update(repr([round(q, 9) for q in x.exterior.coords[0]]).encode())
                        h.update(repr(round(x.area, 9)).encode())
                h.update(repr((P["n_wires_fit"], P["coils_overflow"])).encode())
                pp = _P()
                pp.stack_length = float(g.get("motor_length", 0)) * 1e-3
                pp.num_slots = g.get("num_slots")
                k_end = end_winding_factor(pp, g, coil_overrides=c.get("coil_overrides"), cad_overrides=c.get("cad_overrides"))
                out[key] = [h.hexdigest()[:16], winding.turns_per_coil(g), winding.conductors_per_slot(g), repr(k_end)]
            except Exception as e:                       # a machine that errors must error the same way
                out[key] = ["ERR", repr(e)[:80]]
    return out


def test_live_catalog_copies_are_unchanged():
    import pytest
    bundle, gold = os.environ.get("MOTRES_GOLDEN_BUNDLE"), os.environ.get("MOTRES_GOLDEN_JSON")
    if not bundle or not gold or not os.path.isdir(bundle) or not os.path.isfile(gold):
        pytest.skip("no catalog copy / golden file (MOTRES_GOLDEN_BUNDLE, MOTRES_GOLDEN_JSON)")
    want = json.load(open(gold))
    got = digest_all(bundle)
    assert set(got) == set(want)
    bad = [k for k in want if got[k] != want[k]]
    assert not bad, bad


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "write":
        json.dump(digest_all(sys.argv[2]), open(sys.argv[3], "w"), sort_keys=True, indent=0)
        print("written", sys.argv[3])
