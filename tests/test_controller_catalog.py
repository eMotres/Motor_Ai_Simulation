"""The catalogue of complete commercial controllers (config/controllers/catalog.yaml) - owner 2026-10-26.

The DTI entries are transcribed from the manufacturer's manual: only stated values, each with its page.  The tests pin the
numbers, the "nothing invented" rule (power devices stay empty), the read routes, the free-text link on read and the
battery-vs-controller check.  Specification drafts here are synthetic.
"""
from __future__ import annotations

import copy

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim.development import models
from motor_ai_sim.inverter import controller_catalog as cat
from motor_ai_sim.routes import controller_catalog as route


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(route.router)
    return TestClient(app)


def _draft(**fields):
    return {"schema_version": 2, "fields": {k: {"status": "filled", "value": v} for k, v in fields.items()},
            "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}


def test_catalog_file_is_valid_and_has_the_two_dti_entries():
    doc = cat.load_catalog()
    assert cat.validate_catalog(doc) == []
    ids = [e["id"] for e in doc["controllers"]]
    assert ids == ["dti-hv-550-rev4", "dti-hv-850-rev4"]
    for entry in doc["controllers"]:
        assert entry["brand"] == "DTI" and entry["revision"] == "Rev 4"
        assert "User manual" in entry["source"]["document"]


def test_dti_hv850_values_are_the_manuals_with_pages():
    e = cat.get_controller("dti-hv-850-rev4")
    sup = e["supply_voltage_V"]
    assert (sup["min"]["value"], sup["max"]["value"], sup["abs_max"]["value"]) == (30, 800, 830)
    assert sup["max"]["page"] == 9 and sup["nominal"] is None            # no nominal is stated: it stays empty
    pc = e["phase_current"]
    assert [(c["dc_V"], c["value"], c["unit"]) for c in pc["continuous"]] == [(400, 850, "Apk"), (800, 516, "Apk")]
    assert {(p["value"], p["unit"]) for p in pc["peak"]} == {(850, "Apk"), (600, "Arms")} and all(p["page"] == 10 for p in pc["peak"])
    assert pc["peak_duration_s"] == {**pc["peak_duration_s"], "value": 10, "basis": "figure", "page": 11}
    assert pc["continuous_Arms_at_supply_max"]["basis"] == "derived"
    assert abs(pc["continuous_Arms_at_supply_max"]["value"] - 516 / 2 ** 0.5) < 0.06
    assert (e["switching_frequency_Hz"]["min"]["value"], e["switching_frequency_Hz"]["max"]["value"]) == (8000, 14000)
    assert e["mass_kg"]["value"] == 6.8 and e["mass_kg"]["page"] == 12
    assert (e["dimensions_mm"]["height"], e["dimensions_mm"]["width"], e["dimensions_mm"]["length"]) == (77, 213, 420)
    assert e["protection"]["ip"] == "IP65" and e["cooling"]["type"] == "liquid"
    assert e["dc_input_current_max_A"]["value"] == 400
    # NOTHING INVENTED: the manual does not name the transistors, so the entry has none
    assert e["power_devices"]["device_type"] is None and e["power_devices"]["voltage_class_V"] is None
    assert cat.summary(e)["power_devices_known"] is False
    hv550 = cat.get_controller("dti-hv-550-rev4")
    assert hv550["phase_current"]["peak"][1]["value"] == 390 and hv550["dc_input_current_max_A"]["value"] == 280
    assert hv550["phase_current"]["peak_duration_s"] is None                    # Figure 4 states no separate peak time
    assert cat.summary(hv550)["prefill"].get("2_6.peak_duration_s") is None


def test_every_catalog_value_has_a_basis_and_a_page():
    for entry in cat.load_catalog()["controllers"]:
        problems = []
        cat._walk_provenance(entry, entry["id"], problems)
        assert problems == []
    broken = {"controllers": [{"id": "x", "brand": "B", "model": "M", "revision": "1", "display_name": "B M",
                               "source": {"document": "d"}, "supply_voltage_V": {"max": {"value": 100, "unit": "V"}}}]}
    assert any("basis" in p for p in cat.validate_catalog(broken))
    broken["controllers"][0]["supply_voltage_V"]["max"].update(basis="guess", page=3)
    assert any("basis" in p for p in cat.validate_catalog(broken))
    broken["controllers"][0]["supply_voltage_V"]["max"].update(basis="stated", page=None)
    assert any("page" in p for p in cat.validate_catalog(broken))


def test_prefill_values_are_the_specification_fields_the_entry_knows():
    pre = cat.summary(cat.get_controller("dti-hv-850-rev4"))["prefill"]
    assert pre["2_6.peak_Arms"]["value"] == 600 and pre["2_6.peak_duration_s"]["value"] == 10
    assert pre["2_6.continuous_Arms"]["value"] == pytest.approx(364.9)
    assert pre["2_7.switching_frequency_Hz"]["value"] == 8000
    assert pre["2_8"]["value"] == "Absolute encoder" and pre["2_8"]["basis"] == "inferred"
    assert "2_5.device_type" not in pre and "2_5.voltage_class_V" not in pre      # unknown stays unknown
    assert all(v["page"] for v in pre.values())


def test_read_routes():
    c = _client()
    body = c.get("/api/controllers/catalog").json()
    assert [r["id"] for r in body["controllers"]] == ["dti-hv-550-rev4", "dti-hv-850-rev4"]
    assert body["controllers"][1]["supply_max_V"] == 800
    one = c.get("/api/controllers/catalog/dti-hv-850-rev4").json()
    assert one["entry"]["phase_current"]["peak"][0]["page"] == 10 and one["summary"]["display_name"] == "DTI HV-850 (Rev 4)"
    assert c.get("/api/controllers/dti-hv-550-rev4").json()["summary"]["model"] == "HV-550"
    assert c.get("/api/controllers/catalog/nope").status_code == 404


def test_free_text_controller_is_recognised_and_linked_on_read_without_data_loss():
    stored = _draft(**{"2_4": {"manufacturer_model": "DTI HV-850 (Rev 4)", "supplier": "Customer", "count": 2},
                       "2_6": {"continuous_Arms": 111}})
    linked = cat.link_catalog_controller(stored)
    # the old supplier CHOICE ("Customer") becomes the company name of the catalogue brand; the old answer stays readable
    assert linked["fields"]["2_4"]["value"] == {"manufacturer_model": "DTI HV-850 (Rev 4)", "supplier": "DTI",
                                                "supplier_legacy": "Customer", "count": 2, "catalog_id": "dti-hv-850-rev4"}
    models.normalize_draft(linked)                                              # supplier_legacy / catalog_id are valid properties
    assert linked["fields"]["2_6"] == stored["fields"]["2_6"]                  # the customer's own limit is not overwritten
    assert "catalog_id" not in stored["fields"]["2_4"]["value"]                # the stored draft itself is untouched
    assert cat.link_catalog_controller(linked) == linked                       # idempotent
    for text in ("dti hv850", "HV-850", "DTI  HV-850   rev. 4"):
        assert cat.match_free_text(text)["id"] == "dti-hv-850-rev4", text
    for text in ("Own design", "", None, "Sevcon Gen4", "HV-8500"):
        assert cat.match_free_text(text) is None, text
    other = _draft(**{"2_4": {"manufacturer_model": "Own design"}})
    assert cat.link_catalog_controller(other) == other
    # a stale id (an entry removed from the file later) is kept, never replaced
    stale = _draft(**{"2_4": {"manufacturer_model": "DTI HV-850 (Rev 4)", "catalog_id": "gone"}})
    assert cat.link_catalog_controller(stale)["fields"]["2_4"]["value"]["catalog_id"] == "gone"


def test_supplier_is_a_company_name_never_overwritten_and_filled_when_empty():
    typed = _draft(**{"2_4": {"manufacturer_model": "DTI HV-850 (Rev 4)", "supplier": "Erium GmbH"}})
    assert cat.link_catalog_controller(typed)["fields"]["2_4"]["value"]["supplier"] == "Erium GmbH"
    assert "supplier_legacy" not in cat.link_catalog_controller(typed)["fields"]["2_4"]["value"]
    empty = _draft(**{"2_4": {"manufacturer_model": "DTI HV-550"}})
    assert cat.link_catalog_controller(empty)["fields"]["2_4"]["value"]["supplier"] == "DTI"
    for legacy in ("Customer", "MOTRES", "third party"):
        v = cat.link_catalog_controller(_draft(**{"2_4": {"manufacturer_model": "DTI HV-850 (Rev 4)", "supplier": legacy}}))["fields"]["2_4"]["value"]
        assert v["supplier"] == "DTI" and v["supplier_legacy"] == legacy
    # an own-design controller keeps its old choice untouched
    own = _draft(**{"2_4": {"manufacturer_model": "Own design", "supplier": "Customer"}})
    assert cat.link_catalog_controller(own) == own
    # the supplier is no longer one of a fixed list: any company text is a valid answer
    models.normalize_draft(_draft(**{"2_4": {"manufacturer_model": "Own design", "supplier": "Example Drives Ltd"}}))


def test_the_specification_read_route_links_the_controller(monkeypatch):
    """GET /spec runs the same link (models + routes wiring), and the saved draft normalises with the new catalog_id property."""
    draft = models.normalize_draft(_draft(**{"2_4": {"manufacturer_model": "DTI HV-850 (Rev 4)", "supplier": "Customer"}}))
    linked = cat.link_catalog_controller(models.with_defaults(draft))
    models.normalize_draft(linked)                                              # catalog_id is a valid property of 2_4
    assert linked["fields"]["2_4"]["value"]["catalog_id"] == "dti-hv-850-rev4"


def test_battery_above_the_controller_supply_maximum_is_a_validation_error():
    pack = {"voltage_nominal_V": 756, "voltage_max_V": 806}
    draft = _draft(**{"2_1": pack, "2_4": {"manufacturer_model": "DTI HV-850 (Rev 4)", "supplier": "Customer"}})
    errors = models.validation_errors(models.normalize_draft(draft))
    hit = [e for e in errors if e["field"] == "2_1.voltage_max_V"]
    assert hit and "806" in hit[0]["message"] and "800" in hit[0]["message"] and "DTI HV-850" in hit[0]["message"]
    # the same pack on a controller that is not in the catalogue, or a pack inside the limit: no error
    ok = _draft(**{"2_1": {"voltage_nominal_V": 756, "voltage_max_V": 800}, "2_4": {"manufacturer_model": "DTI HV-850 (Rev 4)"}})
    assert not [e for e in models.validation_errors(models.normalize_draft(ok)) if e["field"] == "2_1.voltage_max_V"]
    own = _draft(**{"2_1": pack, "2_4": {"manufacturer_model": "Own design"}})
    assert not [e for e in models.validation_errors(models.normalize_draft(own)) if e["field"] == "2_1.voltage_max_V"]
    # a pack given only by cells is checked on cells x cell maximum
    cells = _draft(**{"2_1": {"voltage_nominal_V": 700, "series_cells": 192, "cell_voltage_max_V": 4.2},
                      "2_4": {"manufacturer_model": "DTI HV-850", "catalog_id": "dti-hv-850-rev4"}})
    assert cat.battery_vs_controller(cells)["message"].startswith("Battery maximum 806.4 V")


def test_malformed_catalog_is_refused(tmp_path, monkeypatch):
    bad = tmp_path / "catalog.yaml"
    bad.write_text(yaml.safe_dump({"controllers": [{"id": "A B"}]}), encoding="utf-8")
    monkeypatch.setattr(cat, "_PATH", bad)
    with pytest.raises(cat.CatalogError):
        cat.load_catalog()
    assert _client().get("/api/controllers/catalog").status_code == 500


def test_catalog_path_falls_back_to_shared_then_image_seed(tmp_path, monkeypatch):
    """The API image has no <app>/config (only config-seed/): the route answered 500 all day 2026-10-27."""
    import motor_ai_sim.workspace as ws
    missing = tmp_path / "repo" / "config" / "controllers" / "catalog.yaml"
    seed = tmp_path / "config-seed" / "controllers" / "catalog.yaml"
    seed.parent.mkdir(parents=True)
    seed.write_text("controllers: []\n", encoding="utf-8")
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(cat, "_REPO_PATH", missing)
    monkeypatch.setattr(cat, "_PATH", missing)
    monkeypatch.setattr(cat, "_SEED_PATH", seed)
    monkeypatch.setattr(ws, "shared_root", lambda: shared)
    assert cat.catalog_path() == seed
    own = shared / "controllers" / "catalog.yaml"
    own.parent.mkdir()
    own.write_text("controllers: []\n", encoding="utf-8")
    assert cat.catalog_path() == own
    fixture = tmp_path / "fixture.yaml"
    monkeypatch.setattr(cat, "_PATH", fixture)
    assert cat.catalog_path() == fixture
