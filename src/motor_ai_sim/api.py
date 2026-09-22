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

# ── Persistent rotating log ──────────────────────────────────────────────────
# logs/api.log, rotated at midnight, 3 days kept — enough for a post-mortem
# and never grows unbounded.  Born of a real failure (2026-08-23): a demag
# view produced a physically impossible map, the process that solved it was
# already restarted, and logs/api.log held six lines from a long-dead run —
# the defect could not be reconstructed.  The handler is attached to the ROOT
# logger so every module's INFO/WARNING lands in the file (uvicorn's own
# loggers propagate there too); the console keeps whatever basicConfig set up.
# Attached ONCE per process and only in the SERVER process — refine_proc eval
# subprocesses import modules directly (never this file), so there is no
# multi-process contention on the rotation.
def _attach_file_log() -> None:
    import os as _os
    import sys as _sys
    from logging.handlers import TimedRotatingFileHandler
    # NOT under pytest: the suite imports this module too, and a second
    # process appending to (and rotating!) the same file garbles the very
    # post-mortem record this exists for.
    if "pytest" in _sys.modules or _os.environ.get("MOTOR_AI_NO_FILE_LOG"):
        return
    _dir = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))), "logs")
    try:
        _os.makedirs(_dir, exist_ok=True)
        _fh = TimedRotatingFileHandler(
            _os.path.join(_dir, "api.log"), when="midnight", backupCount=3,
            encoding="utf-8", delay=True)
        _fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        _fh.setLevel(logging.INFO)
        _root = logging.getLogger()
        if not any(isinstance(h, TimedRotatingFileHandler) for h in _root.handlers):
            _root.addHandler(_fh)
        # uvicorn keeps its access/error loggers non-propagating with its own
        # console handlers; the post-mortem needs the REQUEST lines in the file
        # (yesterday's forensic question was literally "what did the demag view
        # get called with") — so let them propagate to the root file handler.
        for _ln in ("uvicorn.access", "uvicorn.error"):
            logging.getLogger(_ln).propagate = True
    except Exception as _e:   # noqa: BLE001 — a log-file problem must not kill the API
        logging.getLogger(__name__).warning("file log unavailable: %s", _e)


_attach_file_log()

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional

from pydantic import BaseModel

from motor_ai_sim.config import (get_config, get_material_assignments,
                                 clear_config_cache,
                                 DEFAULT_CONFIG_PATH as _DEFAULT_CONFIG_PATH)
from motor_ai_sim.routes.geometry import router as geometry_router
from motor_ai_sim.routes.pipeline import router as pipeline_router
from motor_ai_sim.routes.simulation import router as simulation_router
from motor_ai_sim.routes.static3d import router as static3d_router
from motor_ai_sim.routes.mechanical import router as mechanical_router
from motor_ai_sim.routes.panel_settings import router as panel_settings_router
from motor_ai_sim.routes.thermal import router as thermal_router
from motor_ai_sim.routes.coupled import router as coupled_router
from motor_ai_sim.routes.controller import router as controller_router
from motor_ai_sim.routes.bearings import router as bearings_router
from motor_ai_sim.routes.wire_stock import router as wire_stock_router
from motor_ai_sim.routes.optimization import router as optimization_router
from motor_ai_sim.routes.presets import router as presets_router
from motor_ai_sim.routes.catalog import router as catalog_router
from motor_ai_sim.routes.saved_sims import router as saved_sims_router
from motor_ai_sim.routes.freecad import router as freecad_router
from motor_ai_sim.routes.fusion import router as fusion_router
from motor_ai_sim.routes.family import router as family_router
# The PDF report export — same /api/family prefix and the same die gate as the
# datasheet route it sits beside, in its own module because it reads four other
# routers' last-result stores (see routes/report.py).
from motor_ai_sim.routes.report import router as report_router
from motor_ai_sim.routes.my_motors import router as my_motors_router
from motor_ai_sim.routes.auth_local import router as auth_local_router
from motor_ai_sim.routes.sweep_config import router as sweep_config_router
from motor_ai_sim.routes.account import router as account_router
from motor_ai_sim.routes.admin import router as admin_router
from motor_ai_sim.routes.support import router as support_router
from motor_ai_sim.routes.modules import router as modules_router
from motor_ai_sim.routes.kernel import router as kernel_router
from motor_ai_sim.routes.jobs_api import router as jobs_router
from motor_ai_sim.routes.history import router as history_router
from motor_ai_sim.services.geometry_service import get_current_geometry, params_to_dict
from motor_ai_sim import materials as mat_lib
from motor_ai_sim.materials import UnknownMaterialError
from motor_ai_sim import materials_store
from motor_ai_sim.auth import require_admin

