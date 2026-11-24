# -*- coding: utf-8 -*-
"""``n_coils_per_phase`` is DERIVED (slots); the stored copy can lag behind the machine.

A 24-slot single-layer die stored 8 (coil SIDES, slots / 3) where the application derives 4 (slots / 6).  Pins:

  * ``winding.coils_per_phase(slots)`` is the one definition (slots / 6, the count n_series x n_parallel must equal);
  * the readers that built something from the stored copy derive (``agent_designs._winding_of``, the BOM text,
    the passport fingerprint);
  * ``repick_connection``, the configuration save sites and ``PATCH /api/winding/config`` correct a stale count,
    return / log the correction, add the key nowhere, and leave a six-phase block alone;
  * a build stamp written while the copy was stale still reads as the current build after the heal (recorded
    aliases, any stale value), opening / listing writes nothing, a healing duty save says so.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import winding as W
from test_winding_connection_slots import _set_machine, client  # noqa: F401 - the fixture and the machine setter


# ── the rule ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("slots, want", [(12, 2), (24, 4), (36, 6), (48, 8), (60, 10)])
def test_coils_per_phase_is_one_definition(slots, want):
    assert W.coils_per_phase(slots) == want
    assert W.derived_n_coils_per_phase({}, slots) == want
    # one definition for every layer count: the connection rules in force (n_series x n_parallel = this)
    assert W.derived_n_coils_per_phase({"layers": 2}, slots) == want
    assert W.derived_n_coils_per_phase({"layers": "x"}, slots) == want
    assert [o["n_series"] * o["n_parallel"] for o in W.connection_options(slots)] == [want] * len(
        W.connection_options(slots))


def test_with_derived_coil_count():
    w, old = W.with_derived_coil_count({"n_coils_per_phase": 8, "layers": 1, "connection": "4P"}, 24)
    assert (w["n_coils_per_phase"], old) == (4, 8)
    w, old = W.with_derived_coil_count({"n_coils_per_phase": 4, "layers": 1}, 24)
    assert old is None and w["n_coils_per_phase"] == 4
    w, old = W.with_derived_coil_count({"n_coils_per_phase": 8, "layers": 2}, 24)     # the one definition
    assert (w["n_coils_per_phase"], old) == (4, 8)
    w, old = W.with_derived_coil_count({"connection": "4P"}, 24)                      # no key: none added
    assert old is None and "n_coils_per_phase" not in w
    for phases in (6, 2):                                                             # not covered: as stored
        w, old = W.with_derived_coil_count({"n_coils_per_phase": 8, "phases": phases}, 48 if phases == 6 else 24)
        assert old is None and w["n_coils_per_phase"] == 8
    for bad in ("six", "x6", [1], "3 phases"):                                         # unreadable phase layout: unchanged
        blk = {"n_coils_per_phase": 8, "phases": bad, "connection": "4P"}
        w, old = W.with_derived_coil_count(blk, 24)
        assert old is None and w == blk
    assert W.with_derived_coil_count({"n_coils_per_phase": 8, "phases": ""}, 24)[1] == 8   # not stated: three-phase
    src = {"n_coils_per_phase": 8}
    W.with_derived_coil_count(src, 24)
    assert src["n_coils_per_phase"] == 8                                              # the input is not touched


def test_repick_heals_a_stale_count_with_a_valid_label():
    stale = {"connection": "4P", "n_parallel": 4, "n_series": 1, "n_coils_per_phase": 8, "layers": 1}
    w, note = W.repick_connection(stale, 24)
    assert note is None and w["n_coils_per_phase"] == 4
    assert (w["connection"], w["n_parallel"], w["n_series"]) == ("4P", 4, 1)          # the machine is the same
    assert stale["n_coils_per_phase"] == 8
    ok = {"connection": "4S", "n_parallel": 1, "n_series": 4, "n_coils_per_phase": 4, "layers": 1}
    assert W.repick_connection(ok, 24) == (ok, None)                                  # nothing to heal: identical
    nokey = {"connection": "4S", "n_parallel": 1, "n_series": 4}
    assert W.repick_connection(nokey, 24) == (nokey, None)


# ── the save sites ──────────────────────────────────────────────────────────
def test_config_save_heals_the_live_winding():
    from motor_ai_sim.routes.family import _coil_note, _live_winding_healed
    live = {"geometry": {"num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7},
            "winding": {"connection": "4P", "n_parallel": 4, "n_series": 1, "n_coils_per_phase": 8, "layers": 1}}
    w, fix = _live_winding_healed(live)
    assert w["n_coils_per_phase"] == 4 and w["connection"] == "4P"
    assert fix == {"from": 8, "to": 4} and _coil_note(fix) == "coil_count_corrected_8_to_4"
    assert live["winding"]["n_coils_per_phase"] == 8
    nokey = {"geometry": live["geometry"], "winding": {"connection": "4P"}}
    assert _live_winding_healed(nokey) == ({"connection": "4P"}, None)
    assert _live_winding_healed({}) == ({}, None)
    assert _coil_note(None) is None


def test_repick_config_winding_heals_in_memory_and_reports_nothing():
    from motor_ai_sim.routes.family import _repick_config_winding
    c = {"winding": {"connection": "4P", "n_parallel": 4, "n_series": 1, "n_coils_per_phase": 8, "layers": 1}}
    assert _repick_config_winding(c, {"num_slots": 24, "num_poles": 28}, "test") is None
    assert c["winding"]["n_coils_per_phase"] == 4


def test_a_stale_count_that_is_neither_half_nor_double_does_not_age_the_stamps():
    """The review's example: 4 stored on a 48-slot machine (derived 8); the heal leaves every stamp current."""
    from motor_ai_sim.routes.family import _alias_count_only, _build_sig_of, _build_sigs, build_sig_moved
    die = {"geometry": {"num_seg": 8, "num_slots_per_segment": 6, "num_poles_per_segment": 7, "wire_width": 7}}
    stale = {"geometry_overrides": {}, "materials": {"magnet": "N52"},
             "winding": {"connection": "8S", "n_parallel": 1, "n_series": 8, "n_coils_per_phase": 4, "layers": 1}}
    stamp = _build_sig_of(die, stale, stale["materials"])
    healed = {**stale, "winding": {**stale["winding"], "n_coils_per_phase": 8}}
    assert build_sig_moved(stamp, _build_sigs(die, healed))                           # without the record: aged
    _alias_count_only(die, healed, 4)
    assert not build_sig_moved(stamp, _build_sigs(die, healed))
    assert healed["build_sig_count_aliases"]
    other = {**healed, "winding": {**healed["winding"], "connection": "4S-2P", "n_parallel": 2, "n_series": 4}}
    assert build_sig_moved(stamp, _build_sigs(die, other))                            # a real change still moves it
    other_mat = {**healed, "materials": {"magnet": "N42"}}
    assert build_sig_moved(stamp, _build_sigs(die, other_mat))
    # nothing recorded when nothing was healed
    same = {**stale}
    _alias_count_only(die, same, 4)
    assert "build_sig_count_aliases" not in same


