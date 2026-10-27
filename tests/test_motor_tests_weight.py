# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The Tests "Weight" row compares the bench with the WHOLE motor (owner 2026-10-27).

The bench weighs the whole motor; the model used to be ``summary.mass_total_kg`` (the active parts only: 9,400 g on the
bench against 7,334 g, -22 % in red).  Now: the full mass when it is complete - a CAD bill of materials, or the
configuration's stated CAD mass (``cad_mass``, set by an admin) - with a delta; the active mass labelled "active parts
only", with the reason and no delta, when it is not.  Closed-form, no solver."""
from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.modules import motor_mass as MM
from motor_ai_sim.motor_tests import comparison as CMP

DIE = "CIANO14 40 new"
client = TestClient(app)


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    from motor_ai_sim.modules import component_library as L
    from motor_ai_sim.routes import family as fam
    from tests._component_dies import make_dies
    dies = make_dies(tmp_path)
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    monkeypatch.setenv("MOTRES_DIES_DIR", str(dies))
    monkeypatch.setenv("MOTRES_CAD_DIR", str(tmp_path / "cad"))          # no CAD BOM for any configuration
    L._LIB = None                                                         # noqa: SLF001
    MM._FULL_CACHE.clear()                                                # noqa: SLF001
    yield dies
    L._LIB = None                                                         # noqa: SLF001


def _state(dies, cfg, grams, source="Fusion assembly v35", date="2026-10-27"):
    f = dies / DIE / f"{cfg}.yaml"
    d = yaml.safe_load(f.read_text()) or {}
    d["cad_mass"] = {"grams": grams, "source": source, "date": date}
    f.write_text(yaml.safe_dump(d), encoding="utf-8")
    MM._FULL_CACHE.clear()                                                # noqa: SLF001


# -- motor_mass: a stated CAD mass is the full mass when there is no BOM ---------------------------------

def test_no_bom_and_no_cad_mass_is_partial(catalog):
    s = MM.mass_summary(DIE, "L20")
    assert s["partial"] is True and s["source"] == "active_only"


def test_a_stated_cad_mass_is_the_full_mass(catalog):
    _state(catalog, "L20", 250.0)
    s = MM.mass_summary(DIE, "L20")
    assert s["partial"] is False and s["source"] == "cad_stated"
    assert s["full_kg"] == pytest.approx(0.25)
    assert s["non_active_kg"] == pytest.approx(0.25 - s["active_kg"])
    assert "Fusion assembly v35" in s["basis"]


@pytest.mark.parametrize("bad", [{"grams": 0.5, "source": "x"}, {"grams": 2e8, "source": "x"},
                                 {"grams": "a", "source": "x"}, {"grams": 100.0}])
def test_the_cad_mass_is_validated(bad):
    with pytest.raises(ValueError):
        MM.validate_cad_mass(bad)


# -- the Tests weight row ---------------------------------------------------------------------------------

REC = {"weight": [{"what": "whole motor", "mass_g": 9400.0}]}


def _weight(model):
    return CMP._weight_row(REC["weight"][0], model)


def test_full_cad_mass_is_compared_with_a_delta():
    r = _weight({"mass_kg": 7.334, "mass": {"partial": False, "full_kg": 9.721111, "active_kg": 7.334,
                                             "source": "cad_stated", "cad_source": "Fusion CIANO20 200_50"}})
    assert r["model"] == pytest.approx(9721.111)
    # the table's own convention, (model - measured) / measured: the CAD is 3.4 % heavier than the bench
    assert r["delta_pct"] == pytest.approx(3.416, abs=0.01)
    assert "Fusion CAD mass" in r["detail"] and r["params"]["model_scope"] == "full"


def test_active_parts_only_has_no_delta_and_says_why():
    r = _weight({"mass_kg": 7.334, "mass": {"partial": True, "full_kg": 7.334, "active_kg": 7.334,
                                             "source": "active_only",
                                             "partial_reason": "no CAD bill of materials has been built"}})
    assert r["model"] == pytest.approx(7334.0)
    assert r["delta_pct"] is None and r["status"] == "info"
    assert "active parts only" in r["detail"] and "no CAD bill of materials" in r["detail"]


def test_without_any_mass_summary_the_run_mass_is_still_not_compared():
    r = _weight({"mass_kg": 7.334})
    assert r["delta_pct"] is None and "active parts only" in r["detail"]


# -- the admin route ----------------------------------------------------------------------------------------

def test_the_route_is_admin_only_and_writes_the_block(catalog, monkeypatch):
    from motor_ai_sim.routes import family as fam
    calls = []
    monkeypatch.setattr("motor_ai_sim.admin_audit.record", lambda *a, **k: calls.append((a, k)))
    url = f"/api/family/config/{DIE}/L20/cad-mass"
    body = {"grams": 9721.111, "source": "Fusion assembly", "date": "2026-10-27"}
    app.dependency_overrides[fam.require_catalog_write] = lambda: {"user": {"email": "eng@x"}, "is_admin": False}
    try:
        assert client.put(url, json=body).status_code == 403
        app.dependency_overrides[fam.require_catalog_write] = lambda: {"user": {"email": "admin@x"}, "is_admin": True}
        monkeypatch.setattr(fam, "_require_die_write", lambda *a, **k: None)
        assert client.put(url, json=dict(body, grams=0.1)).status_code == 422
        r = client.put(url, json=body)
        assert r.status_code == 200, r.text
        assert r.json()["cad_mass"]["grams"] == 9721.111
        assert MM.config_cad_mass(catalog / DIE, "L20")["source"] == "Fusion assembly"
        assert calls and calls[-1][0][1] == "config.cad_mass"
        assert client.put(url, json={"grams": None}).json()["cad_mass"] is None
        assert MM.config_cad_mass(catalog / DIE, "L20") is None
    finally:
        app.dependency_overrides.pop(fam.require_catalog_write, None)
