"""M0 — freeze and hash every passport input BEFORE any run (spec P28, P29, G30).

One immutable, canonical, resolved job snapshot per machine.  Every number is
normalised, the snapshot is schema-versioned and hashed, and the SAME snapshot
is handed to every job of the passport (base, grid, checks), so no sub-solve
can inherit a panel's context.  It fails closed: a missing input raises, it is
never replaced by a default.  (``routes.simulation._geometry_fingerprint`` is
NOT this hash — it mixes the live config and returns "nofp" on failure.)

Pure functions over parsed YAML/dicts; the environment probe
(:func:`baseline_block`) is the only part that imports the solver stack.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

SNAPSHOT_SCHEMA = "passport-v1-snapshot-1"

#: The five Mesh-tab keys a run consumes (mesh_settings.MESH_FALLBACK) and
#: the duty block's names for them (routes.family._DUTY_MESH_TO_CONFIG).
DUTY_MESH_KEYS = (("mesh.meshSize", "mesh_size_mm", float),
                  ("mesh.minSize", "min_size_mm", float),
                  ("mesh.outerAir", "outer_air_factor", float),
                  ("mesh.gapLayers", "gap_layers", float),
                  ("mesh.nSectors", "n_sectors", int))

#: Duty mesh-block keys that change the BUILD (forwarded to the solver).
DUTY_MESH_BUILD_KEYS = ("mesh.componentMesh", "mesh.ironTemplate", "mesh.geoMesh",
                        "mesh.poleCopy", "mesh.structuredGap", "mesh.motionBand",
                        "mesh.bandThickness")

#: Parts a configuration does not name fall back to these (routes.family
#: _ABSENT_MATERIALS) — the rule a duty load applies today.
ABSENT_MATERIALS = {"slot_insulation": "Nomex", "wire_insulation": "polyimide"}

AIR_PARTS = ("air_gap", "in_band", "out_band")


class SnapshotError(ValueError):
    """A required pilot input is missing or unreadable (fail closed)."""


# ─────────────────────────────────────────────────────────────────────────────
#  Canonical form + hashing
# ─────────────────────────────────────────────────────────────────────────────

def _norm(v: Any) -> Any:
    """Canonical value: floats as 15-significant-digit strings (so 0.1 + 0.2
    style noise and -0.0 never move a hash), dicts sorted, tuples as lists."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            return repr(v)
        if v == 0.0:
            return "0"
        return format(v, ".15g")
    if isinstance(v, Mapping):
        return {str(k): _norm(v[k]) for k in sorted(v, key=str)}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    try:                                    # numpy scalars
        import numpy as _np
        if isinstance(v, _np.generic):
            return _norm(v.item())
    except Exception:                       # noqa: BLE001
        pass
    return str(v)


