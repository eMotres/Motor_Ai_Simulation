"""scripts/migrate_derived_geometry.py — review of PR #75 (Codex, 2026-09-30).

* inline comments and post-colon spacing survive a rewrite;
* a differing same-date backup is refused (nothing written);
* a configuration-only change moves build_sig and is restamped, path-aware:
  only the result holders move, a `build_sig:` elsewhere is left alone;
* writes are atomic (no temp file left behind);
* slot_hs moves neither mass, nor the validator, nor the solver's polygons.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from motor_ai_sim.geometry import motor_geometry as mg
from motor_ai_sim.routes import family as fam
from tests.test_die_derived_geometry import CFG, DIE, STALE

REPO = Path(__file__).resolve().parents[1]


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "migrate_derived_geometry.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO), timeout=300)


def _mk_die(root: Path, geo: dict, cfg: dict = None) -> Path:
    (root / DIE).mkdir(parents=True, exist_ok=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": True, "geometry": geo}, sort_keys=False),
        encoding="utf-8")
    if cfg is not None:
        (root / DIE / f"{CFG}.yaml").write_text(
            yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return root / DIE / "die.yaml"


def test_rewrite_keeps_inline_comments_and_spacing(tmp_path):
    die = _mk_die(tmp_path / "dies", dict(STALE))
    t = die.read_text(encoding="utf-8").replace(
        "  stator_inner_radius: 12.100000000000001",
        "  stator_inner_radius:   12.100000000000001   # bore")
    die.write_text(t, encoding="utf-8")
    r = _run(str(tmp_path / "dies"), "--apply", "--date", "20300102")
    assert r.returncode == 0, r.stdout + r.stderr
    out = die.read_text(encoding="utf-8")
    assert "  stator_inner_radius:   12.000000000000002   # bore\n" in out
    assert not list(die.parent.glob(".*migrate-tmp"))           # atomic write


def test_differing_same_date_backup_is_refused(tmp_path):
    die = _mk_die(tmp_path / "dies", dict(STALE))
    (die.parent / "die.yaml.bak-20300103").write_text("older\n", encoding="utf-8")
    before = die.read_bytes()
    r = _run(str(tmp_path / "dies"), "--apply", "--date", "20300103")
    assert r.returncode != 0
    assert "differs from the current file" in r.stdout
    assert die.read_bytes() == before


def test_configuration_only_change_is_restamped_path_aware(tmp_path):
    root = tmp_path / "dies"
    good = mg.refresh_derived_geometry(STALE)
    ov = {"wire_width": 2.5, "wire_spacing_x": 0.1, "insulation_thickness": 0.06,
          "wire_split": 1, "slot_width": 9.9}                    # stale copy
    base = {"name": CFG, "die": DIE, "geometry_overrides": ov}
    old_sig = fam._build_sig({"geometry": good}, base)
    cfg = dict(base, notes={"build_sig": old_sig},
               duties=[{"name": "peak", "result": {"build_sig": old_sig},
                        "runs": {"current": {"build_sig": old_sig,
                                             "result": {"build_sig": old_sig}}}},
                       {"name": "old", "result": {"build_sig": "000000000000"}}])
    _mk_die(root, good, cfg)
    cf = root / DIE / f"{CFG}.yaml"

    r = _run(str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 result stamp(s) on the old sig" in r.stdout      # config-only change

    # a `build_sig: <old>` outside a result holder makes the edit unprovable:
    # refused, nothing written
    before = cf.read_bytes()
    r = _run(str(root), "--apply", "--restamp-results", "--date", "20300104")
    assert r.returncode != 0 and "restamp refused" in r.stdout
    assert cf.read_bytes() == before                  # all-or-nothing apply
    assert not list(root.rglob("*.bak-20300104"))

    # without the stray line the restamp goes through, holders only
    cfg.pop("notes")
    cf.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    r = _run(str(root), "--apply", "--restamp-results", "--date", "20300105")
    assert r.returncode == 0, r.stdout + r.stderr
    c = yaml.safe_load(cf.read_text(encoding="utf-8"))
    assert c["geometry_overrides"]["slot_width"] == pytest.approx(2.82)
    new_sig = fam._build_sig({"geometry": good}, c)
    assert new_sig != old_sig
    assert c["duties"][0]["result"]["build_sig"] == new_sig
    assert c["duties"][0]["runs"]["current"]["result"]["build_sig"] == new_sig
    assert c["duties"][0]["runs"]["current"]["build_sig"] == new_sig
    assert c["duties"][1]["result"]["build_sig"] == "000000000000"

    r = _run(str(root))                                          # idempotent
    assert r.returncode == 0 and "0 file(s), 0 field(s) stale" in r.stdout


def test_slot_hs_moves_no_mass_validation_or_solver_input():
    """The builder never reads slot_hs, so neither the CAD areas (mass), the
    region validator nor the polygons the mesher receives may move with it."""
    import motor_ai_sim.masses as M
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.geometry_validation import validate_geometry
    from tests.test_geometry_validation import GEO_30MM

    g1 = dict(GEO_30MM)
    g2 = dict(GEO_30MM, slot_hs=0.9)

    def _areas(g):
        M._AREA_CACHE.clear()
        return M.cad_areas_m2(dict(g))

    a1, a2 = _areas(g1), _areas(g2)
    assert a1 and a1 == a2

    v1, v2 = validate_geometry(dict(g1)), validate_geometry(dict(g2))
    assert (sorted((x.code, x.severity) for x in v1.violations)
            == sorted((x.code, x.severity) for x in v2.violations))

    def _polys(g):
        m = CadQueryMotor()
        m.set_parameters(dict(g))
        out = {}
        for k, v in m.get_2d_polygons(rotor_angle_deg=0.0).items():
            items = v if isinstance(v, (list, tuple)) else [v]
            out[k] = [round(float(getattr(x, "area", 0.0) or 0.0), 9) for x in items]
        return out

    assert _polys(g1) == _polys(g2)
