"""Modulus PINN training bridge.

The PINN can only run in the WSL venv (CUDA + physicsnemo-sym), but the API
server runs on Windows.  So the Windows backend shells out to WSL to launch the
GPU training (scripts/pinn_train_web.py) and serves the live progress JSON that
the training writes to the shared filesystem — letting the web UI watch training
(loss/torque/GPU-temp) in real time without Modulus being installed on Windows.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/simulation/pinn", tags=["pinn"])

_ROOT = Path(__file__).resolve().parents[3]          # project root (…/motor_ai_sim)
_PROGRESS = _ROOT / "pinn_progress.json"
_FIELD_PNG = _ROOT / "pinn_field.png"               # Modulus field dump (Ar, Ai, |B|)


def _render_geometry_compare() -> bytes:
    """Render the geometry the PINN samples (MotorDomains2D — arc-sector magnets +
    rectangle slots) next to the REAL FEM geometry (CadQueryMotor polygons —
    trapezoid spoke magnets + T-teeth + coils).  Returns PNG bytes."""
    import io
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from motor_ai_sim.routes.simulation import _get_motor_geom   # cached real polys
    from motor_ai_sim.simulation.geometry_2d import MotorDomains2D, params_from_config

    MM = 1000.0
    polys = _get_motor_geom(0.0)          # reuse FEM's cached CadQuery polygons
    gp = params_from_config()
    geo = MotorDomains2D(gp)

    fig, (axL, axR) = plt.subplots(1, 2, figsize=(13, 6.6), facecolor="#0f172a")

    def scat(ax, dom, n, c, label=None):
        pts = dom.sample_interior(n)
        ax.scatter(np.asarray(pts["x"]).ravel() * MM, np.asarray(pts["y"]).ravel() * MM,
                   s=3, c=c, label=label, linewidths=0)

    scat(axL, geo["shaft"], 500, "#777", "shaft")
    scat(axL, geo["rotor_core"], 500, "#444", "rotor iron")
    scat(axL, geo["stator_core"], 1400, "#aaa", "stator iron")
    scat(axL, geo["air_gap"], 250, "#7db", "air gap")
    fs = fN = fS = True
    for _n, dom, _w in geo.slot_domains():
        scat(axL, dom, 220, "orange", "slot/coil" if fs else None); fs = False
    for _n, dom, pol in geo.magnet_domains():
        if pol > 0:
            scat(axL, dom, 220, "#ef4444", "magnet N" if fN else None); fN = False
        else:
            scat(axL, dom, 220, "#3b82f6", "magnet S" if fS else None); fS = False
    axL.set_title("MODULUS / PINN  (MotorDomains2D)\narc-sector magnets + rectangle slots",
                  fontsize=11, color="#fbbf24")
    axL.legend(loc="upper right", fontsize=7, markerscale=3, facecolor="#1e293b", labelcolor="#e2e8f0")

    def fill_poly(ax, poly, **kw):
        if poly is None:
            return
        for g in list(getattr(poly, "geoms", [poly])):
            if getattr(g, "is_empty", True):
                continue
            try:
                xy = np.array(g.exterior.coords)
            except Exception:
                continue
            ax.fill(xy[:, 0], xy[:, 1], **kw)
            for hole in getattr(g, "interiors", []):
                ax.fill(*np.array(hole.coords).T, color="#0f172a")

    fill_poly(axR, polys.get("stator"), color="#aaa", label="stator")
    fill_poly(axR, polys.get("rotor"), color="#444")
    fill_poly(axR, polys.get("shaft"), color="#777")
    fN = fS = fc = True
    for mp, pol in polys.get("magnets", []):
        lab = ("magnet N" if (pol > 0 and fN) else ("magnet S" if (pol <= 0 and fS) else None))
        fill_poly(axR, mp, color="#ef4444" if pol > 0 else "#3b82f6", label=lab)
        if pol > 0: fN = False
        else: fS = False
    for c in polys.get("coils", []):
        fill_poly(axR, c, color="orange", label="coil" if fc else None); fc = False
    axR.set_title("REAL / FEM  (CadQueryMotor.get_2d_polygons)\ntrapezoid spoke magnets + T-teeth + coils",
                  fontsize=11, color="#60a5fa")
    axR.legend(loc="upper right", fontsize=7, facecolor="#1e293b", labelcolor="#e2e8f0")

    R = gp.r_stator_out * MM * 1.05
    for ax in (axL, axR):
        ax.set_aspect("equal"); ax.set_xlim(-R, R); ax.set_ylim(-R, R)
        ax.set_facecolor("#0f172a"); ax.tick_params(colors="#94a3b8", labelsize=7)
        for s in ax.spines.values():
            s.set_color("#334155")
        ax.set_xlabel("x [mm]", color="#94a3b8", fontsize=8)
    fig.suptitle("Geometry the Modulus PINN samples  vs  the REAL motor (FEM) — they DON'T match",
                 fontsize=12.5, color="#f1f5f9")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=95, facecolor="#0f172a")
    plt.close(fig)
    return buf.getvalue()
# WSL launch context — the venv + project (mounted at /mnt/c) + GPU lib path.
_WSL_VENV = "~/motor_ai_sim_env/.venv/bin/activate"
_WSL_PROJ = "/mnt/c/Users/vadim/Projects/motor_ai_sim"

_lock = threading.Lock()
_proc: dict = {"p": None}


class StartReq(BaseModel):
    steps: int = 3000


@router.post("/start")
def start(req: StartReq):
    """Launch the thermally-gentle PINN training in WSL (background subprocess)."""
    with _lock:
        p = _proc["p"]
        if p is not None and p.poll() is None:
            raise HTTPException(status_code=409, detail="PINN training already running")
        steps = max(150, min(int(req.steps), 50000))
        # Export the REAL geometry (Windows/shapely) → pinn_geom.json so the WSL
        # trainer samples the SAME polygons the FEM solves (trapezoid magnets +
        # real coils + T-tooth iron), kept current with any geometry change.
        import sys as _sys
        try:
            subprocess.run([_sys.executable, str(_ROOT / "_export_geom.py")],
                           cwd=str(_ROOT), timeout=120,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        # Sector trainer: 90° wedge + anti-periodic BC + thermal guard + collocation
        # resampling.  Samples inside the real polygons and dumps the Modulus field
        # to pinn_field.png (served by /field) at the end.
        cmd = (f"source {_WSL_VENV} && cd {_WSL_PROJ} && "
               f"LD_LIBRARY_PATH=/usr/lib/wsl/lib PYTHONPATH=src "
               f"python scripts/pinn_sector_train.py {steps}")
        try:
            new = subprocess.Popen(["wsl.exe", "bash", "-lc", cmd],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise HTTPException(status_code=500,
                                detail="wsl.exe not found — the Modulus PINN runs only on Windows + WSL")
        _proc["p"] = new
        try:
            _PROGRESS.write_text(json.dumps({
                "running": True, "step": 0, "max_steps": steps,
                "torque_pinn": 0.0, "torque_fem": 24.87, "b_max": 0.0,
                "gpu_temp": None, "history": [],
            }))
        except OSError:
            pass
        return {"started": True, "steps": steps}


@router.get("/progress")
def progress():
    """Return the latest training progress (written by the WSL trainer)."""
    if not _PROGRESS.exists():
        return {"running": False, "step": 0, "max_steps": 0, "history": []}
    try:
        d = json.loads(_PROGRESS.read_text())
    except Exception:
        return {"running": False, "step": 0, "max_steps": 0, "history": []}
    with _lock:
        p = _proc["p"]
        if p is not None and p.poll() is not None and d.get("running") and not d.get("done"):
            d["running"] = False
            d["crashed"] = True
    return d


_GEOM_PNG = _ROOT / "geom_compare.png"
_geom_cache: dict = {"png": None}


@router.get("/geometry")
def geometry(refresh: int = 0):
    """Serve the PINN-vs-FEM geometry comparison PNG.

    Default: serve the pre-generated geom_compare.png from disk (instant).
    ?refresh=1: regenerate it in a SEPARATE process (matplotlib + CadQuery/OCC
    are not thread-safe and deadlock the uvicorn worker pool if run in-process),
    then serve the fresh file."""
    if refresh:
        import sys
        try:
            subprocess.run([sys.executable, str(_ROOT / "_geom_compare.py")],
                           cwd=str(_ROOT), timeout=120,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    if _GEOM_PNG.exists():
        return Response(content=_GEOM_PNG.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="no geometry image yet — train/render first")


_VSFEM_PNG = _ROOT / "pinn_vs_fem.png"


@router.get("/vs_fem")
def vs_fem():
    """Serve the pre-generated PINN-vs-FEM comparison (|B| both ways, air-gap
    B_r/B_φ/B_r·B_φ, capability table).  Generated offline by _pinn_vs_fem.py
    (a real FEM solve + the saved PINN net) — too heavy to render per-request."""
    if _VSFEM_PNG.exists():
        return Response(content=_VSFEM_PNG.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="no PINN-vs-FEM image yet")


@router.get("/field")
def field():
    """Serve the Modulus PINN field dump (A_z real/imag + |B|, full disk via the
    90° anti-periodic symmetry).  Written by the sector trainer at the end of a
    run.  no-store so the UI always sees the latest dump."""
    if not _FIELD_PNG.exists():
        raise HTTPException(status_code=404, detail="no PINN field yet — train first")
    data = _FIELD_PNG.read_bytes()
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/stop")
def stop():
    """Stop training (terminate the WSL subprocess + kill the python in WSL)."""
    with _lock:
        p = _proc["p"]
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
        _proc["p"] = None
    try:
        subprocess.run(["wsl.exe", "bash", "-lc", "pkill -f pinn_sector_train.py"],
                       timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    # reset the progress file so the UI doesn't show a stale "running" state
    try:
        d = json.loads(_PROGRESS.read_text()) if _PROGRESS.exists() else {}
    except Exception:
        d = {}
    d.update({"running": False, "stopped": True})
    try:
        _PROGRESS.write_text(json.dumps(d))
    except OSError:
        pass
    return {"stopped": True}
