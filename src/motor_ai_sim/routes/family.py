"""Die -> Configuration -> Duty catalog.

One stamped lamination (the die) is a fixed 2-D cross-section; what a real
product varies on top of it is the stack length, the wire and the winding —
that is a CONFIGURATION — and each configuration is operated at a handful of
named points (current / rpm / mode / gamma) — those are DUTIES.

Storage is one folder per die under config/dies/:

    config/dies/D200-24s28p/
        die.yaml            # the stamped geometry (all 44 keys) + lock
        M1-L200.yaml        # configuration: free geometry keys + winding + duties
        G1-L160.yaml

This router only reads/writes THESE files.  Applying a duty to the live
motor is the frontend's job through the existing endpoints (geometry PUT,
winding PATCH, simulation-config PATCH) — no new write-path into the live
config exists here, so nothing already working can be broken by this layer.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from motor_ai_sim.auth import caller_identity, require_admin
from motor_ai_sim.config import DEFAULT_CONFIG_PATH, get_config

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/family", tags=["family"])

_DIES_DIR = Path(DEFAULT_CONFIG_PATH).parent / "dies"

# The keys a CONFIGURATION may change on a stamped die.  Everything else in
# the geometry block belongs to the tooling and is locked the day the die is
# cut: stack length is just how many laminations you drop in, and the wire
# stack (dims / count / split / insulation) is wound, not stamped.
FREE_GEO_KEYS = (
    "motor_length",
    "wire_width", "wire_height",
    "wire_spacing_x", "wire_spacing_y",
    "num_wires_per_slot", "wire_split",
    "insulation_thickness",
)

# Display names double as file/dir names, so the charset is "safe on every
# filesystem": letters, digits, space and light punctuation.  Real product
# names have spaces ("CILN28 200", "CIANO14 30_10").
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.,()#+°·\- ]{0,63}$")


def _check_name(name: str, what: str) -> str:
    n = (name or "").strip()
    if not _NAME_RE.match(n) or n != n.strip():
        raise HTTPException(422, detail=(
            f"{what} name '{name}' is not usable as a file name — use letters, "
            "digits, spaces or '-_.,()#+', up to 64 characters, starting with "
            "a letter or digit"))
    return n


def _die_dir(die: str) -> Path:
    return _DIES_DIR / die


def _die_file(die: str) -> Path:
    return _die_dir(die) / "die.yaml"


def _cfg_file(die: str, cfg: str) -> Path:
    return _die_dir(die) / f"{cfg}.yaml"


def _load_yaml(p: Path, what: str) -> dict:
    try:
        with open(p, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        if not isinstance(d, dict):
            raise ValueError("not a mapping")
        return d
    except FileNotFoundError:
        raise HTTPException(404, detail=f"{what} not found: {p.name}")
    except Exception as e:  # noqa: BLE001 — a broken file must be SAID, not 500'd blind
        raise HTTPException(500, detail=f"{what} file {p.name} is unreadable: {e}")


def _save_yaml(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(d, f, sort_keys=False, allow_unicode=True)
    tmp.replace(p)


def _plain(v):
    """OmegaConf/DictConfig → plain python (yaml-safe)."""
    try:
        from omegaconf import OmegaConf
        if OmegaConf.is_config(v):
            return OmegaConf.to_container(v, resolve=True)
    except Exception:
        pass
    return v


def _live_cfg() -> dict:
    c = _plain(get_config(reload=True))
    return c if isinstance(c, dict) else dict(c)


def _sim_of(c: dict) -> dict:
    return dict(c.get("simulation") or {})


# ── models ───────────────────────────────────────────────────────────────────

class DutySpec(BaseModel):
    name: str
    mode: str = "motor"                  # "motor" | "generator"
    current_arms: Optional[float] = None  # None + from_current → read Simulation
    rpm: Optional[float] = None
    gamma_deg: Optional[float] = None
    torque_nm: Optional[float] = None    # target/rated torque — shown on the card
    power_kw: Optional[float] = None     # target/rated power — shown on the card
    note: str = ""
    from_current: bool = False           # fill the Nones from the live Simulation


class DieCreate(BaseModel):
    name: str


# Computed numbers a RUN produced at this duty's operating point — never
# invented, only recorded off a finished Simulation run.
_RESULT_KEYS = ("efficiency_pct", "ripple_pct", "v_ll_peak_v", "loss_w", "mass_kg")


class DutyResult(BaseModel):
    die: str
    config: str
    duty: str
    result: dict


class ConfigCreate(BaseModel):
    die: str
    name: str
    role: str = "motor"                  # informative: "motor" | "generator"


class DutyCreate(BaseModel):
    die: str
    config: str
    duty: DutySpec


# ── tree ─────────────────────────────────────────────────────────────────────

@router.get("/tree")
def tree(authorization: str = Header(default=None)):
    # Clients read the catalog and LOAD duties; changing it is the vendor's
    # job.  can_write tells the frontend whether to draw the editing controls —
    # the mutating endpoints enforce the same rule server-side regardless.
    _who = caller_identity(authorization)
    out = []
    if not _DIES_DIR.is_dir():
        return {"dies": out, "can_write": bool(_who["is_admin"])}
    for dd in sorted(_DIES_DIR.iterdir()):
        if not dd.is_dir() or not (dd / "die.yaml").is_file():
            continue
        die = _load_yaml(dd / "die.yaml", "die")
        geo = die.get("geometry") or {}
        cfgs = []
        for cf in sorted(dd.glob("*.yaml")):
            if cf.name == "die.yaml":
                continue
            c = _load_yaml(cf, "configuration")
            ov = c.get("geometry_overrides") or {}
            w = c.get("winding") or {}
            cfgs.append({
                "name": cf.stem,
                "role": c.get("role", "motor"),
                "locked": bool(c.get("locked", False)),
                "stack_mm": ov.get("motor_length"),
                "wire_height_mm": ov.get("wire_height"),
                "wire_width_mm": ov.get("wire_width"),
                "turns": ov.get("num_wires_per_slot"),
                "connection": w.get("connection"),
                "magnet": (c.get("materials") or {}).get("magnet"),
                "steel": (c.get("materials") or {}).get("stator_core"),
                "duties": [
                    {"name": d.get("name"), "mode": d.get("mode", "motor"),
                     "current_arms": d.get("current_arms"), "rpm": d.get("rpm"),
                     "gamma_deg": d.get("gamma_deg"),
                     "torque_nm": d.get("torque_nm"), "power_kw": d.get("power_kw"),
                     "note": d.get("note", ""), "result": d.get("result")}
                    for d in (c.get("duties") or [])
                ],
            })
        out.append({
            "name": dd.name,
            "locked": bool(die.get("locked", True)),
            "created": die.get("created"),
            "slots": geo.get("num_slots"), "poles": geo.get("num_poles"),
            "stator_diameter": geo.get("stator_diameter"),
            "thumb_svg": die.get("thumb_svg"),
            "configs": cfgs,
        })
    return {"dies": out, "can_write": bool(_who["is_admin"])}


# ── die ──────────────────────────────────────────────────────────────────────

@router.post("/die")
def create_die(req: DieCreate, _admin: dict = Depends(require_admin)):
    name = _check_name(req.name, "die")
    if _die_file(name).exists():
        raise HTTPException(409, detail=f"die '{name}' already exists — delete it "
                                        "first or pick another name")
    live = _live_cfg()
    geo = _plain(live.get("geometry"))
    if not geo:
        raise HTTPException(500, detail="live config has no geometry block")
    sim = _sim_of(live)
    # The card's picture is the REAL stamped cross-section — same generator the
    # motor catalog uses; None (unbuildable) just means the card falls back to
    # a schematic, never a failed die creation.
    try:
        from motor_ai_sim.routes.presets import _gen_thumb_svg
        thumb = _gen_thumb_svg(dict(geo))
    except Exception:
        thumb = None
    _save_yaml(_die_file(name), {
        "name": name,
        "locked": True,
        "created": datetime.now().isoformat(timespec="seconds"),
        # d-axis is a property of the stamped topology — snapshot the pin.
        "daxis_deg": sim.get("daxis_deg"),
        "thumb_svg": thumb,
        "geometry": dict(geo),
    })
    log.info("family: die '%s' created from the live geometry (%d keys)",
             name, len(geo))
    return {"ok": True, "die": name}


class DieRename(BaseModel):
    name: str


@router.patch("/die/{die}")
def rename_die(die: str, req: DieRename, _admin: dict = Depends(require_admin)):
    """Rename a die: the folder moves, die.yaml and every configuration's
    back-reference are updated."""
    die = _check_name(die, "die")
    new = _check_name(req.name, "die")
    src = _die_dir(die)
    if not (src / "die.yaml").is_file():
        raise HTTPException(404, detail=f"die '{die}' not found")
    if new == die:
        return {"ok": True, "die": die}
    dst = _die_dir(new)
    if dst.exists():
        raise HTTPException(409, detail=f"die '{new}' already exists")
    src.rename(dst)
    d = _load_yaml(dst / "die.yaml", "die")
    d["name"] = new
    _save_yaml(dst / "die.yaml", d)
    for cf in dst.glob("*.yaml"):
        if cf.name == "die.yaml":
            continue
        c = _load_yaml(cf, "configuration")
        c["die"] = new
        _save_yaml(cf, c)
    log.info("family: die '%s' renamed to '%s'", die, new)
    return {"ok": True, "die": new}


@router.delete("/die/{die}")
def delete_die(die: str, force: bool = False, _admin: dict = Depends(require_admin)):
    die = _check_name(die, "die")
    dd = _die_dir(die)
    if not (dd / "die.yaml").is_file():
        raise HTTPException(404, detail=f"die '{die}' not found")
    cfgs = [p.stem for p in dd.glob("*.yaml") if p.name != "die.yaml"]
    if cfgs and not force:
        raise HTTPException(409, detail=(
            f"die '{die}' still has {len(cfgs)} configuration(s): "
            f"{', '.join(cfgs)} — delete them first (or pass force=true)"))
    for p in dd.glob("*.yaml"):
        p.unlink()
    try:
        dd.rmdir()
    except OSError:
        pass                             # non-yaml leftovers — keep the folder
    log.warning("family: die '%s' deleted (%d configuration(s) with it)",
                die, len(cfgs))
    return {"ok": True, "deleted_configs": cfgs}


# ── configuration ────────────────────────────────────────────────────────────

@router.post("/config")
def create_config(req: ConfigCreate, _admin: dict = Depends(require_admin)):
    die = _check_name(req.die, "die")
    name = _check_name(req.name, "configuration")
    if not _die_file(die).is_file():
        raise HTTPException(404, detail=f"die '{die}' not found")
    if name == "die":
        raise HTTPException(422, detail="'die' is reserved")
    if _cfg_file(die, name).exists():
        raise HTTPException(409, detail=f"configuration '{name}' already exists "
                                        f"under die '{die}'")
    if req.role not in ("motor", "generator"):
        raise HTTPException(422, detail="role must be 'motor' or 'generator'")
    live = _live_cfg()
    geo = _plain(live.get("geometry")) or {}
    sim = _sim_of(live)
    mat = _plain(live.get("materials")) or {}
    _save_yaml(_cfg_file(die, name), {
        "name": name,
        "die": die,
        "role": req.role,
        "created": datetime.now().isoformat(timespec="seconds"),
        "geometry_overrides": {k: geo.get(k) for k in FREE_GEO_KEYS
                               if geo.get(k) is not None},
        "winding": _plain(live.get("winding")) or {},
        # The build's ACTIVE materials — a configuration is a physical product,
        # and its magnet grade / core steel are part of what was built.
        "materials": {k: mat.get(k)
                      for k in ("magnet", "stator_core", "rotor_core")
                      if mat.get(k)},
        # Coil temperature deliberately NOT snapshotted: it is an operating
        # condition the Simulation tab owns, not a property of the build.
        "end_winding_factor": sim.get("end_winding_factor"),
        "duties": [],
    })
    log.info("family: configuration '%s/%s' created from the live machine",
             die, name)
    return {"ok": True, "die": die, "config": name}


class ConfigRename(BaseModel):
    name: str


@router.patch("/config/{die}/{cfg}")
def rename_config(die: str, cfg: str, req: ConfigRename, _admin: dict = Depends(require_admin)):
    """Rename a configuration (its file moves with it; duties ride along)."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    new = _check_name(req.name, "configuration")
    if new == "die":
        raise HTTPException(422, detail="'die' is reserved")
    src = _cfg_file(die, cfg)
    if not src.is_file():
        raise HTTPException(404, detail=f"configuration '{die}/{cfg}' not found")
    if new == cfg:
        return {"ok": True, "config": cfg}
    dst = _cfg_file(die, new)
    if dst.exists():
        raise HTTPException(409, detail=f"configuration '{new}' already exists "
                                        f"under die '{die}'")
    c = _load_yaml(src, "configuration")
    c["name"] = new
    _save_yaml(dst, c)
    src.unlink()
    log.info("family: configuration '%s/%s' renamed to '%s'", die, cfg, new)
    return {"ok": True, "config": new}


