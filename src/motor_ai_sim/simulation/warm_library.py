"""A per-workspace LIBRARY of settled warm states (owner 2026-10-30).

Owner: "we set 40 frames and it is already 280 steps — why is the warm-up not
kept?  It could have been taken from the sweep or from earlier runs."  The
single ``config/.warm_cache.npz`` slot ("last") held ONE state; the interactive
Simulation accepted it only within 5 % of current / 5 deg of gamma, so after any
edit, any sweep or any other operating point the next Run started cold.

This module keeps MANY settled states side by side, one file per (machine
discretisation, operating point), written by every accepted coupled-eddy
current-drive run — interactive Runs and sweep / optimizer points alike — and
bounded as an LRU (``SB_WARM_LIB_MB``, default 300 MB, sweep entries at most half
of it so a long sweep cannot push out the states of the machine being worked
on).

THE KEY.  A state is only ever offered to a run on the SAME machine
discretisation: the merged-geometry fingerprint, an exact digest of the solved
stator and rotor meshes (coordinates and connectivity — so mesh size, gap
layers, fillets and outer air are all in it), the sector count, and the
resolved schedule terms the legacy seed gate already treats as hard
(steps/period, periods, parallel paths, coil temperature, magnet scale, eddy
time scheme).  A geometry that differs from every stored state finds nothing
here: there is no cross-mesh projection in this library.

THE OPERATING POINT is NOT in the key: the eddy history of any current / gamma
at a speed within 5 % may seed the march, and the unchanged settle test at the
handoff decides when the warm-up ends (a far-away seed only costs the frames a
cold start would have cost).  The Br ratchet map is different — the ratchet is
irreversible, so a map burned in at another operating point is another magnet
— and it is offered only to a run at the SAME operating point (``op_digest``),
and only when that map was itself built from a pristine magnet by a full
pre-pass at that operating point (``br_repro_op``), never from a sweep lineage.

Every file is written atomically (tmp + replace), a reader holds it for
milliseconds, and nothing here may ever fail a run: every public function
swallows its own errors and answers "nothing stored".
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import numpy as np

log = logging.getLogger(__name__)

SCHEMA = "warm_lib_v1"
_DEFAULT_MB = 300.0
_DEFAULT_MAX_ENTRIES = 400
# The speed band a seed may come from — the same 5 % the legacy gate uses
# (f_elec sets the whole dA/dt scale).
RPM_BAND = 0.05


def enabled() -> bool:
    """``SB_WARM_LIB=1`` switches the library on (reads and writes).

    OFF by default, like every FEM speed-up switch (scripts/api.env.example):
    the regression tests clear the legacy slot and the ``.warm_cache.npz``
    mirror to get a cold run, and a library they do not know about would seed
    them anyway.  The API server and the eval subprocesses it starts (they
    inherit its environment) set it."""
    return os.environ.get("SB_WARM_LIB", "0") == "1"


def lib_dir() -> Path:
    """``<workspace root>/.warm_lib`` — per WORKSPACE, like the legacy mirror:
    a field state from another user's machine is not a warm start."""
    from motor_ai_sim.workspace import root as _ws_root
    return _ws_root() / ".warm_lib"


def _budget_bytes() -> int:
    try:
        mb = float(os.environ.get("SB_WARM_LIB_MB", "") or _DEFAULT_MB)
    except ValueError:
        mb = _DEFAULT_MB
    return int(max(mb, 0.0) * 1024 * 1024)


def _max_entries() -> int:
    try:
        return max(1, int(os.environ.get("SB_WARM_LIB_MAX", "") or _DEFAULT_MAX_ENTRIES))
    except ValueError:
        return _DEFAULT_MAX_ENTRIES


def _digest(obj: Any, n: int = 16) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str,
                                   separators=(",", ":")).encode("utf-8")
                        ).hexdigest()[:n]


def mesh_digest(meshes: List[Any]) -> str:
    """Exact identity of the solved meshes: every coordinate and every
    connectivity entry.  Two runs share a digest only if they solved the
    same triangles."""
    h = hashlib.sha1()
    for m in meshes:
        p = np.ascontiguousarray(np.asarray(m.p, dtype=np.float64))
        t = np.ascontiguousarray(np.asarray(m.t, dtype=np.int64))
        h.update(str(p.shape).encode()); h.update(p.view(np.uint8))
        h.update(str(t.shape).encode()); h.update(t.view(np.uint8))
    return h.hexdigest()[:20]


def machine_key(geo_fp: str, mesh_fp: str, wmeta: Mapping[str, Any],
                nsect: int, extra: Optional[Mapping[str, Any]] = None) -> str:
    """The library key: the machine DISCRETISATION, not the operating point."""
    return _digest({"schema": SCHEMA, "geo": str(geo_fp), "mesh": str(mesh_fp),
                    "meta": dict(wmeta), "nsect": int(nsect),
                    "extra": dict(extra or {})})


