"""Three bugs found while running a private study die (owner 2026-11-09).

1. A duplicated die's new configurations were auto-linked to the SOURCE die's
   customer project (``development.autolink``): see also
   ``tests/test_project_live_activity.py`` (the pure autolink rules).  Here: the
   family hook, with a real project store and a duplicated die.
2. The open-frame thermal model counted one coil per tooth (``n_coils =
   num_slots``) on a single-layer winding that winds every second tooth.
3. ``wire_split`` strips were counted as extra SERIES turns (34 turns for 17
   rows split in two), and a slot-width change silently created a new die.

Synthetic data only; no FEM solve, nothing reads or writes ``config/``.
"""
from __future__ import annotations

import json

import pytest

from motor_ai_sim.development import autolink, motors, store
from motor_ai_sim.routes import family as fam
from motor_ai_sim.winding import (coil_count, conductors_per_slot,
                                  n_parallel_effective, turns_per_coil,
                                  winding_footprint_mm)

# the die fixture and helpers of the shared-die tests (a throwaway catalog)
from tests.test_die_shared_snapshot_guard import (DIE, GEO, _activate, _bytes,  # noqa: F401
                                                  _ctx, _names, dies)


# ─────────────────────────────────────────────────────────────────────────────
# 1. a duplicated die is never linked to the source die's project
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def customer_project(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "customer-project", "status": "design", "members": [], "linked_motors": [
        {"die": "SOURCE DIE", "config": "base", "status": "current"}]})
    store.save_index({"motors": {
        motors._key("SOURCE DIE", "base"): {"project_id": "customer-project", "die": "SOURCE DIE", "config": "base"}}})
    monkeypatch.setattr(motors, "_capture_project_motor_snapshot", lambda *a, **k: None)
    return tmp_path


def _linked_configs(project_id: str = "customer-project"):
    return [(m["die"], m["config"]) for m in store.get_project(project_id)["linked_motors"]]


def test_a_config_created_in_a_duplicated_die_does_not_join_the_source_dies_project(customer_project, monkeypatch):
    """The 2026-11-09 leak, through the family hook: the study die is a copy of
    SOURCE DIE (``derived_from``), and the editor's loaded motor is SOURCE DIE's
    ``base`` (the new configuration's ``source_config``)."""
    monkeypatch.setattr(autolink, "writes_shared_catalog", lambda: True)
    monkeypatch.setattr(autolink, "source_dies", lambda die, copied_from=None: [die, "SOURCE DIE"])  # derived_from
    fam._autolink_new_config("STUDY DIE", "S1", None, copied_from="SOURCE DIE", source_config="base")
    fam._autolink_new_config("STUDY DIE", "S2", None)                       # created from scratch, no source
    assert _linked_configs() == [("SOURCE DIE", "base")]
    assert not any("STUDY" in k for k in store.load_index()["motors"])


def test_a_new_config_in_the_linked_die_itself_still_auto_links(customer_project, monkeypatch):
    monkeypatch.setattr(autolink, "writes_shared_catalog", lambda: True)
    fam._autolink_new_config("SOURCE DIE", "base 2", None, source_config="base")
    assert ("SOURCE DIE", "base 2") in _linked_configs()


# ─────────────────────────────────────────────────────────────────────────────
# 2. open-frame coil count follows the winding as wound
# ─────────────────────────────────────────────────────────────────────────────

def test_coil_count_single_layer_is_every_second_tooth_double_layer_every_tooth():
    assert coil_count(24, 1) == 12            # CIANO20 200_50: 24 slots, layers 1, layout A|a|b|B...
    assert coil_count(24, 2) == 24
    assert coil_count(12, 1) == 6
    assert coil_count(12, 2) == 12
    assert coil_count(0, 1) == 0


def test_open_frame_n_coils_comes_from_the_winding_layers_and_says_so():
    from motor_ai_sim.routes.thermal import _coils_as_wound
    n, note = _coils_as_wound({"winding": {"layers": 1}}, 24)
    assert n == 12 and "12 coils" in note and "single layer" in note and "num_slots = 24" in note
    n, note = _coils_as_wound({"winding": {"layers": 2}}, 24)
    assert n == 24 and "24 coils" in note and "double layer" in note
    # the ?geo= override changes the tooth count, the winding block supplies the layers
    assert _coils_as_wound({"winding": {"layers": 1}}, 48)[0] == 24
    # a config without the field is a single-layer winding (the stored default)
    assert _coils_as_wound({}, 24)[0] == 12