@router.delete("/config/{die}/{cfg}")
def delete_config(die: str, cfg: str, _admin: dict = Depends(require_admin)):
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    p = _cfg_file(die, cfg)
    if not p.is_file():
        raise HTTPException(404, detail=f"configuration '{die}/{cfg}' not found")
    n = len((_load_yaml(p, "configuration").get("duties")) or [])
    p.unlink()
    log.warning("family: configuration '%s/%s' deleted (%d duty(ies) with it)",
                die, cfg, n)
    return {"ok": True}


# ── duty ─────────────────────────────────────────────────────────────────────

@router.post("/duty")
def upsert_duty(req: DutyCreate, _admin: dict = Depends(require_admin)):
    die = _check_name(req.die, "die")
    cfg = _check_name(req.config, "configuration")
    dname = _check_name(req.duty.name, "duty")
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    d = req.duty
    if d.mode not in ("motor", "generator"):
        raise HTTPException(422, detail="duty mode must be 'motor' or 'generator'")
    if d.from_current:
        sim = _sim_of(_live_cfg())
        if d.current_arms is None:
            d.current_arms = sim.get("current_a", sim.get("max_current"))
        if d.rpm is None:
            d.rpm = sim.get("rpm")
        if d.gamma_deg is None:
            d.gamma_deg = sim.get("gamma_deg", sim.get("phase_offset_deg"))
    missing = [k for k in ("current_arms", "rpm", "gamma_deg")
               if getattr(d, k) is None]
    if missing:
        raise HTTPException(422, detail=(
            f"duty '{dname}' is missing {', '.join(missing)} — give the values "
            "or pass from_current=true with the Simulation tab set up"))
    if not (float(d.current_arms) > 0 and float(d.rpm) > 0):
        raise HTTPException(422, detail="current_arms and rpm must be positive")
    duties = [x for x in (c.get("duties") or []) if x.get("name") != dname]
    entry = {"name": dname, "mode": d.mode,
             "current_arms": round(float(d.current_arms), 3),
             "rpm": round(float(d.rpm), 1),
             "gamma_deg": round(float(d.gamma_deg), 3),
             "note": d.note or ""}
    if d.torque_nm is not None:
        entry["torque_nm"] = round(float(d.torque_nm), 2)
    if d.power_kw is not None:
        entry["power_kw"] = round(float(d.power_kw), 2)
    # An upsert of the operating point KEEPS a previously recorded result —
    # results die only with the duty (or when overwritten by a new record).
    prev = next((x for x in (c.get("duties") or []) if x.get("name") == dname), None)
    if prev and prev.get("result"):
        entry["result"] = prev["result"]
    duties.append(entry)
    c["duties"] = duties
    _save_yaml(p, c)
    log.info("family: duty '%s/%s/%s' saved (%s, %.1f Arms @ %.0f rpm, γ=%.2f°)",
             die, cfg, dname, d.mode, d.current_arms, d.rpm, d.gamma_deg)
    return {"ok": True}


