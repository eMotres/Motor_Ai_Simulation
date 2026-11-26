"""shaft_conv follow-up of PR #900: the stored result carries it only beside a marked summary; the project motors block
shows the shaft fields of marked records only; a one-off repair lists / restores the flag.  Temp stores only."""
from __future__ import annotations

import yaml
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import shaft_convention as SC
from motor_ai_sim import shaft_conv_repair as repair
from motor_ai_sim.api import app
from motor_ai_sim.development import motors

DIE, CFG, DUTY = "SYNTH DIE 40", "L40", "rated"
client = TestClient(app)
MARKED = {"shaft_convention": SC.SHAFT_CONVENTION, "T_shaft_Nm": 4.1, "shaft_k3d": 0.97, "op_mode": "motor",
          "rpm": 6000.0, "I_phase_rms_A": 85.0, "gamma_deg": 12.0, "T_em_avg_Nm": 4.2}
UNMARKED = {"rpm": 6000.0, "I_phase_rms_A": 85.0, "gamma_deg": 12.0, "T_em_avg_Nm": 4.2}


@pytest.fixture()
def dies(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-09-02T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0, "magnet_height": 3.0,
                     "motor_length": 40.0}}, sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor", "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"}, "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor", "saved_at": "2026-09-02T10:00:00", "current_arms": 85.0,
                    "rpm": 6000.0, "gamma_deg": 12.0, "note": ""}]}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    return root


def _duty(dies):
    doc = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    return next(d for d in doc["duties"] if d["name"] == DUTY)


def _save_duty(summary):
    body = {"name": DUTY, "mode": "motor", "from_current": False, "current_arms": 85.0, "rpm": 6000.0,
            "gamma_deg": 12.0, "drive": "current", "mesh": {"sim.drive": "current"}, "summary": dict(summary)}
    assert client.post("/api/family/duty", json={"die": DIE, "config": CFG, "duty": body}).status_code == 200


def _result(flag=None, marker=None, **extra):
    body = {"die": DIE, "config": CFG, "duty": DUTY, "drive": "current",
            "result": {"efficiency_pct": 90.0, "mass_kg": 1.0, **({"shaft_conv": flag} if flag is not None else {})}}
    if marker:
        body["shaft_convention"] = marker
    return client.post("/api/family/duty_result", json=body)


def _run(summary):
    return client.post("/api/family/duty_run", json={"die": DIE, "config": CFG, "duty": DUTY, "drive": "current",
                                                     "summary": dict(summary), "allow_unverified": True})


def test_marked_summary_and_client_flag_store_shaft_conv(dies):
    _save_duty(MARKED)
    assert _result(flag=1).status_code == 200
    assert _duty(dies)["result"]["shaft_conv"] == 1


def test_unmarked_summary_and_client_flag_do_not_store_it(dies):
    _save_duty(UNMARKED)
    assert _result(flag=1).status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]
    # ... and the client's own marker string alone cannot be wrong about the summary it saves later
    assert _run(UNMARKED).status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]


def test_result_before_run_cannot_publish_a_client_witness(dies):
    # the real order: /duty_result first (the stored summary is still the old run's), /duty_run after
    _save_duty(UNMARKED)
    assert _result(flag=1, marker=SC.SHAFT_CONVENTION).status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]
    assert _run(MARKED).status_code == 200
    assert _duty(dies)["result"]["shaft_conv"] == 1
    assert _duty(dies)["runs"]["current"]["result"]["shaft_conv"] == 1


def test_no_flag_with_marked_summary_is_derived_by_the_run_save(dies):
    _save_duty(UNMARKED)
    assert _result().status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]
    assert _run(MARKED).status_code == 200
    assert _duty(dies)["result"]["shaft_conv"] == 1


def test_run_with_unmarked_summary_drops_a_stale_flag(dies):
    _save_duty(MARKED)
    _result(flag=1)
    assert _run(UNMARKED).status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]


def test_unmarked_result_keeps_its_old_shape(dies):
    _save_duty(UNMARKED)
    assert _result().status_code == 200
    res = _duty(dies)["result"]
    assert set(res) == {"efficiency_pct", "mass_kg", "recorded_at", "build_sig"} or set(res) == {
        "efficiency_pct", "mass_kg", "recorded_at"}