# Boot (migration Stage 6).  Two lines, both no-ops on this workstation:
# ``run_startup_checks`` refuses the boot only on a case-ambiguous catalog — a
# condition NTFS cannot even express — and warns about an implicit AUTH_SECRET
# or a missing report dependency; ``watchdog_notify`` does nothing at all
# unless systemd set NOTIFY_SOCKET.  See both modules for why.
from contextlib import asynccontextmanager as _asynccontextmanager
from motor_ai_sim.startup_checks import run_startup_checks as _run_startup_checks
from motor_ai_sim import watchdog_notify as _watchdog


@_asynccontextmanager
async def _lifespan(_app):
    _run_startup_checks()
    # Resume any incomplete sweeps from before the restart
    try:
        from motor_ai_sim import sweep_resume as _sweep_resume
        _sweep_resume.resume_incomplete_sweeps()
    except Exception as _e:
        logging.getLogger(__name__).warning("sweep resumption failed: %s", _e)
    _watchdog.start()
    try:
        yield
    finally:
        _watchdog.stop()


app = FastAPI(
    title="Motor Geometry API",
    description="REST API for electric motor geometry parameters",
    version="0.1.0",
    lifespan=_lifespan,
)

import os as _os
_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:3000",
    # Production origins come from the ALLOWED_ORIGINS env (comma-separated),
    # e.g. "https://emotres.com" — set per deployment, nothing hardcoded.
] + [o.strip() for o in _os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# WHOSE machine is this request about?  (migration Stage 1)
#
# Added FIRST, so it ends up INNERMOST: every handler that actually runs has its
# workspace resolved, and the tier gate's own 401/403 — which never reach a
# handler — cost nothing.  With ``WORKSPACES_ROOT`` unset (this workstation) the
# middleware does not even read the headers and every call resolves to the
# process config, exactly as before.
from motor_ai_sim.workspace import install_workspace_resolver
install_workspace_resolver(app)

# Tier gate NEXT (inner), CORS LAST (outer) so 401/403 from the gate still
# carry CORS headers — otherwise the browser shows a CORS error, not the 403.
from motor_ai_sim.auth import install_tier_gate
install_tier_gate(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Download routes speak through response headers; CORS hides everything but
    # the six simple ones, so the browser saw neither the filename nor which
    # FreeCAD bundle kind it got (App Control fallback, 2026-09-14).
    expose_headers=["Content-Disposition", "X-Bundle-Kind", "X-Bundle-Reason"],
)


# An unresolvable material ASSIGNMENT is a bad request, not a server fault: the
# name came from the shared config or from the request's own `mat=` override,
# and the only useful answer names it.  Registered app-wide so every physics
# route reports it the same way instead of a 500 stack trace — the failure it
# replaces was a log.warning nobody saw plus silently different physics (a 9x
# wrong shaft eddy loss; docs/SOLVER_TRIALS_2026-07-30.md F6).
@app.exception_handler(UnknownMaterialError)
async def _unknown_material_handler(request, exc: UnknownMaterialError):
    return JSONResponse(status_code=400,
                        content={"detail": f"unknown material: {exc}"})


app.include_router(geometry_router)
app.include_router(fusion_router)
app.include_router(pipeline_router)
app.include_router(simulation_router)
app.include_router(static3d_router)
app.include_router(mechanical_router)
# /api/panel_settings — the server-side memory of every tab's input fields (the
# same bargain the Simulation tab has with the config): see routes/panel_settings.
app.include_router(panel_settings_router)
# /api/thermal — split out of the simulation router on 2026-09-07 so the Thermal
# tab has the same contract the Mechanical one has (last result, mesh preview,
# timings).  Registered next to it deliberately: they are siblings.
app.include_router(thermal_router)
# /api/coupled — the EM<->thermal ORCHESTRATOR (2026-09-08, user: "не надо всё
# смешивать, нужен оркестратор").  A THIRD router above the two solvers, not a
# route inside either: it calls get_fem_transient and solve_thermal_field through
# their own public entry points and iterates the winding / magnet temperatures to
# the fixed point.  Neither solver learns about the other, and with the
# Electromagnetic tab's toggle off nothing here is reachable at all.
app.include_router(coupled_router)
# /api/controller — the INVERTER (2026-09-22, owner: «давай начнём делать модуль
# инвертора … чтобы была возможность комбинировать мосты так, как нам надо»).
# A fourth analysis router beside coupled/thermal/mechanical: a device card
# library, a coil->bridge map, the loss and junction-temperature arithmetic over
# them, and the waveform the motor will be fed with in Stage 2.  It solves no
# field and writes nothing but the duty's own `controller` block.
app.include_router(controller_router)
# /api/bearings — the catalogue, and the MECHANICAL half of the loss picture
# (SKF frictional moment + rotor windage).  Analytics only: it never solves a
# field and never writes; the machine's own bearing assignment is written by
# PATCH /api/family/config/{die}/{cfg}/bearings, next to its battery.
app.include_router(bearings_router)
app.include_router(wire_stock_router)
app.include_router(optimization_router)
app.include_router(presets_router)
app.include_router(catalog_router)
app.include_router(saved_sims_router)
app.include_router(freecad_router)
app.include_router(family_router)
app.include_router(report_router)
app.include_router(my_motors_router)
app.include_router(auth_local_router)
app.include_router(sweep_config_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(support_router)
app.include_router(modules_router)
app.include_router(kernel_router)
# Stage 4: the queue's own surface — what is running, queued and finished FOR
# THIS CALLER, and the one owner-checked cancel every Stop button delegates to.
app.include_router(jobs_router)
# /api/history — the persistent "don't recompute this" layer (2026-09-22):
# browse, load and delete the last few results of each solve kind that has
# wired itself into motor_ai_sim.run_history (today: mechanical.rotor_stress).
app.include_router(history_router)


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
                     'slot_insulation', 'wire_insulation',
                     # carbon-fibre retaining ring; only exists when
                     # sleeve_thickness > 0, see GET /api/materials
                     'sleeve'}
# The config THIS PROCESS is pointed at — ``MOTOR_AI_SIM_CONFIG`` included.
#
# 2026-09-15.  This was a hardcoded ``Path(__file__)…/config/motor_config.yaml``,
# bound at import and immune to the redirect, and four handlers WRITE through it:
# PATCH /api/materials, PATCH /api/parts, PATCH /api/winding and
# PATCH /api/mesh/config.  A redirected process (a test, an in-process sandboxed
# run) therefore reassigned the steel, restated a part, rewired the winding or
# re-meshed THE MACHINE THE USER HAS LOADED — the one thing the redirect exists
# to make impossible (config.py, after the 2026-08-06 incident).
#
# ``DEFAULT_CONFIG_PATH`` already resolves the env var, so with no env var set
# this is byte-identical to the old constant and the live API is unchanged.  It
# stays a module ATTRIBUTE because tests monkeypatch it by name
# (tests/test_family_activate_mesh_sync.py).
#
# 2026-09-15, migration Stage 1: resolved PER CALL against the caller's
# workspace, so on a multi-user server these four writers edit the machine of
# whoever asked.  The name survives — a monkeypatched value in the module dict
# wins over the resolver, and ``__getattr__`` answers a plain read.
def _config_path() -> Path:
    _ov = globals().get("_CONFIG_PATH")
    if _ov is not None:
        return Path(str(_ov))
    from motor_ai_sim.config import config_path as _resolve_cfg_path
    return Path(str(_resolve_cfg_path()))


def __getattr__(name):
    if name == "_CONFIG_PATH":
        return _config_path()
    raise AttributeError(name)


class MaterialAssignment(BaseModel):
    part: str
    material: str


@app.get("/api/materials")
def get_materials():
    try:
        out = dict(get_material_assignments() or {})
        # A part introduced after every machine in the field was saved has no
        # entry in any `materials:` block, so the Materials tab would show it as
        # unassigned and the solver would run on a bare density constant.  Fill
        # the code default in — but ONLY when the part actually EXISTS on this
        # machine (sleeve_thickness > 0).  Adding it unconditionally would put a
        # new key into the ?mat= payload of every request, moving the physics
        # fingerprint of every machine and marking stored duty results as
        # "computed on an older build" for a part that is not there.
        try:
            from motor_ai_sim.materials import DEFAULT_PART_MATERIAL
            from motor_ai_sim.config import get_geometry_params
            _geo = get_geometry_params().to_dict()
            if float(_geo.get("sleeve_thickness", 0.0) or 0.0) > 0.0:
                out.setdefault("sleeve", DEFAULT_PART_MATERIAL["sleeve"])
        except Exception:      # noqa: BLE001 — a default is a convenience
            pass
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _set_material_in_yaml(content: str, part: str, material: str):
    """Set `part: <material>` inside the `materials:` top-level block.

    Replaces the key when it is there and APPENDS it when it is not.  Appending
    is not a convenience: a part introduced after this config was written (the
    retaining sleeve) exists in the code and in the geometry but in no
    `materials:` block anywhere, so a replace-only writer would refuse every
    assignment to it — the Materials tab would offer the row and the save would
    500.  Same reason the geometry writer honours SCHEMA_FALLBACK.

    Returns (new_content, wrote_bool).
    """
    lines = content.splitlines(keepends=True)
    in_materials = False
    replaced = False
    result = []
    blk_start = None            # index in `result` of the `materials:` line
    blk_end = None              # index in `result` just past the block's last entry
    indent = "  "
    for line in lines:
        if re.match(r'^materials\s*:', line):
            in_materials = True
            blk_start = len(result)
        elif in_materials and re.match(r'^\S', line):
            in_materials = False  # exited the block
            blk_end = len(result)

        if in_materials and blk_start is not None and len(result) > blk_start:
            m_i = re.match(r'^(\s+)\S', line)
            if m_i:
                indent = m_i.group(1)
                blk_end = len(result) + 1

        if in_materials and not replaced:
            m = re.match(rf'^(\s+{re.escape(part)}\s*:\s*)(.*)$', line)
            if m:
                line = m.group(1) + material + '\n'
                replaced = True

        result.append(line)

    if replaced or blk_start is None:
        return ''.join(result), replaced
    at = blk_end if blk_end is not None else len(result)
    result.insert(at, f"{indent}{part}: {material}\n")
    return ''.join(result), True


@app.patch("/api/materials")
def update_material(assignment: MaterialAssignment):
    """Assign a library material to a motor part, saved to motor_config.yaml."""
    if assignment.part not in _ASSIGNABLE_PARTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown part '{assignment.part}'. Valid: {sorted(_ASSIGNABLE_PARTS)}"
        )
    try:
        content = _config_path().read_text(encoding="utf-8")
        new_content, replaced = _set_material_in_yaml(content, assignment.part, assignment.material)
        if not replaced:
            raise ValueError(f"Key '{assignment.part}' not found under materials: in config")
        _config_path().write_text(new_content, encoding="utf-8")
        clear_config_cache()
        # A different steel / magnet is a DIFFERENT MACHINE: the field, the
        # losses and the torque all move.  The physics caches are keyed on a
        # fingerprint that covers this, but an entry solved with the old
        # assignment left sitting beside the new one is exactly what the
        # relaxed snapshot lookup will serve (incident 2026-09-03: G2-L40
        # reported "+3.8 %" off the previous steel).  Drop them here.
        try:
            from motor_ai_sim.routes.simulation import clear_simulation_caches
            clear_simulation_caches(reason="material assignment changed")
        except Exception:
            pass
        return {"status": "ok", "assignments": get_material_assignments(reload=True)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Per-part accounting state (included / reference / excluded) ───────────
# Rides the SAME config file and the SAME per-request channel as the material
# assignment above; see motor_ai_sim.part_states for why.


from motor_ai_sim.part_states import config_part_states as _config_part_states


class PartStateAssignment(BaseModel):
    part: str
    state: str


@app.get("/api/parts")
def get_part_states_endpoint():
    """``{part: 'reference'|'excluded'}`` — only the parts that are NOT plain
    ``included``.  ``{}`` on an ordinary machine."""
    try:
        from motor_ai_sim.part_states import config_part_states
        return config_part_states()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _set_part_state_in_yaml(content: str, part: str, state: str) -> str:
    """Rewrite the top-level ``parts:`` block with ``part`` set to ``state``.

    The block is created when it does not exist and REMOVED when the change
    leaves every part included — so a default machine's config file is
    byte-identical to one written before this feature, and a diff of the file
    shows the accounting decision rather than an empty scaffold.
    """
    import re as _re
    lines = content.splitlines(keepends=True)
    start = end = None
    current: dict = {}
    for i, line in enumerate(lines):
        if start is None:
            if _re.match(r'^parts\s*:', line):
                start = i
            continue
        if _re.match(r'^\S', line):            # first non-indented line ends it
            end = i
            break
        m = _re.match(r'^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\S+)\s*$', line)
        if m:
            current[m.group(1)] = m.group(2).strip('"\'')
    if start is not None and end is None:
        end = len(lines)

    from motor_ai_sim.part_states import normalize_states, INCLUDED
    current = normalize_states(current)
    if state == INCLUDED:
        current.pop(part, None)
    else:
        current[part] = state
    current = {k: v for k, v in current.items() if v != INCLUDED}

    block = ("parts:\n" + "".join(f"  {k}: {current[k]}\n"
                                  for k in sorted(current))) if current else ""
    if start is None:
        if not block:
            return content                     # nothing to add, nothing to do
        sep = "" if content.endswith("\n") or not content else "\n"
        return content + sep + block
    return "".join(lines[:start]) + block + "".join(lines[end:])


@app.patch("/api/parts")
def update_part_state(assignment: PartStateAssignment):
    """Set a part's accounting state, saved to motor_config.yaml.

    ``included`` — ours: in the field, the mass, the inertia, the datasheet.
    ``reference`` — the customer's part sitting in our field (a frameless
    motor's shaft): solved with its assigned material, its losses honestly
    reported, and out of every mass, inertia and per-mass density.
    ``excluded`` — solved as air, weighs nothing, drawn nowhere.
    """
    from motor_ai_sim.part_states import (STATEFUL_PARTS, STATES,
                                          MAGNETICALLY_ACTIVE_PARTS, EXCLUDED)
    if assignment.part not in STATEFUL_PARTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown part '{assignment.part}'. "
                   f"Valid: {sorted(STATEFUL_PARTS)}")
    if assignment.state not in STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown state '{assignment.state}'. Valid: {list(STATES)}")
    try:
        content = _config_path().read_text(encoding="utf-8")
        _config_path().write_text(
            _set_part_state_in_yaml(content, assignment.part, assignment.state),
            encoding="utf-8")
        clear_config_cache()
        # A part turned to air (or handed back to the customer) changes the
        # magnetic circuit AND every mass — the same "different machine" as a
        # geometry edit, so the field / transient / snapshot stores go with it.
        try:
            from motor_ai_sim.routes.simulation import clear_simulation_caches
            clear_simulation_caches(
                reason=f"part state changed ({assignment.part} -> {assignment.state})")
        except Exception:
            pass
        from motor_ai_sim.part_states import config_part_states
        out = {"status": "ok", "parts": config_part_states()}
        # The solver refuses nothing (the user asked for "любую деталь"), but
        # removing a magnetically active part is an experiment, not a
        # packaging choice — say so instead of letting a plausible-looking
        # torque out of the door.
        if (assignment.state == EXCLUDED
                and assignment.part in MAGNETICALLY_ACTIVE_PARTS):
            out["warning"] = (
                f"'{assignment.part}' is magnetically active — solving it as "
                "air removes it from the magnetic circuit. Torque, EMF and "
                "losses below describe that machine, not the real one.")
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Materials library (EM simulation database) ────────────────────────────

