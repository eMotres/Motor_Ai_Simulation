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
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")


def _check_name(name: str, what: str) -> str:
    n = (name or "").strip()
    if not _NAME_RE.match(n):
        raise HTTPException(422, detail=(
            f"{what} name '{name}' is not usable as a file name — use letters, "
            "digits, '-', '_' or '.', up to 64 characters, starting with a "
            "letter or digit"))
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
def tree():
    out = []
    if not _DIES_DIR.is_dir():
        return {"dies": out}
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
                "stack_mm": ov.get("motor_length"),
                "wire_height_mm": ov.get("wire_height"),
                "wire_width_mm": ov.get("wire_width"),
                "turns": ov.get("num_wires_per_slot"),
                "connection": w.get("connection"),
                "duties": [
                    {"name": d.get("name"), "mode": d.get("mode", "motor"),
                     "current_arms": d.get("current_arms"), "rpm": d.get("rpm"),
                     "gamma_deg": d.get("gamma_deg"),
                     "torque_nm": d.get("torque_nm"), "power_kw": d.get("power_kw"),
                     "note": d.get("note", "")}
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
    return {"dies": out}


# ── die ──────────────────────────────────────────────────────────────────────

@router.post("/die")
def create_die(req: DieCreate):
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


@router.delete("/die/{die}")
def delete_die(die: str, force: bool = False):
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
def create_config(req: ConfigCreate):
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
    _save_yaml(_cfg_file(die, name), {
        "name": name,
        "die": die,
        "role": req.role,
        "created": datetime.now().isoformat(timespec="seconds"),
        "geometry_overrides": {k: geo.get(k) for k in FREE_GEO_KEYS
                               if geo.get(k) is not None},
        "winding": _plain(live.get("winding")) or {},
        # Coil temperature deliberately NOT snapshotted: it is an operating
        # condition the Simulation tab owns, not a property of the build.
        "end_winding_factor": sim.get("end_winding_factor"),
        "duties": [],
    })
    log.info("family: configuration '%s/%s' created from the live machine",
             die, name)
    return {"ok": True, "die": die, "config": name}


@router.delete("/config/{die}/{cfg}")
def delete_config(die: str, cfg: str):
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
def upsert_duty(req: DutyCreate):
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
    duties.append(entry)
    c["duties"] = duties
    _save_yaml(p, c)
    log.info("family: duty '%s/%s/%s' saved (%s, %.1f Arms @ %.0f rpm, γ=%.2f°)",
             die, cfg, dname, d.mode, d.current_arms, d.rpm, d.gamma_deg)
    return {"ok": True}


@router.delete("/duty/{die}/{cfg}/{duty}")
def delete_duty(die: str, cfg: str, duty: str):
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
