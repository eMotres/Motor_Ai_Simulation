"""The application as the one source of motor data for emotres.com (owner 2026-11-17).

Two things live here:

* the per-motor **site record** (admin only, off by default): the flag "show on the site" and the few site-only fields the
  application does not have, kept in ONE sidecar file beside the other object stores
  (``<shared root>/object_registry/site_publication.json``, override ``MOTRES_OBJECT_REGISTRY_ROOT``), never inside a
  ``die.yaml`` or a configuration, so every stored motor stays byte-identical;
* the **public view** of a flagged motor: built from an explicit whitelist (like the MCP machine facts,
  ``mcp_tools``), from the SHARED catalog only, whoever asks.

NEVER in the public view: customer names, projects, productions, ids of people, an incomplete designation, a result the
owner did not mark for publication, lamination geometry (not even a section thumbnail: the picture is a typed URL), a
test point, anything of a motor without the flag.  Prices: ``price_text`` is a sentence the owner typed; nothing computes it.

Answer shape: ``docs/schemas/site_motors.v1.json`` (``docs/SITE_PUBLIC_API.md``).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim import json_store

SCHEMA_VERSION = 1
AVAILABILITY = ("in_production", "to_order", "engineering_sample")
TEXT_MAX = 120
HISTORY_MAX = 2000
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_IMAGE = re.compile(r"^https://[^\s<>\"']{4,400}$")


def store_path() -> Path:
    from motor_ai_sim.display_state import registry_root
    return registry_root() / "site_publication.json"


def key(die: str, config: str) -> str:
    if not die or not config:
        raise ValueError("die and config required")
    return f"motor:{die}/{config}"


def _empty() -> Dict[str, Any]:
    return {"version": 1, "motors": {}, "history": []}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load() -> Dict[str, Any]:
    data = json_store.read_json(store_path(), default=None)
    if not isinstance(data, dict):
        return _empty()
    if not isinstance(data.get("motors"), dict):
        data["motors"] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def _text(v: Any) -> str:
    return " ".join(str(v or "").split())[:TEXT_MAX]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def record_of(die: str, config: str, motors: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The site record of a motor; a motor nobody touched is ``show: False`` with empty fields."""
    motors = load()["motors"] if motors is None else motors
    r = motors.get(key(die, config))
    r = r if isinstance(r, dict) else {}
    return {"show": bool(r.get("show", False)),
            "availability": r.get("availability") if r.get("availability") in AVAILABILITY else None,
            "lead_time_text": _text(r.get("lead_time_text")), "slug": str(r.get("slug") or ""),
            "price_text": _text(r.get("price_text")), "image_url": str(r.get("image_url") or ""),
            "closed_ok": r.get("closed_ok") if isinstance(r.get("closed_ok"), dict) else None,
            "points": [str(x) for x in (r.get("points") or []) if str(x)],
            "rated_point": str(r.get("rated_point") or ""),
            "updated_at": str(r.get("updated_at") or ""), "updated_by": str(r.get("updated_by") or "")}