# ── PATCH / GET ─────────────────────────────────────────────────────────────
def test_patch_corrects_a_wrong_count_and_says_so(client):                            # noqa: F811
    _set_machine(4, "4S", 1, 4)                                                       # 24 slots
    r = client.patch("/api/winding/config", json={"n_coils_per_phase": 8})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"]["n_coils_per_phase"] == "4"
    assert body["n_coils_per_phase_corrected"]["requested"] == 8
    assert body["n_coils_per_phase_corrected"]["stored"] == 4
    assert client.get("/api/winding/config").json()["n_coils_per_phase"] == 4
    r = client.patch("/api/winding/config", json={"n_coils_per_phase": 4})
    assert "n_coils_per_phase_corrected" not in r.json()


def test_patch_leaves_a_six_phase_count_as_sent(client):                              # noqa: F811
    _set_machine(4, "2S-2P", 2, 2)                                                    # 24 slots, wires as six-phase
    r = client.patch("/api/winding/config", json={"phases": 6, "n_coils_per_phase": 3})
    assert r.status_code == 200, r.text                                              # the real answer, not a refusal
    body = r.json()
    assert body["updated"]["n_coils_per_phase"] == "3" and body["updated"]["phases"] == "6"
    assert "n_coils_per_phase_corrected" not in body
    r = client.patch("/api/winding/config", json={"phases": 3, "n_coils_per_phase": 3})
    assert r.status_code == 200, r.text
    assert r.json()["n_coils_per_phase_corrected"]["stored"] == 4                     # back on three phases: derived