#: The mechanical keys the rotor-stress solver sizes parts with — E along and
#: across the fibres, the shear modulus, Poisson, the three strengths, the
#: isotropic or orthotropic thermal expansion, and the matrix's service ceiling.
_MECH_KEYS = (
    "youngs_modulus_gpa", "youngs_modulus_transverse_gpa",
    "shear_modulus_gpa", "poisson_ratio",
    "tensile_strength_mpa", "compressive_strength_mpa", "yield_strength_mpa",
    "cte_ppm_k", "cte_ppm_k_1", "cte_ppm_k_2", "max_service_temp_c",
)


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
                "thickness_mm": m.thickness_mm,
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
                # The card's reference temperature and its two reversible
                # coefficients (2026-09-08): a client that builds a ?mat=
                # override from THIS payload must carry them, or a solve at
                # another magnet temperature has nothing to scale from.
                "temperature_c": getattr(m, "temperature_c", None),
                "alpha_br_pct_per_k": getattr(m, "alpha_br_pct_per_k", None),
                "beta_hcj_pct_per_k": getattr(m, "beta_hcj_pct_per_k", None),
                # The card's MAXIMUM WORKING TEMPERATURE (2026-09-16).  It is
                # the number the report judges a magnet by
                # (`report._magnet_limit`) and it was reachable only one card
                # at a time, through /api/materials/library/magnet/{name} —
                # so the duty-cycle editor had no default magnet limit and
                # every cycle was answered with the magnets unjudged unless
                # somebody typed a temperature.  Absent on a card that does
                # not state one; the report's coercivity-class fallback stays
                # where it is, in the report.
                "max_working_temp_c": getattr(m, "max_working_temp_c", None),
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

        # Insulators (insulation / wire enamel) — thermal + cost, EM-inert
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

        # ── MECHANICAL, straight off the raw records ──────────────────
        #
        # User 2026-09-10: *"так и не вижу механических свойств материалов в
        # каталоге"* — and they were right twice over: the card only rendered
        # them for insulators, and this endpoint never sent them at all.
        #
        # Merged from the RAW library rather than from the parsed dataclasses:
        # only some of those carry the mechanical keys and none carries the
        # thermal expansion, while the raw record always has whatever the yaml
        # was written with.  Same source the rotor-stress solver reads
        # (`mechanical.rotor_stress._raw_material`), so the catalogue cannot
        # show one number while the solve uses another.  Absent keys stay
        # absent — a card with no mechanical data grows no empty rows.
        try:
            from motor_ai_sim import materials as _mats
            _raw_lib = _mats._load()   # noqa: SLF001 - the intended loader
            for _cat, _cards in result.items():
                _raw_cat = _raw_lib.get(_cat) or {}
                for _name, _card in _cards.items():
                    _raw = _raw_cat.get(_name) or {}
                    for _k in _MECH_KEYS:
                        _v = _raw.get(_k)
                        if _v is not None:
                            _card[_k] = _v
        except Exception as _e_mech:      # noqa: BLE001 - never break the list
            logging.getLogger(__name__).debug(
                "materials library: mechanical keys skipped (%s)", _e_mech)

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
        # Geometry-derived end-winding factor k_end = (π·(wire_w/2+tooth_w/2) + L)/L,
        # recomputed from the (possibly overridden) geometry so the UI cell stays in sync.
        try:
            from motor_ai_sim.simulation.geometry_2d import params_from_config as _pfc
            from motor_ai_sim.simulation.fem_solver_2d import end_winding_factor_geom as _ewf
            from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _mgo
            # merge_geo_override, not a dict-update — same reason as everywhere
            # else the two tiers meet: the config's DERIVED fields must not
            # survive next to the override's primaries.
            _kgeo = _mgo(dict(config.get("geometry", {})), _ov)
            _kend = round(float(_ewf(_pfc(geo_override=_ov), _kgeo)), 3)
        except Exception:
            _kend = 0.0
        return {
            "geometry": geo_dict,
            "materials": get_material_assignments(),
            # {} on an ordinary machine; names the parts that are the
            # customer's (`reference`) or absent (`excluded`).
            "parts": _config_part_states(),
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

# ONE definition of the label and its parser, in motor_ai_sim.winding — the
# solver needs them too (it takes a per-request `connection=` now) and cannot
# import FastAPI to get them.
from motor_ai_sim.winding import connection_label as _conn_label  # noqa: E402


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
    # Ampere-turns per slot are TURNS × coil current, and k wires wound in hand
    # make one turn out of k of the slot's conductors: the slot carries the same
    # copper and the same total conductor current, but n_wires/k times it.
    from motor_ai_sim.winding import (turns_per_coil as _tpc,
                                      wire_parallel_from_geo as _wp_geo,
                                      n_parallel_effective as _npe)
    try:
        wire_parallel = _wp_geo(geo)
        turns_coil = _tpc(geo)
        npar_eff = _npe(n_parallel, geo)
    except ValueError as _wpe:
        raise HTTPException(status_code=422, detail=str(_wpe))
    amp_turns  = turns_coil * I_coil
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
        # Strands in hand and the SERIES turns they leave (geometry, not a
        # connection — it lives in geometry.wire_parallel).
        "wire_parallel":      int(wire_parallel),
        "turns_per_coil":     int(turns_coil),
        "n_parallel_eff":     int(npar_eff),
        "layers":             int(w.get("layers", 1)),
        "num_slots":          num_slots,
        "layout":             layout_str,
        # per-slot [phase, direction] for the visual phase-map
        "layout_slots":       [[p, d] for p, d in lay],
    }