# ── the project motors block ────────────────────────────────────────────────────────────────

def test_motors_block_shaft_fields_on_marked_records_only():
    cfg = {"duties": [
        {"name": "m", "rpm": 1000, "result": {"t_em_avg_nm": 8, "p_mech_w": 700, "shaft_conv": 1},
         "summary": {**MARKED, "P_mech_W": 700.0}},
        {"name": "u", "rpm": 1000, "result": {"t_em_avg_nm": 8, "p_mech_w": 700},
         "summary": {"T_em_avg_Nm": 8.0, "P_mech_W": 700.0, "T_shaft_Nm": 7.9, "shaft_k3d": 0.9}}]}
    rows = motors._results("SYNTH", "A", cfg)
    em_m, em_u = rows[0]["electromagnetic"], rows[1]["electromagnetic"]
    assert em_m["t_shaft_nm"] == 4.1 and em_m["shaft_k3d"] == 0.97 and em_m["shaft_convention"] == SC.SHAFT_CONVENTION
    assert em_m["t_em_avg_nm"] == 8 and em_m["p_mech_w"] == 700
    assert not {"t_shaft_nm", "shaft_k3d", "shaft_convention"} & set(em_u)      # unmarked: exact old shape


def test_missing_shaft_number_is_absent_not_zero():
    out = motors.shaft_fields({"shaft_convention": SC.SHAFT_CONVENTION, "shaft_k3d": None})
    assert out == {"shaft_convention": SC.SHAFT_CONVENTION}
    assert motors.shaft_fields(None) == {} and motors.shaft_fields({"T_shaft_Nm": 1.0}) == {}


def test_safe_projection_passes_only_the_marker_and_finite_numbers():
    assert motors.safe_shaft_fields({"shaft_convention": "other", "t_shaft_nm": 1}) == {}
    assert motors.safe_shaft_fields({"shaft_convention": SC.SHAFT_CONVENTION, "t_shaft_nm": float("nan"),
                                     "shaft_k3d": 0.9}) == {"shaft_convention": SC.SHAFT_CONVENTION, "shaft_k3d": 0.9}


# ── the repair tool ─────────────────────────────────────────────────────────────────────────

def _doc():
    return {"duties": [
        {"name": "a", "summary": dict(MARKED), "result": {"efficiency_pct": 90.0},
         "runs": {"pwm_voltage": {"summary": dict(MARKED), "result": {"efficiency_pct": 88.0}}}},
        {"name": "b", "summary": dict(MARKED), "result": {"efficiency_pct": 90.0, "shaft_conv": 1}},
        {"name": "c", "summary": dict(UNMARKED), "result": {"efficiency_pct": 90.0}},
        {"name": "d", "summary": dict(MARKED)}]}


def test_plan_lists_duty_and_drive_without_values():
    plan = repair.plan_config(_doc())
    assert plan == [{"duty": "a", "drive": None}, {"duty": "a", "drive": "pwm_voltage"}]


def test_apply_sets_only_the_one_key_and_is_idempotent():
    doc = _doc()
    before = yaml.safe_dump(doc)
    assert repair.apply_config(doc) == 2
    assert doc["duties"][0]["result"] == {"efficiency_pct": 90.0, "shaft_conv": 1.0}
    after = yaml.safe_load(yaml.safe_dump(doc))
    del after["duties"][0]["result"]["shaft_conv"]
    del after["duties"][0]["runs"]["pwm_voltage"]["result"]["shaft_conv"]
    assert yaml.safe_dump(after) == before
    assert repair.apply_config(doc) == 0 and repair.plan_config(doc) == []


