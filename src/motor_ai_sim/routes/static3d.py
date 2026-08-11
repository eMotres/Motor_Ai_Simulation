"""Look at what the 3D static solver built.

``simulation/static3d`` has been proving things with numbers for weeks — k_flux,
k_T, k_L, the spill curve, the demag margins, all in
``config/end_effect_3d.json``.  Nobody has SEEN any of it.  This router serves
the three things needed to look: the sector's solid bodies, the tet mesh's
surface (with cut planes, so the inside is reachable), and a solved field on
those surfaces.

Two disciplines are load-bearing here and are not negotiable:

* **Nothing is served without saying what it is.**  Every payload carries the
  sector angle and periodicity (the drawn machine is HALF a machine), the
  fingerprint of the geometry it was built from, and — for a field — which
  solve produced it, at what mesh size, with what Picard residual, linear or
  nonlinear iron.  A picture of a field with no provenance is decoration.
* **A stale field is flagged, never served quietly.**  Results live on disk
  keyed by the fingerprint of the machine they were solved for.  If the machine
  in front of the user has moved, the cached field comes back with
  ``stale_geometry: true`` and the UI paints it red — the same contract
  ``routes.simulation``'s restore path follows.

Cost honesty: a solve is minutes, not milliseconds, so nothing here solves on a
GET.  ``GET /field`` on a machine with no cached solve returns
``available: false`` and a MEASURED wall-time quote; the user starts the run
themselves with ``POST /solve``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from motor_ai_sim.simulation.static3d import viewer as V

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/static3d", tags=["static3d"])

_ROOT = Path(__file__).resolve().parents[3]
_PRESETS_PATH = _ROOT / "config" / "motor_presets.json"
_PASSPORT_PATH = _ROOT / "config" / "end_effect_3d.json"
_CACHE_DIR = _ROOT / "config" / ".static3d_cache"

DEFAULT_PRESET = "my_40mm_last"

#: The materials the Stage A / Stage B passport was measured with.  The live
#: ``config/motor_config.yaml`` has been reassigned since (F52SH), and a field
#: drawn with a different magnet grade is not the field the passport's numbers
#: describe — so the tab pins these by default and says that it did.  Pinning is
#: per-request (a ContextVar, ``material_context``), never a write.
STAGE_A_MATERIALS = {"stator_core": "B15AHV950M", "rotor_core": "B15AHV950M",
                     "magnet": "F45SH_120C", "shaft": "Aluminium_6061"}

_LOCK = threading.RLock()
_SECTION_CACHE: Dict[tuple, Any] = {}
_ENTRY_CACHE: Dict[str, dict] = {}       # cache-file stem -> loaded arrays
_ENTRY_ORDER: List[str] = []
_MAX_LOADED = 3


# --------------------------------------------------------------------------
# the machine
# --------------------------------------------------------------------------

def _load_presets() -> dict:
    from motor_ai_sim.json_store import read_json
    return read_json(str(_PRESETS_PATH), {}) or {}


def _preset_geometry(name: str) -> dict:
    p = _load_presets().get(name)
    if not isinstance(p, dict) or not isinstance(p.get("geometry"), dict):
        raise HTTPException(
            status_code=404,
            detail=f"preset {name!r} has no geometry block "
                   f"(known: {', '.join(sorted(_load_presets())[:20])})")
    return dict(p["geometry"])


def _machine_fingerprint(geo: dict, materials: Dict[str, str]) -> str:
    """md5[:16] of the machine THIS tab draws — geometry plus the four materials.

    Deliberately not ``routes.simulation._geometry_fingerprint``: that one folds
    in the live config, so a pinned-preset solve would appear to go stale every
    time the user edited an unrelated motor on the Geometry tab.  This hashes
    the dict in hand, exactly as ``fem_solver_2d._daxis_geo_fingerprint`` does
    for the same reason — a cache key must track what was solved, not what
    happened to be on screen.
    """
    payload = {"geo": dict(geo or {}), "mat": dict(sorted((materials or {}).items()))}
    return hashlib.md5(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _resolve_materials(mode: str) -> Tuple[Dict[str, str], str]:
    mode = (mode or "stage_a").lower()
    if mode in ("stage_a", "passport", "pinned"):
        return dict(STAGE_A_MATERIALS), "stage_a"
    if mode == "live":
        try:
            from motor_ai_sim.config import get_material_assignments
            a = dict(get_material_assignments() or {})
        except Exception:
            a = {}
        return ({k: a[k] for k in
                 ("stator_core", "rotor_core", "magnet", "shaft") if k in a},
                "live")
    raise HTTPException(status_code=422,
                        detail="materials must be 'stage_a' or 'live'")


class _Pinned:
    """Pin the material assignment for the duration of a block.

    ``material_context`` is the project's existing per-request override — a
    ContextVar that ``fem_solver_2d.build_materials`` already reads.  Using it
    (rather than mutating the cached config the way the tests do) is what keeps
    a 3D request from changing what a concurrent 2D request solves.
    """

    def __init__(self, assignment: Dict[str, str]):
        self.assignment = dict(assignment or {})
        self._prev = None

    def __enter__(self):
        from motor_ai_sim.material_context import (get_request_materials,
                                                   set_request_materials)
        self._prev = get_request_materials()
        set_request_materials({"assignment": self.assignment, "materials": {}})
        return self

    def __exit__(self, *exc):
        from motor_ai_sim.material_context import set_request_materials
        set_request_materials(self._prev)
        return False


def _get_section(preset: str, mat_mode: str):
    """The ``MotorSection`` for a preset, built once per process.

    Building it costs a CadQuery rebuild (~1 s) and is pure — same preset, same
    materials, same section — so it is cached on those two keys and nothing
    else.
    """
    materials, mode = _resolve_materials(mat_mode)
    geo = _preset_geometry(preset)
    fp = _machine_fingerprint(geo, materials)
    key = (preset, mode, fp)
    with _LOCK:
        hit = _SECTION_CACHE.get(key)
    if hit is not None:
        return hit, fp, materials, mode, geo
    from motor_ai_sim.simulation.static3d.motor_geometry import load_motor_section
    with _Pinned(materials):
        section = load_motor_section(geo_override=geo)
    # ``load_motor_section`` stamps ``materials`` from the LIVE config's
    # assignment table, which is not what the per-request override just solved
    # with — leaving the section reporting F52SH while its Br is F45SH's 1.19 T.
    # The label has to be the material the physics actually used.
    if materials:
        section.materials = dict(materials)
    with _LOCK:
        _SECTION_CACHE[key] = section
        if len(_SECTION_CACHE) > 4:
            _SECTION_CACHE.pop(next(iter(_SECTION_CACHE)))
    return section, fp, materials, mode, geo


def _fidelity(name: str) -> dict:
    f = V.FIDELITY.get((name or "coarse").lower())
    if f is None:
        raise HTTPException(
            status_code=422,
            detail=f"fidelity must be one of {sorted(V.FIDELITY)}")
    return f


# --------------------------------------------------------------------------
# disk cache
# --------------------------------------------------------------------------

def _knob_tag(fidelity: str) -> str:
    """4 hex of the rung's ACTUAL knobs.

    The rung NAME is not the rung: "coarse" was retuned once already (h_gap 0.9
    -> 0.35 when the gap turned out never to have been the problem), and a cache
    keyed on the name alone would have gone on serving the old mesh under the
    new label forever.  The knobs are in the key, so retuning a rung simply
    misses.
    """
    f = V.FIDELITY.get(fidelity) or {}
    k = {kk: f.get(kk) for kk in
         ("box_factor", "h_gap", "h_solid", "n_stack", "n_cap", "end_bias",
          "order", "tol", "max_iter", "damping")}
    return hashlib.md5(json.dumps(k, sort_keys=True).encode()).hexdigest()[:4]


def _stem(fp: str, fidelity: str, kind: str) -> str:
    return f"{fp}__{fidelity}-{_knob_tag(fidelity)}__{kind}"


def _paths(stem: str) -> Tuple[Path, Path]:
    return _CACHE_DIR / f"{stem}.npz", _CACHE_DIR / f"{stem}.json"


class _CachedMesh:
    """Just enough of a ``MeshTet`` for the viewer: nodes and tets.

    Rebuilding a real ``skfem.MeshTet`` from the cache would re-sort the element
    array, and the region tag is stored PER ELEMENT INDEX — a silent
    re-ordering would repaint every region.  The viewer only ever reads ``p``
    and ``t``, so the cache hands back exactly that, in the stored order.
    """

    def __init__(self, p: np.ndarray, t: np.ndarray):
        self.p = p
        self.t = t


class _CachedTM:
    """A ``TaggedTetMesh`` restored from disk, with no gmsh and no skfem."""

    def __init__(self, p, t, cell_region, names, meta):
        self.mesh = _CachedMesh(p, t)
        self.cell_region = cell_region
        self.names = dict(names)
        self.meta = dict(meta)

    def elements(self, name: str) -> np.ndarray:
        rid = self.names.get(name)
        if rid is None:
            return np.empty(0, dtype=np.int64)
        return np.flatnonzero(self.cell_region == rid)

    @property
    def n_elements(self) -> int:
        return int(self.t_shape)

    @property
    def t_shape(self) -> int:
        return int(self.mesh.t.shape[1])

    @property
    def n_vertices(self) -> int:
        return int(self.mesh.p.shape[1])


def _write_entry(stem: str, meta: dict, arrays: Dict[str, np.ndarray]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    npz, js = _paths(stem)
    tmp = str(npz) + ".tmp.npz"
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, npz)
    tmpj = str(js) + ".tmp"
    with open(tmpj, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1, default=_jsonable)
    os.replace(tmpj, js)


def _jsonable(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _read_entry(stem: str) -> Optional[dict]:
    with _LOCK:
        hit = _ENTRY_CACHE.get(stem)
    if hit is not None:
        return hit
    npz, js = _paths(stem)
    if not npz.exists() or not js.exists():
        return None
    try:
        with open(js, encoding="utf-8") as fh:
            meta = json.load(fh)
        z = np.load(npz)
        d = {"meta": meta, "arrays": {k: z[k] for k in z.files}}
    except Exception as e:                                # pragma: no cover
        log.warning("static3d: unreadable cache entry %s: %s", stem, e)
        return None
    with _LOCK:
        _ENTRY_CACHE[stem] = d
        _ENTRY_ORDER.append(stem)
        while len(_ENTRY_ORDER) > _MAX_LOADED:
            _ENTRY_CACHE.pop(_ENTRY_ORDER.pop(0), None)
    return d


def _tm_from_entry(entry: dict) -> _CachedTM:
    a = entry["arrays"]
    m = entry["meta"]
    return _CachedTM(a["p"], a["t"], a["cell_region"],
                     m.get("names", {}), m.get("mesh_meta", {}))


def _list_entries() -> List[dict]:
    if not _CACHE_DIR.exists():
        return []
    out = []
    for js in sorted(_CACHE_DIR.glob("*.json")):
        try:
            with open(js, encoding="utf-8") as fh:
                m = json.load(fh)
        except Exception:
            continue
        npz = js.with_suffix(".npz")
        fid = m.get("fidelity")
        # An entry whose stem no longer matches the rung it claims was built
        # with knobs that have since been retuned.  It is not deleted (it cost
        # real minutes and the user may want to know it exists) but it is never
        # silently served, and the listing says which it is.
        current = (bool(fid) and fid in V.FIDELITY
                   and js.stem == _stem(str(m.get("fingerprint")), fid,
                                        str(m.get("kind"))))
        out.append({
            "stem": js.stem,
            "current_knobs": bool(current),
            "fingerprint": m.get("fingerprint"),
            "fidelity": m.get("fidelity"),
            "kind": m.get("kind"),
            "solved_utc": m.get("solved_utc"),
            "wall_s": m.get("wall_s"),
            "bytes": (npz.stat().st_size if npz.exists() else 0),
            "solve": m.get("solve"),
        })
    return out


# --------------------------------------------------------------------------
# building and solving
# --------------------------------------------------------------------------

def _build_mesh(section, fid: dict):
    from motor_ai_sim.simulation.static3d.motor_mesh import (build_motor_mesh,
                                                             build_section_mesh_2d)
    sect = build_section_mesh_2d(section, box_factor=fid["box_factor"],
                                 h_gap=fid["h_gap"], h_solid=fid["h_solid"])
    # end_bias 1.0 = EQUAL layers through the stack — a plain extrusion of the
    # cross-section.  The solver's default (3.0) tightens toward the end face,
    # which at the viewer's 3-4 layers means 4.22/1.56/0.22 mm: the picture then
    # shows a mesh that is 19x finer at one end than the other and reads as an
    # error in the model.  The VIEW should show the machine; the grading belongs
    # to the solve, where it is measured.
    tm, _ = build_motor_mesh(section, sect=sect, n_stack=fid["n_stack"],
                             n_cap=fid["n_cap"],
                             end_bias=float(fid.get("end_bias", 3.0)))
    return tm, sect


def _mesh_meta(tm, fid: dict, fname: str) -> dict:
    m = dict(tm.meta or {})
    return {
        "fidelity": fname,
        "knobs": {k: fid.get(k) for k in
                  ("box_factor", "h_gap", "h_solid", "n_stack", "n_cap",
                   "end_bias", "order")},
        "mesh_meta": {k: m.get(k) for k in
                      ("n_layers", "z_levels_mm", "stack_half_mm", "r_box_mm",
                       "z_box_mm", "n_tri_2d", "n_nodes_2d", "sector_deg",
                       "antiperiodic")},
        "names": {k: int(v) for k, v in dict(tm.names).items()},
    }


def _ensure_mesh_entry(section, fp: str, fname: str, fid: dict) -> dict:
    """The tet mesh for one fidelity, from disk if it is there, else built."""
    stem = _stem(fp, fname, "mesh")
    hit = _read_entry(stem)
    if hit is not None:
        return hit
    t0 = time.perf_counter()
    tm, _sect = _build_mesh(section, fid)
    wall = time.perf_counter() - t0
    meta = _mesh_meta(tm, fid, fname)
    meta.update(fingerprint=fp, kind="mesh", wall_s=round(wall, 2),
                solved_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    _write_entry(stem, meta, {
        "p": np.asarray(tm.mesh.p, dtype=np.float64),
        "t": np.asarray(tm.mesh.t, dtype=np.int32),
        "cell_region": np.asarray(tm.cell_region, dtype=np.int16),
    })
    return _read_entry(stem)


# ---- the background solve -------------------------------------------------

_solve_state: Dict[str, Any] = {
    "running": False, "phase": "idle", "progress": 0.0, "started": None,
    "elapsed_s": 0.0, "error": None, "cancel": False, "stem": None,
    "fidelity": None, "nonlinear": None, "quote_s": None, "message": None,
}
_solve_lock = threading.RLock()


def _solve_worker(preset: str, mat_mode: str, fname: str, nonlinear: bool,
                  stem: str) -> None:
    t_start = time.perf_counter()
    try:
        section, fp, materials, mode, geo = _get_section(preset, mat_mode)
        fid = _fidelity(fname)

        def phase(p: str, frac: float) -> None:
            with _solve_lock:
                _solve_state.update(phase=p, progress=float(frac),
                                    elapsed_s=round(time.perf_counter() - t_start, 1))

        phase("meshing", 0.05)
        with _Pinned(materials):
            tm, sect = _build_mesh(section, fid)
            if _cancelled():
                return
            phase("solving", 0.15)
            from motor_ai_sim.simulation.static3d import end_effect as EE
            ss = EE.solve_sector(section, sect, order=fid["order"],
                                 n_stack=fid["n_stack"], n_cap=fid["n_cap"],
                                 tol=fid["tol"], max_iter=fid["max_iter"],
                                 damping=fid["damping"],
                                 linear_iron=not nonlinear)
            if _cancelled():
                return
            phase("fields", 0.80)
            fa = V.field_arrays(ss.sol, ss.tm, section)
            phase("gap profile", 0.88)
            try:
                spill = EE.spill_profile(ss, n_in=25, n_out=6)
            except Exception as e:
                log.warning("static3d: spill profile failed: %s", e)
                spill = None
            phase("demag", 0.95)
            try:
                demag = EE.demag_exposure(ss, n_slices=6)
            except Exception as e:
                log.warning("static3d: demag exposure failed: %s", e)
                demag = None

        wall = time.perf_counter() - t_start
        pic = dict(ss.sol.picard or {})
        meta = _mesh_meta(ss.tm, fid, fname)
        meta.update(
            fingerprint=fp, kind=("magnet_nonlinear" if nonlinear
                                  else "magnet_linear"),
            preset=preset, materials=materials, materials_mode=mode,
            wall_s=round(wall, 1),
            solved_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            solve={
                "excitation": "magnet-only (I = 0)",
                "formulation": "total magnetic scalar potential",
                "iron": ("nonlinear B-H (Picard)" if nonlinear
                         else "LINEAR iron — mu_r fixed, no saturation, so "
                              "this spoke rotor's bridges never saturate and "
                              "short-circuit the magnets (gap B1 0.056 T "
                              "against 1.51 T nonlinear)"),
                "laminated_iron": True,
                "element_order": int(fid["order"]),
                "tets": int(ss.tm.n_elements),
                "nodes": int(ss.tm.n_vertices),
                "ndofs": int(ss.sol.ndofs),
                "solver": str(ss.sol.solver),
                "solve_time_s": round(float(ss.sol.solve_time), 2),
                "wall_s": round(wall, 1),
                "picard_iterations": pic.get("iterations"),
                "picard_converged": pic.get("converged"),
                "picard_residual": pic.get("history", [None])[-1] if pic.get("history") else None,
                "picard_tol": pic.get("tol"),
                "picard_max_iter": fid["max_iter"],
                "boundary_flux_Wb": float(ss.sol.boundary_flux()),
                "Br_T": float(section.Br_T),
                "mu_rec": float(section.mu_rec),
            },
            spill=_spill_json(spill),
            demag=demag,
        )
        arrays = {
            "p": np.asarray(ss.tm.mesh.p, dtype=np.float64),
            "t": np.asarray(ss.tm.mesh.t, dtype=np.int32),
            "cell_region": np.asarray(ss.tm.cell_region, dtype=np.int16),
            "Bmag": np.asarray(fa["Bmag"], dtype=np.float32),
            "B": np.asarray(fa["B"], dtype=np.float32),
            "demag_el": np.asarray(fa["demag"], dtype=np.float32),
        }
        _write_entry(stem, meta, arrays)
        with _LOCK:
            _ENTRY_CACHE.pop(stem, None)
        with _solve_lock:
            _solve_state.update(phase="done", progress=1.0, message=None,
                                elapsed_s=round(wall, 1))
    except Exception as e:                                # pragma: no cover
        log.exception("static3d solve failed")
        with _solve_lock:
            _solve_state.update(phase="error", error=str(e))
    finally:
        with _solve_lock:
            _solve_state["running"] = False
            _solve_state["cancel"] = False


def _cancelled() -> bool:
    with _solve_lock:
        if _solve_state.get("cancel"):
            _solve_state.update(phase="cancelled", message="cancelled by user")
            return True
    return False


def _spill_json(spill: Optional[dict]) -> Optional[dict]:
    if not spill:
        return None
    return {
        "z_m": [float(v) for v in spill["z_m"]],
        "z_over_half": [float(v) for v in spill["z_over_half"]],
        "B1_T": [float(v) for v in spill["B1_T"]],
        "profile": [float(v) for v in spill["profile"]],
        "B1_mid_T": float(spill["B1_mid_T"]),
        "k_flux_self": float(spill["k_flux_self"]),
        "stack_half_m": float(spill["stack_half_m"]),
    }


# --------------------------------------------------------------------------
# passport
# --------------------------------------------------------------------------

def _passport() -> dict:
    from motor_ai_sim.json_store import read_json
    return read_json(str(_PASSPORT_PATH), {}) or {}


def _passport_match(geo: dict) -> dict:
    """Is the passport describing the machine this tab is drawing?

    Compared on the passport's OWN record of what it was solved for
    (``pinned_geometry.geometry``), not on a fingerprint — the passport's stored
    fingerprint was taken against a live config that has since moved three
    times, so it can no longer be reproduced, while the geometry dict it also
    stored can be compared exactly and forever.
    """
    p = _passport()
    pinned = ((p.get("pinned_geometry") or {}).get("geometry") or {})
    if not pinned:
        return {"comparable": False, "matches": None,
                "why": "the passport records no pinned geometry"}
    keys = set(pinned) | set(geo or {})
    diff = []
    for k in sorted(keys):
        a, b = pinned.get(k), (geo or {}).get(k)
        try:
            same = (a is None and b is None) or abs(float(a) - float(b)) <= 1e-9
        except (TypeError, ValueError):
            same = a == b
        if not same:
            diff.append({"key": k, "passport": a, "shown": b})
    return {"comparable": True, "matches": not diff, "differences": diff[:20],
            "n_differences": len(diff),
            "source": (p.get("pinned_geometry") or {}).get("source")}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

def _stale_block(fp: str, entry_fp: Optional[str]) -> dict:
    """The staleness verdict, in the app's own vocabulary.

    ``stale_geometry`` is ``None`` — never ``False`` — when the stored result
    carries no fingerprint at all: "cannot prove it is this machine" must not
    read as "it is".
    """
    if not entry_fp:
        return {"stale_geometry": None, "stale_reason": None,
                "fingerprint_live": fp, "fingerprint_solved": None}
    stale = entry_fp != fp
    return {"stale_geometry": bool(stale),
            "stale_reason": ("geometry" if stale else None),
            "fingerprint_live": fp, "fingerprint_solved": entry_fp}


@router.get("/machine")
def machine(preset: str = Query(DEFAULT_PRESET),
            materials: str = Query("stage_a")):
    """The machine the 3D tab is about, and what is already computed for it."""
    try:
        section, fp, mats, mode, geo = _get_section(preset, materials)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    p = _passport()
    entries = [e for e in _list_entries() if e.get("fingerprint") == fp]
    return {
        "preset": preset,
        "fingerprint": fp,
        "materials_requested": mats,
        "materials_mode": mode,
        "materials_note": (
            "pinned to the grades the passport was measured with; the live "
            "config has since been reassigned"
            if mode == "stage_a" else "the live config's assignment"),
        "machine": V.machine_summary(section),
        "fidelities": {k: {kk: v[kk] for kk in
                           ("box_factor", "h_gap", "h_solid", "n_stack",
                            "n_cap", "order", "tol", "max_iter", "note")}
                       for k, v in V.FIDELITY.items()},
        "cost_quote_s": {f"{k[0]}/{'nonlinear' if not k[1] else 'linear'}": v
                         for k, v in V.COST_S.items()},
        "cost_basis": {f"{k[0]}/{'nonlinear' if not k[1] else 'linear'}": v
                       for k, v in V.COST_BASIS.items()},
        "passport": {
            "version": p.get("version"),
            "generated_utc": p.get("generated_utc"),
            "k_flux": p.get("k_flux"),
            "k_flux_self": p.get("k_flux_self"),
            "k_T": ((p.get("stage_b") or {}).get("torque") or {}).get("k_T"),
            "k_L": (((p.get("stage_b") or {}).get("long_stack_honesty_test")
                     or {}).get("the_inductive_end_effect_factor")
                    or {}).get("k_L_stack_12mm"),
            "machine": p.get("machine"),
            "model": {k: (p.get("model") or {}).get(k) for k in
                      ("sector_deg", "n_sectors", "periodicity",
                       "element_order", "formulation", "h_gap_mm",
                       "h_solid_mm", "cross_section_tri", "axial_layers",
                       "box_r_mm", "picard")},
            "match": _passport_match(geo),
        },
        "cached": entries,
    }


@router.get("/geometry")
def geometry(preset: str = Query(DEFAULT_PRESET),
             materials: str = Query("stage_a")):
    """The sector's solid bodies — no mesh, no solve."""
    try:
        section, fp, mats, mode, geo = _get_section(preset, materials)
        out = V.geometry_payload(section)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    out.update(preset=preset, fingerprint=fp, materials_requested=mats,
               materials_mode=mode,
               what=("ONE anti-periodic sector of the cross-section, extruded "
                     "over the stack; the machine is n_sectors x this"))
    return out