from motor_ai_sim.winding import parse_connection as _parse_connection  # noqa: E402


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

    content = _config_path().read_text(encoding="utf-8")
    lines   = content.splitlines(keepends=True)
    in_winding = False
    in_sim     = False
    result     = []
    replaced   = set()

    for line in lines:
        if re.match(r'^winding\s*:', line):
            in_winding = True
            in_sim = False
        elif re.match(r'^simulation\s*:', line):
            in_sim = True
            in_winding = False
        elif (in_winding or in_sim) and re.match(r'^\S', line):
            in_winding = in_sim = False

        # simulation.connection is a MIRROR of the winding block's — nothing in
        # the physics reads it (the solver reads cfg["winding"]), but the
        # optimizer's plan and the Stage-D script did, and a mirror that is only
        # written by the other path goes stale: 4S selected here, a 12-hour
        # optimization planned at the 2S-2P the mirror still held.  Keep them
        # equal at the only place the connection changes.
        if in_sim and "connection" in updates:
            m = re.match(r'^(\s+connection\s*:\s*)(.*)$', line)
            if m:
                line = m.group(1) + updates["connection"] + '\n'

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

    _config_path().write_text(''.join(result), encoding="utf-8")
    clear_config_cache()
    # A winding change (connection / layers / layout) alters the field & torque,
    # so flush the simulation caches (mesh / field / transient) like a geometry edit.
    if {"connection", "layers", "layout"} & set(updates):
        try:
            from motor_ai_sim.routes.simulation import clear_simulation_caches
            clear_simulation_caches(reason="winding changed")
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
        config = _yaml.safe_load(_config_path().read_text(encoding="utf-8")) or {}
        # GUARD: write_text is not atomic (truncate-then-write), so a concurrent
        # reader can catch the file mid-write and parse an EMPTY config.  Writing
        # that back would nuke geometry/simulation/winding (data loss).  Never
        # persist a config that lost its core sections — bail instead.
        if "geometry" not in config:
            raise HTTPException(status_code=503,
                detail="config read incomplete (concurrent write) — mesh not saved, retry")
        config.setdefault("mesh", {}).update(updates)
        # Atomic write: temp file + os.replace, so readers never see a partial.
        _tmp = _config_path().with_suffix(".yaml.tmp")
        _tmp.write_text(
            _yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        _os.replace(_tmp, _config_path())
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