def test_the_end_winding_area_halves_with_the_coil_count():
    from motor_ai_sim.routes.thermal import _coils_as_wound
    from motor_ai_sim.simulation import cooling_models as cm
    kw = dict(air_speed_mps=10.0, t_ambient_c=30.0, bar_thickness_m=5e-3, bar_width_m=6e-3,
              end_turn_length_m=20e-3, n_sides=2)
    single = cm.end_windings_path(n_coils=_coils_as_wound({"winding": {"layers": 1}}, 24)[0], **kw)
    double = cm.end_windings_path(n_coils=_coils_as_wound({"winding": {"layers": 2}}, 24)[0], **kw)
    assert single["n_coils"] == 12 and double["n_coils"] == 24
    assert single["area_m2"] == pytest.approx(0.5 * double["area_m2"], rel=1e-9)


def test_the_thermal_route_uses_the_helper_for_both_end_winding_models():
    import inspect
    from motor_ai_sim.routes import thermal as th
    src = inspect.getsource(th)
    assert "_n_coils, _n_coils_note = _coils_as_wound(cfg, num_slots)" in src
    assert "n_coils=int(_coils_as_wound(cfg, num_slots)[0])" in src       # the end-face model
    assert "one coil per tooth (concentrated winding): n = num_slots" not in src


# ─────────────────────────────────────────────────────────────────────────────
# 3a. wire_split strips are parallel conductors of ONE turn
# ─────────────────────────────────────────────────────────────────────────────

SPLIT_GEO = {"num_wires_per_slot": 17, "wire_width": 3.2, "wire_split": 2, "wire_spacing_x": 0.05,
             "wire_height": 1.0}


def test_a_split_adds_no_turn_and_divides_the_strip_current():
    assert turns_per_coil(SPLIT_GEO) == 17                          # was 34
    assert turns_per_coil(dict(SPLIT_GEO, wire_split=1)) == 17
    assert conductors_per_slot(SPLIT_GEO) == 34                     # two drawn strips per row
    assert n_parallel_effective(1, SPLIT_GEO) == 2                  # each strip carries I_coil / 2
    assert n_parallel_effective(2, dict(SPLIT_GEO, wire_parallel=1)) == 4
    # ampere-turns per slot = conductors x strip current = turns x branch current, split or not
    i_branch = 100.0
    assert conductors_per_slot(SPLIT_GEO) * i_branch / n_parallel_effective(1, SPLIT_GEO) \
        == turns_per_coil(SPLIT_GEO) * i_branch
    # strands in hand and strips compose
    g = dict(SPLIT_GEO, num_wires_per_slot=18, wire_parallel=3)
    assert turns_per_coil(g) == 6 and n_parallel_effective(1, g) == 6


def test_the_slot_width_grows_only_by_the_strip_insulation():
    """The derivation: N*wire_width + (N-1)*2*wire_spacing_x.  A split of one 6.4 mm bar into
    two 3.2 mm strips adds exactly the 2*wire_spacing_x between them (0.1 mm here)."""
    bar = dict(SPLIT_GEO, wire_width=6.4, wire_split=1)
    assert winding_footprint_mm(bar) == pytest.approx(6.4)
    assert winding_footprint_mm(SPLIT_GEO) == pytest.approx(2 * 3.2 + 2 * 0.05)        # 6.5
    assert winding_footprint_mm(SPLIT_GEO) - winding_footprint_mm(bar) == pytest.approx(0.1)


def test_the_winding_config_route_reports_the_unsplit_turn_count(monkeypatch):
    """PUT /api/geometry {"num_wires_per_slot": 17, "wire_width": 3.2, "wire_split": 2} read back
    turns_per_coil 34 and doubled the amp-turns; it is 17 now."""
    from motor_ai_sim import api
    cfg = {"geometry": {"num_seg": 8, "num_slots_per_segment": 3, "num_poles_per_segment": 2.5,
                        "num_slots": 24, "num_poles": 20, **SPLIT_GEO},
           "winding": {"connection": "4S", "n_parallel": 1, "n_series": 4, "n_coils_per_phase": 4, "layers": 1},
           "simulation": {"max_current": 100.0}}
    monkeypatch.setattr(api, "get_config", lambda: cfg)
    out = api.get_winding_config()
    assert out["turns_per_coil"] == 17
    assert out["n_parallel_eff"] == 2
    assert out["amp_turns_per_slot"] == 1700.0           # 17 turns x 100 A, not 34 x 100 A
    cfg["geometry"]["wire_split"] = 1
    assert api.get_winding_config()["amp_turns_per_slot"] == 1700.0


