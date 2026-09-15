"""Per-duty FIELDS — every duty's own maps, not only the last solve's.

WHY THIS EXISTS (2026-09-09).  The user asked for the solved FIELDS to be kept,
all of them: *"давай сделаем сохранение всех полей моделирования, как
электромагнитных, так и тепловых и механических"*.  A configuration has several
duties and the report must show each duty's maps SIDE BY SIDE; today every field
lives in exactly one place per MACHINE —

  * ``routes.simulation._transient_field_snap`` + ``config/.last_transient_field.pkl``
    (measured on the live CIANO10 200 opt: 1.39 MB),
  * ``routes.thermal._LAST`` + ``config/.last_thermal.pkl``          (1.44 MB),
  * ``routes.mechanical._LAST`` + ``config/.last_mechanical.pkl``    (4.00 MB),

— 6.8 MB that the NEXT solve overwrites.  So a report of a four-duty
configuration could draw one duty's temperature map and had to say so in the
caption.  ``motor_ai_sim.duty_results`` already fixed the SCALAR half of exactly
this problem (a compact row per (die, configuration, duty, kind)); this module is
its array half, and mirrors it deliberately: same context resolution
(``.family_context.json`` read directly, never through the catalog router), same
"a persist failure never fails a solve" rule, same atomic tmp + ``os.replace``.

WHERE IT LIVES.  Under the run sidecars' own root — ``<die>/runs/<cfg>/`` — as::

    config/dies/<die>/runs/<configuration>/<duty-stem>/fields/<kind>.npz

The duty stem is ``routes.family._run_stem``'s (the Latin run of the name plus 8
hex of its sha1), so a duty called ``пик`` gets a folder, and the Cyrillic-С trap
of 2026-09-01 ('30C' vs '30С') cannot make two duties share one.  Living inside
``runs/<configuration>/`` is not a detail: ``family._refile_config_runs`` moves
that whole folder when a configuration is renamed or duplicated, so the fields
follow the machine for free.

WHAT IS KEPT — exactly what the report's map builders read, and nothing else.
``report._em_maps`` / ``_thermal_map`` / ``_mech_map`` consume a mesh plus one
value array per picture; anything else in those payloads (per-step series, the
mode shapes, the contact histories, the eddy J field) belongs to the tab that
computed it and stays in its own pickle.

  ``em``           vertices, triangles, tags, |B| per element, cycle-averaged
                   loss density per element, demag coefficient per element (when
                   the run carried one), A_z per node (when present);
  ``thermal``      vertices, triangles, domain_per_tri, temperature_per_node,
                   heat_flux_per_tri, part names (in ``meta``);
  ``rotor_stress`` vertices, triangles, the per-part tag array, von Mises /
                   max-principal / safety factor per element, displacement per
                   node, and the contact segments per pair while they are cheap
                   — the PRIMARY case only, which is the one the map draws.

SIZE DISCIPLINE, and it is measured, not hoped for.  Geometry is stored float32
/ int32 and values float32 — a colour map does not need float64 — and one file
per (duty, kind), so a re-solve REPLACES rather than accumulates.  MEASURED on the live
machine's own three stores — CIANO10 200 opt / L180 gen / 'peak 0.5x9 mm',
2026-09-09, packed straight out of the three pickles above::

    em            314 949 B      (out of a 1 386 037 B pickle)
    thermal       317 360 B      (out of a 1 435 781 B pickle)
    rotor_stress  317 943 B      (out of a 3 999 682 B pickle)
    -----------------------------------------------------------
    one duty      950 252 B  ≈ 0.91 MB   out of 6 821 500 B  ≈ 6.8 MB

so the ~2 MB ceiling holds with room to spare on a 200 mm machine meshed for a
contact solve, and eight duties of it cost about what ONE machine-level snapshot
costs today.  ``python -m pytest tests/test_duty_fields.py -q -s`` prints the
same three numbers for the 30 mm fixture, so the claim is re-measured on every
run rather than quoted from here.

A duty deleted from the catalogue takes its folder with it (``drop``, called from
``routes.family.delete_duty``), which is what stops a machine with a long history
of renamed duties from growing a graveyard.

NOTHING HERE MAY FAIL A SOLVE.  Every public function swallows its own errors and
returns a falsy answer, exactly as ``duty_results`` does: a six-minute coupled run
that produced a real answer must not be reported as failed because a numpy file
was locked by a virus scanner.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: What a duty may carry a field for, in report order.
KINDS: Tuple[str, ...] = ("em", "thermal", "rotor_stress", "modes")

VERSION = 1

#: Contact segments are a convenience, not a map: they are kept only while they
#: are small.  4 numbers per segment, 40 000 segments = 640 kB in float32, which
#: is already more than the stress field itself — beyond this they are dropped
#: and the report draws the map without them.
_CONTACT_MAX_SEGMENTS = 20000


# ---------------------------------------------------------------------------
# Where it lives
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    """The folder the sidecar stores sit in — derived exactly as
    ``duty_results._config_dir`` derives it, so the test sandbox's redirect
    (``MOTOR_AI_SIM_CONFIG``) carries this store with it."""
    try:
        # Stage 1: the caller's WORKSPACE, which with none set is
        # ``Path(DEFAULT_CONFIG_PATH).parent`` — the old expression exactly.
        from motor_ai_sim.workspace import root as _ws_root
        return Path(str(_ws_root()))
    except Exception:                                       # noqa: BLE001
        return Path(__file__).resolve().parents[2] / "config"


def _family() -> Any:
    """``routes.family`` IF it is already imported — never an import of it.

    This module is called from the tail of three solve routes and must not drag
    the catalog router (and its FastAPI surface) in.  When the router IS loaded
    — the running backend, and every test that drives the API — its ``_DIES_DIR``
    and ``_run_stem`` are the authority, so a suite that redirects the catalogue
    to a throwaway tree redirects these files with it.
    """
    return sys.modules.get("motor_ai_sim.routes.family")


def _dies_dir() -> Path:
    fam = _family()
    root = getattr(fam, "_DIES_DIR", None) if fam is not None else None
    return Path(str(root)) if root else (_config_dir() / "dies")


def _duty_stem(duty: str) -> str:
    """The duty's folder name.  ``family._run_stem`` when it is loaded, and a
    byte-identical copy of it otherwise — the same duty name must always map to
    the same folder, or a rename/delete could never find what it wrote."""
    fam = _family()
    fn = getattr(fam, "_run_stem", None) if fam is not None else None
    if callable(fn):
        try:
            return str(fn(duty))
        except Exception:                                   # noqa: BLE001
            pass
    import hashlib
    raw = str(duty)
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")[:40]
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_part}-{h}" if ascii_part else f"duty-{h}"


def fields_dir(die: str, cfg: str, duty: str) -> Path:
    """``<die>/runs/<configuration>/<duty-stem>/fields`` — created on demand."""
    return (_dies_dir() / str(die) / "runs" / str(cfg)
            / _duty_stem(duty) / "fields")


def field_path(die: str, cfg: str, duty: str, kind: str) -> Path:
    return fields_dir(die, cfg, duty) / f"{kind}.npz"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Array coercion
# ---------------------------------------------------------------------------
# float32 / int32 on the way IN, so the size claim above is a property of the
# store and not of the caller's dtype.


def _f32(v: Any) -> Optional[Any]:
    try:
        import numpy as np
        a = np.asarray(v, dtype=np.float32)
        return a if a.size else None
    except Exception:                                       # noqa: BLE001
        return None


def _i32(v: Any) -> Optional[Any]:
    try:
        import numpy as np
        a = np.asarray(v, dtype=np.int32)
        return a if a.size else None
    except Exception:                                       # noqa: BLE001
        return None


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return None if (f != f or f in (float("inf"), float("-inf"))) else f
    except (TypeError, ValueError):
        return None


def _point(src: Any, keys) -> Dict[str, Any]:
    """The operating point as numbers — without it a map cannot be checked
    against the duty it claims to describe."""
    if not isinstance(src, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in keys:
        n = _num(src.get(k))
        if n is not None:
            out[k] = n
    return out


# ---------------------------------------------------------------------------
# The packers — one per kind, each reading the store the tab already keeps
# ---------------------------------------------------------------------------


def pack_em(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One ``_transient_field_snap`` entry → the arrays the |B| / loss / demag
    maps are drawn from.

    ``Bx``/``By`` collapse to |B| here rather than being stored as a pair: the
    report draws the magnitude, and half the bytes of a vector nothing reads is
    half the bytes.  ``P_mm`` is (2, n) in millimetres and ``T`` is (3, m) —
    kept in the solver's own orientation, which ``report._as_xy`` already
    detects, so nothing is transposed twice.
    """
    if not isinstance(entry, dict):
        return None
    try:
        import numpy as np
        fld = entry.get("field") if isinstance(entry.get("field"), dict) else entry
        scal = entry.get("scalars") if isinstance(entry.get("scalars"), dict) else {}
        meta_in = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        p = _f32(fld.get("P_mm") if fld.get("P_mm") is not None
                 else fld.get("vertices"))
        t = _i32(fld.get("T") if fld.get("T") is not None
                 else fld.get("triangles"))
        if p is None or t is None:
            return None
        arrays: Dict[str, Any] = {"vertices": p, "triangles": t}
        tags = _i32(fld.get("tags") if fld.get("tags") is not None
                    else fld.get("domain_per_tri"))
        if tags is not None:
            arrays["tags"] = tags
        bx, by = fld.get("Bx"), fld.get("By")
        if bx is not None and by is not None:
            arrays["b_mag_per_tri"] = _f32(np.hypot(np.asarray(bx, float),
                                                    np.asarray(by, float)))
        elif fld.get("Bmag_per_tri") is not None:
            arrays["b_mag_per_tri"] = _f32(fld.get("Bmag_per_tri"))
        ld = _f32(fld.get("loss_dens"))
        if ld is not None:
            arrays["loss_dens_per_tri"] = ld
        dc = scal.get("demag_coef_per_tri")
        if dc is None:
            dc = fld.get("demag_coef_per_tri")
        dc = _f32(dc)
        if dc is not None:
            arrays["demag_coef_per_tri"] = dc
        az = _f32(fld.get("A") if fld.get("A") is not None
                  else fld.get("A_z_per_node"))
        if az is not None:
            arrays["a_z_per_node"] = az
        arrays = {k: v for k, v in arrays.items() if v is not None}
        meta = {
            "computed_at": meta_in.get("computed_at"),
            "loss_dens_label": fld.get("loss_dens_label"),
            "eddy": bool(meta_in.get("eddy")) if meta_in else None,
            "n_steps_per_period": meta_in.get("n_steps_per_period"),
            "point": _point(scal, ("rpm", "T_avg_Nm", "f_elec_Hz", "V_peak",
                                   "P_elec_in_W", "P_cu_W", "P_fe_W",
                                   "P_mag_eddy_W")),
            "units": {"vertices": "mm", "b_mag_per_tri": "T",
                      "loss_dens_per_tri": "W/m^3",
                      "demag_coef_per_tri": "fraction of Br remaining",
                      "a_z_per_node": "Wb/m"},
        }
        return {"arrays": arrays, "meta": meta}
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_fields: could not pack the EM field (%s)", exc)
        return None


