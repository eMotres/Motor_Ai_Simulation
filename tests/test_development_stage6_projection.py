from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim.development import motors


class _Family:
    @staticmethod
    def _primary_drive(_duty):
        return "sine"

    @staticmethod
    def _read_run_payload(_die, _rel):
        raise AssertionError("test must replace payload reader")


def _transient_payload():
    payload = {key: [1.0, 1.1] for key in (
        "P_cu_W", "P_fe_W", "P_mag_eddy_W", "P_loss_total_W",
        "I_A", "I_B", "I_C", "V_A", "V_B", "V_C")}
    payload.update({"time_s": [0.0, 0.001], "T_em_raw_Nm": [2.9, 3.1],
                    "f_elec_Hz": 100.0, "T_avg_Nm": 3.0, "V_peak": 48.0})
    return payload


def test_simulation_projection_uses_solved_legacy_result_or_summary_only():
    assert not motors._simulation_available({}, "D", _Family)
    assert not motors._simulation_available({"summary": {}}, "D", _Family)
    assert not motors._simulation_available({"result": {"torque_nm": 2}}, "D", _Family)
    assert not motors._simulation_available({"result": {"efficiency_pct": "bad"}}, "D", _Family)
    assert motors._simulation_available({"result": {"efficiency_pct": 91}}, "D", _Family)
    assert not motors._simulation_available({"torque_nm": 2}, "D", _Family)
    assert motors._simulation_available(
        {"torque_nm": 2, "result": {"torque_nm": 2}}, "D", _Family)
    assert motors._simulation_available({"summary": {"T_em_avg_Nm": 2}}, "D", _Family)
    assert not motors._simulation_available(
        {"summary": {"T_em_avg_Nm": 2, "estimated": True}}, "D", _Family)
    assert not motors._simulation_available(
        {"summary": {"T_em_avg_Nm": 2, "status": "failed"}}, "D", _Family)


def test_simulation_projection_requires_primary_stored_payload(monkeypatch):
    duty = {"mesh": {}, "runs": {"sine": {"payload_file": "runs/rated.gz"}}}
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: {})
    assert not motors._simulation_available(duty, "D", _Family)
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: {"metadata": {"run_id": "x"}})
    assert not motors._simulation_available(duty, "D", _Family)
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: {"summary": {"T_em_avg_Nm": 3}})
    assert motors._simulation_available(duty, "D", _Family)
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: {"summary": {"T_em_avg_Nm": 3}})
    assert not motors._simulation_available({**duty, "summary": {"estimated": True}}, "D", _Family)
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: _transient_payload())
    assert motors._simulation_available({**duty, "summary": {}}, "D", _Family)
    broken = _transient_payload(); broken.pop("P_cu_W")
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: broken)
    assert not motors._simulation_available({**duty, "summary": {"T_em_avg_Nm": 3}}, "D", _Family)
    torque_without_time = _transient_payload(); torque_without_time.pop("time_s")
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: torque_without_time)
    assert not motors._simulation_available({**duty, "summary": {}}, "D", _Family)
    estimated = _transient_payload(); estimated["provenance"] = {"kind": "estimate"}
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: estimated)
    assert not motors._simulation_available({**duty, "summary": {}}, "D", _Family)
    missing = {"mesh": {}, "runs": {"sine": {"payload_file": "missing.gz"}}}
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: None)
    assert not motors._simulation_available(missing, "D", _Family)
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: {"metadata": {"run_id": "x"}})
    assert not motors._simulation_available(
        {**duty, "result": {"efficiency_pct": 91}}, "D", _Family)


def test_result_projection_sets_flags_per_duty(monkeypatch):
    monkeypatch.setattr(motors, "_simulation_available",
                        lambda duty, _die, _family: duty["name"] == "rated")
    monkeypatch.setattr(motors, "_comparison_projection", lambda *_: {})
    cfg = {"duties": [{"name": "rated", "summary": {"T_em_avg_Nm": 1}},
                       {"name": "peak", "summary": {"T_em_avg_Nm": 2}}]}
    rows = motors._results("D", "A", cfg, {}, {"rated"})
    assert [(x["duty"], x["can_open_simulation"], x["can_open_configure"])
            for x in rows] == [("rated", True, True), ("peak", False, False)]