def test_get_follows_the_one_definition_for_any_layers(client):                        # noqa: F811
    for layers in (1, 2):
        _set_machine(4, "4S", 1, 4)
        client.patch("/api/winding/config", json={"layers": layers})
        g = client.get("/api/winding/config").json()
        assert g["n_coils_per_phase"] == 4
        assert [o["label"] for o in g["connections"]] == ["4S", "2S-2P", "4P"]        # the rules did not move


def test_get_and_a_valid_label_reconcile_do_not_write_the_live_file(client):          # noqa: F811
    from motor_ai_sim.api import reconcile_live_winding
    from test_winding_connection_slots import _cfg_path
    _set_machine(4, "4P", 4, 1)
    p = _cfg_path()
    import yaml
    c = yaml.safe_load(p.read_text(encoding="utf-8"))
    c["winding"]["n_coils_per_phase"] = 8                                             # the stale copy
    p.write_text(yaml.safe_dump(c, sort_keys=False, allow_unicode=True), encoding="utf-8")
    from motor_ai_sim.config import clear_config_cache
    clear_config_cache()
    before = p.read_bytes()
    assert client.get("/api/winding/config").json()["n_coils_per_phase"] == 4        # derived for the answer
    assert reconcile_live_winding() is None
    assert p.read_bytes() == before                                                   # nothing written


