"""PATCH /die/{die}/geometry: a die's sheet changed IN PLACE, keeping its name (owner 2026-11-27).

Pinned: dry run writes nothing; only the sent keys change (derived refreshed); locked die 409, non-admin 403; configuration,
motor, derived, identity, unknown and non-number keys 422; a validation error 422 and per-configuration; allow_config_misfit
turns a configuration misfit into a warning; stored duties AND cogging read stale through the build signature; the audit row,
the name, the folder and the configurations' ``die`` back-reference are unchanged.  Throwaway catalog, no FEM.
"""
from __future__ import annotations

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim.routes import family as fam

DIE = "TEST 150 sheet die"
WIRE = {"wire_width": 4.0, "wire_spacing_x": 0.1, "insulation_thickness": 0.2,
        "wire_height": 1.75, "num_wires_per_slot": 6, "motor_length": 100}
GEO = dict({"stator_diameter": 150.0, "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
            "core_thickness": 5.6, "slot_height": 20.4, "air_gap": 0.65, "magnet_height": 8.0,
            "rotor_house_height": 10.9, "tooth_width": 11.3, "tooth2_width": 14.8,
            "num_slots": 12, "cut_width": 0.5}, **WIRE)
ADMIN = {"is_admin": True, "user": {"email": "admin@example.com"}}
AUDIT: list = []


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _cfg(name, sig, **ov):
    return {"name": name, "die": DIE, "locked": False, "geometry_overrides": dict(WIRE, **ov),
            "winding": {"connection": "4S"}, "materials": {"magnet": "N52UH"},
            "duties": [{"name": "rated", "mode": "motor", "current_arms": 9.4, "rpm": 120.0,
                        "result": {"build_sig": sig, "T_em_avg_Nm": 80.0}}],
            "cogging": {"build_sig": sig, "peak_to_peak_Nm": 0.4}}


@pytest.fixture
def cat(tmp_path, monkeypatch):
    root = tmp_path / "dies"
    die_doc = {"name": DIE, "locked": False, "created": "2026-11-01", "geometry": dict(GEO), "thumb_svg": "<svg old/>"}
    _write(root / DIE / "die.yaml", die_doc)
    cfg = _cfg("L100", "x", stator_outer_notches=False)
    cfg["duties"][0]["result"]["build_sig"] = cfg["cogging"]["build_sig"] = fam._build_sig(die_doc, cfg)
    _write(root / DIE / "L100.yaml", cfg)
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_require_die_write", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "_require_writable_die", lambda *_a, **_k: None)
    from motor_ai_sim import admin_audit
    AUDIT.clear()
    monkeypatch.setattr(admin_audit, "record", lambda *a, **k: AUDIT.append((a, k)))
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: "<svg new/>")
    return root


def _patch(keys, dry=False, w=ADMIN, **kw):
    return fam.patch_die_geometry(DIE, fam.DieGeometryPatch(keys=keys, dry_run=dry, **kw), _w=w)


def _files(root):
    return {p.name: p.read_bytes() for p in sorted((root / DIE).glob("*.yaml"))}


def test_dry_run_writes_nothing_and_answers_the_document(cat):
    before = _files(cat)
    out = _patch({"rotor_house_height": 3.0, "tooth2_width": 6.6}, dry=True)
    assert out["dry_run"] is True and out["changed"] == ["rotor_house_height", "tooth2_width"]
    assert out["before"] == {"rotor_house_height": 10.9, "tooth2_width": 14.8}
    assert out["after"] == {"rotor_house_height": 3.0, "tooth2_width": 6.6}
    c = out["configurations"]["L100"]
    assert c["build_sig_before"] != c["build_sig_after"] and c["stale_duties"] == ["rated"] and c["cogging_stale"]
    assert _files(cat) == before and AUDIT == []


def test_the_write_changes_only_the_sent_keys_and_keeps_the_identity(cat):
    out = _patch({"rotor_house_height": 3.0})
    d = _yaml(cat / DIE / "die.yaml")
    assert d["geometry"]["rotor_house_height"] == 3.0 and d["geometry"]["tooth2_width"] == 14.8
    assert {k: v for k, v in d["geometry"].items() if k not in ("rotor_house_height",) and k in GEO} == \
        {k: v for k, v in GEO.items() if k != "rotor_house_height"}
    assert d["name"] == DIE and d["locked"] is False and d["created"] == "2026-11-01"
    assert d["thumb_svg"] == "<svg new/>" and out["thumb_regenerated"] is True
    assert _yaml(cat / DIE / "L100.yaml")["die"] == DIE and (cat / DIE).is_dir()
    assert len(AUDIT) == 1 and AUDIT[0][0][1] == "die.geometry"
    assert AUDIT[0][1]["details"] == {"from": {"rotor_house_height": 10.9}, "to": {"rotor_house_height": 3.0}}


