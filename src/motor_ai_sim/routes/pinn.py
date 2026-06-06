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
        # Sector trainer: 90° wedge + anti-periodic BC + thermal guard + collocation
        # resampling.  ~15× faster than the full-domain trainer and dumps the
        # Modulus field to pinn_field.png (served by /field) at the end.
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