def test_repair_dry_run_writes_nothing_and_apply_backs_up(dies, tmp_path):
    p = dies / DIE / f"{CFG}.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["duties"][0].update({"summary": dict(MARKED), "result": {"efficiency_pct": 90.0}})
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    original = p.read_text(encoding="utf-8")
    dry = repair.repair()
    assert dry["applied"] is False and [(r["die"], r["config"], r["duty"]) for r in dry["rows"]] == [(DIE, CFG, DUTY)]
    assert p.read_text(encoding="utf-8") == original
    with pytest.raises(ValueError):
        repair.repair(apply=True)
    done = repair.repair(apply=True, backup_dir=tmp_path / "bak")
    assert done["saved"] == 1 and not done["failed"]
    assert _duty(dies)["result"]["shaft_conv"] == 1
    backups = list((tmp_path / "bak" / DIE).glob(f"{CFG}.yaml.bak-shaftconv-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == original
    assert repair.scan() == []


def _marked_cfg(dies, **extra):
    p = dies / DIE / f"{CFG}.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["duties"][0].update({"summary": dict(MARKED), "result": {"efficiency_pct": 90.0}})
    doc.update(extra)
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


def test_the_repair_save_is_metadata_only_no_derived_geometry_refresh(dies, tmp_path, monkeypatch):
    from motor_ai_sim.geometry import motor_geometry as MG
    calls = []

    def fake_refresh(g):                                    # what a normal save does to a stale derived field
        calls.append(1)
        return {**g, "num_slots": 12}
    monkeypatch.setattr(MG, "refresh_derived_geometry", fake_refresh)
    monkeypatch.setattr(MG, "fresh_derived_on_write", lambda: True)
    p = _marked_cfg(dies, geometry_overrides={"motor_length": 40.0, "num_slots": 99})
    done = repair.repair(apply=True, backup_dir=tmp_path / "bak")
    assert done["saved"] == 1 and not calls
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["geometry_overrides"]["num_slots"] == 99 and doc["duties"][0]["result"]["shaft_conv"] == 1
    # control: a normal save of the same document DOES refresh (so the guard above is meaningful)
    from motor_ai_sim.routes import family as fam
    fam._save_yaml(p, yaml.safe_load(p.read_text(encoding="utf-8")))
    assert calls and yaml.safe_load(p.read_text(encoding="utf-8"))["geometry_overrides"]["num_slots"] == 12


def test_metadata_only_save_never_redirects_or_redacts(dies, monkeypatch):
    from fastapi import HTTPException
    from motor_ai_sim import construction_privacy as cp
    from motor_ai_sim.routes import family as fam
    p = _marked_cfg(dies)
    redirected = []
    monkeypatch.setattr(fam, "_write_target", lambda q: redirected.append(q) or q.with_name("elsewhere.yaml"))
    fam._save_yaml(p, yaml.safe_load(p.read_text(encoding="utf-8")), metadata_only=True)
    assert not redirected and not (p.parent / "elsewhere.yaml").exists()
    # layered save by a caller without the construction gate: a normal save redacts, metadata-only refuses and writes nothing
    monkeypatch.setattr(fam, "_ws_layering", lambda: True)
    monkeypatch.setattr(fam, "_classify_die_dir", lambda d: (fam._WS.LAYER_WORKSPACE, None))
    monkeypatch.setattr(cp, "caller_may_keep_construction", lambda: False)
    before = p.read_bytes()
    with pytest.raises(HTTPException) as exc:
        fam._save_yaml(p, yaml.safe_load(before), metadata_only=True)
    assert exc.value.status_code == 409 and p.read_bytes() == before


def test_frozen_and_production_linked_configurations_are_skipped_in_dry_run_and_apply(dies, tmp_path, monkeypatch):
    from motor_ai_sim import motor_revisions as MR
    p = _marked_cfg(dies)
    original = p.read_bytes()
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "prod"))
    from motor_ai_sim.production import store as ps
    (ps.path_for(DIE, CFG)).mkdir(parents=True, exist_ok=True)
    (ps.path_for(DIE, CFG) / "production.json").write_text("{}", encoding="utf-8")
    dry = repair.scan()
    assert [r.get("skip") for r in dry] == ["production_linked"]
    done = repair.repair(apply=True, backup_dir=tmp_path / "bak")
    assert done["saved"] == 0 and p.read_bytes() == original and not (tmp_path / "bak").exists()
    (ps.path_for(DIE, CFG) / "production.json").unlink()
    monkeypatch.setattr(MR, "find", lambda d, c, **kw: ({"id": "x"}, {"frozen": True}))
    assert [r.get("skip") for r in repair.scan()] == ["frozen_revision"]
    assert repair.repair(apply=True, backup_dir=tmp_path / "bak")["saved"] == 0 and p.read_bytes() == original
    # unreadable protection metadata is a refusal, not a guess
    monkeypatch.setattr(MR, "find", lambda d, c, **kw: (_ for _ in ()).throw(OSError("store unreadable")))
    assert [r.get("skip") for r in repair.scan()] == ["protection_unreadable"]
    assert repair.repair(apply=True, backup_dir=tmp_path / "bak")["saved"] == 0 and p.read_bytes() == original