def pack_thermal(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A thermal ``result`` → the temperature map's own arrays.

    ``report._thermal_map`` reads ``vertices`` / ``triangles`` /
    ``temperature_per_node``; the domain tags and the flux come with them because
    the same page names the parts and the heat paths, and re-deriving either from
    a second source is how two numbers on one page start to disagree.
    """
    if not isinstance(result, dict):
        return None
    try:
        inner = (result.get("field")
                 if isinstance(result.get("field"), dict) else result)
        p = _f32(inner.get("vertices"))
        t = _i32(inner.get("triangles"))
        tn = _f32(inner.get("temperature_per_node"))
        if p is None or t is None or tn is None:
            return None
        arrays: Dict[str, Any] = {"vertices": p, "triangles": t,
                                  "temperature_per_node": tn}
        dom = _i32(inner.get("domain_per_tri"))
        if dom is not None:
            arrays["domain_per_tri"] = dom
        hf = _f32(inner.get("heat_flux_per_tri"))
        if hf is not None:
            arrays["heat_flux_per_tri"] = hf
        names = inner.get("part_names")
        meta = {
            "computed_at": result.get("computed_at"),
            "part_names": ({str(k): str(v) for k, v in names.items()}
                           if isinstance(names, dict) else None),
            "n_sectors": inner.get("n_sectors") or inner.get("symmetry_mult"),
            "point": _point(inner, ("rpm", "T_min", "T_max", "ambient_temp",
                                    "h_conv", "t_sink_c",
                                    "P_loss_total_W", "P_cu_W", "P_fe_W")),
            "units": {"vertices": "m", "temperature_per_node": "degC",
                      "heat_flux_per_tri": "W/m^2"},
        }
        return {"arrays": arrays, "meta": meta}
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_fields: could not pack the thermal field (%s)", exc)
        return None


def pack_rotor_stress(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A rotor-stress ``result`` → the stress map's arrays, PRIMARY case only.

    A stress solve may carry several cases (rated, overspeed, cold); the report
    draws the primary one, and keeping the other two would triple the file to
    picture nothing.  Which case it is rides in ``meta`` so the caption can say
    it, exactly as ``_mech_map`` returns the case name beside its PNG.
    """
    if not isinstance(result, dict):
        return None
    try:
        fld = result.get("field") if isinstance(result.get("field"), dict) else {}
        cases = fld.get("cases") if isinstance(fld.get("cases"), dict) else {}
        case = result.get("primary_case") or next(iter(cases), None)
        cf = cases.get(case) if case else None
        p = _f32(fld.get("vertices"))
        t = _i32(fld.get("triangles"))
        if p is None or t is None or not isinstance(cf, dict):
            return None
        vm = _f32(cf.get("vm_per_tri"))
        if vm is None:
            return None
        arrays: Dict[str, Any] = {"vertices": p, "triangles": t,
                                  "vm_per_tri": vm}
        dom = _i32(fld.get("domain_per_tri"))
        if dom is not None:
            arrays["domain_per_tri"] = dom
        for src, dst in (("s_p1_per_tri", "s_p1_per_tri"),
                         ("sf_per_tri", "sf_per_tri"),
                         ("u_per_node", "u_per_node"),
                         ("u_mag_per_node", "u_mag_per_node")):
            a = _f32(cf.get(src))
            if a is not None:
                arrays[dst] = a
        seg_labels: List[str] = []
        segs = fld.get("contact_segments_per_pair")
        if isinstance(segs, dict):
            for label, seg in segs.items():
                a = _f32(seg)
                if a is None or a.size > 4 * _CONTACT_MAX_SEGMENTS:
                    continue
                arrays[f"contact_seg::{label}"] = a
                seg_labels.append(str(label))
        names = fld.get("part_names")
        meta = {
            "computed_at": result.get("computed_at"),
            "case": str(case) if case else None,
            "cases_available": sorted(str(k) for k in cases),
            "part_names": ({str(k): str(v) for k, v in names.items()}
                           if isinstance(names, dict) else None),
            "contact_pairs": seg_labels,
            "n_sectors": fld.get("n_sectors") or fld.get("symmetry_mult"),
            # The point comes off the RESULT's case block, not the field's: the
            # field carries arrays, the result carries the numbers (rpm, the
            # minimum safety factor, the growth) the caption quotes beside them.
            "point": _point((result.get("cases") or {}).get(case) or {},
                            ("rpm", "sf_min", "sf_min_p05",
                             "rotor_od_growth_um", "max_displacement_um",
                             "torque_balance")),
            "units": {"vertices": "mm", "vm_per_tri": "MPa",
                      "s_p1_per_tri": "MPa", "sf_per_tri": "-",
                      "u_per_node": "um", "u_mag_per_node": "um",
                      "contact_seg::<pair>": "mm (x0,y0,x1,y1 per row)"},
        }
        return {"arrays": arrays, "meta": meta}
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_fields: could not pack the rotor stress field (%s)",
                    exc)
        return None


