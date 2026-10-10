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
    # the rated duty is a node of the (rpm × I_A) grid, named by its duty
    pt = next(p for p in si["points"].values() if p.get("duty") == "rated")
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
    points that carry rpm and I_A on a FULL (rpm × I_A) grid (readVariant refuses
    a gap), and the loss/efficiency fields — or, where the machine cannot run
    there, a status saying why (never a bare null)."""
    seen = 0
    for die, cfg, rec in ps._records():
        for v in rec["pwm_variants"]:
            assert v["id"] and v["device"] and v["carrier_hz"] > 0
            rpms = {p["rpm"] for p in v["points"].values()}
            amps = {p["I_A"] for p in v["points"].values()}
            assert len(v["points"]) == len(rpms) * len(amps), (die, cfg, v["id"])
            for k, p in v["points"].items():
                assert p["rpm"] >= 0 and p["I_A"] > 0, (die, cfg, v["id"], k)
                if p.get("inverter_loss_W") is not None:
                    assert set(p["inverter_loss_W"]) >= {"cond", "sw", "dead"}
                    assert p["eta_drive_pct"] is not None and p["motor_pwm_loss_W"] is not None
                    assert p["p_cont_max_W"] is not None or p.get("p_cont_max_status")
                else:
                    assert p.get("status"), (die, cfg, v["id"], k)
            full = [p for p in v["points"].values() if p.get("inverter_loss_W") is not None]
            assert len(full) >= len(v["points"]) // 2, (die, cfg, v["id"])
            seen += 1
    assert seen >= 5


def test_the_variants_reach_below_and_above_the_rated_current():
    """Configure's default point (I0 of the old preset, 40.7 A on L12) must be
    inside every L12 variant's current range (2026-10-05 gap)."""
    for die, cfg, rec in ps._records():
        if cfg != "L12":
            continue
        for v in rec["pwm_variants"]:
            amps = sorted({p["I_A"] for p in v["points"].values()})
            assert amps[0] < 40.659 < amps[-1], v["id"]


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


def test_references_flag_the_cards_that_are_real_machines(tmp_path, monkeypatch):
    """Two cards of one geometry — the real "<die>" and a legacy duplicate with another name —
    are told apart by `has_machine`, so Configure never picks the duplicate (no pack, no
    controller, no variants) for the loaded machine (live L12 showed a 100-cell default)."""
    import yaml
    from motor_ai_sim import workspace as ws
    from motor_ai_sim.routes import catalog as cat_mod
    d = tmp_path / "Die Q"
    d.mkdir()
    (d / "die.yaml").write_text(yaml.safe_dump({"name": "Die Q"}), encoding="utf-8")
    (d / "C1.yaml").write_text(yaml.safe_dump({"name": "C1", "die": "Die Q"}), encoding="utf-8")
    (d / "C2.yaml").write_text(yaml.safe_dump({"name": "C2", "die": "Die Q"}), encoding="utf-8")
    monkeypatch.setattr(ws, "iter_dies", lambda: [{"name": "Die Q", "die": "Die Q", "dir": str(d)}])
    p = tmp_path / "motor_catalog.json"
    p.write_text(json.dumps({"motors": [
        {**_card(name="Die Q 40_12"), "id": "dup"},            # legacy duplicate of the geometry
        {**_card(name="Die Q"), "id": "die_level"},             # older cards carry the die name alone
        {**_card(name="Die Q C2"), "id": "real"},                # "<die> <configuration>"
        {**_card(name="Elsewhere"), "id": "none"}]}), encoding="utf-8")
    monkeypatch.setattr(cat_mod, "_CATALOG_PATH", p)
    cat_mod._REF_CACHE.update({"key": None, "cards": []})
    import motor_ai_sim.auth as auth
    monkeypatch.setattr(auth, "caller_identity", lambda a=None: {"id": "u@x", "is_admin": True})
    app = FastAPI()
    app.include_router(cat_mod.router)
    got = {m["id"]: m["has_machine"] for m in TestClient(app).get("/api/catalog/references").json()["motors"]}
    assert got == {"dup": False, "die_level": True, "real": True, "none": False}


