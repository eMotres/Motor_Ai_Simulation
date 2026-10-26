"""Cogging torque of a configuration: measure it, save it, read it (owner 2026-10-26).

Three routes under ``/api/simulation/cogging`` (that prefix is already the compute worker's in the deploy-stage-2
routing table, so nginx / roles need no new rule):

* ``POST /run``   - solve the no-load cogging of the LIVE machine (no ``die``) or of a catalogued ``die`` +
  ``config`` (stateless: the machine is built from the two yaml documents, nothing is loaded into the workspace).
  Queued like every other solve.  ``save=true`` (die + config only) files the result on the configuration in the
  same call - this is the call a script / the server queue uses.  Returns the run, its ``cogging_id`` and the flat
  ``view`` a screen shows.
* ``PUT /{die}/{cfg}`` - save a finished run (``cogging_id``) as the ``cogging:`` block of the configuration yaml.
  Refused (409) when the run was of another machine or the configuration changed since it was solved.
* ``GET /{die}/{cfg}`` - the saved block's view (and, with ``waveform=true``, the waveform), with ``stale`` true when
  the build moved since.

The module docstring of :mod:`motor_ai_sim.cogging` is the method note.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from motor_ai_sim import cogging as C
from motor_ai_sim import jobs as _JOBS
from motor_ai_sim.routes import family as _F
from motor_ai_sim.routes.simulation import _material_override_dep, _parse_geo_override

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/simulation/cogging", tags=["cogging"],
                   dependencies=[Depends(_material_override_dep)])

#: Finished runs waiting for an explicit save (a run is a measurement; saving is a decision).  In memory, 16 newest.
_RECENT: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_RECENT_MAX = 16
_RECENT_LOCK = threading.Lock()


def _owner(authorization: Any) -> str:
    """Who asked, in the dialect the stores spell (``auth.caller_identity``): a pending run belongs to its solver."""
    try:
        from motor_ai_sim.auth import caller_identity
        return str(caller_identity(authorization if isinstance(authorization, str) else None).get("id") or "")
    except Exception:                                           # noqa: BLE001
        return ""


def _remember(run: Dict[str, Any], owner: str) -> str:
    rid = C.cogging_id_of(run)
    with _RECENT_LOCK:
        _RECENT[rid] = {"run": run, "owner": owner}
        _RECENT.move_to_end(rid)
        while len(_RECENT) > _RECENT_MAX:
            _RECENT.popitem(last=False)
    return rid


class CoggingRun(BaseModel):
    die: Optional[str] = None            # with `config`: a catalogued machine; absent: the LIVE machine
    config: Optional[str] = None
    magnet_temp_c: Optional[float] = None  # None = the magnet card as the library quotes it
    frozen_nu: bool = False               # frozen permeability: kept for comparison, see cogging.py for why it is off
    samples_per_cycle: int = Field(default=C.SAMPLES_PER_CYCLE, ge=6, le=48)
    mesh_size_mm: Optional[float] = Field(default=None, ge=C.MIN_MESH_MM, le=C.MAX_MESH_MM)   # a different mesh (else the machine's)
    save: bool = False                    # file the result on the configuration (die + config only)
    geo: Optional[str] = None             # live machine only: the per-request geometry override (JSON)


def _docs(die: str, cfg: str) -> tuple:
    die, cfg = _F._check_name(die, "die"), _F._check_name(cfg, "configuration")
    return (_F._load_yaml(_F._die_file(die), "die"),
            _F._load_yaml(_F._cfg_file(die, cfg), "configuration"), die, cfg)


def _stored(die: str, cfg: str) -> Dict[str, Any]:
    try:
        from motor_ai_sim import duty_results
        return duty_results.get(die, cfg) or {}
    except Exception:                                           # noqa: BLE001
        return {}


def _save(die: str, cfg: str, run: Dict[str, Any], who: dict) -> Dict[str, Any]:
    """Write ``run`` as the configuration's ``cogging:`` block (the caller has passed the write gate)."""
    die_doc, cfg_doc, die, cfg = _docs(die, cfg)
    # A LIVE-machine run (no die / config / build signature) is never filed on a configuration: nothing proves the
    # live machine is that configuration, and it would be stamped as if it had been computed on its current build.
    if not (run.get("die") and run.get("config") and run.get("build_sig")):
        raise HTTPException(422, detail=("a run of the live machine cannot be saved on a configuration - run it with "
                                         "`die` and `config` instead; nothing saved"))
    # Only a standard run is saved by anyone; an override (frozen permeability, another mesh, a magnet temperature, a
    # different sample count) is an administrator's decision, and is named wherever the number is shown.
    if run.get("overrides") and not who.get("is_admin"):
        raise HTTPException(403, detail=("this run used non-standard settings (%s); only an administrator may save "
                                         "it - nothing saved" % "; ".join(run["overrides"])))
    machine = C.machine_from_config(die_doc, cfg_doc)
    if run.get("geo_hash") and C.geo_hash(machine["geo_override"]) != run["geo_hash"]:
        raise HTTPException(409, detail=("this run was solved on a different machine than '%s / %s' (geometry "
                                         "differs) - nothing saved" % (die, cfg)))
    if run.get("die") and run.get("config") and (run["die"], run["config"]) != (die_doc.get("name") or die,
                                                                               cfg_doc.get("name") or cfg):
        raise HTTPException(409, detail="this run belongs to '%s / %s' - nothing saved" % (run["die"], run["config"]))
    sig = C.current_build_sig(die_doc, cfg_doc)
    if run.get("build_sig") and run["build_sig"] != sig:
        raise HTTPException(409, detail=("the configuration changed after this run was solved (geometry, winding "
                                         "or materials) - run it again; nothing saved"))
    cfg_doc[C.BLOCK_KEY] = C.build_block(run, build_sig=sig)
    _F._save_yaml(_F._cfg_file(die, cfg), cfg_doc)
    log.info("cogging: saved on '%s/%s': pk-pk %.5f N·m", die, cfg, run["summary"]["pkpk_Nm"])
    return C.view(cfg_doc[C.BLOCK_KEY], current_sig=sig, cfg_doc=cfg_doc, stored_by_duty=_stored(die, cfg)) or {}


