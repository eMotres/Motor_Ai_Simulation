"""The catalog tree is built from cached ROWS, and the cache never crosses accounts (owner 2026-11-12).

``GET /api/family/tree`` took 10-24 s and sent 3.4 MB: every changed file (and the 13 MB duty-results store changes with every
finished job) dropped the whole-tree memo, every die was parsed again, the store was parsed once PER DIE, and each die carried its
64 KB cross-section drawing.  What is under test:

* (a) a second call with nothing changed parses nothing (no yaml load, no store read);
* (b) touching one configuration rebuilds that one row and the answer shows the change; a finished job (the store) rebuilds only
  the configuration it solved;
* (c) the cached rows carry no access: every account gets the rows its own access allows, in either call order - a private
  configuration never reaches an account without its grant, a die never reaches an account without its grant;
* (d) the slimmed payload still carries every field the web client reads, the drawing moved to ``/api/family/thumbs`` (same dies,
  ETag), and the shared parsed store is never mutated by a build.

Everything runs against a throwaway workspace in the pytest tmp area.
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

import pytest
import yaml
from fastapi import Response

from motor_ai_sim import duty_results as dr
from motor_ai_sim import motor_access as ma

SVG = "<svg xmlns='http://www.w3.org/2000/svg'><circle r='5'/></svg>"


def _cfg(name: str, turns: int = 10, **extra) -> dict:
    return {"name": name, "role": "motor",
            "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0, "num_wires_per_slot": turns},
            "winding": {"connection": "star"},
            "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
            "duties": [{"name": "peak", "mode": "motor", "saved_at": "2026-09-02T10:00:00",
                        "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0, "note": "",
                        "result": {"efficiency_pct": 95.0, "mass_kg": 1.0}}],
            **extra}


def _write(p: Path, doc: dict, age_s: float = 30.0) -> None:
    """Write a yaml document and age its mtime, so no 'written this very tick' rule is in play."""
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    t = time.time() - age_s
    os.utime(p, (t, t))


@pytest.fixture()
def cat(tmp_path, monkeypatch):
    """Two dies (A: L1, L2 and a PRIVATE LP; B: L1) in a throwaway workspace, the duty store aged."""
    from motor_ai_sim import config as cfgmod
    from motor_ai_sim import workspace as ws
    from motor_ai_sim.routes import family as fam

    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", str(tmp_path / "motor_config.yaml"), raising=True)
    for var in ("WORKSPACES_ROOT", "SHARED_ROOT", "PUBLISHED_ROOT"):
        monkeypatch.delenv(var, raising=False)
    ws._PROC_CACHE = None
    dies = tmp_path / "dies"
    for die in ("Die A", "Die B"):
        (dies / die).mkdir(parents=True)
        _write(dies / die / "die.yaml", {"name": die, "locked": False, "created": "2026-09-02T10:00:00",
                                         "thumb_svg": SVG,
                                         "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0}})
    _write(dies / "Die A" / "L1.yaml", _cfg("L1"))
    _write(dies / "Die A" / "L2.yaml", _cfg("L2"))
    _write(dies / "Die A" / "LP.yaml", _cfg("LP", visibility="private"))
    _write(dies / "Die B" / "L1.yaml", _cfg("L1"))
    store = tmp_path / ".duty_results.json"
    store.write_text(json.dumps({"version": 1, "results": {
        "Die A": {"L1": {"peak": {"thermal": {"kind": "thermal"}}}}}}), encoding="utf-8")
    os.utime(store, (time.time() - 30, time.time() - 30))
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    monkeypatch.setattr(ma, "get_die_access", lambda _d: {"visibility": "private", "clients": []}, raising=False)
    fam._TREE_CACHE.clear()
    dr._STORE_MEMO.clear()
    yield {"fam": fam, "dies": dies, "store": store, "root": tmp_path}
    fam._TREE_CACHE.clear()
    dr._STORE_MEMO.clear()
    ws._PROC_CACHE = None


ADMIN = {"mode": ma.MODE_ALL, "dies": frozenset(), "email": None, "is_admin": True, "configs": frozenset()}


def _granted(*dies, configs=(), email="x@example.com") -> dict:
    return {"mode": ma.MODE_GRANTED, "dies": frozenset(dies), "email": email, "is_admin": False,
            "configs": frozenset(configs)}


def _tree(fam, acc, **kw) -> dict:
    kw.setdefault("with_mass", False)
    return fam.tree_for_access(Response(), acc, **kw)


def _rows(tree) -> dict:
    return {(d["name"], c["name"]): c for d in tree["dies"] for c in d["configs"]}


@pytest.fixture()
def counters(monkeypatch, cat):
    """Counts configuration / die loads of the tree builder and parses of the duty-results store."""
    fam = cat["fam"]
    seen = {"yaml": [], "store": []}
    real_load, real_read = fam._load_yaml, dr._read_store

    def load(p, what):
        seen["yaml"].append((what, Path(p).parent.name, Path(p).name))
        return real_load(p, what)

    def read(p):
        seen["store"].append(str(p))
        return real_read(p)

    monkeypatch.setattr(fam, "_load_yaml", load)
    monkeypatch.setattr(dr, "_read_store", read)
    return seen


# ── (a) nothing changed, nothing parsed ──────────────────────────────────────

def test_a_second_call_with_unchanged_files_parses_nothing(cat, counters):
    fam = cat["fam"]
    first = _tree(fam, ADMIN)
    assert len(counters["yaml"]) >= 4 and counters["store"], "the first call really built the rows"
    counters["yaml"].clear()
    counters["store"].clear()
    second = _tree(fam, ADMIN)
    assert counters["yaml"] == [], f"re-parsed: {counters['yaml']}"
    assert counters["store"] == [], "the duty-results store was parsed again"
    assert _rows(second).keys() == _rows(first).keys()


def test_the_store_is_parsed_once_for_the_whole_catalog_not_once_per_die(cat, counters):
    _tree(cat["fam"], ADMIN)
    assert len(counters["store"]) == 1, counters["store"]


# ── (b) one changed file rebuilds one row ────────────────────────────────────

def test_touching_one_configuration_rebuilds_only_that_row(cat, counters):
    fam = cat["fam"]
    before = _rows(_tree(fam, ADMIN))
    assert before[("Die A", "L1")]["turns"] == 10
    counters["yaml"].clear()
    _write(cat["dies"] / "Die A" / "L1.yaml", _cfg("L1", turns=14), age_s=0.0)
    after = _rows(_tree(fam, ADMIN))
    configs_loaded = [x for x in counters["yaml"] if x[0] == "configuration"]
    assert configs_loaded == [("configuration", "Die A", "L1.yaml")], configs_loaded
    assert after[("Die A", "L1")]["turns"] == 14, "the answer shows the change"
    assert after[("Die A", "L2")]["turns"] == 10 and after[("Die B", "L1")]["turns"] == 10


def test_a_finished_job_rebuilds_only_the_configuration_it_solved(cat, counters):
    fam = cat["fam"]
    _tree(fam, ADMIN)
    counters["yaml"].clear()
    time.sleep(0.01)
    dr.record("Die B", "L1", "peak", "thermal", {"kind": "thermal", "t_max_c": 90.0})
    after = _rows(_tree(fam, ADMIN))
    configs_loaded = [x for x in counters["yaml"] if x[0] == "configuration"]
    assert configs_loaded == [("configuration", "Die B", "L1.yaml")], configs_loaded


def test_a_new_stored_answer_still_shows_after_a_store_rewrite(cat):
    fam = cat["fam"]
    assert "time_to_limit" not in _rows(_tree(fam, ADMIN))[("Die A", "L1")]["duties"][0]
    ttl = {"within_limits": False, "limiting_part": "winding", "time_to_limit_s": 160.2,
           "limits_c": {"winding": 200.0}, "at_point_c": {"winding": 212.4}, "over_by_K": {"winding": 12.4},
           "starts": {"cold": {"time_to_limit_s": 160.2}, "rated": {"time_to_limit_s": 65.4}}, "note": "n"}
    dr.record("Die A", "L1", "peak", "coupled", {"time_to_limit": ttl})
    assert _rows(_tree(fam, ADMIN))[("Die A", "L1")]["duties"][0]["time_to_limit"]["cold_s"] == 160.2


def test_an_unreadable_signature_never_serves_a_cached_row(cat, monkeypatch):
    fam = cat["fam"]
    _tree(fam, ADMIN)
    monkeypatch.setattr(dr, "config_digest", lambda *_a: None)
    n = []
    real = fam._build_config_row
    monkeypatch.setattr(fam, "_build_config_row", lambda *a, **k: (n.append(1), real(*a, **k))[1])
    _tree(fam, ADMIN)
    _tree(fam, ADMIN)
    assert len(n) == 2 * 4, "with no usable signature every call rebuilds all four rows"


# ── (c) the cache carries no access ──────────────────────────────────────────

@pytest.mark.parametrize("order", ["admin_first", "admin_last"])
def test_accounts_never_share_rows_they_must_not_share(cat, order):
    fam = cat["fam"]
    alice = _granted("Die A")
    bob = _granted("Die B")
    carol = _granted("Die A", configs=["Die A/LP"])
    calls = [("admin", ADMIN), ("alice", alice), ("bob", bob), ("carol", carol)]
    if order == "admin_last":
        calls = calls[1:] + calls[:1]
    got = {name: set(_rows(_tree(fam, acc))) for name, acc in calls}
    assert got["admin"] == {("Die A", "L1"), ("Die A", "L2"), ("Die A", "LP"), ("Die B", "L1")}
    assert got["alice"] == {("Die A", "L1"), ("Die A", "L2")}, "no private configuration, no other die"
    assert got["bob"] == {("Die B", "L1")}
    assert got["carol"] == {("Die A", "L1"), ("Die A", "L2"), ("Die A", "LP")}, "the grant opens exactly its configuration"
    # and again, the rows now served from the cache
    again = {name: set(_rows(_tree(fam, acc))) for name, acc in calls}
    assert again == got


def test_counts_follow_the_visible_rows_not_the_cached_ones(cat):
    fam = cat["fam"]
    _tree(fam, ADMIN)
    die_a = next(d for d in _tree(fam, _granted("Die A"))["dies"])
    assert die_a["config_count"] == 2
    die_a_all = next(d for d in _tree(fam, ADMIN)["dies"] if d["name"] == "Die A")
    assert die_a_all["config_count"] == 3


def test_an_account_with_no_grant_gets_the_note_and_no_dies(cat):
    out = _tree(cat["fam"], _granted())
    assert out["dies"] == [] and out["note_code"] == "no_motors_granted"


# ── (d) the slimmed payload ──────────────────────────────────────────────────

#: every key of a tree die / configuration / duty that the web client reads (web/src/components/catalog/FamilyCatalog.tsx
#: ``Die`` / ``Cfg`` / ``Duty``, lib/simPoints ``TreeCfg`` / ``TreeDuty``, lib/cardMotor, lib/catalogOpenness)
CLIENT_DIE_KEYS = {"name", "locked", "created", "slots", "poles", "stator_diameter", "configs", "cards", "config_count",
                   "source", "has_thumb"}
CLIENT_CFG_KEYS = {"name", "role", "role_source", "role_stored", "locked", "build_sig", "build_sig_aliases", "applications",
                   "application", "application_set", "applications_error", "default_duty", "default_duty_stored",
                   "default_duty_configured", "stack_mm", "wire_height_mm", "wire_width_mm", "turns", "connection",
                   "phases", "star_delta", "magnet", "steel", "parts", "battery", "bearings", "controller", "card_date",
                   "mass", "duties", "name_stack_mismatch"}
CLIENT_DUTY_KEYS = {"name", "mode", "saved_at", "current_arms", "rpm", "gamma_deg", "star_delta", "torque_nm", "power_kw",
                    "note", "result", "duty_cycle", "primary_drive", "runs"}


def test_the_payload_keeps_every_field_the_client_reads_and_drops_only_the_drawing(cat):
    out = _tree(cat["fam"], ADMIN, with_mass=True)
    die = out["dies"][0]
    assert CLIENT_DIE_KEYS <= set(die), CLIENT_DIE_KEYS - set(die)
    assert "thumb_svg" not in die and die["has_thumb"] is True
    cfg = die["configs"][0]
    assert CLIENT_CFG_KEYS <= set(cfg), CLIENT_CFG_KEYS - set(cfg)
    duty = cfg["duties"][0]
    assert CLIENT_DUTY_KEYS <= set(duty), CLIENT_DUTY_KEYS - set(duty)
    assert duty["runs"] == [] and "summary" not in duty and "mesh" not in duty, "run history is described, never shipped"
    assert len(json.dumps(out)) < 20_000


def test_the_drawings_come_from_the_thumbs_route_for_the_same_dies(cat, monkeypatch):
    fam = cat["fam"]
    for acc, names in ((ADMIN, ["Die A", "Die B"]), (_granted("Die B"), ["Die B"])):
        monkeypatch.setattr(fam, "catalog_access", lambda _a=None, _acc=acc: _acc)
        resp = fam.tree_thumbs(authorization=None, if_none_match=None)
        body = json.loads(resp.body)
        assert sorted(body["thumbs"]) == names and all(v == SVG for v in body["thumbs"].values())
        assert [d["name"] for d in _tree(fam, acc)["dies"]] == names, "thumbs and tree name the same dies"
        etag = resp.headers["etag"]
        assert fam.tree_thumbs(authorization=None, if_none_match=etag).status_code == 304
        assert fam.tree_thumbs(authorization=None, if_none_match="W/" + etag).status_code == 304
        assert fam.tree_thumbs(authorization=None, if_none_match='"other"').status_code == 200


def test_a_changed_drawing_changes_the_etag(cat, monkeypatch):
    fam = cat["fam"]
    monkeypatch.setattr(fam, "catalog_access", lambda _a=None: ADMIN)
    e1 = fam.tree_thumbs(authorization=None, if_none_match=None).headers["etag"]
    _write(cat["dies"] / "Die A" / "die.yaml", {"name": "Die A", "locked": False, "thumb_svg": SVG + "<!-- v2 -->",
                                                "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0}},
           age_s=0.0)
    e2 = fam.tree_thumbs(authorization=None, if_none_match=None).headers["etag"]
    assert e1 != e2


def test_a_build_never_mutates_the_shared_parsed_store(cat):
    fam = cat["fam"]
    dr.record("Die A", "L2", "peak", "coupled", {"time_to_limit": {"within_limits": False, "limiting_part": "winding",
                                                                    "starts": {"cold": {"time_to_limit_s": 5.0}},
                                                                    "limits_c": {"winding": 200.0}, "at_point_c": {"winding": 210.0}}})
    p = dr.store_path()
    dr._STORE_MEMO.clear()
    _tree(fam, ADMIN)
    memo = dr._STORE_MEMO[str(p)][2]
    assert memo == dr._read_store(p), "the rows were built from the memo without changing it"
    snap = copy.deepcopy(memo)
    _tree(fam, ADMIN, with_mass=True)
    assert dr._STORE_MEMO[str(p)][2] == snap