def test_configure_projection_uses_exact_or_first_same_die_accessible_reference(monkeypatch, tmp_path):
    from motor_ai_sim import motor_access
    from motor_ai_sim.routes import catalog

    cfg_path = tmp_path / "D" / "A.yaml"
    cfg_path.parent.mkdir()
    cfg_path.write_text("name: A\n", encoding="utf-8")
    docs = [{"die": "D", "name": "A", "duties": [{"name": "rated"}]}]
    access_info = {"A": ["D", "A", str(cfg_path), "shared"]}
    monkeypatch.setattr(catalog, "_family_doc_of_motor", lambda _motor, _geometry=None: {"die": "D"})
    monkeypatch.setattr(catalog, "_die_docs_of", lambda _fam, out: (docs, None) if out.update(access_info) is None else None)
    monkeypatch.setattr(motor_access, "may_see_config_file", lambda *_a, **_kw: True)
    refs = [
        {"id": "ref-exact", "card": {"die": "D", "config": "A"},
         "passport": {"passport": {"L0_mm": 10}, "geo": {}, "fit": {}}},
        {"id": "ref-other", "card": {"die": "D", "config": "B"},
         "passport": {"passport": {"L0_mm": 10}, "geo": {}, "fit": {}}},
    ]
    monkeypatch.setattr(catalog, "_configure_motor",
                        lambda ref_id, _auth: ({"id": ref_id, "name": "D A",
                                               "passport": {"passport": {"L0_mm": 10}}}, Path("catalog.json")))
    cache = {}
    assert motors._configure_reference_duties("D", "A", {"name": "A", "duties": [{"name": "rated"}]},
                                               cfg_path, refs, {"configs": frozenset({"D/A"})},
                                               "Bearer test", {}, cache) == {"rated"}
    assert list(cache) == ["D"]
    assert motors._configure_reference_duties("D", "A", {"name": "A", "duties": [{"name": "rated"}]},
                                               cfg_path, refs[1:], {"configs": frozenset({"D/A"})},
                                               "Bearer test", {}, cache) == {"rated"}
    assert motors._configure_reference_duties("D", "A", {"name": "A", "duties": [{"name": "rated"}]},
                                               cfg_path, [{**refs[0], "card": {"die": "D-shadow", "config": "A"}}],
                                               {"configs": frozenset({"D/A"})}, "Bearer test", {}, {}) == set()
    monkeypatch.setattr(motor_access, "may_see_config_file", lambda *_a, **_kw: False)
    assert motors._configure_reference_duties("D", "A", {"name": "A", "duties": [{"name": "rated"}]},
                                               cfg_path, refs, {"configs": frozenset()},
                                               "Bearer customer", {}, {}) == set()
    incomplete = [{**refs[0], "passport": {"passport": {}, "geo": {}}}]
    assert motors._configure_reference_duties("D", "A", {"name": "A", "duties": [{"name": "rated"}]},
                                               cfg_path, incomplete, {"is_admin": True},
                                               "Bearer test", {}, {}) == set()
    monkeypatch.setattr(catalog, "_configure_motor", lambda *_a: (_ for _ in ()).throw(KeyError("missing")))
    assert motors._configure_reference_duties("D", "A", {"name": "A", "duties": [{"name": "rated"}]},
                                               cfg_path, refs, {"is_admin": True},
                                               "Bearer test", {}, {}) == set()
    monkeypatch.setattr(catalog, "_configure_motor",
                        lambda ref_id, _auth: ({"id": ref_id, "name": "other",
                                               "passport": {"passport": {"L0_mm": 10}}}, Path("catalog.json")))
    monkeypatch.setattr(catalog, "_family_doc_of_motor", lambda *_a: {"die": "D-shadow"})
    assert motors._configure_reference_duties("D", "A", {"name": "A", "duties": [{"name": "rated"}]},
                                               cfg_path, refs, {"is_admin": True},
                                               "Bearer test", {}, {}) == set()