def op_digest(fields: Mapping[str, Any]) -> str:
    """The operating point the Br map belongs to.  Floats are rounded to 9
    significant digits so a value that went through JSON is still itself."""
    def _r(v):
        if isinstance(v, float):
            return float("%.9g" % v)
        return v
    return _digest({k: _r(v) for k, v in fields.items()}, 20)


def _entry_path(key: str, op: str) -> Path:
    return lib_dir() / ("%s_%s.npz" % (key, op))


def _read_meta(p: Path) -> Optional[Dict[str, Any]]:
    try:
        with np.load(p, allow_pickle=False) as z:
            if "lib_meta" not in z.files:
                return None
            m = json.loads(str(z["lib_meta"]))
        if m.get("schema") != SCHEMA:
            return None
        m["_path"] = str(p)
        st = p.stat()
        m["_bytes"] = int(st.st_size)
        m["_mtime"] = float(st.st_mtime)
        return m
    except Exception:       # noqa: BLE001 — an unreadable entry is no entry
        return None


def candidates(key: str) -> List[Dict[str, Any]]:
    """Every stored state of this machine discretisation (metadata only —
    ``np.load`` on an npz is lazy, so this reads a few bytes per file)."""
    if not enabled():
        return []
    try:
        d = lib_dir()
        if not d.is_dir():
            return []
        out = []
        for p in d.glob("%s_*.npz" % key):
            m = _read_meta(p)
            if m is not None and m.get("key") == key:
                out.append(m)
        return out
    except Exception:       # noqa: BLE001
        return []


def pick(cands: List[Dict[str, Any]], *, I: float, gam: float, rpm: float,
         op: str, want_br: bool) -> Optional[Dict[str, Any]]:
    """The best seed among ``cands`` for (I, gamma, rpm).

    1. When the run wants a Br map: an entry at THIS operating point whose
       Br map is reproducible (``br_repro_op == op``) — it carries both the
       eddy history and the magnet.
    2. Otherwise the nearest operating point within the speed band, by
       |dI|/I + |dgamma|/30 deg (+ |drpm|/rpm), newest first on a tie: its eddy
       history only.
    """
    rpm = float(rpm)
    ok = [c for c in cands
          if abs(float(c.get("rpm", 0.0)) - rpm) <= RPM_BAND * max(abs(rpm), 1e-9)]
    if not ok:
        return None
    if want_br:
        same = [c for c in ok if c.get("br_repro_op") == op and c.get("has_br")]
        if same:
            return max(same, key=lambda c: c.get("created", 0.0))

    def _dist(c):
        return (abs(float(c["I"]) - float(I)) / max(abs(float(I)), 1.0)
                + abs(float(c["gam"]) - float(gam)) / 30.0
                + abs(float(c["rpm"]) - rpm) / max(abs(rpm), 1.0))
    return min(ok, key=lambda c: (_dist(c), -float(c.get("created", 0.0))))


