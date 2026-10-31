"""The CATALOG copy of the warm state (owner 2026-10-31).

A catalog motor must hot-start from the converged state of its own duty wherever
the run happens, and the per-workspace warm library (``warm_library``) does not
travel with the catalog.  So a live-machine run also files its settled state next
to the duty's stored fields::

    <dies>/<die>/runs/<cfg>/<duty-stem>/fields/warm.npz

``warm_library.store_at`` writes it, ``duty_fields.save_warm_active`` files it
under the active duty, and ``warm_library.catalog_candidates`` offers every such
file of the workspace's own and the shared catalog to the same key-then-pick
lookup the library uses.

Pure unit tests: no FEM, no server, a throwaway directory tree.  What is pinned:

  (a) a state round-trips through ``store_at`` + ``load`` with everything the
      library kept (A, A2, Ued, solid, solid_grp, Br map, demag history) and
      the new parts: the net-current history ``Ib_prev`` / ``Ib_prev2`` and the
      magnet source per tag ``msrc``;
  (b) the file format did not move: an old-format file (no new keys) loads, and
      a state without the new parts is written with exactly the old members;
  (c) ``store`` keeps its gate, its sweep-lineage rule and its trim, ``store_at``
      has neither the gate nor the trim and never raises;
  (d) ``catalog_candidates`` finds the files of one machine key, ignores the
      others, honours ``SB_WARM_CATALOG=0`` and its file bound, and refuses a
      symlink that leaves the root;  ``catalog_roots`` is the workspace's own
      dies directory plus the shared catalog's, de-duplicated;
  (e) loading a catalog entry never touches the file (a run only reads it);
  (f) ``save_warm_active`` files the state under the active duty, is silent
      without one (or for a ``record: false`` run) and never raises;
  (g) the file survives the duty bookkeeping (rename carries it, drop removes
      it, the report-field listing ignores it).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim import duty_fields as DF
from motor_ai_sim import run_recording
from motor_ai_sim import workspace as WS
from motor_ai_sim.simulation import warm_library as W

KEY = "k" * 16
OTHER_KEY = "l" * 16

_OLD_MEMBERS = {"lib_meta", "doflocs", "A", "Ued", "solid", "A2",
                "br_val", "br_cen", "br_tag", "solid_grp_mag"}
_OLD_META_KEYS = {"schema", "key", "op", "origin", "created", "meta", "I", "rpm",
                  "gam", "geo_fp", "nspp_req", "nsect", "has_br", "br_repro_op",
                  "demag_history"}


def _state(rng, I=60.0, gam=10.0, rpm=15000.0, *, br=True, a2=True, grp=True,
           ib=True, msrc=True):
    wc = {"doflocs": rng.random((30, 2)).astype(np.float32), "A": rng.random(30),
          "Ued": rng.random(4), "solid": rng.random(12),
          "meta": {"nspp": 12, "npd": 1.0, "conn": "np2", "temp": 120.0,
                   "mscale": 1.0, "ts": "bdf2"},
          "I": float(I), "rpm": float(rpm), "gam": float(gam), "geo_fp": "g1",
          "nspp_req": 12, "nsect": 2}
    if a2:
        wc["A2"] = rng.random(30)
    if grp:
        wc["solid_grp"] = {"mag": rng.random(12)}
    if br:
        wc.update(br_val=np.clip(rng.random(9), 0.0, 1.0),
                  br_cen=rng.random((9, 2)), br_tag=np.full(9, 300, int))
    if ib:
        wc["Ib_prev"] = (rng.random(6), rng.random(6))
        wc["Ib_prev2"] = (rng.random(6), rng.random(6))
    if msrc:
        wc["msrc"] = {"300": 1.2e6, "301": -1.2e6}
    return wc


def _fields_file(root: Path, die="D1", cfg="c1", stem="peak-0a1b2c3d") -> Path:
    return root / die / "runs" / cfg / stem / "fields" / W.CATALOG_FILE


def _put(root: Path, wc, *, key=KEY, op="op1", die="D1", cfg="c1",
         stem="peak-0a1b2c3d", **kw) -> Path:
    p = _fields_file(root, die, cfg, stem)
    assert W.store_at(p, wc, key=key, op=op, origin="interactive", **kw) == p
    return p


def _write_old_format(p: Path, wc, *, key, op, origin="interactive",
                      br_repro_op="", demag_history=None) -> None:
    """Byte-for-byte what ``warm_library.store`` wrote before the catalog copy:
    no ``msrc`` in the metadata, no ``Ib_*`` members."""
    has_br = wc.get("br_val") is not None
    meta = {
        "schema": W.SCHEMA, "key": key, "op": op, "origin": origin,
        "created": time.time(), "meta": dict(wc["meta"]), "I": float(wc["I"]),
        "rpm": float(wc["rpm"]), "gam": float(wc["gam"]),
        "geo_fp": str(wc.get("geo_fp") or ""),
        "nspp_req": int(wc.get("nspp_req") or wc["meta"]["nspp"]),
        "nsect": int(wc.get("nsect") or 0), "has_br": bool(has_br),
        "br_repro_op": str(br_repro_op or "") if has_br else "",
        "demag_history": (dict(demag_history) if demag_history else None),
    }
    arrays = {"doflocs": np.asarray(wc["doflocs"]), "A": np.asarray(wc["A"]),
              "Ued": np.asarray(wc["Ued"]), "solid": np.asarray(wc["solid"])}
    if wc.get("A2") is not None:
        arrays["A2"] = np.asarray(wc["A2"])
    if has_br:
        for k in ("br_val", "br_cen", "br_tag"):
            arrays[k] = np.asarray(wc[k])
    for gk, gv in (wc.get("solid_grp") or {}).items():
        arrays["solid_grp_" + gk] = np.asarray(gv, float)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        np.savez(fh, lib_meta=json.dumps(meta, sort_keys=True, allow_nan=False),
                 **arrays)


# ── (a) round trip ──────────────────────────────────────────────────────────

def test_store_at_round_trip_with_the_new_parts(tmp_path):
    rng = np.random.default_rng(1)
    wc = _state(rng)
    dh = {"prepass_periods": 7, "last_torque": {"T_mean": 1.0, "ripple_pct": 2.0}}
    p = tmp_path / "fields" / "warm.npz"
    got = W.store_at(p, wc, key=KEY, op="op1", origin="interactive",
                     br_repro_op="op1", demag_history=dh,
                     extra_meta={"eddy_method": "tdm", "demag_seeded": False})
    assert got == p and p.is_file()
    m = W._read_meta(p)
    assert m["key"] == KEY and m["op"] == "op1" and m["has_br"] is True
    assert m["br_repro_op"] == "op1" and m["origin"] == "interactive"
    assert m["msrc"] == {"300": 1.2e6, "301": -1.2e6}

    out = W.load(m)
    for k in ("doflocs", "A", "A2", "Ued", "solid", "br_val", "br_cen"):
        assert np.array_equal(out[k], wc[k]), k
    assert np.array_equal(out["br_tag"], wc["br_tag"])
    assert out["br_tag"].dtype.kind == "i" and out["br_val"].dtype == float
    assert np.array_equal(out["solid_grp"]["mag"], wc["solid_grp"]["mag"])
    assert out["meta"] == wc["meta"] and out["nsect"] == 2 and out["nspp_req"] == 12
    assert (out["I"], out["rpm"], out["gam"]) == (60.0, 15000.0, 10.0)
    assert out["geo_fp"] == "g1" and out["demag_history"] == dh
    # the net-current history: two levels, each (net, imposed)
    for lvl in ("Ib_prev", "Ib_prev2"):
        assert isinstance(out[lvl], tuple) and len(out[lvl]) == 2, lvl
        assert np.array_equal(out[lvl][0], wc[lvl][0]), lvl
        assert np.array_equal(out[lvl][1], wc[lvl][1]), lvl
    # the magnet source per tag: str keys, float values
    assert out["msrc"] == {"300": 1.2e6, "301": -1.2e6}
    assert all(isinstance(k, str) and isinstance(v, float)
               for k, v in out["msrc"].items())
    assert out["lib"]["eddy_method"] == "tdm" and out["lib"]["key"] == KEY
    assert not any(k.startswith("_") for k in out["lib"])


def test_a_state_without_optional_parts_round_trips(tmp_path):
    rng = np.random.default_rng(2)
    wc = _state(rng, br=False, a2=False, grp=False, ib=False, msrc=False)
    p = tmp_path / "warm.npz"
    assert W.store_at(p, wc, key=KEY, op="o", origin="sweep") == p
    m = W._read_meta(p)
    assert m["has_br"] is False and m["br_repro_op"] == "" and "msrc" not in m
    out = W.load(m)
    for k in ("A2", "br_val", "Ib_prev", "Ib_prev2", "msrc"):
        assert k not in out, k
    assert out["solid_grp"] == {} and out["demag_history"] is None


def test_the_current_history_needs_both_levels_and_1d_arrays(tmp_path):
    rng = np.random.default_rng(3)
    bad = (
        {"Ib_prev": (rng.random(6), rng.random(6))},                  # no level 2
        {"Ib_prev2": (rng.random(6), rng.random(6))},                 # no level 1
        {"Ib_prev": (rng.random(6), rng.random(6)),
         "Ib_prev2": (rng.random((2, 3)), rng.random(6))},            # not 1-D
        {"Ib_prev": (rng.random(6),), "Ib_prev2": (rng.random(6),)},  # not a pair
        {"Ib_prev": None, "Ib_prev2": None},
        {"Ib_prev": ("a", "b"), "Ib_prev2": ("c", "d")},              # not numbers
    )
    for i, extra in enumerate(bad):
        wc = _state(rng, ib=False)
        wc.update(extra)
        p = tmp_path / ("w%d.npz" % i)
        assert W.store_at(p, wc, key=KEY, op="o", origin="interactive") == p, i
        out = W.load(W._read_meta(p))
        assert "Ib_prev" not in out and "Ib_prev2" not in out, i
        with np.load(p, allow_pickle=False) as z:
            assert not any(n.startswith("Ib_") for n in z.files), i


def test_msrc_is_kept_as_str_to_float_and_a_malformed_one_is_dropped(tmp_path):
    rng = np.random.default_rng(4)
    wc = _state(rng, msrc=False)
    wc["msrc"] = {300: np.float64(2.5), 301: 3}                  # int keys, numpy values
    p = tmp_path / "a.npz"
    assert W.store_at(p, wc, key=KEY, op="o", origin="interactive") == p
    assert W.load(W._read_meta(p))["msrc"] == {"300": 2.5, "301": 3.0}
    for i, odd in enumerate(({}, None, "text", {"300": "not a number"}, [1, 2])):
        wc["msrc"] = odd
        q = tmp_path / ("b%d.npz" % i)
        assert W.store_at(q, wc, key=KEY, op="o", origin="interactive") == q, i
        m = W._read_meta(q)
        assert "msrc" not in m and "msrc" not in W.load(m), i


# ── (b) the file format did not move ────────────────────────────────────────

def test_an_old_format_file_still_loads(tmp_path):
    rng = np.random.default_rng(5)
    wc = _state(rng, ib=False, msrc=False)
    p = tmp_path / "old.npz"
    dh = {"prepass_periods": 3, "last_torque": {"T_mean": 1.0, "ripple_pct": 2.0}}
    _write_old_format(p, wc, key=KEY, op="op1", br_repro_op="op1", demag_history=dh)
    m = W._read_meta(p)
    assert m is not None and m["key"] == KEY
    out = W.load(m)
    assert np.array_equal(out["A"], wc["A"]) and np.array_equal(out["A2"], wc["A2"])
    assert np.array_equal(out["br_val"], wc["br_val"])
    assert np.array_equal(out["solid_grp"]["mag"], wc["solid_grp"]["mag"])
    assert out["demag_history"] == dh and out["meta"] == wc["meta"]
    assert "Ib_prev" not in out and "Ib_prev2" not in out and "msrc" not in out


def test_a_state_without_the_new_parts_is_written_with_the_old_members(tmp_path):
    rng = np.random.default_rng(6)
    wc = _state(rng, ib=False, msrc=False)
    p = tmp_path / "new.npz"
    assert W.store_at(p, wc, key=KEY, op="op1", origin="interactive",
                      br_repro_op="op1") == p
    with np.load(p, allow_pickle=False) as z:
        assert set(z.files) == _OLD_MEMBERS
        assert set(json.loads(str(z["lib_meta"]))) == _OLD_META_KEYS


def test_the_library_store_keeps_the_new_parts_too(tmp_path, monkeypatch):
    """``store`` and ``store_at`` share one writer."""
    monkeypatch.setenv("SB_WARM_LIB", "1")
    monkeypatch.setattr(W, "lib_dir", lambda: tmp_path / ".warm_lib")
    rng = np.random.default_rng(7)
    wc = _state(rng)
    assert W.store(wc, key=KEY, op="op1", origin="interactive") is not None
    (m,) = W.candidates(KEY)
    out = W.load(m)
    assert np.array_equal(out["Ib_prev2"][1], wc["Ib_prev2"][1])
    assert out["msrc"] == wc["msrc"]


# ── (c) store vs store_at ───────────────────────────────────────────────────

def test_store_keeps_its_gate_and_trim_and_store_at_has_neither(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "lib_dir", lambda: tmp_path / ".warm_lib")
    trims = []
    monkeypatch.setattr(W, "trim", lambda: trims.append(1) or {"removed": 0})
    rng = np.random.default_rng(8)
    wc = _state(rng)
    # library off (the default): store writes nothing, store_at still writes
    monkeypatch.delenv("SB_WARM_LIB", raising=False)
    assert W.store(wc, key=KEY, op="o", origin="interactive") is None
    assert not (tmp_path / ".warm_lib").exists() and trims == []
    p = tmp_path / "x" / "warm.npz"
    assert W.store_at(p, wc, key=KEY, op="o", origin="interactive") == p
    assert p.is_file() and trims == []
    # library on: store writes into the library dir and trims once
    monkeypatch.setenv("SB_WARM_LIB", "1")
    q = W.store(wc, key=KEY, op="o", origin="interactive")
    assert q is not None and q.parent == tmp_path / ".warm_lib" and q.is_file()
    assert trims == [1]
    assert W.store_at(p, wc, key=KEY, op="o", origin="interactive") == p
    assert trims == [1]


def test_store_still_never_replaces_a_reproducible_map_with_a_sweep_lineage(
        tmp_path, monkeypatch):
    monkeypatch.setenv("SB_WARM_LIB", "1")
    monkeypatch.setattr(W, "lib_dir", lambda: tmp_path / ".warm_lib")
    rng = np.random.default_rng(9)
    good = _state(rng)
    W.store(good, key=KEY, op="op1", origin="interactive", br_repro_op="op1",
            demag_history={"prepass_periods": 7,
                           "last_torque": {"T_mean": 1.0, "ripple_pct": 2.0}})
    W.store(_state(rng), key=KEY, op="op1", origin="sweep", br_repro_op="")
    (m,) = W.candidates(KEY)
    assert m["br_repro_op"] == "op1" and m["origin"] == "interactive"
    assert np.array_equal(W.load(m)["br_val"], good["br_val"])


def test_store_at_never_raises_and_leaves_nothing_behind(tmp_path):
    rng = np.random.default_rng(10)
    none_before = Path("None").exists()
    blocker = tmp_path / "a_file"
    blocker.write_text("not a folder")
    nan_src = _state(rng)
    nan_src["msrc"] = {"300": float("nan")}
    cases = [
        (blocker / "warm.npz", _state(rng)),                     # parent is a file
        (tmp_path / "n1" / "warm.npz", nan_src),                 # NaN cannot be JSON
        (tmp_path / "n2" / "warm.npz", {"A": np.zeros(3)}),      # incomplete state
        (tmp_path / "n3" / "warm.npz", None),
        (tmp_path / "n4" / "warm.npz", "not a state"),
        (None, _state(rng)),                                     # no path
        (tmp_path / "n5" / "warm.npz", dict(_state(rng), I="fast")),   # not a number
    ]
    for p, wc in cases:
        assert W.store_at(p, wc, key=KEY, op="o", origin="interactive") is None, p
    assert W.store_at(tmp_path / "n6" / "warm.npz", _state(rng), key=KEY, op="o",
                      origin="interactive", extra_meta=7) is None
    assert not list(tmp_path.rglob("*.tmp"))
    # a state that is refused creates no folder either
    for n in ("n1", "n2", "n3", "n4", "n5", "n6"):
        assert not (tmp_path / n).exists(), n
    assert Path("None").exists() == none_before       # a missing path is not "None"


# ── (d) the lookup ──────────────────────────────────────────────────────────

def test_catalog_candidates_find_one_key_and_ignore_the_rest(tmp_path):
    root = tmp_path / "dies"
    rng = np.random.default_rng(11)
    p1 = _put(root, _state(rng, 60.0, 0.0), op="o1", die="D1", cfg="c1", stem="peak-1")
    p2 = _put(root, _state(rng, 80.0, 20.0), op="o2", die="D2", cfg="c2", stem="rated-2")
    _put(root, _state(rng), key=OTHER_KEY, op="o3", die="D3", cfg="c3", stem="x-3")
    # not a warm state: the report fields next to it, a corrupt file, a wrong depth
    (_fields_file(root, "D1", "c1", "peak-1").parent / "em.npz").write_bytes(b"x")
    bad = _fields_file(root, "D4", "c4", "bad-4")
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"this is not an npz")
    shallow = root / "D5" / "runs" / "c5" / "fields" / W.CATALOG_FILE
    shallow.parent.mkdir(parents=True)
    W.store_at(shallow, _state(rng), key=KEY, op="o5", origin="interactive")

    cands = W.catalog_candidates(KEY, roots=[root])
    assert sorted(c["op"] for c in cands) == ["o1", "o2"]
    assert all(c["_catalog"] is True and c["key"] == KEY for c in cands)
    assert {Path(c["_path"]) for c in cands} == {p1, p2}
    assert [c["op"] for c in W.catalog_candidates(OTHER_KEY, roots=[root])] == ["o3"]
    assert W.catalog_candidates("m" * 16, roots=[root]) == []
    # the entries are ordinary candidates: pick() takes the nearest operating point
    best = W.pick(cands, I=78.0, gam=18.0, rpm=15000.0, op="x", want_br=False)
    assert best["op"] == "o2"
    # a single path instead of a list, a missing root and a missing path
    assert len(W.catalog_candidates(KEY, roots=root)) == 2
    assert W.catalog_candidates(KEY, roots=[tmp_path / "nowhere"]) == []
    assert W.catalog_candidates(KEY, roots=[]) == []


def test_catalog_candidates_read_every_root_once(tmp_path):
    own, shared = tmp_path / "ws" / "dies", tmp_path / "shared" / "dies"
    rng = np.random.default_rng(12)
    _put(own, _state(rng), op="mine", die="D1")
    _put(shared, _state(rng), op="vendor", die="D2")
    both = W.catalog_candidates(KEY, roots=[own, shared])
    assert sorted(c["op"] for c in both) == ["mine", "vendor"]
    # the same directory listed twice (SHARED_ROOT unset) is read once
    assert len(W.catalog_candidates(KEY, roots=[own, own, str(own)])) == 1


def test_catalog_candidates_default_roots_are_catalog_roots(tmp_path, monkeypatch):
    own, shared = tmp_path / "ws" / "dies", tmp_path / "shared" / "dies"
    rng = np.random.default_rng(13)
    _put(own, _state(rng), op="mine", die="D1")
    _put(shared, _state(rng), op="vendor", die="D2")
    monkeypatch.setattr(W, "catalog_roots", lambda: [own, shared])
    assert sorted(c["op"] for c in W.catalog_candidates(KEY)) == ["mine", "vendor"]
    monkeypatch.setattr(W, "catalog_roots", lambda: [own])
    assert [c["op"] for c in W.catalog_candidates(KEY)] == ["mine"]

    def _boom():
        raise RuntimeError("no workspace")

    monkeypatch.setattr(W, "catalog_roots", _boom)
    assert W.catalog_candidates(KEY) == []              # never raises


def test_the_catalog_switch(tmp_path, monkeypatch):
    root = tmp_path / "dies"
    _put(root, _state(np.random.default_rng(14)))
    monkeypatch.delenv("SB_WARM_CATALOG", raising=False)
    assert W.catalog_enabled() is True                 # default ON
    assert len(W.catalog_candidates(KEY, roots=[root])) == 1
    monkeypatch.setenv("SB_WARM_CATALOG", "1")
    assert W.catalog_enabled() is True
    monkeypatch.setenv("SB_WARM_CATALOG", "0")
    assert W.catalog_enabled() is False
    assert W.catalog_candidates(KEY, roots=[root]) == []
    # independent of the library switch
    monkeypatch.delenv("SB_WARM_LIB", raising=False)
    monkeypatch.delenv("SB_WARM_CATALOG", raising=False)
    assert W.enabled() is False and len(W.catalog_candidates(KEY, roots=[root])) == 1


def test_catalog_candidates_stop_at_the_file_bound(tmp_path):
    root = tmp_path / "dies"
    rng = np.random.default_rng(15)
    for i in range(5):
        _put(root, _state(rng), op="o%d" % i, die="D%d" % i)
    assert len(W.catalog_candidates(KEY, roots=[root])) == 5
    assert len(W.catalog_candidates(KEY, roots=[root], max_files=3)) == 3
    assert W.catalog_candidates(KEY, roots=[root], max_files=0) == []


def _link_dir(link: Path, target: Path) -> bool:
    """A directory symlink, or on Windows without the privilege a junction."""
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import subprocess
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True)
        return done.returncode == 0 and link.exists()
    return False


def test_catalog_candidates_refuse_a_symlink_that_leaves_the_root(tmp_path):
    outside, root = tmp_path / "other_workspace", tmp_path / "dies"
    rng = np.random.default_rng(16)
    _put(outside, _state(rng), op="theirs", die="SECRET")
    _put(root, _state(rng), op="mine", die="D1")
    link = root / "link"
    if not _link_dir(link, outside / "SECRET"):
        pytest.skip("neither a symbolic link nor a junction can be created here")
    try:
        # the glob does reach the foreign file through the link ...
        assert (link / "runs").is_dir()
        # ... and the lookup does not read it
        assert [c["op"] for c in W.catalog_candidates(KEY, roots=[root])] == ["mine"]
    finally:
        try:                                # removes the link, never its target
            os.rmdir(str(link))
        except OSError:
            os.unlink(str(link))
    assert _fields_file(outside, "SECRET").is_file()


def test_catalog_roots_are_the_workspace_dies_and_the_shared_dies(tmp_path, monkeypatch):
    own, shared = tmp_path / "ws" / "dies", tmp_path / "shared" / "dies"
    own.mkdir(parents=True)
    shared.mkdir(parents=True)
    monkeypatch.setattr(DF, "_dies_dir", lambda: own)
    monkeypatch.setattr(WS, "shared_root", lambda: shared.parent)
    assert W.catalog_roots() == [own, shared]
    # SHARED_ROOT unset: both are the same directory -> once
    monkeypatch.setattr(WS, "shared_root", lambda: own.parent)
    assert W.catalog_roots() == [own]
    # a directory that does not exist is not a root
    monkeypatch.setattr(WS, "shared_root", lambda: tmp_path / "no_such_root")
    assert W.catalog_roots() == [own]
    monkeypatch.setattr(DF, "_dies_dir", lambda: tmp_path / "no_such_dies")
    assert W.catalog_roots() == []


def test_catalog_roots_follow_the_shared_root_variable(tmp_path, monkeypatch):
    own, shared = tmp_path / "ws" / "dies", tmp_path / "shared" / "dies"
    own.mkdir(parents=True)
    shared.mkdir(parents=True)
    monkeypatch.setattr(DF, "_dies_dir", lambda: own)
    monkeypatch.setenv("SHARED_ROOT", str(shared.parent))
    assert WS.shared_root() == shared.parent            # the real resolution
    assert W.catalog_roots() == [own, shared]


def test_catalog_roots_survive_a_failing_resolver(tmp_path, monkeypatch):
    shared = tmp_path / "shared" / "dies"
    shared.mkdir(parents=True)

    def _boom():
        raise RuntimeError("no workspace")

    monkeypatch.setattr(DF, "_dies_dir", _boom)
    monkeypatch.setattr(WS, "shared_root", lambda: shared.parent)
    assert W.catalog_roots() == [shared]
    monkeypatch.setattr(WS, "shared_root", _boom)
    assert W.catalog_roots() == []


# ── (e) a run only reads a catalog entry ────────────────────────────────────

def test_loading_a_catalog_entry_does_not_touch_the_file(tmp_path):
    root = tmp_path / "dies"
    p = _put(root, _state(np.random.default_rng(17)))
    old = time.time() - 3600.0
    os.utime(p, (old, old))
    before = p.stat().st_mtime_ns
    (m,) = W.catalog_candidates(KEY, roots=[root])
    assert m["_catalog"] is True
    assert W.load(m) is not None
    assert p.stat().st_mtime_ns == before
    # a library entry (no catalog flag) is still touched: that is the LRU clock
    lib_entry = {k: v for k, v in m.items() if k != "_catalog"}
    assert W.load(lib_entry) is not None
    assert p.stat().st_mtime_ns > before


# ── (f) filing the state under the active duty ──────────────────────────────

DIE, CFG, DUTY = "DIE 1", "cfg1", "peak 200C"


@pytest.fixture()
def dies(tmp_path, monkeypatch):
    d = tmp_path / "ws" / "dies"
    d.mkdir(parents=True)
    monkeypatch.setattr(DF, "_dies_dir", lambda: d)
    monkeypatch.setattr(DF, "active_context", lambda: (DIE, CFG, DUTY))
    return d


def _pub(rng, **over):
    pub = {"wc": _state(rng), "key": KEY, "op": "op1", "origin": "interactive",
           "br_repro_op": "op1",
           "demag_history": {"prepass_periods": 3,
                             "last_torque": {"T_mean": 1.0, "ripple_pct": 2.0}},
           "extra_meta": {"eddy_method": "tdm", "demag_seeded": False}}
    pub.update(over)
    return pub


def test_the_warm_file_name_is_one_name(dies):
    assert DF.WARM_FILE == W.CATALOG_FILE == "warm.npz"
    p = DF.warm_path(DIE, CFG, DUTY)
    assert p == dies / DIE / "runs" / CFG / DF._duty_stem(DUTY) / "fields" / "warm.npz"
    assert p.parent == DF.fields_dir(DIE, CFG, DUTY)
    assert DF.warm_path(DIE, CFG, DUTY, root=dies / "elsewhere") == (
        dies / "elsewhere" / DIE / "runs" / CFG / DF._duty_stem(DUTY) / "fields"
        / "warm.npz")


def test_save_warm_active_files_the_state_under_the_active_duty(dies):
    rng = np.random.default_rng(18)
    pub = _pub(rng)
    out = DF.save_warm_active(pub)
    assert out == str(DF.warm_path(DIE, CFG, DUTY)) and Path(out).is_file()
    (m,) = W.catalog_candidates(KEY, roots=[dies])
    assert Path(m["_path"]) == Path(out)
    assert m["op"] == "op1" and m["br_repro_op"] == "op1" and m["has_br"] is True
    st = W.load(m)
    assert np.array_equal(st["A"], pub["wc"]["A"])
    assert np.array_equal(st["Ib_prev"][0], pub["wc"]["Ib_prev"][0])
    assert st["msrc"] == pub["wc"]["msrc"]
    assert st["demag_history"]["prepass_periods"] == 3
    assert st["lib"]["eddy_method"] == "tdm"
    # a second run REPLACES the file: one per duty, no tmp left over
    pub2 = _pub(rng, op="op2", wc=_state(rng, 90.0, 5.0))
    assert DF.save_warm_active(pub2) == out
    (m2,) = W.catalog_candidates(KEY, roots=[dies])
    assert m2["op"] == "op2" and W.load(m2)["I"] == 90.0
    assert [p.name for p in Path(out).parent.iterdir()] == ["warm.npz"]


def test_save_warm_active_minimal_pub(dies):
    pub = {"wc": _state(np.random.default_rng(19), br=False), "key": KEY,
           "op": "o", "origin": "sweep"}
    out = DF.save_warm_active(pub)
    assert out and W._read_meta(Path(out))["br_repro_op"] == ""


def test_save_warm_active_is_silent_without_work_to_file(dies, monkeypatch):
    rng = np.random.default_rng(20)
    assert DF.save_warm_active(None) is None
    assert DF.save_warm_active({}) is None
    # no active duty
    monkeypatch.setattr(DF, "active_context", lambda: None)
    assert DF.save_warm_active(_pub(rng)) is None
    # a run the caller marked `record: false` is an errand, not the duty's answer
    monkeypatch.setattr(DF, "active_context", lambda: (DIE, CFG, DUTY))
    tok = run_recording.suppress()
    try:
        assert DF.save_warm_active(_pub(rng)) is None
    finally:
        run_recording.restore(tok)
    assert list(dies.rglob("*")) == []                 # nothing was created
    assert DF.save_warm_active(_pub(rng)) is not None  # …and recording is back on


def test_save_warm_active_never_raises_on_a_malformed_pub(dies, monkeypatch):
    rng = np.random.default_rng(21)
    good_wc = _state(rng)
    bad = [5, "text", [], (), 3.5, np.zeros(3), {"wc": None}, {"wc": {}},
           {"wc": {}, "key": KEY}, {"wc": good_wc},
           {"wc": good_wc, "key": KEY, "op": "o"},                    # no origin
           {"wc": {"A": np.zeros(3)}, "key": KEY, "op": "o", "origin": "x"},
           {"wc": good_wc, "key": KEY, "op": "o", "origin": "x", "extra_meta": 7},
           {"wc": good_wc, "key": KEY, "op": "o", "origin": "x", "demag_history": 7}]
    for pub in bad:
        assert DF.save_warm_active(pub) is None, repr(pub)[:60]
    assert list(dies.rglob("*")) == []                 # no folder, no tmp
    # a destination the catalog refuses (an admin's workspace-only die, say)
    def _refuse(*_a, **_k):
        raise ValueError("workspace-only die")
    monkeypatch.setattr(DF, "fields_dir", _refuse)
    assert DF.save_warm_active(_pub(rng)) is None
    assert list(dies.rglob("*")) == []


def test_the_transient_route_hands_the_state_to_the_catalog_writer():
    """Static guard (no solve): the route takes the solver's state out of the
    result payload right after the solve and files it beside the duty's maps."""
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "routes"
           / "simulation.py").read_text(encoding="utf-8")
    i_init = src.index("_warm_pub = None")
    i_call = src.index("_sbres = em_transient_eval(")
    i_pop = src.index('_warm_pub = _sbres.pop("_warm_publish", None)')
    i_next = src.index('_sbres["mesh_settings_source"] = dict(_mesh_src)')
    assert i_init < i_call < i_pop < i_next
    i_em = src.index('_df.save_active(\n                    "em"')
    i_warm = src.index("_df.save_warm_active(_warm_pub)")
    assert i_em < i_warm < src.index("# Bench Ld/Lq ride with every live-machine run")