def test_the_store_is_parsed_once_per_change_and_keeps_only_what_configure_reads(monkeypatch, tmp_path):
    d = tmp_path / "Die B"
    d.mkdir()
    f = d / "L1.json"
    rec = {"pwm_variants": [{"id": "v", "device": "X", "carrier_hz": 1000,
                             "build": {"length_mm": 1.0}, "points": {"a": {"rpm": 1, "I_A": 1}}}],
           "cold": {"huge": list(range(5000))}, "hot_map": {"huge": list(range(5000))}}
    f.write_text(json.dumps(rec), encoding="utf-8")
    monkeypatch.setattr(ps, "_DIR", tmp_path)
    ps._CACHE.clear()
    calls = []
    real = json.loads
    monkeypatch.setattr(ps.json, "loads", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    for _ in range(5):
        assert ps.variants_for("Die B L1", None)[0]["id"] == "v"
    assert len(calls) == 1, "parsed on every request"
    # the heavy blocks are not kept; "full" (a v1 passport = a FULL card) and "date"
    # (the file's mtime) were added with the card badges (2026-10-05)
    assert set(ps._CACHE[str(f)][1]) == {"pwm_variants", "build", "full", "date"}
    # a changed file is re-read (the mtime/size key), an unchanged one is not
    rec["pwm_variants"][0]["id"] = "v2"
    f.write_text(json.dumps(rec) + " ", encoding="utf-8")
    assert ps.variants_for("Die B L1", None)[0]["id"] == "v2" and len(calls) == 2


def test_the_references_route_is_lean_and_visible_like_the_catalogue(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import catalog as cat_mod
    p = tmp_path / "motor_catalog.json"
    cards = [
        {**_card(), "id": "cat_x", "thumb_svg": "<svg>" + "x" * 5000 + "</svg>", "owner": "a@x"},
        {"id": "cat_nopass", "name": DIE, "thumb_svg": "<svg/>"},
        {**_card(name="Hidden"), "id": "cat_priv", "visibility": "private", "owner": "someone@else"},
    ]
    p.write_text(json.dumps({"tiers": [], "diameters_mm": [], "motors": cards}), encoding="utf-8")
    monkeypatch.setattr(cat_mod, "_CATALOG_PATH", p)
    cat_mod._REF_CACHE.update({"key": None, "cards": []})
    monkeypatch.setattr(cat_mod, "_caller_identity", lambda a=None: {"id": "u@x", "is_admin": False}, raising=False)
    import motor_ai_sim.auth as auth
    monkeypatch.setattr(auth, "caller_identity", lambda a=None: {"id": "u@x", "is_admin": False})
    app = FastAPI()
    app.include_router(cat_mod.router)
    c = TestClient(app)
    r = c.get("/api/catalog/references")
    assert r.status_code == 200
    motors = r.json()["motors"]
    assert [m["id"] for m in motors] == ["cat_x"]                       # passport cards only, private one hidden
    # no thumbnail; "card" ({die, config, date} of the full passport card, or None) is
    # the card badge added 2026-10-05
    assert set(motors[0]) == {"id", "name", "diameter_mm", "has_machine", "card", "passport"}
    assert any(v["id"] == "si_48k" for v in motors[0]["passport"]["pwm_variants"])
    assert b"thumb_svg" not in r.content and b"xxxxx" not in r.content   # the 5 KB thumbnail never travels
    # the cache is parsed once and never handed out mutated
    first = cat_mod._REF_CACHE["cards"]
    c.get("/api/catalog/references")
    assert cat_mod._REF_CACHE["cards"] is first and "pwm_variants" not in first[0]["passport"]
    # a rewritten catalogue file is picked up
    p.write_text(json.dumps({"motors": [{**_card(name="Other"), "id": "cat_y"}]}), encoding="utf-8")
    assert [m["id"] for m in c.get("/api/catalog/references").json()["motors"]] == ["cat_y"]