def test_no_consumer_counts_strips_as_turns():
    """Every reader of wire_split goes through the winding helpers or its own strip-count
    (conductors) arithmetic; the series-turn wording is gone from the code that carried it."""
    import inspect
    import motor_ai_sim.winding as w
    import motor_ai_sim.routes._validation as v
    src = inspect.getsource(w.turns_per_coil)
    assert "wire_split" not in src.split('"""')[2]          # the body no longer multiplies by the split
    assert "PARALLEL" in v.SCHEMA_DESCRIPTION_OVERRIDE["wire_split"]
    assert "SERIES" not in v.SCHEMA_DESCRIPTION_OVERRIDE["wire_split"]


# ─────────────────────────────────────────────────────────────────────────────
# 3b. a geometry edit that would create a die says so, and can be refused
# ─────────────────────────────────────────────────────────────────────────────

LIVE_EDIT = dict(GEO, magnet_height=23.0, slot_height=22.0, magnet_down=2.5)    # reshapes the shared die


def test_the_preview_names_the_die_a_save_would_create_and_creates_nothing(dies):
    _activate("L100")
    before, names, ctx = _bytes(dies), _names(dies), _ctx()
    would = fam.preview_die_transition(dict(LIVE_EDIT), prev_geo=dict(GEO))
    assert would["reason"] == "shared_die" and would["from_die"] == DIE
    assert would["to_die"] == "CIANO56 400_80 vadim" and would["creates_die"] is True
    assert any(d.startswith("magnet_height") for d in would["diffs"])
    assert _bytes(dies) == before and _names(dies) == names and _ctx() == ctx   # side-effect free
    # an edit that stays inside the die is not a transition
    assert fam.preview_die_transition(dict(GEO), prev_geo=dict(GEO)) is None


def test_a_refused_transition_creates_nothing_and_reports_it(dies):
    _activate("L100")
    before, names, ctx = _bytes(dies), _names(dies), _ctx()
    report: dict = {}
    assert fam.sync_active_die_geometry(dict(LIVE_EDIT), prev_geo=dict(GEO),
                                        allow_transition=False, report=report) is None
    assert report["blocked"] is True and report["reason"] == "shared_die" and report["from_die"] == DIE
    assert _bytes(dies) == before and _names(dies) == names and _ctx() == ctx


def test_the_default_still_transitions_and_the_report_names_the_new_die(dies):
    _activate("L100")
    report: dict = {}
    new = fam.sync_active_die_geometry(dict(LIVE_EDIT), prev_geo=dict(GEO), report=report)
    assert new == "CIANO56 400_80 vadim"
    assert report["to_die"] == new and report["from_die"] == DIE and report["created"] is True
    assert report["config"] == "L100" and report["reason"] == "shared_die"
    assert new in _names(dies)


def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim.routes import geometry as g
    # the content guards and the family lock are not what is tested here
    monkeypatch.setattr(g, "check_geometry_submission", lambda raw: {})
    monkeypatch.setattr(fam, "geometry_lock_check", lambda raw: None)

    def _no_write(**kw):                                       # a write before the 409 would be the bug
        raise AssertionError("the live geometry was written although the transition was refused")
    monkeypatch.setattr(g, "update_current_geometry", _no_write)
    return TestClient(app)


def test_put_geometry_with_allow_die_transition_false_answers_409_and_writes_nothing(dies, monkeypatch):
    _activate("L100")
    import motor_ai_sim.config as cfgmod
    monkeypatch.setattr(cfgmod, "get_config", lambda: {"geometry": dict(GEO)})
    # the merged prospective geometry is what the die sees after the save
    client = _client(monkeypatch)
    before, names, ctx = _bytes(dies), _names(dies), _ctx()
    body = {"magnet_height": 23.0, "slot_height": 22.0, "magnet_down": 2.5, "allow_die_transition": False}
    r = client.put("/api/geometry", json=body)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "die transition not allowed"
    assert detail["die_transition"]["to_die"] == "CIANO56 400_80 vadim"
    assert "nothing was written" in detail["message"]
    assert _bytes(dies) == before and _names(dies) == names and _ctx() == ctx
    # the same through the query parameter
    r = client.put("/api/geometry?allow_die_transition=false",
                   json={"magnet_height": 23.0, "slot_height": 22.0, "magnet_down": 2.5})
    assert r.status_code == 409, r.text