def test_the_backup_never_overwrites_an_existing_file(tmp_path):
    src = tmp_path / "L40.yaml"
    src.write_text("a: 1\n", encoding="utf-8")
    first = repair._exclusive_backup(src, tmp_path / "bak", "L40", "T1")
    src.write_text("a: 2\n", encoding="utf-8")
    second = repair._exclusive_backup(src, tmp_path / "bak", "L40", "T1")
    assert first != second and first.read_text(encoding="utf-8") == "a: 1\n" and second.read_text(encoding="utf-8") == "a: 2\n"


def test_efficiency_basis_only_on_marked_records(dies):
    _save_duty(MARKED)
    body = {"die": DIE, "config": CFG, "duty": DUTY, "drive": "current", "shaft_convention": SC.SHAFT_CONVENTION,
            "result": {"efficiency_pct": 88.0, "shaft_conv": 1, "efficiency_basis": "coupling"}}
    assert client.post("/api/family/duty_result", json=body).status_code == 200
    assert _duty(dies)["result"]["efficiency_basis"] == "coupling"
    body["result"]["efficiency_basis"] = "nonsense"
    client.post("/api/family/duty_result", json=body)
    assert "efficiency_basis" not in _duty(dies)["result"]
    # an unmarked record never stores a basis, whatever is sent
    _save_duty(UNMARKED)
    body = {"die": DIE, "config": CFG, "duty": DUTY, "drive": "current",
            "result": {"efficiency_pct": 88.0, "shaft_conv": 1, "efficiency_basis": "coupling"}}
    client.post("/api/family/duty_result", json=body)
    assert "efficiency_basis" not in _duty(dies)["result"] and "shaft_conv" not in _duty(dies)["result"]
    # a later unmarked run drops a leftover basis with the flag
    _save_duty(MARKED)
    client.post("/api/family/duty_result", json={**body, "shaft_convention": SC.SHAFT_CONVENTION})
    assert _run(UNMARKED).status_code == 200
    assert "efficiency_basis" not in _duty(dies)["result"]


def test_marked_run_then_result_without_flag_keeps_derived_marker(dies):
    # Review B(b): the second request must not undo the first request's convention.
    _save_duty(UNMARKED)
    assert _run(MARKED).status_code == 200
    assert _result().status_code == 200
    d = _duty(dies)
    assert d["result"]["shaft_conv"] == 1
    assert d["runs"]["current"]["result"]["shaft_conv"] == 1
    assert d["summary"] == d["runs"]["current"]["summary"] == MARKED


def _atomic(summary, drive="current", **extra):
    return client.post("/api/family/duty_run", json={
        "die": DIE, "config": CFG, "duty": DUTY, "drive": drive,
        "summary": {**summary, "drive": drive},
        "result": {"efficiency_pct": 87.62886597938144, "mass_kg": 1,
                   "efficiency_basis": "coupling", "shaft_conv": 99, "untrusted": 123},
        **extra})