@router.post("/run")
@_JOBS.queued("cogging", priority=_JOBS.Priority.DUTY, body_keys=())
def run_cogging(req: CoggingRun, authorization: str = Header(default=None)):
    """Solve the no-load cogging torque (see the module note); optionally save it on the configuration."""
    who = None
    if req.die or req.config:
        if not (req.die and req.config):
            raise HTTPException(422, detail="send both `die` and `config`, or neither (the live machine)")
        _F._require_die_access(req.die, authorization, req.config)
        if req.save:
            who = _F.require_catalog_write(authorization)
            _F._require_die_write(req.die, who, req.config)
        die_doc, cfg_doc, die, cfg = _docs(req.die, req.config)
        machine = C.machine_from_config(die_doc, cfg_doc, {"mesh_size_mm": req.mesh_size_mm})
        ct, _src, basis = C.continuous_torque(cfg_doc, _stored(die, cfg))
        ct = ct if basis == "continuous" else None
        build_sig = C.current_build_sig(die_doc, cfg_doc)
    else:
        if req.save:
            raise HTTPException(422, detail="`save` needs `die` and `config` (the live machine has no configuration)")
        machine = C.machine_live(_parse_geo_override(req.geo))
        if req.mesh_size_mm:
            machine["mesh"] = dict(machine["mesh"], mesh_size_mm=float(req.mesh_size_mm))
        ct, build_sig = None, None
    overrides = C.overrides_of(frozen_nu=req.frozen_nu, mesh_size_mm=req.mesh_size_mm,
                               magnet_temp_c=req.magnet_temp_c, samples_per_cycle=req.samples_per_cycle)
    if req.save and overrides and not (who or {}).get("is_admin"):
        # refused BEFORE the solve: a non-admin cannot save it, so it must not cost the CPU either
        raise HTTPException(403, detail=("`save` with non-standard settings (%s) is for an administrator; drop them "
                                         "to save the standard run" % "; ".join(overrides)))
    try:
        out = C.run(machine, magnet_temp_c=req.magnet_temp_c, frozen_nu=req.frozen_nu,
                    samples_per_cycle=req.samples_per_cycle, continuous_torque_nm=ct)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, detail=str(exc))
    out["overrides"] = overrides
    out["build_sig"] = build_sig
    out["cogging_id"] = _remember(out, _owner(authorization))
    out["saved"] = False
    if req.save and who is not None:
        out["view"] = _save(req.die, req.config, out, who)
        out["saved"] = True
    else:
        s = out["summary"]
        out["view"] = {"pkpk_Nm": s["pkpk_Nm"], "half_amp_Nm": s["half_amp_Nm"],
                       "pct_of_continuous": s.get("pct_of_continuous"),
                       "fundamental_order": s["fundamental_order"], "stale": False}
    return out


class CoggingSave(BaseModel):
    cogging_id: str


@router.put("/{die}/{cfg}")
def save_cogging(die: str, cfg: str, req: CoggingSave, _w: dict = Depends(_F.require_catalog_write)):
    """File a finished run on the configuration (it replaces the previous block)."""
    die, cfg = _F._check_name(die, "die"), _F._check_name(cfg, "configuration")
    _F._require_die_write(die, _w, cfg)
    _docs(die, cfg)                      # a configuration that is not there answers first (same 404 as a hidden one)
    with _RECENT_LOCK:
        entry = _RECENT.get(req.cogging_id)
    # a pending run is its solver's: another account's id answers like an unknown one
    if entry is None or entry["owner"] != _owner(_w.get("authorization")):
        raise HTTPException(404, detail="no such cogging run (it was not solved by you on this server, or it is older "
                                        "than the 16 newest) - run it again")
    run = entry["run"]
    return {"ok": True, "saved": True, "view": _save(die, cfg, run, _w)}


@router.get("/{die}/{cfg}")
def get_cogging(die: str, cfg: str, waveform: bool = False, authorization: str = Header(default=None)):
    """The saved cogging torque of the configuration, with ``stale`` when the build moved since it was solved."""
    die, cfg = _F._check_name(die, "die"), _F._check_name(cfg, "configuration")
    _F._require_die_access(die, authorization, cfg)
    die_doc, cfg_doc, die, cfg = _docs(die, cfg)
    blk = C.block_of(cfg_doc)
    v = (C.view(blk, current_sig=C.current_build_sig(die_doc, cfg_doc), cfg_doc=cfg_doc,
                stored_by_duty=_stored(die, cfg)) if blk else None)
    out: Dict[str, Any] = {"die": die, "config": cfg, "cogging": v}
    if v is not None and waveform:
        out["waveform"] = blk.get("waveform")
    return out