def set_record(die: str, config: str, fields: Dict[str, Any], *, by: str) -> Dict[str, Any]:
    """Merge ``fields`` into the motor's site record (read-latest, atomic); ValueError on a bad value or a taken slug."""
    k = key(die, config)
    upd: Dict[str, Any] = {}
    if "show" in fields:
        upd["show"] = bool(fields["show"])
    if "availability" in fields:
        a = fields["availability"] or None
        if a is not None and a not in AVAILABILITY:
            raise ValueError(f"availability must be one of {AVAILABILITY}")
        upd["availability"] = a
    for f in ("lead_time_text", "price_text"):
        if f in fields:
            upd[f] = _text(fields[f])
    if "slug" in fields:
        s = str(fields["slug"] or "").strip().lower()
        if s and (not _SLUG.match(s) or len(s) > 80):
            raise ValueError("slug: lower-case letters, digits and single hyphens only")
        upd["slug"] = s
    if "image_url" in fields:
        u = str(fields["image_url"] or "").strip()
        if u and not _IMAGE.match(u):
            raise ValueError("image_url must be an https URL")
        upd["image_url"] = u
    if "points" in fields:
        pts = fields["points"]
        if not isinstance(pts, list):
            raise ValueError("points must be a list of saved point names")
        upd["points"] = list(dict.fromkeys(_text(p) for p in pts if _text(p)))[:50]
    if "rated_point" in fields:
        upd["rated_point"] = _text(fields["rated_point"])
    if "closed_reason" in fields:
        reason = _text(fields["closed_reason"])
        upd["closed_ok"] = {"reason": reason, "by": str(by or ""), "at": _now()} if reason else None
    if upd.get("show"):
        blocked = blockers(die, config, {**record_of(die, config), **upd})
        if blocked:
            raise ValueError("cannot show on the site: " + "; ".join(blocked))
    if upd.get("availability") == "in_production" and _production_state(die, config) != "in_production":
        raise ValueError("availability 'in_production' needs the production to be in production (set by an admin in Production)")
    derived = ""
    if upd.get("show"):                                      # the slug is fixed at the first publish: it never shifts later
        docs = _shared_docs(die, config)
        if docs is not None:
            from motor_ai_sim import motor_designation as md
            d = md.describe(die, docs[0], docs[1])
            derived = slugify(d["designation"]) if d["complete"] else ""
    at = _now()

    def mutate(data: Any) -> Any:
        if not isinstance(data, dict):
            data = _empty()
        motors = data.setdefault("motors", {})
        history = data.setdefault("history", [])
        cur = dict(motors.get(k) or {})
        new = {**cur, **upd}
        if new.get("show") and not new.get("slug") and derived:
            taken_slugs = {o.get("slug") for o in motors.values() if isinstance(o, dict)}
            n, cand = 1, derived
            while cand in taken_slugs:
                n += 1
                cand = f"{derived}-{n}"
            new["slug"] = cand
        slug = new.get("slug")
        if slug:
            for other, o in motors.items():
                if other != k and isinstance(o, dict) and o.get("slug") == slug:
                    raise ValueError(f"slug {slug!r} is already used by another motor")
        rp, pts = new.get("rated_point"), new.get("points") or []
        if rp and rp not in pts:
            raise ValueError("rated_point must be one of the published points")
        new["updated_at"], new["updated_by"] = at, str(by or "")
        motors[k] = new
        history.append({"key": k, "at": at, "by": str(by or ""), "changed": sorted(upd)})
        if len(history) > HISTORY_MAX:
            del history[: len(history) - HISTORY_MAX]
        return data

    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store.mutate_json(path, mutate, default=_empty())
    _CACHE.clear()
    return record_of(die, config)


# ── the public view ────────────────────────────────────────────────────────────────────────────────────────────────────

def _f(v: Any, nd: int = 3) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if x == x and abs(x) != float("inf") else None


def _winding_words(seg: Dict[str, Any], w: Dict[str, Any]) -> str:
    turns = seg["turns"]["value"]
    wire = {"R": "round wire", "F": "flat (rectangular) wire"}.get(seg["wire"]["value"], "")
    m = re.search(r"(\d+)\s*S\D*(\d+)\s*P", str(w.get("connection") or "").upper())
    conn = f"{m.group(1)} in series, {m.group(2)} in parallel" if m else ""
    star = {"Y": "star", "D": "delta"}.get(seg["star_delta"]["value"], "")
    sets = "two three-phase sets" if seg["connection"].get("sets") == 2 else "one three-phase set"
    parts = [f"{turns} turns per phase", wire, conn, star, sets]
    return ", ".join(p for p in parts if p)


def _shared_docs(die: str, config: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], Path, Path]]:
    import yaml
    from motor_ai_sim.workspace import shared_root
    d = Path(str(shared_root())) / "dies" / die
    df, cf = d / "die.yaml", d / f"{config}.yaml"
    if not df.is_file() or not cf.is_file():
        return None
    try:
        return (yaml.safe_load(df.read_text(encoding="utf-8")) or {}, yaml.safe_load(cf.read_text(encoding="utf-8")) or {}, df, cf)
    except Exception:                                       # noqa: BLE001 - an unreadable file is not published
        return None


