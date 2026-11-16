"""Saved optimisation runs of a motor: list, open, import (headless runs and runs of the tab).

Additive: a new folder beside the die (``optimization_runs/<configuration>/``), nothing existing is read or changed by
the import, and nothing is applied to a motor (Apply keeps its own rules).  ``POST /import?dry_run=1`` writes nothing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from motor_ai_sim.optimization import run_store as RS
from motor_ai_sim.routes.family import require_catalog_write

router = APIRouter(prefix="/api/optimization/runs", tags=["optimization-runs"])


def _fam():
    from motor_ai_sim.routes import family as F
    return F


def _read_dirs(F, die: str):
    out = [F._die_dir(die)]
    try:
        src = F._src_die_dir(die)
    except Exception:  # noqa: BLE001 - a missing layer is not an error for a list
        src = None
    if src is not None:
        out.append(src)
    return out


class RunImport(BaseModel):
    die: str
    config: str
    state: Dict[str, Any]
    run_id: Optional[str] = None
    request: Optional[Any] = None
    source: Optional[str] = None
    overwrite: bool = False


def _context_machine():
    """die / config of the machine open in this workspace (the stamp every sweep already carries)."""
    from motor_ai_sim.routes.optimization import _machine_stamp
    st = _machine_stamp()
    return st.get("die"), st.get("config")


@router.get("")
def list_saved_runs(die: Optional[str] = None, config: Optional[str] = None,
                    authorization: str = Header(default=None)):
    """Saved runs of ``die``/``config``; both omitted = the machine open in this workspace."""
    F = _fam()
    if not die or not config:
        die, config = _context_machine()
        if not die or not config:
            return {"die": None, "config": None, "runs": []}
    die, config = F._check_name(die, "die"), F._check_name(config, "configuration")
    F._require_die_access(die, authorization, config)
    return {"die": die, "config": config, "runs": RS.list_runs(*_read_dirs(F, die), config=config)}


@router.get("/{run_id}")
def open_saved_run(run_id: str, die: str, config: str, authorization: str = Header(default=None)):
    F = _fam()
    die, config = F._check_name(die, "die"), F._check_name(config, "configuration")
    F._require_die_access(die, authorization, config)
    try:
        rec = RS.read_run(*_read_dirs(F, die), config=config, run_id=run_id)
    except RS.RunRejected as e:
        raise HTTPException(422, detail=str(e))
    if rec is None:
        raise HTTPException(404, detail=f"no saved optimisation run '{run_id}' on {die}/{config}")
    return rec


@router.post("/import")
def import_run(req: RunImport, dry_run: bool = False, _w: dict = Depends(require_catalog_write)):
    F = _fam()
    die, config = F._check_name(req.die, "die"), F._check_name(req.config, "configuration")
    F._require_die_write(die, _w, config)
    try:
        rec = RS.build_record(req.state, req.run_id, req.request, req.source)
    except RS.RunRejected as e:
        raise HTTPException(422, detail=str(e))
    exists = RS.read_run(*_read_dirs(F, die), config=config, run_id=rec["run_id"]) is not None
    plan = {"die": die, "config": config, "run_id": rec["run_id"], "would_overwrite": exists,
            **{k: rec[k] for k in ("kind", "n_evals", "iter", "variables", "best_cost", "converged",
                                   "final_validation_status", "has_machine_fp", "gap_layers")}}
    if dry_run:
        return {"dry_run": True, "written": False, **plan}
    if exists and not req.overwrite:
        raise HTTPException(409, detail={"error": "run_exists", **plan})
    try:
        RS.write_run(F._w_die_dir(die), config, rec, overwrite=req.overwrite)
    except FileExistsError:
        raise HTTPException(409, detail={"error": "run_exists", **plan})
    return {"dry_run": False, "written": True, **plan}
