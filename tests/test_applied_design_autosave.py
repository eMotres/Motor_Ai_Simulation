"""Applying an optimizer design ARCHIVES it — every time, or says why not.

A design applied from the Optimization tab used to live only in the editor's
config until somebody remembered to press Save.  A backend restart threw away a
full day of optimization exactly that way.  So the apply itself archives, and
these are the properties that make the archive worth trusting:

* it saves a NEW motor.  Not the source motor, not the editor's active preset,
  not anything else in the store — every other entry is byte-identical after;
* the geometry it files is the one that ACTUALLY LANDED (the config the apply
  wrote), compared field by field against the overrides the apply asked for;
  a value the geometry validator clamped is REPORTED, not filed silently as if
  it were what the optimizer scored;
* the description carries WHAT PRODUCED IT — mode, run id, operating point,
  ripple gate, F and the point's metrics — so a card found weeks later can be
  traced back to its run;
* a save that fails FAILS LOUDLY.  There is no path that returns "ok" with
  nothing in the store: that silence is the whole bug this feature exists for;
* and each of the UI's apply paths is wired to it — the auto card's apply, the
  optimizer's best and hand-picked point, and the sweep study's picked point.

The store is a temp file; nothing here touches config/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

_WEB = Path(__file__).resolve().parents[1] / "web" / "src"

# The machine the apply landed in — what the endpoint must file.
GEO_APPLIED = {
    "stator_diameter": 40.0, "num_seg": 2, "num_slots_per_segment": 12,
    "num_poles_per_segment": 14, "motor_length": 35.0, "magnet_thickness": 3.3,
    "tooth_width": 4.15, "wire_width": 1.2,
}
SIM_CFG = {"max_current": 20.0, "rpm": 1000.0, "phase_offset_deg": 0.0}


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """A synthetic presets + catalog store and a synthetic config, isolated from
    the real config/ directory (the user is working in that editor)."""
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    presets_path = tmp_path / "motor_presets.json"
    catalog_path = tmp_path / "motor_catalog.json"
    presets_path.write_text("{}", encoding="utf-8")
    catalog_path.write_text(
        json.dumps({"tiers": [], "diameters_mm": [], "motors": []}), encoding="utf-8")

    monkeypatch.setattr(pre_mod, "_PRESETS_PATH", presets_path)
    monkeypatch.setattr(pre_mod, "_CATALOG_PATH", catalog_path)
    monkeypatch.setattr(cat_mod, "_CATALOG_PATH", catalog_path)
    monkeypatch.setattr(pre_mod, "_gen_thumb_svg", lambda geo: None)
    monkeypatch.setattr(pre_mod, "_enrich_card_entry",
                        lambda entry, geo, sim, met: None)
    monkeypatch.setattr("motor_ai_sim.config.get_config",
                        lambda: {"geometry": dict(GEO_APPLIED),
                                 "simulation": dict(SIM_CFG),
                                 "mesh": {"mesh_size_mm": 4.0}})
    return {"presets": presets_path, "catalog": catalog_path}


@pytest.fixture(autouse=True)
def identities(monkeypatch):
    """The shipping identity path with only the token verification faked."""
    from motor_ai_sim import auth

    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"admin@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)

    def _fake_resolve(authorization):
        if not isinstance(authorization, str) or not authorization.lower().startswith("bearer "):
            return None
        email = authorization.split(" ", 1)[1].strip().lower()
        return {"uid": f"uid-{email}", "email": email,
                "tier": "admin" if email == "admin@example.com" else "free"}

    monkeypatch.setattr(auth, "resolve_user", _fake_resolve)


ALICE = "Bearer alice@example.com"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _req(**kw):
    from motor_ai_sim.routes import presets as pre_mod
    base = dict(
        mode="auto", source_name="CIANO28 150_35", source_id="my_ciano28",
        run_id="perp5pct_ext9_p2", objective="baseline_line",
        operating_point={"current_a": 85.0, "rpm": 4000.0, "gamma_deg": 12.5},
        ripple_max_pct=5.0, f_above_baseline=0.0312,
        metrics={"T_em_Nm": 1.8421, "T_ripple_pct": 4.31, "efficiency": 0.9423,
                 "torque_per_mass": 2.713, "mass_total_kg": 0.679},
        overrides={"magnet_thickness": 3.3, "tooth_width": 4.15},
    )
    base.update(kw)
    return pre_mod.ArchiveAppliedRequest(**base)


def _archive(**kw):
    from motor_ai_sim.routes import presets as pre_mod
    return pre_mod.archive_applied_design(_req(**kw), authorization=ALICE)


def _seed_existing(pid="my_ciano28", name="CIANO28 150_35"):
    """A motor already in the store — the one the user is editing."""
    from motor_ai_sim.routes import presets as pre_mod
    return pre_mod.save_current_as_preset(
        pre_mod.SavePresetRequest(id=pid, name=name, geometry=dict(GEO_APPLIED),
                                  simulation=dict(SIM_CFG)),
        authorization=ALICE)


# ── it saves, and it saves the RIGHT machine ────────────────────────────────

@pytest.mark.parametrize("mode,label", [("auto", "opt"), ("cmaes", "opt"),
                                        ("descent", "opt"), ("screen", "refine"),
                                        ("sweep", "sweep"), ("picked", "pick")])
def test_every_apply_mode_saves_exactly_one_new_motor(stores, mode, label):
    _seed_existing()
    before = _read(stores["presets"])
    out = _archive(mode=mode)
    after = _read(stores["presets"])

    new = [k for k in after if k not in before]
    assert len(new) == 1, "an apply must file exactly one motor"
    assert new[0] == out["id"]
    # The name says where it came from and what produced it, at a glance.
    assert after[new[0]]["name"].startswith("CIANO28 150_35 · ")
    assert f" {label} " in after[new[0]]["name"]
    assert re.search(r"\d\d:\d\d$", after[new[0]]["name"])


def test_saved_geometry_is_field_for_field_what_was_applied(stores):
    _seed_existing()
    out = _archive()
    saved = _read(stores["presets"])[out["id"]]
    assert saved["geometry"] == pytest.approx(GEO_APPLIED)
    for k, v in GEO_APPLIED.items():           # field by field, explicitly
        assert saved["geometry"][k] == pytest.approx(v), k
    assert out["deviations"] == []


def test_the_operating_point_of_the_run_beats_the_editors_idle_one(stores):
    """The design was found at 85 A / 4000 rpm; the config still holds the idle
    20 A / 1000 rpm.  A motor saved with the idle point re-solves as a far worse
    machine than the one the optimizer measured."""
    out = _archive()
    sim = _read(stores["presets"])[out["id"]]["simulation"]
    assert sim["max_current"] == pytest.approx(85.0)
    assert sim["rpm"] == pytest.approx(4000.0)
    assert sim["phase_offset_deg"] == pytest.approx(12.5)


def test_nothing_else_in_the_store_is_touched(stores):
    """Not the source motor, not the editor's active preset, not a bystander."""
    _seed_existing()
    _seed_existing("other_motor", "Somebody else's 30 mm")
    before = _read(stores["presets"])
    _archive()
    after = _read(stores["presets"])
    for pid, entry in before.items():
        assert after[pid] == entry, f"'{pid}' was modified by an archive"