def blockers(die: str, config: str, rec: Optional[Dict[str, Any]] = None) -> List[str]:
    """What forbids showing this motor on the public site, whatever the flag says: a retired shared die, a private
    configuration, a die the owner opened to selected clients only (the access rules of ``motor_access``), and a CLOSED die
    (not in the open data set: a customer's motor) unless the record carries an explicit admin decision ``closed_ok``."""
    out: List[str] = []
    try:
        from motor_ai_sim import motor_access as MA
        from motor_ai_sim import workspace as WS
        if WS.is_tombstoned(die):
            out.append("the die is retired")
        docs = _shared_docs(die, config)
        if docs is not None and MA.file_visibility(docs[3]) == MA.CFG_PRIVATE:
            out.append("the configuration is private")
        if MA.get_die_access(die).get("visibility") == MA.VIS_SELECTED:
            out.append("the die is open to selected clients only")
        from motor_ai_sim.routes import family as fam
        from motor_ai_sim.workspace import shared_root
        if fam._die_source_of(Path(str(shared_root())) / "dies" / die) != "open" and not ((rec or {}).get("closed_ok") or {}).get("reason"):
            out.append("the die is closed (not in the open data set): an admin must record why it may be public (closed_ok)")
    except Exception:                                       # noqa: BLE001 - when the rules cannot be read, nothing is published
        out.append("the access rules could not be read")
    return out


def _production_state(die: str, config: str) -> str:
    try:
        from motor_ai_sim import production_lifecycle as PL
        return PL.state_of(die, config)["state"]
    except Exception:                                       # noqa: BLE001
        return "prepared"