def test_the_old_thumbnail_stays_when_it_cannot_be_made(cat, monkeypatch):
    from motor_ai_sim.routes import presets as _presets

    def boom(*_a, **_k):
        raise RuntimeError("no thumb")
    monkeypatch.setattr(_presets, "_gen_thumb_svg", boom)
    out = _patch({"rotor_house_height": 3.0})
    assert out["thumb_regenerated"] is False and _yaml(cat / DIE / "die.yaml")["thumb_svg"] == "<svg old/>"


def test_stored_results_are_kept_and_read_stale_duties_and_cogging(cat):
    from motor_ai_sim import cogging as cg
    c0 = _yaml(cat / DIE / "L100.yaml")
    d0 = _yaml(cat / DIE / "die.yaml")
    assert fam.stored_build_stale(d0, c0, "rated") is False
    assert cg.is_stale(c0["cogging"], fam._build_sigs(d0, c0)) is False
    _patch({"rotor_house_height": 3.0})
    c1, d1 = _yaml(cat / DIE / "L100.yaml"), _yaml(cat / DIE / "die.yaml")
    assert c1["duties"][0]["result"]["T_em_avg_Nm"] == 80.0 and c1["cogging"]["peak_to_peak_Nm"] == 0.4
    assert fam.stored_build_stale(d1, c1, "rated") is True
    assert cg.is_stale(c1["cogging"], fam._build_sigs(d1, c1)) is True


def test_locked_die_409_and_non_admin_403(cat):
    d = _yaml(cat / DIE / "die.yaml")
    _write(cat / DIE / "die.yaml", dict(d, locked=True))
    before = _files(cat)
    with pytest.raises(HTTPException) as e:
        _patch({"rotor_house_height": 3.0})
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        _patch({"rotor_house_height": 3.0}, w={"is_admin": False})
    assert e.value.status_code == 403
    assert _files(cat) == before and AUDIT == []


@pytest.mark.parametrize("key,val", [
    ("motor_length", 90), ("wire_width", 3.0),                 # FREE (configuration) keys
    ("magnet_fill_up", 0.5),                                   # MOTOR key
    ("stator_diameter", 160.0), ("num_poles_per_segment", 8),  # identity keys
    ("num_slots", 24), ("slot_width", 7.0),                    # derived keys
    ("no_such_key", 1.0),                                      # unknown
    ("rotor_house_height", "3.0"), ("rotor_house_height", True), ("rotor_house_height", float("nan")),   # not a number
])
def test_keys_a_die_sheet_does_not_take_are_422_and_write_nothing(cat, key, val):
    before = _files(cat)
    with pytest.raises(HTTPException) as e:
        _patch({key: val})
    assert e.value.status_code == 422 and key in str(e.value.detail)
    assert _files(cat) == before and AUDIT == []


def test_a_value_the_validation_refuses_is_422_and_writes_nothing(cat):
    before = _files(cat)
    with pytest.raises(HTTPException) as e:
        _patch({"tooth_width": -2.0})
    assert e.value.status_code == 422
    assert _files(cat) == before and AUDIT == []


def test_a_configuration_misfit_refuses_unless_allowed(cat, monkeypatch):
    real = fam._sheet_errors

    def fake(geo):                                  # the configuration's own wide wire stops fitting the new sheet
        if geo.get("tooth2_width") == 6.6 and geo.get("wire_width") == 4.0:
            return [{"field": "wire_width", "message": "wire split too wide for this slot"}]
        return real(geo)
    monkeypatch.setattr(fam, "_sheet_errors", fake)
    before = _files(cat)
    dry = _patch({"tooth2_width": 6.6}, dry=True)
    assert dry["ok"] is False and dry["configurations"]["L100"]["outcome"] == "refused"
    with pytest.raises(HTTPException) as e:
        _patch({"tooth2_width": 6.6})
    assert e.value.status_code == 422 and "L100" in e.value.detail["configurations"]
    assert _files(cat) == before
    out = _patch({"tooth2_width": 6.6}, allow_config_misfit=True)
    assert out["ok"] is True and out["configurations"]["L100"]["outcome"] == "warning"
    assert out["configurations"]["L100"]["validation"][0]["field"] == "wire_width"
    assert _yaml(cat / DIE / "die.yaml")["geometry"]["tooth2_width"] == 6.6