def test_two_applies_in_the_same_second_do_not_collide(stores):
    a = _archive()
    b = _archive()
    assert a["id"] != b["id"]
    assert len(_read(stores["presets"])) == 2


# ── provenance: the card can be traced back to its run ──────────────────────

def test_description_carries_what_produced_the_design(stores):
    out = _archive(mode="screen")
    d = _read(stores["presets"])[out["id"]]["description"]
    assert "mode=screen" in d
    assert "run=perp5pct_ext9_p2" in d
    assert "objective=baseline_line" in d
    assert "85.0 A" in d and "4000 rpm" in d and "12.5" in d      # operating point
    assert "ripple gate <= 5.00%" in d
    assert "F=+0.0312" in d and "above" in d                       # the verdict
    assert "1.842" in d and "4.31%" in d                           # T and ripple
    assert "94.23%" in d                                           # efficiency
    assert "CIANO28 150_35" in d and "my_ciano28" in d             # where from


def test_provenance_is_also_machine_readable(stores):
    out = _archive()
    p = _read(stores["presets"])[out["id"]]["provenance"]
    assert p["src"] == "optimizer_apply" and p["mode"] == "auto"
    assert p["run_id"] == "perp5pct_ext9_p2"
    assert p["operating_point"]["current_a"] == pytest.approx(85.0)
    assert p["ripple_max_pct"] == pytest.approx(5.0)
    assert p["F_above_baseline"] == pytest.approx(0.0312)
    assert p["overrides"]["magnet_thickness"] == pytest.approx(3.3)
    assert p["source_id"] == "my_ciano28"


def test_efficiency_is_stored_as_a_fraction_whichever_dialect_arrives(stores):
    """One chart hands over 0.9423, another 94.23 — the stored field must mean
    the same thing either way, or no consumer can use it."""
    a = _archive(metrics={"efficiency": 0.9423})
    b = _archive(metrics={"efficiency": 94.23})
    st = _read(stores["presets"])
    assert st[a["id"]]["metrics"]["efficiency"] == pytest.approx(0.9423)
    assert st[b["id"]]["metrics"]["efficiency"] == pytest.approx(0.9423)