@router.get("/mesh")
def mesh(preset: str = Query(DEFAULT_PRESET),
         materials: str = Query("stage_a"),
         fidelity: str = Query("coarse"),
         cut_z_mm: Optional[float] = Query(None),
         cut_theta_deg: Optional[float] = Query(None),
         air: bool = Query(False),
         air_r_factor: float = Query(1.2, ge=1.0, le=8.0),
         max_tris: int = Query(V.MAX_TRIS_DEFAULT, ge=200, le=200000)):
    """The SURFACE of the tet mesh — the volume mesh is never shipped.

    Building the mesh the first time costs a gmsh run (seconds); after that it
    is read from ``config/.static3d_cache``.  ``counts`` in the payload is the
    real volume mesh, whatever the surface shows.
    """
    try:
        section, fp, mats, mode, geo = _get_section(preset, materials)
        fid = _fidelity(fidelity)
        entry = _ensure_mesh_entry(section, fp, fidelity, fid)
        tm = _tm_from_entry(entry)
        out = V.surface_payload(
            tm, section, cut_z_mm=cut_z_mm, cut_theta_deg=cut_theta_deg,
            draw_air=bool(air),
            # How far out the DRAWN air reaches, as a multiple of the stator
            # radius.  1.6 was still a slab that buried the machine (the mesh is
            # 69 % air by element count), so the default is now 1.2 — just
            # enough to see the gap and the end region — and it is a knob
            # because "show me more air" is a legitimate thing to ask for.
            air_r_max_mm=float(air_r_factor) * float(section.r_stator_out_mm),
            max_tris=int(max_tris))
    except HTTPException:
        raise
    except Exception as e:
        log.exception("static3d mesh failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    out.update(
        preset=preset, fingerprint=fp, fidelity=fidelity,
        knobs=entry["meta"].get("knobs"),
        build_wall_s=entry["meta"].get("wall_s"),
        materials_requested=mats, materials_mode=mode,
        sector=V.machine_summary(section),
        what=("the BOUNDARY faces of the tet mesh (material interfaces and the "
              "cut planes) — not the volume elements themselves"))
    return out


