"""FastAPI server for motor geometry API.

Usage:
    uvicorn motor_ai_sim.api:app --reload --port 8000
    python -m motor_ai_sim.api
"""

import logging
import re
from pathlib import Path

# ── the solver's own log has to REACH the backend log ─────────────────────
# uvicorn configures its `uvicorn.*` loggers and leaves the root logger alone
# at WARNING, so everything this project logs at INFO — the torque method, the
# eddy-solve loss split, the loss map's volume-integral cross-check, why a
# field-view snapshot probe missed — was written and then dropped on the floor.
# Every one of those lines exists to be READ when a number looks wrong; a
# cross-check nobody can see is not a cross-check.
#
# skfem is pinned back to WARNING: it logs two INFO lines per assembly, which
# is thousands of lines per transient and would bury exactly what this is for.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("skfem").setLevel(logging.WARNING)

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from pydantic import BaseModel

from motor_ai_sim.config import get_config, get_material_assignments, clear_config_cache
from motor_ai_sim.routes.geometry import router as geometry_router
from motor_ai_sim.routes.pipeline import router as pipeline_router
from motor_ai_sim.routes.simulation import router as simulation_router
from motor_ai_sim.routes.optimization import router as optimization_router
from motor_ai_sim.routes.presets import router as presets_router
from motor_ai_sim.routes.catalog import router as catalog_router
from motor_ai_sim.routes.saved_sims import router as saved_sims_router
from motor_ai_sim.routes.sweep_config import router as sweep_config_router
from motor_ai_sim.routes.account import router as account_router
from motor_ai_sim.routes.admin import router as admin_router
from motor_ai_sim.routes.support import router as support_router
from motor_ai_sim.routes.modules import router as modules_router
from motor_ai_sim.routes.kernel import router as kernel_router
from motor_ai_sim.services.geometry_service import get_current_geometry, params_to_dict
from motor_ai_sim import materials as mat_lib
from motor_ai_sim import materials_store
from motor_ai_sim.auth import require_admin

app = FastAPI(
    title="Motor Geometry API",
    description="REST API for electric motor geometry parameters",
    version="0.1.0",
)