def test_motors_endpoint_projects_positive_flags_and_batches_shared_reads(monkeypatch, tmp_path):
    from motor_ai_sim import duty_results, motor_access
    from motor_ai_sim.development import access
    from motor_ai_sim.routes import catalog, family

    die_dir = tmp_path / "D"
    die_dir.mkdir()
    (die_dir / "die.yaml").write_text("name: D\ngeometry: {}\n", encoding="utf-8")
    for name in ("A", "B"):
        (die_dir / f"{name}.yaml").write_text(
            f"die: D\nname: {name}\nduties:\n  - name: rated\n"
            "    summary:\n      T_em_avg_Nm: 2\n  - name: pending\n",
            encoding="utf-8")
    project = {"id": "p", "linked_motors": [
        {"die": "D", "config": "A"}, {"die": "D", "config": "B"}]}
    monkeypatch.setattr(access, "require_project",
                        lambda *_a, **_k: ({"email": "admin@test", "role": "admin"}, project, "owner"))
    monkeypatch.setattr(access, "is_engineer", lambda _u: True)
    monkeypatch.setattr(motors, "_index", lambda: {"motors": {
        motors._key("D", "A"): {"project_id": "p"},
        motors._key("D", "B"): {"project_id": "p"}}})
    monkeypatch.setattr(family, "_require_die_access", lambda *_a, **_k: None)
    monkeypatch.setattr(family, "_cfg_file", lambda _die, cfg: die_dir / f"{cfg}.yaml")
    counters = {"references": 0, "access": 0, "docs": 0, "results": 0, "family": 0, "resolver": 0}
    refs = [{"id": "ref-a", "card": {"die": "D", "config": "A"},
             "passport": {"passport": {"L0_mm": 10}, "geo": {}, "fit": {}}}]
    def references(_authorization):
        counters["references"] += 1
        return {"motors": refs}
    def access_doc(_authorization):
        counters["access"] += 1
        return {"is_admin": True}
    def docs(_fam, out):
        counters["docs"] += 1
        out.update({name: ["D", name, str(die_dir / f"{name}.yaml"), "shared"]
                    for name in ("A", "B")})
        return ([{"die": "D", "name": name} for name in ("A", "B")], {})
    def result_index(_die):
        counters["results"] += 1
        return {}
    def family_doc(_motor, _geometry=None):
        counters["family"] += 1
        return {"die": "D"}
    def resolve_motor(ref_id, _auth):
        counters["resolver"] += 1
        return ({"id": ref_id, "name": "D A",
                 "passport": {"passport": {"L0_mm": 10}}}, Path("catalog.json"))
    monkeypatch.setattr(catalog, "get_references", references)
    monkeypatch.setattr(catalog, "_family_doc_of_motor", family_doc)
    monkeypatch.setattr(catalog, "_die_docs_of", docs)
    monkeypatch.setattr(catalog, "_configure_motor", resolve_motor)
    monkeypatch.setattr(motor_access, "catalog_access", access_doc)
    monkeypatch.setattr(motor_access, "may_see_config_file", lambda *_a, **_k: True)
    monkeypatch.setattr(duty_results, "index", result_index)

    app = FastAPI()
    app.include_router(motors.router)
    response = TestClient(app).get("/api/projects/p/motors")
    assert response.status_code == 200, response.text
    rows = response.json()["motors"]
    assert len(rows) == 2
    for row in rows:
        assert row["can_open_configure"] is True and row["configure_duty"] == "rated"
        assert row["can_open_simulation"] is True and row["simulation_duty"] == "rated"
        assert [(r["duty"], r["can_open_configure"], r["can_open_simulation"])
                for r in row["results"]] == [
                    ("rated", True, True), ("pending", True, False)]
    assert counters == {"references": 1, "access": 1, "docs": 1, "results": 1, "family": 1, "resolver": 1}


def test_non_array_chart_time_cannot_enable_simulation_from_summary(monkeypatch):
    monkeypatch.setattr(_Family, "_read_run_payload", lambda *_: {"time_s": "broken"})
    duty = {"runs": {"sine": {"payload_file": "synthetic.gz"}},
            "summary": {"T_em_avg_Nm": 2}}
    assert not motors._simulation_available(duty, "D", _Family)