@router.get("/field")
def field(preset: str = Query(DEFAULT_PRESET),
          materials: str = Query("stage_a"),
          fidelity: str = Query("coarse"),
          nonlinear: bool = Query(True),
          quantity: str = Query("Bmag"),
          cut_z_mm: Optional[float] = Query(None),
          cut_theta_deg: Optional[float] = Query(None),
          vectors: bool = Query(False),
          max_tris: int = Query(V.MAX_TRIS_DEFAULT, ge=200, le=200000),
          max_vectors: int = Query(2000, ge=0, le=20000)):
    """A solved field on the drawn surfaces — from cache only, never solved here.

    No cached solve is not an error: it is ``available: false`` plus the
    measured cost of getting one, so the browser can offer the run instead of
    starting a ten-minute job nobody asked for.
    """
    if quantity not in ("Bmag", "demag"):
        raise HTTPException(status_code=422,
                            detail="quantity must be 'Bmag' or 'demag'")
    try:
        section, fp, mats, mode, geo = _get_section(preset, materials)
        _fidelity(fidelity)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    kind = "magnet_nonlinear" if nonlinear else "magnet_linear"
    stem = _stem(fp, fidelity, kind)
    entry = _read_entry(stem)
    if entry is None:
        others = [e for e in _list_entries()
                  if e.get("kind", "").startswith("magnet")]
        return {
            "available": False,
            "preset": preset, "fingerprint": fp, "fidelity": fidelity,
            "quantity": quantity, "nonlinear": bool(nonlinear),
            "quote_s": V.COST_S.get((fidelity, not nonlinear)),
            "quote_basis": V.COST_BASIS.get((fidelity, not nonlinear)),
            "quote_note": ("a nonlinear solve is a Picard loop, so the cost is "
                           "the honest one and not the optimistic one; "
                           "quote_basis says whether it was timed or scaled"),
            "reason": "no cached solve for this machine at this fidelity",
            "cached_elsewhere": others,
        }

    a = entry["arrays"]
    meta = entry["meta"]
    tm = _tm_from_entry(entry)
    values = np.asarray(a["demag_el" if quantity == "demag" else "Bmag"],
                        dtype=float)

    reg = np.asarray(tm.cell_region)
    air_id = int(dict(tm.names).get("air", 0))
    if quantity == "demag":
        finite = np.isfinite(values)
        lo, hi = V.percentile_range(values[finite]) if finite.any() else (0.0, 1.0)
        unit, note = "A/m", ("H . M_hat inside the magnets; negative is "
                             "demagnetising. Everything else is blank because "
                             "the quantity is not defined there.")
    else:
        lo, hi = V.percentile_range(values[reg != air_id])
        unit, note = "T", ("|B| per element, piecewise constant — the element's "
                           "own value, not interpolated across faces.")

    try:
        out = V.surface_payload(
            tm, section, cut_z_mm=cut_z_mm, cut_theta_deg=cut_theta_deg,
            values_el=values, max_tris=int(max_tris))
    except Exception as e:
        log.exception("static3d field failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    if quantity == "demag":
        # Blank every region the quantity is not defined in, rather than paint
        # it the bottom colour — the same contract the 2D loss map enforces.
        out["regions"] = [r for r in out["regions"] if r.get("kind") == "magnet"]
        out["faces_shown"] = int(sum(r["tri_count"] for r in out["regions"]))

    vec = None
    if vectors and max_vectors > 0:
        vec = _vectors_from_entry(a, tm, cut_z_mm, cut_theta_deg,
                                  int(max_vectors), air_id)

    out.update(
        available=True,
        preset=preset, fingerprint=fp, fidelity=fidelity, quantity=quantity,
        materials_requested=mats, materials_mode=mode,
        scale={"vmin": lo, "vmax": hi, "unit": unit,
               "clip": "1st / 99th percentile of the drawn elements",
               "note": note},
        solve=meta.get("solve"),
        knobs=meta.get("knobs"),
        solved_utc=meta.get("solved_utc"),
        spill=meta.get("spill"),
        demag_slices=meta.get("demag"),
        vectors=vec,
        sector=V.machine_summary(section),
        **_stale_block(fp, meta.get("fingerprint")),
    )
    return out


def _vectors_from_entry(a, tm, cut_z_mm, cut_theta_deg, max_n, air_id):
    B = np.asarray(a["B"], dtype=float)
    keep = V.cut_mask(tm.mesh, cut_z_mm, cut_theta_deg)
    keep &= np.asarray(tm.cell_region) != air_id
    idx = np.flatnonzero(keep)
    total = int(idx.size)
    if total == 0:
        return {"points": [], "vectors": [], "shown": 0, "total": 0,
                "decimated": False}
    if total > max_n:
        idx = idx[np.unique(np.linspace(0, total - 1, max_n).astype(np.int64))]
    c = V.element_centroids(tm.mesh)[:, idx] / 1e-3
    return {"points": np.round(c.T.reshape(-1), 4).tolist(),
            "vectors": np.round(B[:, idx].T.reshape(-1), 6).tolist(),
            "shown": int(idx.size), "total": total,
            "decimated": bool(total > max_n),
            "units": {"points": "mm", "vectors": "T"}}


@router.get("/passport")
def passport(preset: str = Query(DEFAULT_PRESET),
             materials: str = Query("stage_a")):
    """The measured passport — the numbers the pictures are supposed to match."""
    p = _passport()
    try:
        _section, fp, _m, _mode, geo = _get_section(preset, materials)
    except Exception:
        fp, geo = None, {}
    sb = p.get("stage_b") or {}
    return {
        "version": p.get("version"),
        "generated_utc": p.get("generated_utc"),
        "fingerprint_live": fp,
        "match": _passport_match(geo),
        "k_flux": p.get("k_flux"),
        "k_flux_self": p.get("k_flux_self"),
        "k_T": (sb.get("torque") or {}).get("k_T"),
        "k_L": ((sb.get("long_stack_honesty_test") or {})
                .get("the_inductive_end_effect_factor") or {}
                ).get("k_L_stack_12mm"),
        "spill_profile": p.get("spill_profile"),
        "demag": p.get("demag"),
        "model": p.get("model"),
        "machine": p.get("machine"),
        "solves": p.get("solves"),
    }


@router.get("/solves")
def solves():
    """Everything this tab has on disk, and how big it is."""
    e = _list_entries()
    return {"entries": e,
            "cache_dir": str(_CACHE_DIR),
            "total_bytes": int(sum(x["bytes"] for x in e))}


class SolveRequest(BaseModel):
    preset: str = DEFAULT_PRESET
    materials: str = "stage_a"
    fidelity: str = "coarse"
    nonlinear: bool = True


@router.post("/solve", status_code=202)
def solve(req: SolveRequest):
    """Start ONE magnet-only sector solve in the background.

    409 if one is already running: this is a single-machine desktop backend and
    two concurrent gmsh/pardiso runs would fight over the same process-global
    locks.
    """
    fid = _fidelity(req.fidelity)
    section, fp, mats, mode, geo = _get_section(req.preset, req.materials)
    kind = "magnet_nonlinear" if req.nonlinear else "magnet_linear"
    stem = _stem(fp, req.fidelity, kind)
    quote = V.COST_S.get((req.fidelity, not req.nonlinear))
    basis = V.COST_BASIS.get((req.fidelity, not req.nonlinear))
    with _solve_lock:
        if _solve_state["running"]:
            raise HTTPException(status_code=409,
                                detail="a 3D solve is already running")
        _solve_state.update(running=True, phase="queued", progress=0.0,
                            started=time.time(), elapsed_s=0.0, error=None,
                            cancel=False, stem=stem, fidelity=req.fidelity,
                            nonlinear=bool(req.nonlinear), quote_s=quote,
                            message=None)
    threading.Thread(target=_solve_worker,
                     args=(req.preset, req.materials, req.fidelity,
                           bool(req.nonlinear), stem),
                     daemon=True, name="static3d-solve").start()
    log.info("static3d: started %s solve at %s fidelity (quote %ss)",
             kind, req.fidelity, quote)
    return {"started": True, "stem": stem, "fingerprint": fp,
            "fidelity": req.fidelity, "nonlinear": bool(req.nonlinear),
            "quote_s": quote, "quote_basis": basis,
            "knobs": {k: fid[k] for k in
                      ("box_factor", "h_gap", "h_solid", "n_stack", "n_cap",
                       "order")}}


@router.get("/solve/progress")
def solve_progress():
    with _solve_lock:
        s = dict(_solve_state)
    if s.get("running") and s.get("started"):
        s["elapsed_s"] = round(time.time() - float(s["started"]), 1)
    return s


@router.post("/solve/cancel")
def solve_cancel():
    with _solve_lock:
        _solve_state["cancel"] = True
    return {"cancelled": True}