@router.post("/duty_result")
def record_duty_result(req: DutyResult, _admin: dict = Depends(require_admin)):
    """Attach a finished run's numbers to a duty.  The frontend verifies the
    run WAS at this duty's operating point before calling; this endpoint only
    validates shape and stores."""
    die = _check_name(req.die, "die")
    cfg = _check_name(req.config, "configuration")
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    found = next((x for x in (c.get("duties") or [])
                  if x.get("name") == req.duty), None)
    if found is None:
        raise HTTPException(404, detail=f"duty '{req.duty}' not found in {die}/{cfg}")
    res = {}
    for k in _RESULT_KEYS:
        v = req.result.get(k)
        if v is None:
            continue
        try:
            res[k] = round(float(v), 3)
        except (TypeError, ValueError):
            raise HTTPException(422, detail=f"result field '{k}' is not a number: {v!r}")
    if not res:
        raise HTTPException(422, detail=(
            "empty result — expected at least one of: " + ", ".join(_RESULT_KEYS)))
    res["recorded_at"] = datetime.now().isoformat(timespec="seconds")
    found["result"] = res
    _save_yaml(p, c)
    log.info("family: result recorded on '%s/%s/%s': %s", die, cfg, req.duty, res)
    return {"ok": True}


@router.delete("/duty/{die}/{cfg}/{duty}")
def delete_duty(die: str, cfg: str, duty: str, _admin: dict = Depends(require_admin)):
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    before = c.get("duties") or []
    after = [x for x in before if x.get("name") != duty]
    if len(after) == len(before):
        raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
    c["duties"] = after
    _save_yaml(p, c)
    return {"ok": True}