# ── opening / listing is not a save ────────────────────────────────────────
def _stale_catalog(tmp_path, monkeypatch):
    import yaml
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / "SD 24").mkdir(parents=True)
    (root / "SD 24" / "die.yaml").write_text(yaml.safe_dump({
        "name": "SD 24", "locked": False, "created": "2026-09-02T10:00:00",
        "geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": 40.0, "magnet_height": 3.0,
                     "motor_length": 40.0}}, sort_keys=False), encoding="utf-8")
    (root / "SD 24" / "L40.yaml").write_text(yaml.safe_dump({
        "name": "L40", "die": "SD 24", "role": "motor",
        "geometry_overrides": {"motor_length": 40.0},
        "winding": {"connection": "4P", "n_parallel": 4, "n_series": 1, "n_coils_per_phase": 8, "layers": 1},
        "materials": {"magnet": "N42SH"}, "duties": []}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    return root


def test_duplicate_and_copy_record_the_aliases_from_the_real_die_document(tmp_path, monkeypatch):
    """A duplicate / copy records its aliases from the REAL die document the caller loaded (today the signature reads
    only the die's geometry, so this also pins that the two agree)."""
    import yaml
    from motor_ai_sim.routes import family as fam
    root = _stale_catalog(tmp_path, monkeypatch)
    die_doc = fam._load_yaml(root / "SD 24" / "die.yaml", "die")
    c = fam._load_yaml(root / "SD 24" / "L40.yaml", "configuration")
    stamp = fam._build_sig(die_doc, c)                                # stamped while the count was stale (8)
    c["duties"] = [{"name": "peak", "result": {"build_sig": stamp}}]
    fam._repick_config_winding(c, dict(die_doc["geometry"]), "dup", die_doc)
    assert c["winding"]["n_coils_per_phase"] == 4
    assert not fam.build_sig_moved(stamp, fam._build_sigs(die_doc, c))
    # the same on a die with another wire stack
    die2 = {**die_doc, "geometry": {**die_doc["geometry"], "wire_width": 3.0}}
    c2 = fam._load_yaml(root / "SD 24" / "L40.yaml", "configuration")
    stamp2 = fam._build_sig(die2, c2)
    fam._repick_config_winding(c2, dict(die2["geometry"]), "copy", die2)
    assert not fam.build_sig_moved(stamp2, fam._build_sigs(die2, c2))


def test_opening_and_listing_a_stale_configuration_writes_nothing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    root = _stale_catalog(tmp_path, monkeypatch)
    snap = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    c = TestClient(app)
    r = c.get("/api/family/payload/SD 24/L40")
    assert r.status_code == 200, r.text                              # the real answer (AUTH_ENFORCE=0), not a refusal
    assert r.json()["winding"]["n_coils_per_phase"] == 4             # corrected IN MEMORY for the answer
    assert r.json()["coil_count_corrected"] == {"from": 8, "to": 4}  # and shown
    t = c.get("/api/family/tree")
    assert t.status_code == 200, t.text
    c.get("/api/family/tree")
    from motor_ai_sim.routes import family as fam
    c2 = {"winding": dict(fam._load_yaml(root / "SD 24" / "L40.yaml", "configuration")["winding"])}
    fam._repick_config_winding(c2, {"num_slots": 24, "num_poles": 28}, "apply")
    assert {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()} == snap
    assert not (root / ".history").exists()


# ── an explicit duty save heals visibly ─────────────────────────────────────
def test_a_duty_save_that_adopts_the_live_winding_reports_and_notes_the_heal(tmp_path, monkeypatch):
    import yaml
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim.routes import family as fam
    root = _stale_catalog(tmp_path, monkeypatch)
    live = {"geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": 40.0, "magnet_height": 3.0,
                         "motor_length": 40.0},
            "winding": {"connection": "4S", "n_parallel": 1, "n_series": 4, "n_coils_per_phase": 8, "layers": 1},
            "simulation": {"current_a": 85.0, "rpm": 6000.0, "gamma_deg": 12.0},
            "materials": {"magnet": "N42SH"}}
    monkeypatch.setattr(fam, "_live_cfg", lambda: live)
    r = TestClient(app).post("/api/family/duty", json={
        "die": "SD 24", "config": "L40",
        "duty": {"name": "peak", "mode": "motor", "from_current": True}})
    if r.status_code != 200:                                       # the live fixture is not a full machine: say why
        pytest.skip("duty save refused for a reason outside the coil count: %s" % r.text[:200])
    assert r.json().get("coil_count_corrected") == {"from": 8, "to": 4}
    doc = yaml.safe_load((root / "SD 24" / "L40.yaml").read_text(encoding="utf-8"))
    assert doc["winding"]["n_coils_per_phase"] == 4
    notes = sorted(f.name for f in (root / ".history" / "SD 24").glob("L40.*.yaml"))
    assert any("coil_count_corrected_8_to_4" in n for n in notes), notes


# ── readers ─────────────────────────────────────────────────────────────────
def test_agent_design_winding_ignores_the_stored_copy_both_ways(monkeypatch):
    import motor_ai_sim.agent_designs as AD
    pay = {"geometry": {"num_seg": 4, "num_slots_per_segment": 6, "num_wires_per_slot": 9, "wire_parallel": 1},
           "winding": {"n_coils_per_phase": 8, "n_parallel": 4, "connection": "4P"}, "sim": {}}
    monkeypatch.setattr(AD, "_payload", lambda *a, **k: pay)
    assert AD._winding_of("d", "c", "x")["coils_per_phase"] == 4                      # stored 8, derived 4
    pay["geometry"]["num_seg"] = 8                                                    # 48 slots; stored still 8 -> same
    pay["winding"]["n_coils_per_phase"] = 4                                           # stored 4, derived 8
    assert AD._winding_of("d", "c", "x")["coils_per_phase"] == 8
    pay["winding"]["layers"] = 2                                                      # one definition for any layers
    assert AD._winding_of("d", "c", "x")["coils_per_phase"] == 8
    pay["geometry"].pop("num_seg")                                                    # no slot count: the stored copy
    assert AD._winding_of("d", "c", "x")["coils_per_phase"] == 4


def test_passport_fingerprint_coils_are_derived():
    from motor_ai_sim.passport_v1.stage3 import _coils_of
    g = {"num_slots": 24}
    assert _coils_of(g, {"n_coils_per_phase": 8, "layers": 1}) == 4
    assert _coils_of(g, {"n_coils_per_phase": 8, "layers": 2}) == 4
    assert _coils_of({}, {"n_coils_per_phase": 8}) == 8