import os as _os
_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:3000",
    # Firebase Hosting (production frontend)
    "https://aerostator-core-simulation.web.app",
    "https://aerostator-core-simulation.firebaseapp.com",
] + [o.strip() for o in _os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Tier gate FIRST (inner), CORS LAST (outer) so 401/403 from the gate still
# carry CORS headers — otherwise the browser shows a CORS error, not the 403.
from motor_ai_sim.auth import install_tier_gate
install_tier_gate(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(geometry_router)
app.include_router(pipeline_router)
app.include_router(simulation_router)
app.include_router(optimization_router)
app.include_router(presets_router)
app.include_router(catalog_router)
app.include_router(saved_sims_router)
app.include_router(sweep_config_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(support_router)
app.include_router(modules_router)
app.include_router(kernel_router)


# (There is no FEM worker pool to warm any more.  It existed to hide the
# remesh-per-frame transient's ~11 s-per-frame gmsh build behind N processes,
# each paying a ~5 s cold-import of gmsh + scikit-fem at server start.  The
# sliding band meshes ONCE and solves every frame on that mesh, so the pool —
# and the couple of dozen idle python processes it kept alive for the server's
# lifetime — went with the remesh path.)


@app.get("/")
def root():
    return {
        "name": "Motor Geometry API",
        "version": "0.1.0",
        "endpoints": [
            "/api/geometry",
            "/api/geometry/summary",
            "/api/materials",
            "/api/materials/library",
            "/api/materials/library/{category}",
            "/api/materials/library/{category}/{name}",
            "/api/config",
            "/api/simulation/status",
            "/api/simulation/run",
            "/api/simulation/result/{job_id}",
            "/api/simulation/config",
        ],
    }


@app.get("/api/health")
def health_check():
    return {"status": "healthy"}


_ASSIGNABLE_PARTS = {'stator_core', 'slot', 'rotor_core', 'magnet', 'shaft',
                     'slot_insulation', 'wire_insulation'}
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "motor_config.yaml"


class MaterialAssignment(BaseModel):
    part: str
    material: str


@app.get("/api/materials")
def get_materials():
    try:
        return get_material_assignments()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _set_material_in_yaml(content: str, part: str, material: str):
    """Replace `part: <value>` only inside the `materials:` top-level block.
    Returns (new_content, replaced_bool).
    """
    lines = content.splitlines(keepends=True)
    in_materials = False
    replaced = False
    result = []
    for line in lines:
        if re.match(r'^materials\s*:', line):
            in_materials = True
        elif in_materials and re.match(r'^\S', line):
            in_materials = False  # exited the block

        if in_materials and not replaced:
            m = re.match(rf'^(\s+{re.escape(part)}\s*:\s*)(.*)$', line)
            if m:
                line = m.group(1) + material + '\n'
                replaced = True

        result.append(line)
    return ''.join(result), replaced


@app.patch("/api/materials")
def update_material(assignment: MaterialAssignment):
    """Assign a library material to a motor part, saved to motor_config.yaml."""
    if assignment.part not in _ASSIGNABLE_PARTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown part '{assignment.part}'. Valid: {sorted(_ASSIGNABLE_PARTS)}"
        )
    try:
        content = _CONFIG_PATH.read_text(encoding="utf-8")
        new_content, replaced = _set_material_in_yaml(content, assignment.part, assignment.material)
        if not replaced:
            raise ValueError(f"Key '{assignment.part}' not found under materials: in config")
        _CONFIG_PATH.write_text(new_content, encoding="utf-8")
        clear_config_cache()
        return {"status": "ok", "assignments": get_material_assignments(reload=True)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Materials library (EM simulation database) ────────────────────────────

@app.get("/api/materials/library")
def get_materials_library():
    """Return all available materials from the EM simulation library."""
    try:
        result: dict = {}

        # Steels
        result["steel"] = {}
        for name, m in mat_lib.all_steels().items():
            result["steel"][name] = {
                "description": m.description,
                "form": m.form,
                "sigma": m.sigma,
                "density": m.density,
                "stacking_factor": m.stacking_factor,
                "core_loss_model": m.core_loss_model,
                "core_loss_kh": m.core_loss_kh,
                "core_loss_kc": m.core_loss_kc,
                "core_loss_ke": m.core_loss_ke,
                "core_loss_curve_unit": m.core_loss_curve_unit,
                "bh_curve": m.bh_curve,
                "core_loss_curves": m.core_loss_curves,
            }

        # Magnets
        result["magnet"] = {}
        for name, m in mat_lib.all_magnets().items():
            result["magnet"][name] = {
                "description": m.description,
                "Br": m.Br,
                "Hc": m.Hc,
                "mu_rec": m.mu_rec,
                "sigma": m.sigma,
                "density": m.density,
                "energy_product_kj_m3": m.energy_product_kj_m3,
                "bh_curve": m.bh_curve,
            }

        # Conductors
        result["conductor"] = {}
        for name, m in mat_lib.all_conductors().items():
            result["conductor"][name] = {
                "description": m.description,
                "sigma": m.sigma,
                "resistivity": m.resistivity,
                "density": m.density,
                "thermal_conductivity": m.thermal_conductivity,
                "specific_heat": m.specific_heat,
                "thermal_alpha": m.thermal_alpha,
                "wire_width_mm": m.wire_width_mm,
                "wire_height_mm": m.wire_height_mm,
            }

        # Insulators (slot liner / wire enamel) — thermal + cost, EM-inert
        result["insulator"] = {}
        for name, m in mat_lib.all_insulators().items():
            result["insulator"][name] = {
                "description": m.description,
                "sigma": m.sigma,
                "density": m.density,
                "thermal_conductivity": m.thermal_conductivity,
                "specific_heat": m.specific_heat,
                "mu_r": m.mu_r,
            }

        # Coolants / fluids (liquids + air) — properties feed the cooling model
        result["coolant"] = {}
        for name, m in mat_lib.all_coolants().items():
            result["coolant"][name] = {
                "description": m.description,
                "phase": m.phase,
                "density": m.density,
                "specific_heat": m.specific_heat,
                "thermal_conductivity": m.thermal_conductivity,
                "kinematic_viscosity": m.kinematic_viscosity,
                "prandtl": m.prandtl,
                "sigma": m.sigma,
            }

        # Merge the admin-managed GLOBAL layer (Firestore) over the built-in
        # library; each entry is tagged _source/_editable. Empty merge locally.
        return materials_store.merge_library(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/materials/library/{category}")
def get_materials_library_category(category: str):
    """Return materials for a specific category:
    steel | magnet | conductor | insulator | coolant."""
    try:
        valid = {"steel", "magnet", "conductor", "insulator", "coolant"}
        if category not in valid:
            raise HTTPException(status_code=404, detail=f"Unknown category '{category}'")
        names = mat_lib.list_materials(category)[category]  # type: ignore[arg-type]
        return {"category": category, "materials": names}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/materials/library/{category}/{name}")
def get_material_detail(category: str, name: str):
    """Return full detail for a single material."""
    try:
        m = mat_lib.get_material(category, name)  # type: ignore[arg-type]
        import dataclasses
        return dataclasses.asdict(m)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Global materials library (admin-managed shared layer) ─────────────────────

class GlobalMaterial(BaseModel):
    category: str
    name: str
    props: dict = {}


@app.post("/api/materials/global")
def upsert_global_material(body: GlobalMaterial, admin_user: dict = Depends(require_admin)):
    """Create or update a material in the SHARED (global) library. Admin only.
    Persisted to Firestore `materials_global`, merged over the built-in library."""
    who = admin_user.get("email") or admin_user.get("uid") or "admin"
    try:
        return materials_store.upsert_global(body.category, body.name, body.props, who)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/materials/global/{category}/{name}")
def delete_global_material(category: str, name: str, admin_user: dict = Depends(require_admin)):
    """Delete a material from the shared library (admin only). Deleting a built-in
    hides it (the bundled YAML can't be mutated at runtime)."""
    who = admin_user.get("email") or admin_user.get("uid") or "admin"
    try:
        builtin_names = mat_lib.list_materials(category).get(category, [])
    except Exception:
        builtin_names = []
    try:
        return materials_store.delete_global(category, name, who, builtin_names=builtin_names)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/version")
def get_app_version():
    """App version + build metadata. Single source = repo-root VERSION (baked into
    the image). Lets the frontend display the version and detect frontend/backend
    skew. gitSha/builtAt are stamped at deploy time via env (scripts/release.ps1)."""
    import os
    from pathlib import Path
    version = "0.0.0"
    try:
        version = (Path(__file__).resolve().parent.parent.parent / "VERSION").read_text(
            encoding="utf-8").strip() or version
    except Exception:
        pass
    return {
        "version": version,
        "gitSha": os.environ.get("APP_GIT_SHA", "unknown"),
        "builtAt": os.environ.get("APP_BUILT_AT"),
    }


@app.get("/api/config")
def get_full_config(geo: Optional[str] = None):
    try:
        config = get_config()
        mesh_cfg = config.get("mesh", {})
        sim_cfg = config.get("simulation", {})
        # Per-request geometry override (multi-user): merge the caller's ACTIVE
        # geometry onto the global one so a signed-in user doesn't read the shared
        # sandbox.  Absent/malformed → global config (back-compat).
        geo_dict = params_to_dict(get_current_geometry())
        _ov = None
        if geo:
            try:
                import json as _json
                _o = _json.loads(geo)
                if isinstance(_o, dict) and _o:
                    _ov = _o
                    geo_dict = {**geo_dict, **_o}
            except Exception:
                pass
        # Geometry-derived end-winding factor k_end = (π·tooth_w/2 + L)/L,
        # recomputed from the (possibly overridden) geometry so the UI cell stays in sync.
        try:
            from motor_ai_sim.simulation.geometry_2d import params_from_config as _pfc
            from motor_ai_sim.simulation.fem_solver_2d import end_winding_factor_geom as _ewf
            _kgeo = {**config.get("geometry", {}), **(_ov or {})}
            _kend = round(float(_ewf(_pfc(geo_override=_ov), _kgeo)), 3)
        except Exception:
            _kend = 0.0
        return {
            "geometry": geo_dict,
            "materials": get_material_assignments(),
            "mesh": {
                "n_radial": mesh_cfg.get("n_radial", 10),
                "n_angular": mesh_cfg.get("n_angular", 64),
                "n_angular_slots": mesh_cfg.get("n_angular_slots", 8),
            },
            "simulation": {
                "max_current": sim_cfg.get("max_current", 10.0),
                "frequency": sim_cfg.get("frequency", 50.0),
                "rpm": sim_cfg.get("rpm", 2000),
            },
            "end_winding_factor": _kend,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Winding connection ────────────────────────────────────────────────────────

def _conn_label(n_series: int, n_parallel: int) -> str:
    """Canonical connection label: all-series '{C}S', all-parallel '{C}P',
    else '{nS}S-{nP}P'."""
    if n_parallel <= 1:
        return f"{n_series}S"
    if n_series <= 1:
        return f"{n_parallel}P"
    return f"{n_series}S-{n_parallel}P"


def _winding_connections(num_slots: int):
    """Valid series/parallel winding connections for a 3-phase SINGLE-LAYER
    winding: coils per phase C = num_slots / 6, and every factor pair
    (n_series x n_parallel = C) is an option (n_parallel = parallel paths).
    e.g. 12 slots -> C=2 -> 2S, 2P ; 24 -> C=4 -> 4S, 2S-2P, 4P ;
    36 -> 6S, 3S-2P, 2S-3P, 6P ; 48 -> 8S, 4S-2P, 2S-4P, 8P."""
    C = max(1, round((num_slots or 0) / 6))
    out = []
    for n_parallel in range(1, C + 1):
        if C % n_parallel:
            continue
        n_series = C // n_parallel
        out.append({"label": _conn_label(n_series, n_parallel),
                    "n_parallel": n_parallel, "n_series": n_series})
    return out


class WindingConfigPatch(BaseModel):
    connection:       Optional[str] = None  # slot-dependent, e.g. "4S" | "2S-2P" | "4P"
    n_coils_per_phase: Optional[int] = None
    layers:            Optional[int] = None  # 1 = single-layer, 2 = double-layer
    layout:            Optional[str] = None  # explicit per-slot "A|a|c|C|…" string


def _current_winding_layout():
    """Return (num_slots, [(phase, dir), …]) for the live config winding."""
    cfg = get_config()
    geo = cfg.get("geometry", {})
    num_slots = int(geo.get("num_slots", 24))
    num_pp    = int(geo.get("num_poles", 28)) // 2
    w = cfg.get("winding", {})
    try:
        from motor_ai_sim.simulation.geometry_2d import build_winding_layout
        lay = build_winding_layout(
            num_slots, num_pp,
            single_layer=(int(w.get("layers", 1)) == 1),
            layout_str=(w.get("layout") or None))
    except Exception:
        lay = []
    return num_slots, lay


@app.get("/api/winding/config")
def get_winding_config():
    """Return current winding connection + per-slot layout (phase, direction)."""
    cfg = get_config()
    w = cfg.get("winding", {})
    n_parallel = w.get("n_parallel", 1)
    n_series   = w.get("n_series", 4)
    n_coils    = w.get("n_coils_per_phase", 4)
    # Derived: I_coil = I_phase / n_parallel
    sim        = cfg.get("simulation", {})
    I_phase    = sim.get("max_current", 85.0)
    geo        = cfg.get("geometry", {})
    n_wires    = geo.get("num_wires_per_slot", 14)
    I_coil     = I_phase / n_parallel
    amp_turns  = n_wires * I_coil
    num_slots, lay = _current_winding_layout()
    # compact layout string (UPPER=+, lower=−) for the editor field
    layout_str = "|".join(p if d > 0 else p.lower() for p, d in lay)
    return {
        "connection":         _conn_label(n_series, n_parallel),
        "connections":        _winding_connections(num_slots),
        "n_coils_per_phase":  n_coils,
        "n_parallel":         n_parallel,
        "n_series":           n_series,
        "I_phase_Arms":       I_phase,
        "I_coil_Arms":        round(I_coil, 2),
        "amp_turns_per_slot": round(amp_turns, 1),
        "layers":             int(w.get("layers", 1)),
        "num_slots":          num_slots,
        "layout":             layout_str,
        # per-slot [phase, direction] for the visual phase-map
        "layout_slots":       [[p, d] for p, d in lay],
    }


def _parse_connection(conn: str):
    """Parse a connection label → (n_parallel, n_series).
    New: '2S-2P' (series-parallel) ; '4S' (all series) ; '4P' (all parallel).
    Back-compat: '2P2S' (old parallel-series form)."""
    import re as _re
    conn = (conn or "").strip()
    m = _re.match(r'^(\d+)S-(\d+)P$', conn)        # nS S - nP P  (new)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = _re.match(r'^(\d+)P(\d+)S$', conn)          # nP P nS S  (old)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _re.match(r'^(\d+)S$', conn)                # all series
    if m:
        return 1, int(m.group(1))
    m = _re.match(r'^(\d+)P$', conn)                # all parallel
    if m:
        return int(m.group(1)), 1
    raise ValueError(f"Unknown connection '{conn}'")


@app.patch("/api/winding/config")
def update_winding_config(patch: WindingConfigPatch):
    """Update winding connection in motor_config.yaml."""
    updates: dict = {}
    if patch.connection is not None:
        try:
            n_par, n_ser = _parse_connection(patch.connection)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        num_slots, _ = _current_winding_layout()
        opts = _winding_connections(num_slots)
        if (n_par, n_ser) not in {(c["n_parallel"], c["n_series"]) for c in opts}:
            raise HTTPException(
                status_code=400,
                detail=f"Connection '{patch.connection}' invalid for {num_slots} slots. "
                       f"Valid: {[c['label'] for c in opts]}")
        updates["connection"] = f'"{_conn_label(n_ser, n_par)}"'
        updates["n_parallel"]  = str(n_par)
        updates["n_series"]    = str(n_ser)
    if patch.n_coils_per_phase is not None:
        updates["n_coils_per_phase"] = str(patch.n_coils_per_phase)
    if patch.layers is not None:
        if int(patch.layers) not in (1, 2):
            raise HTTPException(status_code=400, detail="layers must be 1 or 2")
        updates["layers"] = str(int(patch.layers))
    if patch.layout is not None:
        from motor_ai_sim.simulation.geometry_2d import parse_winding_layout
        ls = patch.layout.strip()
        if ls == "":
            updates["layout"] = '""'                     # clear → auto-generate
        else:
            parsed = parse_winding_layout(ls)
            num_slots, _ = _current_winding_layout()
            bad = sorted({p for p, _ in parsed if p not in ("A", "B", "C")})
            if bad:
                raise HTTPException(status_code=400,
                    detail=f"invalid phase token(s) {bad} — use A/B/C (UPPER=+, lower=−)")
            if len(parsed) != num_slots:
                raise HTTPException(status_code=400,
                    detail=f"layout has {len(parsed)} slots, expected {num_slots}")
            clean = "|".join(p if d > 0 else p.lower() for p, d in parsed)
            updates["layout"] = f'"{clean}"'

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    content = _CONFIG_PATH.read_text(encoding="utf-8")
    lines   = content.splitlines(keepends=True)
    in_winding = False
    result     = []
    replaced   = set()

    for line in lines:
        if re.match(r'^winding\s*:', line):
            in_winding = True
        elif in_winding and re.match(r'^\S', line):
            in_winding = False

        if in_winding:
            for key, val in updates.items():
                m = re.match(rf'^(\s+{re.escape(key)}\s*:\s*)(.*)$', line)
                if m:
                    line = m.group(1) + val + '\n'
                    replaced.add(key)
                    break

        result.append(line)

    missing = set(updates) - replaced
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Keys not found in winding: block: {missing}"
        )

    _CONFIG_PATH.write_text(''.join(result), encoding="utf-8")
    clear_config_cache()
    # A winding change (connection / layers / layout) alters the field & torque,
    # so flush the simulation caches (mesh / field / transient) like a geometry edit.
    if {"connection", "layers", "layout"} & set(updates):
        try:
            from motor_ai_sim.routes.simulation import clear_simulation_caches
            clear_simulation_caches()
        except Exception:
            pass
    return {"status": "ok", "updated": {k: v.strip('"') for k, v in updates.items()}}


# ── Mesh ──────────────────────────────────────────────────────────────────────

class MeshConfigPatch(BaseModel):
    n_radial:       Optional[int]   = None
    n_angular:      Optional[int]   = None
    n_angular_slots: Optional[int]  = None
    # FEM mesh settings (Mesh tab) — persisted so they survive sessions and are
    # used by every consumer, not just the browser that set them.
    mesh_size_mm:     Optional[float] = None
    min_size_mm:      Optional[float] = None
    outer_air_factor: Optional[float] = None
    gap_layers:       Optional[float] = None
    normal_deviation: Optional[float] = None
    n_sectors:        Optional[int]   = None


@app.get("/api/mesh/config")
def get_mesh_config():
    """Return mesh config — collocation points + the FEM mesh settings (persisted
    in motor_config.yaml so the Mesh-tab sliders survive every session)."""
    cfg = get_config()
    m = cfg.get("mesh", {})
    return {
        "n_radial":        m.get("n_radial", 10),
        "n_angular":       m.get("n_angular", 64),
        "n_angular_slots": m.get("n_angular_slots", 8),
        # FEM mesh settings (Mesh tab) — same defaults as the build2d endpoint
        "mesh_size_mm":     m.get("mesh_size_mm", 4.0),
        "min_size_mm":      m.get("min_size_mm", 0.3),
        "outer_air_factor": m.get("outer_air_factor", 1.3),
        "gap_layers":       m.get("gap_layers", 3.0),
        "normal_deviation": m.get("normal_deviation", 6.0),
        "n_sectors":        m.get("n_sectors", 4),
    }


@app.patch("/api/mesh/config")
def update_mesh_config(patch: MeshConfigPatch):
    """Persist mesh parameters into motor_config.yaml (adds keys if missing), so
    they are permanent and used by every consumer — not just the browser."""
    import yaml as _yaml, os as _os
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        config = _yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        # GUARD: write_text is not atomic (truncate-then-write), so a concurrent
        # reader can catch the file mid-write and parse an EMPTY config.  Writing
        # that back would nuke geometry/simulation/winding (data loss).  Never
        # persist a config that lost its core sections — bail instead.
        if "geometry" not in config:
            raise HTTPException(status_code=503,
                detail="config read incomplete (concurrent write) — mesh not saved, retry")
        config.setdefault("mesh", {}).update(updates)
        # Atomic write: temp file + os.replace, so readers never see a partial.
        _tmp = _CONFIG_PATH.with_suffix(".yaml.tmp")
        _tmp.write_text(
            _yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        _os.replace(_tmp, _CONFIG_PATH)
        clear_config_cache()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to write mesh config: {e}")
    return {"status": "ok", "updated": updates}


def main():
    import uvicorn
    uvicorn.run("motor_ai_sim.api:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