# ── locks & the active family context ────────────────────────────────────────
# WHICH die/configuration the live machine currently is — written when a duty
# is loaded, read by the geometry route to enforce the locks.  A sidecar file,
# not motor_config.yaml, so this layer still never rewrites the live config.
_CTX_FILE = Path(DEFAULT_CONFIG_PATH).parent / ".family_context.json"


def _read_ctx() -> Optional[dict]:
    try:
        import json
        d = json.loads(_CTX_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("die") else None
    except Exception:
        return None


class Activate(BaseModel):
    die: str
    config: str


@router.post("/activate")
def activate(req: Activate):
    """Mark die/config as the machine now loaded in the editor.  Open to every
    caller — it is part of LOADING, not of editing the catalog."""
    die = _check_name(req.die, "die")
    cfg = _check_name(req.config, "configuration")
    d = _load_yaml(_die_file(die), "die")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    import json
    _CTX_FILE.write_text(json.dumps({"die": die, "config": cfg,
                                     "at": datetime.now().isoformat(timespec="seconds")}),
                         encoding="utf-8")
    return {"ok": True, "die_locked": bool(d.get("locked", True)),
            "config_locked": bool(c.get("locked", False))}


class LockPatch(BaseModel):
    locked: bool


@router.patch("/die/{die}/lock")
def lock_die(die: str, req: LockPatch, _admin: dict = Depends(require_admin)):
    die = _check_name(die, "die")
    d = _load_yaml(_die_file(die), "die")
    d["locked"] = bool(req.locked)
    _save_yaml(_die_file(die), d)
    log.info("family: die '%s' %s", die, "locked" if req.locked else "UNLOCKED")
    return {"ok": True, "locked": bool(req.locked)}


@router.patch("/config/{die}/{cfg}/lock")
def lock_config(die: str, cfg: str, req: LockPatch,
                _admin: dict = Depends(require_admin)):
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    c["locked"] = bool(req.locked)
    _save_yaml(_cfg_file(die, cfg), c)
    log.info("family: configuration '%s/%s' %s", die, cfg,
             "locked" if req.locked else "UNLOCKED")
    return {"ok": True, "locked": bool(req.locked)}


def _num_eq(a, b) -> bool:
    try:
        fa, fb = float(a), float(b)
        return abs(fa - fb) <= 1e-9 * max(1.0, abs(fa), abs(fb))
    except (TypeError, ValueError):
        return a == b


def geometry_lock_check(update: dict) -> Optional[dict]:
    """Called by the geometry PUT route.  Returns a refusal detail when the
    ACTIVE die/configuration locks forbid this update, else None.

    Lock levels (user-defined):
      die+config locked -> geometry untouchable (only Simulation params move);
      die locked only   -> only the FREE keys (stack length, wire, turns) move;
      nothing locked    -> everything moves.
    A locked key still ACCEPTS its canonical value — that is how loading the
    configuration itself passes."""
    ctx = _read_ctx()
    if not ctx:
        return None
    die, cfg = str(ctx.get("die") or ""), str(ctx.get("config") or "")
    try:
        d = _load_yaml(_die_file(die), "die")
    except HTTPException:
        return None                       # stale context — die is gone
    try:
        c = _load_yaml(_cfg_file(die, cfg), "configuration")
    except HTTPException:
        c = {}
    die_locked = bool(d.get("locked", True))
    cfg_locked = bool(c.get("locked", False))
    if not die_locked and not cfg_locked:
        return None
    die_geo = d.get("geometry") or {}
    ov = c.get("geometry_overrides") or {}
    bad = []
    for k, v in update.items():
        free = k in FREE_GEO_KEYS
        if free:
            if not cfg_locked:
                continue                  # die-lock alone leaves free keys open
            canon = ov.get(k, die_geo.get(k))
            lock_name = f"configuration '{cfg}'"
        else:
            if not die_locked:
                continue
            canon = die_geo.get(k)
            lock_name = f"die '{die}'"
        if canon is not None and _num_eq(v, canon):
            continue                      # writing the canonical value is a no-op
        bad.append({"field": k, "reason": f"locked by {lock_name}",
                    "locked_value": canon})
    if not bad:
        return None
    lvl = ("die and configuration are locked — geometry is read-only, only "
           "Simulation parameters may change"
           if (die_locked and cfg_locked) else
           "die is locked — only stack length, wire size and turn count may change")
    return {
        "error": "geometry is locked",
        "die": die, "config": cfg,
        "die_locked": die_locked, "config_locked": cfg_locked,
        "invalid_parameters": bad,
        "hint": lvl + ". Unlock in the Motors catalog (admin) or load another configuration.",
    }


# ── apply payload ────────────────────────────────────────────────────────────

@router.get("/payload/{die}/{cfg}")
def payload(die: str, cfg: str, duty: Optional[str] = None):
    """Everything the frontend needs to APPLY a configuration (and optionally a
    duty) through the EXISTING endpoints: merged geometry, winding, sim values.
    Nothing is written here."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    d = _load_yaml(_die_file(die), "die")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    geo = dict(d.get("geometry") or {})
    geo.update(c.get("geometry_overrides") or {})
    poles = int(geo.get("num_poles") or 0)
    out = {
        "die": die, "config": cfg,
        "geometry": geo,
        "winding": c.get("winding") or {},
        "materials": c.get("materials") or {},
        "sim": {
            "end_winding_factor": c.get("end_winding_factor"),
            "daxis_deg": d.get("daxis_deg"),
            "connection": (c.get("winding") or {}).get("connection"),
        },
    }
    if duty is not None:
        found = next((x for x in (c.get("duties") or [])
                      if x.get("name") == duty), None)
        if found is None:
            raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
        rpm = float(found.get("rpm") or 0)
        out["duty"] = dict(found)
        out["sim"].update({
            "mode": found.get("mode", "motor"),
            "current_a": found.get("current_arms"),
            "rpm": rpm,
            "gamma_deg": found.get("gamma_deg"),
            "frequency": round(rpm * (poles / 2) / 60.0, 2) if poles else None,
        })
    return out