def build_motor(die: str, config: str, rec: Dict[str, Any], *, registry: Optional[Any] = None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """``(public motor, problems)``: the motor is None while something blocks it (the problems say what, for the admin
    preview); the public routes drop such a motor silently."""
    problems: List[str] = []
    docs = _shared_docs(die, config)
    if docs is None:
        return None, ["not in the shared catalog"]
    die_doc, cfg_doc, df, cf = docs
    from motor_ai_sim import motor_designation as md
    from motor_ai_sim.duty_kind import is_test_duty
    des = md.describe(die, die_doc, cfg_doc)
    blocked = blockers(die, config, rec)
    if blocked:
        return None, blocked
    if rec.get("availability") == "in_production" and _production_state(die, config) != "in_production":
        return None, ["availability 'in_production' contradicts the production state (it is not in production)"]
    if not des["complete"]:
        problems.append("designation incomplete: " + ", ".join(des["unknown"]))
    if not rec.get("availability"):
        problems.append("availability not set")
    seg = des["segments"]
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.mcp_tools import _battery_v, _duty_headline
    from motor_ai_sim.routes import family as fam
    row = fam._build_config_row(die_doc, cfg_doc, config, (dr.index(die, shared=True).get(config)) or {})
    by_name = {str(d.get("name")): d for d in row.get("duties") or [] if not is_test_duty(d)}
    points = []
    for name in rec.get("points") or []:
        d = by_name.get(name)
        if d is None:
            problems.append(f"point {name!r} is not a stored point of this motor")
            continue
        h = _duty_headline(d)
        points.append({"name": h["duty"], "mode": h["mode"], "speed_rpm": h["speed_rpm"], "torque_nm": h["torque_nm"],
                       "power_kw": h["power_kw"], "phase_current_a_rms": h["phase_current_a_rms"],
                       "efficiency_pct": h["efficiency_pct"], "line_voltage_peak_v": h["line_voltage_peak_v"],
                       "dc_bus_nominal_v": _battery_v(cfg_doc)["dc_bus_nominal_v"], "mass_kg": h["mass_kg"]})
    rated = next((p for p in points if p["name"] == rec.get("rated_point")), None)
    masses = [p["mass_kg"] for p in ([rated] if rated else points) if p["mass_kg"] is not None]
    oid = None
    try:
        from motor_ai_sim import object_registry as OR
        oid = (registry or OR.index()).resolve("item", "catalog", f"{die}/{config}")
    except Exception:                                       # noqa: BLE001 - no registry yet: no id
        oid = None
    mt = max(df.stat().st_mtime, cf.stat().st_mtime)
    updated = max(rec.get("updated_at") or "", datetime.fromtimestamp(mt, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    if problems and (not des["complete"] or not rec.get("availability")):
        return None, problems
    motor = {
        "id": oid,
        "slug": rec.get("slug") or slugify(des["designation"]),
        "designation": des["designation"],
        "family": des["family"],
        "outer_diameter_mm": _f(seg["od"]["value"], 1),
        "slots": int(seg["winding"].get("slots") or 0) or None,
        "poles": int(float(seg["poles"]["value"])) if seg["poles"]["value"].replace(".", "").isdigit() else None,
        "stack_length_mm": _f(seg["length"]["value"], 1),
        "winding": {"code": des["designation"].split(" ")[-1], "words": _winding_words(seg, cfg_doc.get("winding") or {})},
        "steel": (cfg_doc.get("materials") or {}).get("stator_core") or None,
        "steel_class": _STEEL_CLASS.get(seg["steel"]["value"]),
        "mass_kg": masses[0] if masses else None,
        "rated_point": rated,
        "points": points,
        "availability": rec["availability"],
        "lead_time_text": rec.get("lead_time_text") or None,
        "price_text": rec.get("price_text") or None,
        "image_url": rec.get("image_url") or None,
        "updated_at": updated,
    }
    return motor, problems


_STEEL_CLASS = {"N": "non-oriented silicon steel", "G": "grain-oriented silicon steel", "C": "cobalt-iron"}
_CACHE: Dict[str, Tuple[float, Any]] = {}
CACHE_S = 60.0


def published() -> List[Dict[str, Any]]:
    """Every flagged, complete motor in the public shape, sorted by designation (cached about a minute)."""
    hit = _CACHE.get("all")
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    data = load()["motors"]
    try:
        from motor_ai_sim import object_registry as OR
        reg = OR.index()
    except Exception:                                       # noqa: BLE001
        reg = None
    out = []
    for k, r in sorted(data.items()):
        if not k.startswith("motor:") or not isinstance(r, dict) or not r.get("show"):
            continue
        die, _s, cfg = k[len("motor:"):].partition("/")
        motor, _p = build_motor(die, cfg, record_of(die, cfg, data), registry=reg)
        if motor is not None:
            out.append((motor, record_of(die, cfg, data), k))
    taken = {m["slug"] for m, r, _k in out if r.get("slug")}
    final = []
    for m, r, _k in sorted(out, key=lambda t: (t[0]["designation"], t[1].get("slug") or "", t[2])):
        if not r.get("slug"):                               # derived from the designation: equal designations get a stable suffix
            base, n, slug = m["slug"], 1, m["slug"]
            while slug in taken:
                n += 1
                slug = f"{base}-{n}"
            taken.add(slug)
            m = {**m, "slug": slug}
        final.append(m)
    out = final
    _CACHE["all"] = (time.monotonic(), out)
    return out


def envelope(motors: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "generated_at": _now(), "count": len(motors), "motors": motors}


# ── a die / configuration rename or move: the keys follow (die_rename.py, config_move.py) ─────────────────────────────

def _retarget(doc: Any, fn) -> int:
    if not isinstance(doc, dict):
        return 0
    n = 0

    def new_key(k: str) -> Optional[str]:
        kind, _s, rest = str(k).partition(":")
        die, sep, cfg = rest.partition("/")
        if kind != "motor" or not sep:
            return None
        hit = fn(die, cfg)
        return f"motor:{hit[0]}/{hit[1]}" if hit else None

    motors = doc.get("motors") if isinstance(doc.get("motors"), dict) else {}
    for k in list(motors):
        nk = new_key(k)
        if nk and nk != k:
            if nk in motors:
                raise ValueError(f"site publication already has {nk!r}")
            motors[nk] = motors.pop(k)
            n += 1
    for row in doc.get("history") or []:
        if isinstance(row, dict):
            nk = new_key(row.get("key", ""))
            if nk and nk != row.get("key"):
                row["key"] = nk
                n += 1
    return n


def rename_die_in(doc: Any, old: str, new: str) -> int:
    return _retarget(doc, lambda die, cfg: (new, cfg) if die == old else None)


def move_config_in(doc: Any, die: str, config: str, new_die: str, new_config: str) -> int:
    return _retarget(doc, lambda d, c: (new_die, new_config) if (d, c) == (die, config) else None)
