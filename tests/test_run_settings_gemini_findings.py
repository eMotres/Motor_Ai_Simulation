"""Follow-up of #845 (second-review findings): the Descent baseline uses the panel's resolved run settings, the resolver names the
finalist step raise (cogging quality) without changing the run's identity, and the web record keeps only the keys the resolver
accepts."""
from __future__ import annotations

import math
import re
from pathlib import Path

from motor_ai_sim import run_settings as RS
from motor_ai_sim.routes import optimization as O

ROOT = Path(__file__).resolve().parents[1]


def _cfg(slots=12, poles=14):
    cfg = dict(O.get_config())
    geo = dict(cfg.get("geometry") or {})
    geo.update(num_slots=slots, num_poles=poles)
    cfg["geometry"] = geo
    return cfg


def test_baseline_request_takes_steps_gap_mesh_from_the_resolved_run_settings(monkeypatch):
    seen = []

    def fake_eval(_ov, cur, steps, _coil, **kw):
        seen.append({"steps": steps, **kw})
        return {"ok": True, "res": {}}

    monkeypatch.setattr(O, "_subprocess_eval", fake_eval)
    monkeypatch.setattr(O, "_make_bline", lambda a, b, bump: {"bump_pct": bump})
    resolved = RS.resolve("simulation", {}, O.get_config(), "descent")
    st = resolved["settings"]
    # the stored (Mesh tab) values the request would have carried before: different on purpose
    req = O.BaselineRequest(steps_per_period=24, gap_layers=1.0 if st["gap_layers"] != 1 else 2.0, mesh_size_mm=7.0, min_size_mm=2.0,
                            rotor_eddy=not st["rotor_eddy"], n_sectors=-1,
                            run_settings=O.RunSettingsIn(preset="simulation", overrides={}, expect_sig=resolved["sig"]))
    out = O.descent_baseline(req)
    assert out["baseline_line"]["bump_pct"] == 10.0
    assert len(seen) == 2
    for k in seen:
        assert k["steps"] == int(st["steps_per_period"])
        assert k["gap_layers"] == float(st["gap_layers"])
        assert k["mesh_size_mm"] == float(st["mesh_size_mm"]) and k["min_size_mm"] == float(st["min_size_mm"])
        assert k["rotor_eddy"] == bool(st["rotor_eddy"]) and k["n_sectors"] == int(st["n_sectors"])


def test_baseline_without_run_settings_is_unchanged(monkeypatch):
    seen = []
    monkeypatch.setattr(O, "_subprocess_eval", lambda _o, cur, steps, _c, **kw: seen.append((steps, kw)) or {"ok": True, "res": {}})
    monkeypatch.setattr(O, "_make_bline", lambda a, b, bump: {})
    O.descent_baseline(O.BaselineRequest(steps_per_period=24, gap_layers=2.0, mesh_size_mm=0.5, min_size_mm=0.05))
    assert seen[0][0] == 24 and seen[0][1]["gap_layers"] == 2.0
    assert seen[0][1]["mesh_size_mm"] == 1.0 and seen[0][1]["min_size_mm"] == 0.1     # the old floors


def test_finalist_note_names_the_cogging_quality_minimum_and_is_not_part_of_the_identity():
    cfg = _cfg(12, 14)                      # lcm 84 / 7 pairs = 12 cycles -> 6 samples = 72 steps at least
    r = RS.resolve("optimization", {"steps_per_period": 48}, cfg, "descent")
    note = r["finalist_note"]
    assert note == {"min_steps": 72, "run_steps": 48, "reason": "cogging_quality"}
    assert r["settings"]["steps_per_period"] == 48            # the run keeps its own steps
    assert r["sig"] == RS.identity_sig(48, r["settings"]["gap_layers"])
    assert not any(c["key"] == "steps_per_period" for c in r["clamps"])
    again = RS.resolve("optimization", {"steps_per_period": 48}, cfg, "descent")
    assert again["sig"] == r["sig"]


def test_finalist_note_absent_when_the_run_already_has_enough_steps():
    r = RS.resolve("simulation", {"steps_per_period": 108}, _cfg(12, 14), "sweep")
    assert r["finalist_note"] is None


def test_web_override_keys_match_the_resolver():
    src = (ROOT / "web/src/lib/runSettings.ts").read_text(encoding="utf8")
    got = set()
    for name in ("OVERRIDE_KEYS_NUMBER", "OVERRIDE_KEYS_BOOLEAN"):
        m = re.search(name + r"\s*=\s*\[([^\]]*)\]", src)
        assert m, name
        got |= set(re.findall(r"'([a-z_]+)'", m.group(1)))
    server = set()
    for keys in RS._OVERRIDABLE.values():
        server |= set(keys)
    assert got == server