def pack_modes(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A modal ``result`` → the mesh and every mode's shape (2026-09-11).

    User: *"для модального анализа сделай таблицу из мелких картинок с 12
    частотами, 3 строки и 4 столбца"*.  The solver has always computed the
    shapes (``with_shapes=True``); the machine-level pickle dropped them and the
    per-duty row kept only the frequencies, so there was nothing to draw from.

    SIZE: a shape is a normalised (peak component = 1) per-vertex displacement,
    so float16 holds it to 3 decimals — 12 modes × 8 000 nodes × 2 × 2 B =
    384 kB before compression, about what one stress map costs.
    """
    if not isinstance(result, dict):
        return None
    try:
        fld = result.get("field") if isinstance(result.get("field"), dict) else {}
        p = _f32(fld.get("vertices"))
        t = _i32(fld.get("triangles"))
        shapes = fld.get("modes")
        if p is None or t is None or not shapes:
            return None
        import numpy as np
        u = np.asarray(shapes, dtype=np.float32)          # (n_modes, n_nodes, 2)
        if u.ndim != 3 or u.shape[2] != 2:
            return None
        arrays: Dict[str, Any] = {"vertices": p, "triangles": t,
                                  "u_modes": u.astype(np.float16)}
        dom = _i32(fld.get("domain_per_tri"))
        if dom is not None:
            arrays["domain_per_tri"] = dom
        rows = [m for m in (result.get("modes") or []) if isinstance(m, dict)]
        names = fld.get("part_names")
        meta = {
            "computed_at": result.get("computed_at"),
            "body": result.get("body"), "support": result.get("support"),
            "rpm": _num(result.get("rpm")),
            "extent": _num(fld.get("extent")),
            "modes": [{"index": m.get("index"), "f_hz": _num(m.get("f_hz")),
                       "order": m.get("order"),
                       "nearest": (m.get("nearest") if isinstance(
                           m.get("nearest"), dict) else None)}
                      for m in rows[:u.shape[0]]],
            "part_names": ({str(k): str(v) for k, v in names.items()}
                           if isinstance(names, dict) else None),
            "units": {"vertices": "mm", "u_modes": "normalised (peak = 1)"},
        }
        return {"arrays": arrays, "meta": meta}
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_fields: could not pack the mode shapes (%s)", exc)
        return None


_PACKERS = {"em": pack_em, "thermal": pack_thermal,
            "rotor_stress": pack_rotor_stress, "modes": pack_modes}


# ---------------------------------------------------------------------------
# Writing and reading
# ---------------------------------------------------------------------------


def save(die: str, cfg: str, duty: str, kind: str, payload: Dict[str, Any],
         *, geometry_fingerprint: Optional[str] = None,
         computed_at: Optional[str] = None,
         point: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Pack ONE solve's field under (die, configuration, duty, kind).

    ``payload`` is the store entry the tab already keeps — a
    ``_transient_field_snap`` entry for ``em``, the thermal ``result`` for
    ``thermal``, the rotor-stress ``result`` for ``rotor_stress``.  The caller
    hands over what it has; the packing is this module's business, so a solve
    route stays a solve route.

    Returns the path written, or ``None`` — never raises.
    """
    try:
        if not (die and cfg and duty) or kind not in _PACKERS:
            return None
        packed = _PACKERS[kind](payload)
        if not packed or not packed.get("arrays"):
            return None
        import numpy as np
        meta = dict(packed.get("meta") or {})
        meta.update({
            "version": VERSION, "kind": kind,
            "die": str(die), "config": str(cfg), "duty": str(duty),
            "saved_at": _now(),
        })
        if computed_at:
            meta["computed_at"] = computed_at
        if geometry_fingerprint:
            meta["geometry_fingerprint"] = geometry_fingerprint
        if point:
            meta["point"] = {**(meta.get("point") or {}),
                             **{k: _num(v) for k, v in point.items()
                                if _num(v) is not None}}
        arrays = packed["arrays"]
        meta["arrays"] = {k: {"shape": list(np.asarray(v).shape),
                              "dtype": str(np.asarray(v).dtype)}
                          for k, v in arrays.items()}
        p = field_path(die, cfg, duty, kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        # `meta` as a 0-d string array: the file stays loadable with
        # allow_pickle=False, which is what makes a stray npz in this tree
        # data and never code.
        np.savez_compressed(tmp, meta=np.array(json.dumps(meta, default=str)),
                            **arrays)
        # numpy appends .npz to a path that has no suffix; it does not here,
        # but be explicit rather than depend on that.
        written = tmp if tmp.exists() else tmp.with_name(tmp.name + ".npz")
        os.replace(written, p)
        log.info("duty_fields: %s field of '%s/%s/%s' stored (%d B)",
                 kind, die, cfg, duty, p.stat().st_size)
        return str(p)
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_fields: could not store the %s field of %s/%s/%s (%s)",
                    kind, die, cfg, duty, exc)
        return None


def load(die: str, cfg: str, duty: str, kind: str) -> Optional[Dict[str, Any]]:
    """The stored arrays plus ``meta``, or ``None``.

    ``None`` covers every way this can be empty — never solved, deleted by hand,
    half-written by a machine that lost power — because the report's answer to
    all of them is the same sentence ("not solved for this duty"), and a
    traceback is not that sentence.
    """
    try:
        p = field_path(die, cfg, duty, kind)
        if not p.is_file():
            return None
        import numpy as np
        out: Dict[str, Any] = {}
        with np.load(p, allow_pickle=False) as z:
            for name in z.files:
                if name == "meta":
                    try:
                        out["meta"] = json.loads(str(z["meta"]))
                    except Exception:                       # noqa: BLE001
                        out["meta"] = {}
                else:
                    out[name] = z[name]
        if len(out) <= 1:
            return None
        out.setdefault("meta", {})
        return out
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_fields: could not read the %s field of %s/%s/%s (%s)",
                    kind, die, cfg, duty, exc)
        return None