# ── the OD-notch flag (stored only when OFF; absent = on) ───────────────────────────────────────────────────────────────
def test_notches_back_on_with_a_fitting_tooth2_removes_the_key_and_writes(cat):
    out = _patch({"stator_outer_notches": True, "tooth2_width": 6.6})
    g = _yaml(cat / DIE / "die.yaml")["geometry"]
    assert "stator_outer_notches" not in g and g["tooth2_width"] == 6.6          # absent = on
    # the die was notched already (key absent): only tooth2 changes; a SENT flag is judged on every configuration all the
    # same, so L100 (own override: round) is checked on the NOTCHED sheet and fits with the narrow tooth2
    assert out["changed"] == ["tooth2_width"] and out["configurations"]["L100"]["outcome"] == "ok"


def test_notches_on_with_the_wide_tooth2_does_not_fit_the_notched_sheet_and_is_refused(cat):
    # sent while the die is notched already: nothing changes for the flag; make the die round first, then ask for notches
    d = _yaml(cat / DIE / "die.yaml")
    _write(cat / DIE / "die.yaml", dict(d, geometry=dict(d["geometry"], stator_outer_notches=False)))
    before = _files(cat)
    with pytest.raises(HTTPException) as e:
        _patch({"stator_outer_notches": True})
    assert e.value.status_code == 422 and "L100" in e.value.detail["configurations"]
    assert "wire" in e.value.detail["configurations"]["L100"][0]["message"]
    assert _files(cat) == before
    ok = _patch({"stator_outer_notches": True}, allow_config_misfit=True)
    assert ok["configurations"]["L100"]["outcome"] == "warning"


def test_notches_off_is_stored_as_false_and_a_non_boolean_flag_is_422(cat):
    out = _patch({"stator_outer_notches": False})
    assert _yaml(cat / DIE / "die.yaml")["geometry"]["stator_outer_notches"] is False and out["changed"] == ["stator_outer_notches"]
    for bad in (1, "false", None):
        with pytest.raises(HTTPException) as e:
            _patch({"stator_outer_notches": bad})
        assert e.value.status_code == 422


def test_a_sent_flag_is_judged_even_when_the_die_already_has_that_value(cat):
    with pytest.raises(HTTPException) as e:           # die notched (absent), tooth2 14.8: L100 on the notched sheet does not fit
        _patch({"stator_outer_notches": True})
    assert e.value.status_code == 422 and "L100" in e.value.detail["configurations"]


# ── the configuration's free keys, in place ────────────────────────────────────────────────────────────────────────────
def _free(keys, dry=False, w=ADMIN):
    return fam.patch_config_free_keys(DIE, "L100", fam.ConfigFreeKeysPatch(keys=keys, dry_run=dry), _w=w)


def test_free_keys_the_package_for_a_round_split_configuration(cat):
    # die sheet first (narrow tooth2; L100 only warned), then the configuration: wire 8.0, split 1, notches back on
    _patch({"tooth2_width": 6.6, "stator_outer_notches": True}, allow_config_misfit=True)
    dry = _free({"wire_width": 8.0, "wire_split": 1, "stator_outer_notches": True}, dry=True)
    assert dry["dry_run"] is True and dry["ok"] is True and "stator_outer_notches" in dry["changed"]
    assert _yaml(cat / DIE / "L100.yaml")["geometry_overrides"]["stator_outer_notches"] is False
    out = _free({"wire_width": 8.0, "wire_split": 1, "stator_outer_notches": True})
    ov = _yaml(cat / DIE / "L100.yaml")["geometry_overrides"]
    assert "stator_outer_notches" not in ov and ov["wire_width"] == 8.0 and ov["wire_split"] == 1
    assert out["before"]["stator_outer_notches"] is False and out["after"]["stator_outer_notches"] is True
    assert out["build_sig_before"] != out["build_sig_after"] and out["stale_duties"] == ["rated"]
    assert AUDIT[-1][0][1] == "config.free_keys"


def test_free_keys_refusals_and_lock(cat):
    before = _files(cat)
    for bad in ({"tooth2_width": 6.6}, {"magnet_fill_up": 0.5}, {"wire_width": "8"}, {"stator_outer_notches": 1}):
        with pytest.raises(HTTPException) as e:
            _free(bad)
        assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        _free({"wire_width": 4.0}, w={"is_admin": False})
    assert e.value.status_code == 403
    c = _yaml(cat / DIE / "L100.yaml")
    _write(cat / DIE / "L100.yaml", dict(c, locked=True))
    with pytest.raises(HTTPException) as e:
        _free({"wire_width": 5.0})
    assert e.value.status_code == 409
    _write(cat / DIE / "L100.yaml", c)
    assert _files(cat) == before