# ── (g) the duty bookkeeping carries the file ───────────────────────────────

def test_rename_drop_and_listing_handle_the_warm_file(dies):
    rng = np.random.default_rng(22)
    old = DF.warm_path(DIE, CFG, "old duty")
    assert W.store_at(old, _state(rng), key=KEY, op="o", origin="interactive") == old
    # the listing of report fields (em / thermal / ...) does not mention it,
    # and it names no die of its own
    assert DF.have(DIE, CFG) == {}
    assert DF.meta_die(old) is None
    # rename moves it with the duty's other files (retagging skips a file
    # without a report-field meta)
    assert DF.rename(DIE, CFG, "old duty", "new duty") == 1
    new = DF.warm_path(DIE, CFG, "new duty")
    assert new.is_file() and not old.exists() and not old.parent.exists()
    (m,) = W.catalog_candidates(KEY, roots=[dies])
    assert Path(m["_path"]) == new
    assert W.load(m)["I"] == 60.0
    # drop removes it with the duty
    assert DF.drop(DIE, CFG, "new duty") == 1
    assert not new.exists()
    assert W.catalog_candidates(KEY, roots=[dies]) == []


def test_the_route_asks_the_solver_for_the_state_and_the_pool_carries_it():
    """Static guard (no solve): the solver's opt-in is set for the MAIN solve
    only, reset right after it (and on the error path), and listed among the
    context variables a solve-pool worker receives."""
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "routes"
           / "simulation.py").read_text(encoding="utf-8")
    i_set = src.index("_wwp_tok = _WWP.set(")
    i_call = src.index("_sbres = em_transient_eval(")
    i_reset = src.index("_WWP.reset(_wwp_tok)\n            _wwp_tok = None")
    i_pop = src.index('_warm_pub = _sbres.pop("_warm_publish", None)')
    assert i_set < i_call < i_reset < i_pop
    assert src.count("_WWP.set(") == 1
    assert "if _wwp_tok is not None:" in src[i_pop:]
    # a six-phase set open is a failure case of the duty's machine: never asked
    # for, never filed (owner rule of #531, same as the duty's maps)
    assert "and _six_open is None)" in src[i_set:i_call]
    i_save = src.index("_df.save_warm_active(_warm_pub)")
    assert ("if _six_open is not None:\n                    raise _NotFiled()"
            in src[i_save - 300:i_save])
    from motor_ai_sim import solve_pool as SP
    assert (("motor_ai_sim.simulation.fem_solver_2d", "_WANT_WARM_PUBLISH_CTX")
            in SP.CARRIED_VARS)
    from motor_ai_sim.simulation import fem_solver_2d as FS
    assert FS._WANT_WARM_PUBLISH_CTX.get() is False      # off by default


