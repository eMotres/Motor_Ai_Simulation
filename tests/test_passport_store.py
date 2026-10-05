"""Configure's computed drive variants reach the catalogue response (owner
2026-10-05): the passport pilot's records (config/passports/<die>/<config>.json,
exported from docs/data/passport_pilot_d40/full) supply ``pwm_variants`` to the
matching catalogue card — and to nothing else.

No solver: the catalogue is a temp file; the store is the REPO's real files.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import passport_store as ps

DIE = "CIANO14 40 new"


def _card(name=DIE, L0=12.0, **extra):
    return {"id": "cat_x", "name": name, "diameter_mm": 40,
            "passport": {"passport": {"L0_mm": L0, "N0": 7}, "fit": {}, "geo": {},
                         "poles": 14, "slots": 12, **extra}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from motor_ai_sim.routes import catalog as cat_mod
    p = tmp_path / "motor_catalog.json"
    cards = [_card(), {**_card(name="CIANO14 40 new L20", L0=20.0), "id": "cat_l20"},
             {**_card(name="CIANO28 150_35", L0=35.0), "id": "cat_other"},
             {"id": "cat_nopass", "name": DIE, "diameter_mm": 40}]
    p.write_text(json.dumps({"tiers": [], "diameters_mm": [40], "motors": cards}), encoding="utf-8")
    monkeypatch.setattr(cat_mod, "_CATALOG_PATH", p)
    app = FastAPI()
    app.include_router(cat_mod.router)
    return TestClient(app)


def _by_id(cat, mid):
    return next(m for m in cat["motors"] if m["id"] == mid)


def test_the_catalogue_response_carries_the_pilots_variants_for_l12(client):
    cat = client.get("/api/catalog").json()
    sp = _by_id(cat, "cat_x")["passport"]
    ids = [v["id"] for v in sp["pwm_variants"]]
    assert "si_48k" in ids and any(i.startswith("gan_") for i in ids)
    si = next(v for v in sp["pwm_variants"] if v["id"] == "si_48k")
    assert si["device"] == "IQE018N06NM6SC" and si["technology"] == "Si"
    assert si["carrier_hz"] == 48000.0 and si["n_parallel"] == 1
    assert si["bus_v"]["max"] == pytest.approx(25.2)
    pt = si["points"]["13000rpm_rated"]
    assert pt["rpm"] == 13000 and pt["I_A"] == pytest.approx(42.78)
    assert set(pt["inverter_loss_W"]) == {"cond", "sw", "dead"}
    # the L12 record's build (12 mm) is the one picked, not L20's
    assert si["build"]["length_mm"] == 12.0
    # the passport's own model is untouched
    assert sp["passport"] == {"L0_mm": 12.0, "N0": 7} and sp["poles"] == 14


def test_the_single_passport_route_carries_them_too(client):
    sp = client.get("/api/catalog/cat_x/passport").json()
    assert any(v["id"] == "si_48k" for v in sp["pwm_variants"])


def test_l20_is_matched_by_name_other_cards_and_unpassported_cards_are_unchanged(client):
    cat = client.get("/api/catalog").json()
    l20 = _by_id(cat, "cat_l20")["passport"]
    assert l20["pwm_variants"] and all(v["build"]["length_mm"] == 20.0 for v in l20["pwm_variants"])
    assert "pwm_variants" not in _by_id(cat, "cat_other")["passport"]
    assert "passport" not in _by_id(cat, "cat_nopass")


def test_every_stored_variant_is_readable_by_configure():
    """What the web's usableVariants() and readVariant() need: id, device, a carrier,
    points that carry rpm and I_A, and the loss/efficiency fields."""
    seen = 0
    for die, cfg, rec in ps._records():
        for v in rec["pwm_variants"]:
            assert v["id"] and v["device"] and v["carrier_hz"] > 0
            for k, p in v["points"].items():
                assert p["rpm"] >= 0 and p["I_A"] > 0, (die, cfg, v["id"], k)
                # a point the pilot could not solve (e.g. past a board limit) carries
                # nulls; Configure shows "-" for it, it never invents a number
                if p.get("inverter_loss_W") is not None:
                    assert set(p["inverter_loss_W"]) >= {"cond", "sw", "dead"}
                    assert p["eta_drive_pct"] is not None and p["motor_pwm_loss_W"] is not None
            full = [p for p in v["points"].values() if p.get("inverter_loss_W") is not None]
            assert len(full) >= len(v["points"]) - 1, (die, cfg, v["id"])
            seen += 1
    assert seen >= 5


def test_ambiguity_is_never_guessed(monkeypatch, tmp_path):
    d = tmp_path / "Die A"
    d.mkdir()
    for cfg, L in (("L10", 10.0), ("L20", 20.0)):
        (d / f"{cfg}.json").write_text(json.dumps({"pwm_variants": [{
            "id": "v", "device": "X", "carrier_hz": 1000, "build": {"length_mm": L},
            "points": {"a": {"rpm": 1, "I_A": 1}}}]}), encoding="utf-8")
    monkeypatch.setattr(ps, "_DIR", tmp_path)
    assert ps.variants_for("Die A", None) is None            # two configs, no length: no guess
    assert ps.variants_for("Die A", 15.0) is None            # no config of that length
    assert ps.variants_for("Die A", 20.0)[0]["build"]["length_mm"] == 20.0
    assert ps.variants_for("Die A L10", None)[0]["build"]["length_mm"] == 10.0   # "<die> <config>"
    assert ps.variants_for("nobody", 10.0) is None and ps.variants_for("", 1) is None
    # an unusable variant (no points) is dropped, and a record with none gives nothing
    (d / "L30.json").write_text(json.dumps({"pwm_variants": [{"id": "v", "device": "X",
                                                              "carrier_hz": 1000}]}), encoding="utf-8")
    assert ps.variants_for("Die A L30", None) is None