def canonical_json(obj: Any) -> str:
    return json.dumps(_norm(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("ascii")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: Path, patterns: Iterable[str] = ("*.py",)) -> Dict[str, Any]:
    """sha256 over (relative path, file sha256) of every matching file."""
    root = Path(root)
    files: List[Path] = []
    for pat in patterns:
        files.extend(p for p in root.rglob(pat) if p.is_file()
                     and "__pycache__" not in p.parts)
    files = sorted(set(files))
    h = hashlib.sha256()
    for p in files:
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8") + b"\0" + file_sha256(p).encode("ascii") + b"\n")
    return {"root": str(root), "n_files": len(files), "sha256": h.hexdigest()}


# ─────────────────────────────────────────────────────────────────────────────
#  Resolution of one machine (die + configuration + duty)
# ─────────────────────────────────────────────────────────────────────────────

def _req(d: Mapping[str, Any], key: str, what: str) -> Any:
    if key not in d or d[key] is None or d[key] == "":
        raise SnapshotError(f"{what}: required key {key!r} missing")
    return d[key]


def find_duty(cfg: Mapping[str, Any], name: str) -> Dict[str, Any]:
    for du in cfg.get("duties") or []:
        if isinstance(du, dict) and du.get("name") == name:
            return du
    raise SnapshotError(f"configuration {cfg.get('name')!r} has no duty {name!r}")


def duty_mesh_settings(duty: Mapping[str, Any]) -> Dict[str, Any]:
    """The run's mesh settings, read ONLY from the duty's Mesh-tab block
    (owner 2026-09-30).  All five fidelity keys must be present (fail closed)
    plus the build switches the web forwards (lib/emRunPayload.ts)."""
    m = duty.get("mesh")
    if not isinstance(m, Mapping):
        raise SnapshotError(f"duty {duty.get('name')!r} has no mesh block")
    out: Dict[str, Any] = {}
    for src, dst, typ in DUTY_MESH_KEYS:
        v = m.get(src)
        if v is None or v == "" or isinstance(v, bool):
            raise SnapshotError(f"duty {duty.get('name')!r}: mesh key {src} missing")
        out[dst] = typ(round(float(v))) if typ is int else float(v)
    comp = m.get("mesh.componentMesh") or {}
    if not isinstance(comp, Mapping):
        raise SnapshotError("mesh.componentMesh is not a mapping")
    out["component_mesh_mm"] = {str(k): float(v) for k, v in comp.items()}
    # The web payload: structured_gap = structuredGap || ironTemplate; P2
    # forces the structured belt anyway (routes.simulation, element_order 2).
    iron_t = bool(m.get("mesh.ironTemplate", True))
    out["iron_template"] = iron_t
    out["geo_mesh"] = bool(m.get("mesh.geoMesh", True))
    out["pole_copy"] = bool(m.get("mesh.poleCopy", False))
    out["structured_gap"] = True
    out["structured_gap_requested"] = bool(m.get("mesh.structuredGap", False)) or iron_t
    # Stored but not consumed by the transient (Mesh-view-only keys) — kept so
    # the record states what was ignored, never silently.
    out["not_consumed"] = {k: m.get(k) for k in ("mesh.normalDev", "mesh.aspect",
                                                  "mesh.slidingBand", "mesh.rotorAngle",
                                                  "mesh.motionBand", "mesh.bandThickness")
                           if k in m}
    return out


def resolve_materials(cfg: Mapping[str, Any], *, fallback_parts: Mapping[str, str],
                      fallback_source: str) -> Dict[str, Dict[str, str]]:
    """{part: {"name", "source"}} for every part a solve reads.

    A part the configuration names wins; a liner / enamel it does not name
    takes the project default (routes.family._ABSENT_MATERIALS); any other
    part it does not name is taken from ``fallback_parts`` with its source
    recorded (fail closed if not there either)."""
    named = dict(cfg.get("materials") or {})
    parts = ("magnet", "stator_core", "rotor_core", "shaft", "sleeve", "slot",
             "slot_insulation", "wire_insulation")
    out: Dict[str, Dict[str, str]] = {}
    for part in parts:
        if named.get(part):
            out[part] = {"name": str(named[part]), "source": "configuration"}
        elif part in ABSENT_MATERIALS:
            out[part] = {"name": ABSENT_MATERIALS[part],
                         "source": "absent-part default (routes.family._ABSENT_MATERIALS)"}
        elif fallback_parts.get(part):
            out[part] = {"name": str(fallback_parts[part]), "source": fallback_source}
        else:
            raise SnapshotError(f"material for part {part!r} not resolvable")
    for part in AIR_PARTS:
        out[part] = {"name": "air", "source": "air placeholder (always air)"}
    return out


def library_cards(lib: Mapping[str, Any], names: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """The RAW card dicts (library values actually used), by name."""
    out: Dict[str, Dict[str, Any]] = {}
    for n in sorted(set(names)):
        hit = None
        for section, cards in lib.items():
            if isinstance(cards, Mapping) and n in cards:
                hit = {"section": section, "card": dict(cards[n])}
                break
        if hit is None:
            raise SnapshotError(f"material card {n!r} not in the library")
        out[n] = hit
    return out


def resolve_geometry(die: Mapping[str, Any], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """Die geometry + the configuration's overrides, derived fields recomputed
    by ``merge_geo_override`` (spec §2)."""
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    base = dict(_req(die, "geometry", "die"))
    ov = dict(cfg.get("geometry_overrides") or {})
    return dict(merge_geo_override(base, ov))


def machine_snapshot(*, tag: str, die: Mapping[str, Any], cfg: Mapping[str, Any],
                     rated_duty: str, peak_duty: Optional[str],
                     materials_lib: Mapping[str, Any],
                     fallback_parts: Mapping[str, str], fallback_source: str,
                     source_files: Mapping[str, str],
                     owner_inputs: Mapping[str, Any]) -> Dict[str, Any]:
    """The frozen, resolved inputs of one pilot machine and their signatures."""
    geo = resolve_geometry(die, cfg)
    winding = dict(_req(cfg, "winding", "configuration"))
    winding.setdefault("star_delta", "star")
    rd = find_duty(cfg, rated_duty)
    pk = find_duty(cfg, peak_duty) if peak_duty else None
    mesh = duty_mesh_settings(rd)
    m_rd = rd.get("mesh") or {}

    def _f(v):
        s = str(v if v is not None else "").strip()
        return None if s == "" else float(s)

    def _duty_point(du):
        mm = du.get("mesh") or {}
        return {
            "name": du.get("name"), "mode": du.get("mode", "motor"),
            "current_arms": float(_req(du, "current_arms", "duty")),
            "rpm": float(_req(du, "rpm", "duty")),
            "gamma_deg": float(_req(du, "gamma_deg", "duty")),
            "star_delta": du.get("star_delta") or mm.get("sim.starDelta") or "star",
            "coil_temp_c": _f(mm.get("sim.coilTemp")),
            "magnet_temp_c": _f(mm.get("sim.magnetTempC")),
            "end_winding_factor": _f(mm.get("sim.endWinding")),
            "steps_per_period": int(_req(mm, "sim.stepsPP", "duty mesh")),
            "eddy_coupled": bool(mm.get("sim.eddyCoupled", False)),
            "demag": bool(mm.get("sim.demag", False)),
            "drive": mm.get("sim.drive", "current"),
            "connection": mm.get("sim.connection"),
            "mesh": duty_mesh_settings(du),
            "saved_at": du.get("saved_at"),
            "stored_geo_sig": (du.get("summary") or {}).get("_geoSig"),
            "stored_summary": {k: (du.get("summary") or {}).get(k) for k in (
                "T_em_avg_Nm", "T_ripple_pct", "P_loss_total_W", "P_core_W",
                "P_stranded_W", "P_solid_W", "P_mag_W", "efficiency",
                "efficiency_shaft", "V_line_peak_V", "V1_LL_V", "R_phase_ohm",
                "psi_pm_Wb", "Ld_mH", "Lq_mH", "coil_temp_C", "end_winding_factor",
                "torque_method", "n_steps_per_period")},
        }

    rated = _duty_point(rd)
    if rated["coil_temp_c"] is None:
        raise SnapshotError("rated duty has no coil temperature")
    if rated["end_winding_factor"] is None:
        raise SnapshotError("rated duty has no end-winding factor")
    mats = resolve_materials(cfg, fallback_parts=fallback_parts,
                             fallback_source=fallback_source)
    names = {v["name"] for v in mats.values()}
    cards = library_cards(materials_lib, names)
    battery = dict(_req(cfg, "battery", "configuration"))
    for k in ("v_min", "v_nom", "v_max"):
        _req(battery, k, "battery")
    bearings = dict(cfg.get("bearings") or {})
    temps = {
        "hot_magnet_c": rated["magnet_temp_c"],
        "hot_magnet_source": ("rated duty sim.magnetTempC" if rated["magnet_temp_c"]
                              is not None else "magnet card temperature"),
        "hot_coil_c": rated["coil_temp_c"],
        "hot_coil_source": "rated duty sim.coilTemp",
        "cold_c": 20.0,
        "cold_source": "spec §8.1 (mandatory COLD = 20 °C, magnets and winding)",
    }
    part_states = dict((rd.get("summary") or {}).get("part_states") or {})
    snap = {
        "schema": SNAPSHOT_SCHEMA,
        "tag": tag,
        "die": die.get("name"), "configuration": cfg.get("name"),
        "role": cfg.get("role"),
        "geometry": geo,
        "winding": winding,
        "materials": mats,
        "material_cards": cards,
        "temperatures": temps,
        "part_states": {"stored": part_states,
                        "effective": "all default (included); shaft included "
                                     "(owner 2026-09-29)"},
        "mesh": mesh,
        "rated_duty": rated,
        "peak_duty": (_duty_point(pk) if pk else None),
        "battery": battery,
        "bearings": bearings,
        "owner_inputs": dict(owner_inputs),
        "source_files": dict(source_files),
    }
    snap["signatures"] = signatures(snap)
    return snap


def signatures(snap: Mapping[str, Any]) -> Dict[str, str]:
    """The §2 signatures of a snapshot (sha256 of canonical JSON)."""
    rd = snap["rated_duty"]
    sig = {
        "geometry_sig": sha256_of(snap["geometry"]),
        "winding_sig": sha256_of({"winding": snap["winding"],
                                  "end_winding_factor": rd["end_winding_factor"],
                                  "wire": {k: snap["geometry"].get(k) for k in (
                                      "num_wires_per_slot", "wire_width",
                                      "wire_height", "wire_split",
                                      "wire_parallel")}}),
        "materials_sig": sha256_of({"assignment": {k: v["name"] for k, v in
                                                   snap["materials"].items()},
                                    "cards": snap["material_cards"]}),
        "thermal_sig": sha256_of(snap["temperatures"]),
        "bus_sig": sha256_of(snap["battery"]),
        "mesh_sig": sha256_of(snap["mesh"]),
        "duty_sig": sha256_of({"rated": rd, "peak": snap.get("peak_duty")}),
        "part_states_sig": sha256_of(snap["part_states"]),
    }
    return sig


def snapshot_hash(snap: Mapping[str, Any]) -> str:
    """Hash of the whole resolved snapshot (signatures included)."""
    return sha256_of(snap)


# ─────────────────────────────────────────────────────────────────────────────
#  Computation baseline (B9 reproducibility record)
# ─────────────────────────────────────────────────────────────────────────────

def _pkg_version(name: str) -> Optional[str]:
    try:
        from importlib.metadata import version
        return str(version(name))
    except Exception:                        # noqa: BLE001
        return None


def baseline_block(*, code_commit: str, code_dirty: bool, src_root: Path,
                   image_digest: str, mounts: Mapping[str, str],
                   extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Everything the run depends on that is not a machine input (B9)."""
    env = {k: v for k, v in os.environ.items()
           if k.startswith(("SB_", "MKL_", "OMP_", "OPENBLAS_", "MOTOR_AI_SIM_"))
           or k in ("SHARED_ROOT", "WORKSPACES_ROOT", "PYTHONHASHSEED")}
    try:
        from motor_ai_sim.simulation.geo_mesh import cdt_provenance
        mesher = cdt_provenance()
    except Exception as e:                   # noqa: BLE001
        mesher = {"error": f"{type(e).__name__}: {e}"}
    try:
        import numpy as _np
        blas = {}
        try:
            cfg = _np.show_config(mode="dicts")       # numpy >= 1.25
            blas = {k: (cfg.get("Build Dependencies") or {}).get(k)
                    for k in ("blas", "lapack")}
        except Exception:                    # noqa: BLE001
            blas = {}
    except Exception:                        # noqa: BLE001
        blas = {}
    out = {
        "code_commit": code_commit,
        "code_dirty": bool(code_dirty),
        "source_tree": tree_digest(src_root),
        "image_digest": image_digest,
        "mounts": dict(mounts),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "libs": {n: _pkg_version(n) for n in (
            "numpy", "scipy", "scikit-fem", "pypardiso", "netgen-mesher", "mkl",
            "gmsh", "PyYAML", "shapely")},
        "blas": blas,
        "mesher": mesher,
        "env": env,
        "cpu_count": os.cpu_count(),
        "solver_settings": {
            "element_order": 2,
            "torque_method": "coulomb",
            "eddy_method": "tdm (solver default since 3ba0f9b)",
            "tdm_demag": "full (one-period ratchet pre-pass)",
            "nonlinear": "Newton, resid tol per solver default",
        },
    }
    if extra:
        out.update(dict(extra))
    out["baseline_sig"] = sha256_of({k: v for k, v in out.items()
                                     if k not in ("platform", "cpu_count", "mounts",
                                                  "invocation")})
    return out