def test_a_new_motor_gets_a_card_and_the_save_stamps(stores):
    out = _archive()
    saved = _read(stores["presets"])[out["id"]]
    assert saved["saved_at"] and saved["updated_at"]
    assert saved["owner"] == "alice@example.com"
    assert saved["locked"] is False and saved["template"] is False
    assert saved["geo_sig"]                       # stamped like every other write
    cards = _read(stores["catalog"])["motors"]
    assert [c for c in cards if c["id"] == f"cat_{out['id']}"]


# ── a clamped value is reported, never filed silently ───────────────────────

def test_a_value_the_validator_clamped_is_reported_not_hidden(stores):
    """The optimizer asked for a 4.9 mm tooth; the geometry validator clamped it
    to the 4.15 that fits.  The motor is still saved — losing it would be the
    worse failure — but the card says the machine is not what was scored."""
    out = _archive(overrides={"tooth_width": 4.9, "magnet_thickness": 3.3})
    assert [d["field"] for d in out["deviations"]] == ["tooth_width"]
    assert out["deviations"][0]["applied"] == pytest.approx(4.9)
    assert out["deviations"][0]["saved"] == pytest.approx(4.15)
    d = _read(stores["presets"])[out["id"]]["description"]
    assert "clamped by the geometry validator" in d and "tooth_width" in d
    # …and what is FILED is the geometry that really landed, not the request.
    assert _read(stores["presets"])[out["id"]]["geometry"]["tooth_width"] == pytest.approx(4.15)


# ── failure is loud ─────────────────────────────────────────────────────────

def test_a_failing_store_write_raises_instead_of_reporting_success(stores, monkeypatch):
    from motor_ai_sim.routes import presets as pre_mod

    def _boom(pid, preset):
        raise OSError("[Errno 28] No space left on device")

    monkeypatch.setattr(pre_mod, "_put_preset", _boom)
    with pytest.raises(HTTPException) as e:
        _archive()
    assert e.value.status_code == 500
    assert "No space left on device" in e.value.detail      # names the reason
    assert "applied" in e.value.detail                      # …and what to do
    assert _read(stores["presets"]) == {}                   # nothing pretended


def test_an_empty_geometry_is_refused_rather_than_saved_as_a_blank_motor(stores, monkeypatch):
    monkeypatch.setattr("motor_ai_sim.config.get_config",
                        lambda: {"geometry": {}, "simulation": {}})
    with pytest.raises(HTTPException) as e:
        _archive()
    assert e.value.status_code == 422
    assert "no geometry" in e.value.detail
    assert _read(stores["presets"]) == {}


def test_a_missing_catalog_card_does_not_turn_a_saved_motor_into_a_failure(stores, monkeypatch):
    """The motor is on disk; only its card is missing.  Reporting that as a
    failed save would send the engineer re-saving a design that IS saved."""
    from motor_ai_sim.routes import presets as pre_mod

    def _boom(*a, **kw):
        raise RuntimeError("catalog is unwritable")

    monkeypatch.setattr(pre_mod, "_upsert_catalog_entry", _boom)
    out = _archive()
    assert out["id"] in _read(stores["presets"])


# ── the UI paths are actually wired to it ───────────────────────────────────
#
# The archive is only worth anything if every apply button reaches it.  These
# read the shipping frontend sources: a new apply path added without archiving
# fails here rather than silently reintroducing the bug.

def _src(rel: str) -> str:
    return (_WEB / rel).read_text(encoding="utf-8")


def _body(text: str, start: str) -> str:
    """The source from `start` up to the next top-level handler in that file —
    enough to tell whether THIS handler calls the archive."""
    i = text.index(start)
    return text[i:i + 4000]


def test_the_optimizers_apply_paths_call_the_archive():
    store = _src("stores/motorStore.ts")
    assert "autoSaveAppliedDesign" in store
    for fn in ("applyDescentBest: async () => {", "applyDescentPoint: async (pt: any) => {"):
        assert "autoSaveAppliedDesign" in _body(store, fn), fn


def test_the_sweep_studys_picked_point_calls_the_archive():
    panel = _src("components/sweep/SweepStudyPanel.tsx")
    assert "autoSaveAppliedDesign" in _body(panel, "const applyPoint = async (p: any) => {")


def test_the_cards_that_apply_show_where_it_was_saved():
    """Item 4 of the brief: one short line per card, not a text wall."""
    for rel in ("components/sweep/AutoOptimizePanel.tsx",
                "components/sweep/DescentPanel.tsx",
                "components/sweep/SweepStudyPanel.tsx"):
        assert "appliedSaveLine" in _src(rel), rel


def test_the_archive_helper_never_sends_geometry_of_its_own():
    """The saved geometry must be the config the apply landed in — the one
    version of the truth both sides can check.  A client-side snapshot would
    reintroduce the silent-substitution class this project has been bitten by."""
    helper = _src("lib/appliedAutoSave.ts")
    assert "from_applied" in helper
    assert re.search(r"^\s*geometry:", helper, re.M) is None
