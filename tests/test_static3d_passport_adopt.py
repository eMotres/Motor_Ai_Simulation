"""POST /api/static3d/passport/adopt: a standalone Stage A result becomes the machine's end-effect passport (synthetic)."""
import json

import pytest

from motor_ai_sim.routes import static3d as S


def _sa(**over):
    sa = {"geometry_fingerprint": "abc123def456", "generated_utc": "2026-10-26T11:47:46Z", "k_flux": 0.9534,
          "k_flux_self": 0.9635,
          "machine": {"num_slots": 24, "num_poles": 20, "stator_od_mm": 200.0, "stack_mm": 50.0,
                      "materials": {"stator_core": "SteelX"}},
          "model": {"h_gap_mm": 0.2, "mesh_rule": "physical"},
          "l_stack_curve": [{"stack_mm": 50.0, "k_flux": 0.95341}, {"stack_mm": 40.0, "k_flux": 0.95007}]}
    sa.update(over)
    return sa


def test_entry_has_the_lookup_shape():
    fp, e = S.adopt_entry(_sa(), "bench motor")
    assert fp == "abc123def456"
    assert e["machine"] == "24s/20p OD 200"            # what _end3d_lookup matches an inherited passport by
    assert e["k_flux"] == pytest.approx(0.9534)
    assert e["k_flux_vs_L"] == {"50.0": 0.95341, "40.0": 0.95007}
    assert e["note"] == "bench motor" and e["adopted_utc"]


@pytest.mark.parametrize("bad, why", [
    ({"geometry_fingerprint": ""}, "geometry_fingerprint"),
    ({"k_flux": None}, "k_flux"),
    ({"k_flux": 1.2}, "outside"),
    ({"machine": {"num_slots": 24}}, "num_poles"),
])
def test_refuses_an_incomplete_result(bad, why):
    with pytest.raises(ValueError, match=why):
        S.adopt_entry(_sa(**bad))


def test_route_writes_the_workspace_store(tmp_path, monkeypatch):
    from motor_ai_sim import workspace
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    (tmp_path / "end_effect_passports.json").write_text(json.dumps({"other": {"k_flux": 0.95}}), encoding="utf-8")
    out = S.adopt_passport(S.AdoptPassport(stage_a=_sa()), _admin={"email": "a@b"})
    assert out["ok"] and not out["replaced"]
    store = json.loads((tmp_path / "end_effect_passports.json").read_text(encoding="utf-8"))
    assert set(store) == {"other", "abc123def456"} and store["abc123def456"]["machine"] == "24s/20p OD 200"
    assert S.adopt_passport(S.AdoptPassport(stage_a=_sa(k_flux=0.96)), _admin={})["replaced"] is True


def test_route_answers_422_on_a_bad_result():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        S.adopt_passport(S.AdoptPassport(stage_a={"k_flux": 0.9}), _admin={})
    assert e.value.status_code == 422