def test_a_sandbox_refile_carries_the_warm_state(tmp_path):
    """A sandboxed run's duty state lands beside the real catalog's maps with
    them (copied, bytes unchanged); a sandbox without one reports nothing
    missing for it."""
    from motor_ai_sim import duty_refile as RF
    rng = np.random.default_rng(23)
    sandbox, real = tmp_path / "sbx", tmp_path / "real" / "dies"
    src = RF.sandbox_fields_dir(sandbox, DIE, CFG, DUTY) / DF.WARM_FILE
    assert W.store_at(src, _state(rng), key=KEY, op="o",
                      origin="interactive") == src
    rep = RF.refile_fields(sandbox, DIE, CFG, DUTY, dies_dir=real,
                           allow_repack=False)
    dst = RF.target_fields_dir(DIE, CFG, DUTY, real) / DF.WARM_FILE
    assert rep["written"]["warm"]["how"] == "copied"
    assert dst.read_bytes() == src.read_bytes()
    assert "warm" not in rep["missing"]
    (m,) = W.catalog_candidates(KEY, roots=[real])
    assert Path(m["_path"]) == dst
    src.unlink()
    dst.unlink()
    rep2 = RF.refile_fields(sandbox, DIE, CFG, DUTY, dies_dir=real,
                            allow_repack=False)
    assert "warm" not in rep2["written"] and "warm" not in rep2["missing"]
    assert not dst.exists()