def load(meta: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The full state of a picked entry, in the shape ``_warm_cache_load``
    returns (plus ``lib`` = its library metadata).  Touches the file's mtime:
    that is the LRU clock."""
    try:
        p = Path(meta["_path"])
        with np.load(p, allow_pickle=False) as z:
            f = set(z.files)
            m = json.loads(str(z["lib_meta"]))
            wc: Dict[str, Any] = {
                "doflocs": z["doflocs"], "A": z["A"], "Ued": z["Ued"],
                "solid": z["solid"], "meta": dict(m["meta"]),
                "I": float(m["I"]), "rpm": float(m["rpm"]), "gam": float(m["gam"]),
                "geo_fp": str(m.get("geo_fp") or ""),
                "nspp_req": int(m.get("nspp_req") or m["meta"].get("nspp", 0)),
                "nsect": int(m.get("nsect") or 0),
                "solid_grp": {k[len("solid_grp_"):]: np.asarray(z[k], float)
                              for k in f if k.startswith("solid_grp_")},
            }
            if "A2" in f:
                wc["A2"] = z["A2"]
            if {"br_val", "br_cen", "br_tag"} <= f:
                wc["br_val"] = np.asarray(z["br_val"], float)
                wc["br_cen"] = np.asarray(z["br_cen"], float)
                wc["br_tag"] = np.asarray(z["br_tag"], int)
        wc["demag_history"] = m.get("demag_history")
        wc["lib"] = {k: v for k, v in m.items() if not k.startswith("_")}
        try:
            os.utime(p, None)
        except OSError:
            pass
        return wc
    except Exception as e:       # noqa: BLE001
        log.info("warm library: entry unreadable (%s) - not used", e)
        return None


def store(wc: Mapping[str, Any], *, key: str, op: str, origin: str,
          br_repro_op: str = "", demag_history: Optional[Mapping] = None,
          extra_meta: Optional[Mapping[str, Any]] = None) -> Optional[Path]:
    """Write one settled state.  Replaces this (key, op)'s previous state,
    then trims the library to its budget.  Never raises."""
    if not enabled():
        return None
    fd = None
    tmp = None
    try:
        d = lib_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = _entry_path(key, op)
        has_br = wc.get("br_val") is not None
        if has_br and not br_repro_op and p.is_file():
            # A sweep lineage's magnet (continued across geometries/currents)
            # never replaces a map a full pre-pass built at this very point:
            # that one is the only kind an interactive run may continue.
            old = _read_meta(p)
            if old is not None and old.get("has_br") and old.get("br_repro_op"):
                try:
                    os.utime(p, None)
                except OSError:
                    pass
                return p
        meta = {
            "schema": SCHEMA, "key": key, "op": op, "origin": str(origin),
            "created": time.time(),
            "meta": dict(wc["meta"]), "I": float(wc["I"]), "rpm": float(wc["rpm"]),
            "gam": float(wc["gam"]), "geo_fp": str(wc.get("geo_fp") or ""),
            "nspp_req": int(wc.get("nspp_req") or wc["meta"]["nspp"]),
            "nsect": int(wc.get("nsect") or 0),
            "has_br": bool(has_br),
            "br_repro_op": str(br_repro_op or "") if has_br else "",
            "demag_history": (dict(demag_history) if demag_history else None),
        }
        meta.update(dict(extra_meta or {}))
        arrays = {"doflocs": np.asarray(wc["doflocs"]), "A": np.asarray(wc["A"]),
                  "Ued": np.asarray(wc["Ued"]), "solid": np.asarray(wc["solid"])}
        if wc.get("A2") is not None:
            arrays["A2"] = np.asarray(wc["A2"])
        if has_br:
            for k in ("br_val", "br_cen", "br_tag"):
                arrays[k] = np.asarray(wc[k])
        for gk, gv in (wc.get("solid_grp") or {}).items():
            arrays["solid_grp_" + str(gk)] = np.asarray(gv, float)
        fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(d))
        tmp = Path(tmp_name)
        with os.fdopen(fd, "wb") as fh:
            fd = None
            np.savez(fh, lib_meta=json.dumps(meta, sort_keys=True, allow_nan=False),
                     **arrays)
        for i in range(6):       # a reader's open handle blocks the rename on Windows
            try:
                os.replace(tmp, p)
                tmp = None
                break
            except OSError:
                time.sleep(0.02 * (i + 1))
        if tmp is not None:
            log.info("warm library: could not replace %s (held open) - not stored", p.name)
            return None
        trim()
        return p
    except Exception as e:       # noqa: BLE001 — an accelerator, never a failure
        log.info("warm library: state not stored (%s)", e)
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def trim() -> Dict[str, int]:
    """LRU eviction to the budget (oldest mtime first).  Sweep entries are
    held to half of it first, so a long sweep cannot evict the interactive
    states.  Stale tmp files from a crashed writer are removed too."""
    removed = 0
    try:
        d = lib_dir()
        if not d.is_dir():
            return {"removed": 0}
        now = time.time()
        for t in d.glob("*.tmp"):
            try:
                if now - t.stat().st_mtime > 600:
                    t.unlink()
            except OSError:
                pass
        ents = [m for m in (_read_meta(p) for p in d.glob("*.npz")) if m is not None]
        ents.sort(key=lambda m: m["_mtime"])          # oldest first
        budget, cap = _budget_bytes(), _max_entries()

        def _drop(m):
            nonlocal removed
            try:
                Path(m["_path"]).unlink()
                removed += 1
            except OSError:
                pass
            ents.remove(m)

        sweep = [m for m in ents if m.get("origin") == "sweep"]
        while sweep and sum(m["_bytes"] for m in sweep) > budget // 2:
            _drop(sweep.pop(0))
        while ents and (sum(m["_bytes"] for m in ents) > budget or len(ents) > cap):
            _drop(ents[0])
    except Exception as e:       # noqa: BLE001
        log.info("warm library: trim failed (%s)", e)
    return {"removed": removed}


def clear() -> None:
    """Remove every stored state of this workspace (tests)."""
    try:
        d = lib_dir()
        if d.is_dir():
            for p in list(d.glob("*.npz")) + list(d.glob("*.tmp")):
                try:
                    p.unlink()
                except OSError:
                    pass
    except Exception:       # noqa: BLE001
        pass