def have(die: str, cfg: str) -> Dict[str, List[Dict[str, Any]]]:
    """WHICH duties of this configuration have WHICH fields — a listing only.

    ``{duty: [{kind, bytes, saved_at, computed_at, path}, …]}``, in ``KINDS``
    order.  Richer than the ``{duty: [kind]}`` the caller strictly needs: the
    route serves sizes and stamps beside the kinds, and deriving those from a
    second walk of the tree would be a second answer able to disagree with this
    one.  ``kinds_present`` is the plain-list view over the same walk.

    Never opens an array: the duty NAME and the timestamps come out of each
    file's ``meta`` member (``np.load`` is lazy — a zip directory read), the
    size out of ``stat``.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        root = _dies_dir() / str(die) / "runs" / str(cfg)
        if not root.is_dir():
            return out
        import numpy as np
        for stem in sorted(root.iterdir()):
            fdir = stem / "fields"
            if not fdir.is_dir():
                continue
            rows: List[Dict[str, Any]] = []
            duty_name = None
            for kind in KINDS:
                p = fdir / f"{kind}.npz"
                if not p.is_file():
                    continue
                meta: Dict[str, Any] = {}
                try:
                    with np.load(p, allow_pickle=False) as z:
                        if "meta" in z.files:
                            meta = json.loads(str(z["meta"]))
                except Exception:                           # noqa: BLE001
                    meta = {}
                duty_name = duty_name or meta.get("duty")
                rows.append({
                    "kind": kind,
                    "bytes": int(p.stat().st_size),
                    "saved_at": meta.get("saved_at"),
                    "computed_at": meta.get("computed_at"),
                    "geometry_fingerprint": meta.get("geometry_fingerprint"),
                    "arrays": sorted((meta.get("arrays") or {}).keys()),
                    "path": str(p),
                })
            if rows:
                out[str(duty_name or stem.name)] = rows
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_fields: could not list %s/%s (%s)", die, cfg, exc)
    return out


def kinds_present(die: str, cfg: str) -> Dict[str, List[str]]:
    """``{duty: [kind, …]}`` — the plain view of :func:`have`."""
    return {duty: [r["kind"] for r in rows] for duty, rows in have(die, cfg).items()}


def drop(die: str, cfg: str, duty: Optional[str] = None) -> int:
    """Delete one duty's fields (or every duty's of a configuration).

    Called when the catalogue deletes the duty they describe: a duty that no
    longer exists must not keep a map in the next report, and a duty re-created
    under the same name is a NEW operating point, not the old one's field.
    Returns how many files went.
    """
    n = 0
    try:
        root = _dies_dir() / str(die) / "runs" / str(cfg)
        if not root.is_dir():
            return 0
        stems = ([root / _duty_stem(duty)] if duty is not None
                 else [d for d in root.iterdir() if d.is_dir()])
        for stem in stems:
            fdir = stem / "fields"
            if not fdir.is_dir():
                continue
            n += sum(1 for _ in fdir.glob("*.npz"))
            shutil.rmtree(fdir, ignore_errors=True)
            # …and the duty folder itself when nothing else lives in it.  The
            # run payloads are files in `runs/<cfg>/`, not in here, so an empty
            # stem folder is ours and nobody else's.
            try:
                if stem.is_dir() and not any(stem.iterdir()):
                    stem.rmdir()
            except OSError:
                pass
        if n:
            log.info("duty_fields: dropped %d field file(s) of %s/%s/%s",
                     n, die, cfg, duty if duty is not None else "*")
    except Exception as exc:                                # noqa: BLE001
        log.warning("duty_fields: could not drop %s/%s/%s (%s)",
                    die, cfg, duty, exc)
    return n


# ---------------------------------------------------------------------------
# Which duty is loaded right now — the seam the solve routes call
# ---------------------------------------------------------------------------


def active_context() -> Optional[Tuple[str, str, str]]:
    """``(die, configuration, duty)`` of the machine the editor has loaded.

    ``duty_results.active_context`` verbatim (same file, same rule): read
    straight off ``config/.family_context.json`` so importing this from a solve
    route costs nothing, and ``None`` when no duty is named — a solve of a
    machine that is not a catalogued duty has nowhere to be filed, and inventing
    a place for it is how one duty's map ends up under another's name.
    """
    try:
        from motor_ai_sim import duty_results as dr
        return dr.active_context()
    except Exception:                                       # noqa: BLE001
        pass
    try:
        d = json.loads((_config_dir() / ".family_context.json")
                       .read_text(encoding="utf-8"))
        die, cfg, duty = d.get("die"), d.get("config"), d.get("duty")
        if die and cfg and duty:
            return str(die), str(cfg), str(duty)
    except Exception:                                       # noqa: BLE001
        pass
    return None


def save_active(kind: str, payload: Dict[str, Any], **kw) -> Optional[str]:
    """Store this solve's field under the ACTIVE duty, or do nothing.

    The one call a solve route makes.  No active duty → nothing is written and
    nothing is said louder than a debug line: solving a machine outside the
    catalogue is a normal thing to do.

    …and neither is a solve made ON BEHALF of some other duty.  A run the caller
    marked ``record: false`` (``run_recording``) is an errand — the duty-cycle
    editor's calibration run, made at 14.7 A while the editor holds a 45.96 A
    peak duty — and filing it here is how that peak duty's field sidecar came
    back as a 14.7 A map on 2026-09-15.  Same silence as "no active duty": the
    run is fine, it just has nowhere to be filed.
    """
    try:
        from motor_ai_sim import run_recording as _rr
        if _rr.suppressed():
            log.debug("duty_fields: recording suppressed — %s field not stored",
                      kind)
            return None
    except Exception:                                       # noqa: BLE001
        pass
    ctx = active_context()
    if ctx is None:
        log.debug("duty_fields: no active duty — %s field not stored", kind)
        return None
    return save(ctx[0], ctx[1], ctx[2], kind, payload, **kw)