@pytest.mark.parametrize("drive", ["current", "pwm_voltage"])
@pytest.mark.parametrize("marked", [True, False])
def test_atomic_save_without_waveforms_publishes_one_sanitized_pair(dies, monkeypatch, drive, marked):
    from motor_ai_sim.routes import family as fam
    writes = []
    save = fam._save_yaml
    def observe(path, doc, *args, **kwargs):
        import copy
        writes.append(copy.deepcopy(doc))
        return save(path, doc, *args, **kwargs)
    monkeypatch.setattr(fam, "_save_yaml", observe)
    summary = MARKED if marked else UNMARKED
    r = _atomic(summary, drive)
    assert r.status_code == 200, r.text
    assert r.json()["primary"] is True and len(writes) == 1
    d = _duty(dies)
    run = d["runs"][drive]
    assert d["summary"] == run["summary"] == {**summary, "drive": drive}
    assert d["result"] == run["result"]
    assert (d["result"].get("shaft_conv") == 1) == marked
    assert "untrusted" not in d["result"]
    assert d["result"]["efficiency_pct"] == (87.62886597938144 if marked else 87.629)
    assert "payload_file" not in run


@pytest.mark.parametrize("first", ["run", "result"])
@pytest.mark.parametrize("marked", [True, False])
def test_both_request_orders_and_interrupted_second_save(dies, first, marked):
    _save_duty(UNMARKED if marked else MARKED)
    summary = MARKED if marked else UNMARKED
    calls = {"run": lambda: _atomic(summary), "result": lambda: _result(flag=1, marker=SC.SHAFT_CONVENTION)}
    for call in [first, "result" if first == "run" else "run"]:
        assert calls[call]().status_code == 200
        # Check after EACH write: it is also the state if the other request never arrives.
        d = _duty(dies)
        for rec in [d, *d["runs"].values()]:
            assert (rec["result"].get("shaft_conv") == 1) == SC.has_marker(rec["summary"])
    assert SC.has_marker(_duty(dies)["summary"]) == marked


def test_missing_other_drive_summary_never_borrows_marked_headline(dies):
    _save_duty(MARKED)
    before = _duty(dies)
    r = client.post("/api/family/duty_result", json={
        "die": DIE, "config": CFG, "duty": DUTY, "drive": "pwm_voltage",
        "shaft_convention": SC.SHAFT_CONVENTION,
        "result": {"efficiency_pct": 88, "shaft_conv": 1, "efficiency_basis": "coupling"}})
    assert r.status_code == 200 and r.json()["primary"] is False
    d = _duty(dies)
    assert d["summary"] == before["summary"] and "result" not in d
    assert "shaft_conv" not in d["runs"]["pwm_voltage"]["result"]
    assert "efficiency_basis" not in d["runs"]["pwm_voltage"]["result"]


def test_secondary_atomic_run_keeps_primary_bytes_and_current_can_take_over(dies):
    assert _atomic(MARKED).status_code == 200
    before = _duty(dies)
    assert _atomic(UNMARKED, "pwm_voltage").status_code == 200
    d = _duty(dies)
    for key in ("summary", "result", "mesh"):
        assert yaml.safe_dump(d.get(key)) == yaml.safe_dump(before.get(key))
    assert "shaft_conv" not in d["runs"]["pwm_voltage"]["result"]


@pytest.mark.parametrize("failure", ["verification", "result", "write"])
def test_rejected_atomic_save_leaves_previous_pair_byte_identical(dies, monkeypatch, failure):
    from fastapi import HTTPException
    from motor_ai_sim.routes import family as fam
    assert _atomic(UNMARKED).status_code == 200
    p = dies / DIE / f"{CFG}.yaml"
    before = p.read_bytes()
    if failure == "verification":
        response = _atomic({**MARKED, "ripple_self_check_rel": 1.016})
        assert response.status_code == 409
    elif failure == "result":
        response = _atomic(MARKED, result={"mass_kg": "invalid"})
        assert response.status_code == 422
    else:
        def fail(*args, **kwargs):
            raise HTTPException(503, "injected save failure")
        monkeypatch.setattr(fam, "_save_yaml", fail)
        assert _atomic(MARKED).status_code == 503
    assert p.read_bytes() == before


@pytest.mark.parametrize("route", ["duty", "duty_run"])
def test_summary_changes_synchronize_retained_result_and_repair_has_nothing_to_restore(dies, route):
    assert _atomic(MARKED).status_code == 200
    if route == "duty":
        _save_duty(UNMARKED)
    else:
        assert _run(UNMARKED).status_code == 200
    d = _duty(dies)
    for rec in [d, d["runs"]["current"]]:
        assert rec["summary"] == UNMARKED
        assert "shaft_conv" not in rec["result"] and "efficiency_basis" not in rec["result"]
    assert repair.plan_config({"duties": [d]}) == []


