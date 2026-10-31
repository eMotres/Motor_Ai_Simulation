"""The six-phase L_xy probe, kept across runs of one machine (2026-10-31).

`routes.simulation` keeps every solved full-ring probe block per workspace
(`.six_phase_lxy.json`) under a key of everything the probe is solved from
except the operating point, and a later run of the same machine takes the
nearest stored block through the coupled loop's own reuse rule
(`_lxy_reuse_block`, LXY_REUSE_TOL).  Pure unit tests: no mesh, no solve.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from motor_ai_sim.routes import simulation as S


@pytest.fixture()
def store(tmp_path, monkeypatch):
    p = tmp_path / ".six_phase_lxy.json"
    monkeypatch.setattr(S, "_lxy_store_path", lambda: p)
    monkeypatch.delenv("SB_LXY_STORE", raising=False)
    return p


def _blk(i=152.1, g=2.8, t=130.0, lxy=0.0123):
    return {"inductances": {"L_xy_mH": lxy, "Ld_set_fp_mH": 0.62},
            "probe": {"model": "full ring", "frames": 4},
            "per_set_flux": [[0.1, 0.2], [0.3, 0.4]],
            "probe_op": {"I_A": i, "gamma_deg": g, "magnet_temp_c": t},
            "inductances_note": "not kept"}


def test_the_key_covers_the_machine_and_the_code(monkeypatch):
    k = S._lxy_store_key(cfp="a", geo="g", six={"sets": 2}, mesh=[4.0, 0.3])
    assert k and k == S._lxy_store_key(cfp="a", geo="g", six={"sets": 2},
                                       mesh=[4.0, 0.3])
    assert k != S._lxy_store_key(cfp="b", geo="g", six={"sets": 2}, mesh=[4.0, 0.3])
    assert k != S._lxy_store_key(cfp="a", geo="g", six={"sets": 2}, mesh=[3.0, 0.3])
    import motor_ai_sim.run_history as RH
    monkeypatch.setattr(RH, "code_version", lambda: "another-build")
    assert k != S._lxy_store_key(cfp="a", geo="g", six={"sets": 2}, mesh=[4.0, 0.3])


def test_a_stored_probe_is_this_machines_at_its_own_point(store):
    assert S._lxy_store_find("K", i_a=152.1, gamma_deg=2.8, magnet_temp_c=130.0) is None
    assert S._lxy_store_put("K", _blk()) is True
    got = S._lxy_store_find("K", i_a=152.1, gamma_deg=2.8, magnet_temp_c=130.0)
    assert got["inductances"]["L_xy_mH"] == 0.0123
    assert got["per_set_flux"] == [[0.1, 0.2], [0.3, 0.4]]
    assert "inductances_note" not in got
    assert got["inductances_reused"]["source"] == S._LXY_STORE_SOURCE
    assert got["inductances_reused"]["probe_op"]["I_A"] == 152.1
    # another machine (key) never sees it
    assert S._lxy_store_find("OTHER", i_a=152.1, gamma_deg=2.8,
                             magnet_temp_c=130.0) is None


def test_the_reuse_rule_is_the_coupled_loops(store):
    S._lxy_store_put("K", _blk())
    ti, tg, tm = S.LXY_REUSE_TOL
    ok = S._lxy_store_find("K", i_a=152.1 * (1 + 0.9 * ti / 100.0),
                           gamma_deg=2.8 + 0.9 * tg, magnet_temp_c=130.0 + 0.9 * tm)
    assert ok is not None
    for kw in ({"i_a": 152.1 * (1 + 1.5 * ti / 100.0), "gamma_deg": 2.8,
                "magnet_temp_c": 130.0},
               {"i_a": 152.1, "gamma_deg": 2.8 + 1.5 * tg, "magnet_temp_c": 130.0},
               {"i_a": 152.1, "gamma_deg": 2.8, "magnet_temp_c": 130.0 + 1.5 * tm},
               {"i_a": 152.1, "gamma_deg": 2.8, "magnet_temp_c": None}):
        assert S._lxy_store_find("K", **kw) is None, kw


def test_the_nearest_stored_point_wins_and_one_entry_per_point(store):
    S._lxy_store_put("K", _blk(i=150.0, lxy=1.0))
    S._lxy_store_put("K", _blk(i=152.0, lxy=2.0))
    S._lxy_store_put("K", _blk(i=152.0, lxy=3.0))        # replaces the 152.0 one
    got = S._lxy_store_find("K", i_a=151.9, gamma_deg=2.8, magnet_temp_c=130.0)
    assert got["inductances"]["L_xy_mH"] == 3.0
    doc = json.loads(store.read_text(encoding="utf-8"))
    assert len(doc["entries"]) == 2


def test_the_store_is_bounded_and_keeps_the_newest(store, monkeypatch):
    monkeypatch.setattr(S, "_LXY_STORE_MAX", 3)
    for j in range(5):
        S._lxy_store_put("K", _blk(i=100.0 + 10.0 * j, lxy=float(j)))
    doc = json.loads(store.read_text(encoding="utf-8"))
    assert [e["six_phase"]["inductances"]["L_xy_mH"] for e in doc["entries"]] == [
        2.0, 3.0, 4.0]


def test_nothing_is_kept_or_found_when_off_or_in_doubt(store, monkeypatch):
    assert S._lxy_store_put("K", {"probe_op": {"I_A": 1.0}}) is False  # no inductances
    assert S._lxy_store_put("K", {"inductances": {"L": 1.0}}) is False  # no op
    assert S._lxy_store_put(None, _blk()) is False
    assert not store.exists()
    monkeypatch.setenv("SB_LXY_STORE", "0")
    assert S._lxy_store_put("K", _blk()) is False
    assert not store.exists()
    monkeypatch.delenv("SB_LXY_STORE")
    S._lxy_store_put("K", _blk())
    monkeypatch.setenv("SB_LXY_STORE", "0")
    assert S._lxy_store_find("K", i_a=152.1, gamma_deg=2.8, magnet_temp_c=130.0) is None
    monkeypatch.delenv("SB_LXY_STORE")
    # a broken file is "nothing stored", and the next put rewrites it
    store.write_text("{not json", encoding="utf-8")
    assert S._lxy_store_find("K", i_a=152.1, gamma_deg=2.8, magnet_temp_c=130.0) is None
    assert S._lxy_store_put("K", _blk()) is True
    assert S._lxy_store_find("K", i_a=152.1, gamma_deg=2.8,
                             magnet_temp_c=130.0) is not None
    # a value that is not a number never reaches the file
    bad = _blk()
    bad["inductances"]["L_xy_mH"] = float("nan")
    assert S._lxy_store_put("K2", bad) is False


def test_the_route_consults_the_store_and_keeps_what_it_solved():
    """Static guard (no solve): the probe block asks the store when the caller
    gave no probe to reuse, solves with the same d-axis it keyed on, and keeps
    the block it solved."""
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "routes"
           / "simulation.py").read_text(encoding="utf-8")
    i_ctx = src.index("_reuse6 = _lxy_reuse_block(\n                        _lxy_ctx")
    i_key = src.index("_lxy_key = _lxy_store_key(", i_ctx)
    i_find = src.index("_reuse6 = _lxy_store_find(", i_key)
    i_used = src.index("if _reuse6 is not None:", i_find)
    i_solve = src.index("_pr = _msi(", i_used)
    i_put = src.index("_lxy_store_put(_lxy_key, _blk6)", i_solve)
    assert i_ctx < i_key < i_find < i_used < i_solve < i_put
    assert "daxis_deg=_daxis6," in src[i_solve:i_put]