def test_marked_absent_efficiency_survives_atomic_save_and_project_projection(dies):
    assert _atomic(MARKED, result={"efficiency_pct": None, "mass_kg": 1}).status_code == 200
    d = _duty(dies)
    assert d["result"]["efficiency_pct"] is None
    rows = motors._results(DIE, CFG, {"duties": [d]})
    assert rows[0]["electromagnetic"].get("efficiency_pct") is None


def test_overlapping_atomic_saves_keep_both_drives_and_consistent_headline(dies, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    from motor_ai_sim.routes import family as fam
    load = fam._load_yaml
    def delayed(path, what):
        doc = load(path, what)
        if what == "configuration":
            time.sleep(0.03)  # overlapping read-modify-write would lose one drive without the store lock
        return doc
    monkeypatch.setattr(fam, "_load_yaml", delayed)
    barrier = threading.Barrier(2)
    def save(drive):
        barrier.wait(timeout=5)
        return _atomic(MARKED if drive == "current" else UNMARKED, drive)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ["current", "pwm_voltage"]))
    assert [r.status_code for r in responses] == [200, 200]
    d = _duty(dies)
    assert set(d["runs"]) == {"current", "pwm_voltage"}
    assert d["summary"] == d["runs"]["current"]["summary"]
    assert d["result"] == d["runs"]["current"]["result"]
    assert d["result"]["shaft_conv"] == 1
    assert "shaft_conv" not in d["runs"]["pwm_voltage"]["result"]


def test_atomic_summary_keeps_the_primary_terminal_connection_and_build_statement_current(dies):
    p = dies / DIE / f"{CFG}.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["duties"][0].update(star_delta="star", solved_on={"build_sig": "old"})
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert _atomic({**MARKED, "star_delta": "delta"}).status_code == 200
    d = _duty(dies)
    assert d["star_delta"] == "delta" and "solved_on" not in d


@pytest.mark.parametrize("pct", [850 / 970 * 100, 770 / 870 * 100,
    20.1 / 34.2 * 100, 1 / 1.1 * 100, .9 / 1.1 * 100, 80, 60, .7 / .8 * 100,
    None, 0, 4 / 7 * 100])
def test_page_efficiency_precision_survives_both_routes(dies, pct):
    assert _atomic(MARKED, result={"efficiency_pct": pct, "mass_kg": 1}).status_code == 200
    for save_again in [False, True]:
        if save_again:
            response = client.post("/api/family/duty_result", json={
                "die": DIE, "config": CFG, "duty": DUTY, "drive": "current",
                "result": {"efficiency_pct": pct, "mass_kg": 1}})
            assert response.status_code == 200
        saved = _duty(dies)["result"]["efficiency_pct"]
        assert saved == pct
        assert (None if saved is None else f"{saved:.2f}") == (None if pct is None else f"{pct:.2f}")

@pytest.mark.parametrize("metadata", [
    '{"lineages":',  # real truncated JSON, formerly swallowed by json_store.read_json
    '', 'null', '[]', '{}', '{"lineages": []}',
    '{"lineages": {"x": null}}', '{"lineages": {"x": {"revisions": null}}}',
    '{"lineages": {"x": {"revisions": [{"die": "D", "config": "C"}]}}}',
    '{"lineages": {"x": {"revisions": [{"die": "D", "config": "C", "frozen": null}]}}}',
    '{"lineages": {"x": {"revisions": [{"die": "D", "config": "C", "frozen": 0}]}}}',
    '{"lineages": {}, "lineages": {}}', '{"lineages": {}, "other": NaN}',
])
def test_real_corrupt_revision_metadata_refuses_repair_without_any_write(dies, tmp_path, monkeypatch, metadata):
    from motor_ai_sim import motor_revisions as MR
    from motor_ai_sim.routes import family as fam
    p = _marked_cfg(dies)
    monkeypatch.setenv("MOTRES_MOTOR_REVISIONS_ROOT", str(tmp_path / "revisions"))
    MR.root().mkdir()
    MR._file().write_text(metadata, encoding="utf-8")
    before = {q.relative_to(tmp_path): q.read_bytes() for q in tmp_path.rglob('*') if q.is_file()}
    writes = []
    monkeypatch.setattr(fam, "_save_yaml", lambda *a, **kw: writes.append(a))
    dry = repair.repair()
    done = repair.repair(apply=True, backup_dir=tmp_path / "bak")
    assert [r.get("skip") for r in dry["rows"]] == ["protection_unreadable"]
    assert done["rows"] == dry["rows"] and done["saved"] == 0
    assert not writes and not (tmp_path / "bak").exists()
    after = {q.relative_to(tmp_path): q.read_bytes() for q in tmp_path.rglob('*') if q.is_file()}
    assert after == before  # config, registry, history, and every other sandbox file unchanged


@pytest.mark.parametrize("frozen", [False, True])
def test_strict_revision_reader_accepts_real_valid_registry(dies, tmp_path, monkeypatch, frozen):
    import json
    from motor_ai_sim import motor_revisions as MR
    p = _marked_cfg(dies)
    before = p.read_bytes()
    monkeypatch.setenv("MOTRES_MOTOR_REVISIONS_ROOT", str(tmp_path / "revisions"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    MR.root().mkdir()
    MR._file().write_text(json.dumps({"lineages": {"x": {"revisions": [
        {"die": DIE.lower(), "config": CFG.lower(), "frozen": frozen}]}}}), encoding="utf-8")
    assert MR.find(DIE, CFG, strict=True)[1]["frozen"] is frozen
    done = repair.repair(apply=True, backup_dir=tmp_path / "bak")
    assert done["saved"] == (0 if frozen else 1)
    if frozen:
        assert done["rows"][0]["skip"] == "frozen_revision"
        assert p.read_bytes() == before and not (tmp_path / "bak").exists()
    else:
        assert len(list((tmp_path / "bak" / DIE).iterdir())) == 1


def test_revision_corruption_after_scan_is_rechecked_before_backup(dies, tmp_path, monkeypatch):
    from motor_ai_sim import motor_revisions as MR
    p = _marked_cfg(dies)
    before = p.read_bytes()
    monkeypatch.setenv("MOTRES_MOTOR_REVISIONS_ROOT", str(tmp_path / "revisions"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    MR.root().mkdir()
    MR._file().write_text('{"lineages": {}}', encoding="utf-8")
    original_scan = repair.scan

    def corrupt_after_scan():
        rows = original_scan()
        assert rows and not rows[0].get("skip")
        MR._file().write_text('{"lineages":', encoding="utf-8")
        return rows

    monkeypatch.setattr(repair, "scan", corrupt_after_scan)
    done = repair.repair(apply=True, backup_dir=tmp_path / "bak")
    assert done["saved"] == 0
    assert done["failed"] == [{"die": DIE, "config": CFG, "error": "protection_unreadable"}]
    assert p.read_bytes() == before and not (tmp_path / "bak").exists()


@pytest.mark.parametrize("extra,source,eff", [(None, "unknown", 100 / 110 * 100), (20, "live_bearings", 80 / 110 * 100)])
def test_atomic_run_persists_resolved_mechanical_context_with_full_precision(dies, extra, source, eff):
    summary = {**MARKED, "P_mech_W": 100, "P_loss_total_W": 10,
               "P_mech_extra_W": extra, "P_mech_extra_source": source}
    response = client.post("/api/family/duty_run", json={"die": DIE, "config": CFG, "duty": DUTY,
        "drive": "current", "summary": summary,
        "result": {"efficiency_pct": eff, "efficiency_basis": "shaft_before_bearings" if extra is None else "coupling"}})
    assert response.status_code == 200, response.text
    duty = _duty(dies)
    for saved in [duty, duty["runs"]["current"]]:
        assert saved["summary"]["P_mech_extra_W"] == extra
        assert saved["summary"]["P_mech_extra_source"] == source
        assert saved["result"]["efficiency_pct"] == eff
        assert f'{saved["result"]["efficiency_pct"]:.2f}' == f'{eff:.2f}'
