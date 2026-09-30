"""MCP Stage 3 — agent DRAFT designs and simulations through the job queue.

docs/MCP_2026-09-28.md, section "Stage 3".

THE RULES THIS MODULE EXISTS TO KEEP
====================================
1. **One identity, one workspace.**  An agent is a delegated credential of the
   user (``agent_keys.Principal``).  Its drafts live in the USER's workspace
   (``<ws>/agent_designs/<design_id>/``) and its runs are the USER's jobs in the
   user's queue (``<ws>/.jobs.json``), tagged with the agent that asked.
2. **Explicit addressing only.**  Nothing here reads or writes the user's
   ACTIVE context: not ``motor_config.yaml``, not ``.family_context.json``, not
   a die or configuration file, not a saved duty.  Every call names a die /
   configuration / duty or a ``design_id``.  A draft is simulated in its OWN
   sandbox workspace (``<draft>/sandbox/motor_config.yaml``, built from the
   draft, never from the live machine), so the solver's per-workspace caches,
   stores and "last result" files land beside the draft (incident 2026-09-27:
   a duty load overwrote a die — the live machine is not a scratch pad).
3. **Drafts only.**  Agent writes create DRAFT machines, marked "created by
   agent <client>", visible in the web (Motors -> Agent drafts), revertible to
   what the agent created and deletable.  Overwriting a saved duty needs
   ``duties:write``, which does not exist in this stage.
4. **Loud validation.**  ``start_design`` answers ``needs_input`` — the field,
   why it is needed, the options or the range — instead of guessing anything a
   motor engineer would have to be asked.  Assumptions it DOES make (ambient
   40 degC when not given, no envelope when none is given) are listed.
5. **No invented laminations.**  A starting point is an EXISTING die the user
   may see, scaled by stack length, turns and parallel paths within stated
   ranges.  New lamination synthesis is deferred.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from motor_ai_sim.agent_keys import Principal

log = logging.getLogger(__name__)

DESIGNS_DIR = "agent_designs"
DESIGN_FILE = "design.json"
SANDBOX_DIR = "sandbox"
_ID_RE = re.compile(r"^d-[0-9a-f]{12}$")

WHATS = ("em", "thermal", "coupled")
COOLINGS = ("air", "liquid", "robotics")
DUTIES = ("S1", "S2", "S3", "peak")
MODES = ("motor", "generator")

#: Valid ranges of the scaling a starting point may be stretched by.
STACK_FACTOR_RANGE = (0.5, 2.0)
TURNS_FACTOR_RANGE = (0.25, 2.0)
#: Current-density factor vs the base duty (J scales with the current factor
#: at a constant slot fill, see ``_scale``): continuous duty vs short duty.
J_FACTOR_MAX = {"S1": 1.3, "S2": 1.8, "S3": 1.8, "peak": 2.2}
#: Peak line voltage allowed = this x DC bus (SVPWM: V_LL,peak <= V_dc).
BUS_UTILISATION = 0.9
#: Above this x the fastest recorded duty of a configuration, the speed is
#: outside what was verified on that machine (mechanics not checked).
SPEED_VERIFIED_FACTOR = 1.2
POWER_TOLERANCE = 0.05
MAX_DRAFTS_PER_USER = 50
#: The ranges ``needs_input`` quotes (and the public input catalogue in
#: ``mcp_discovery`` repeats) — one definition, so the two never drift.
TORQUE_RANGE_NM = (0.01, 20000.0)
SPEED_RANGE_RPM = (50.0, 60000.0)
DC_BUS_RANGE_V = (12.0, 1500.0)
DC_BUS_OPTIONS_V = (24, 48, 96, 400, 800)
AMBIENT_RANGE_C = (-60.0, 150.0)
AMBIENT_DEFAULT_C = 40.0
STEPS_RANGE = (12, 360)


class DesignError(ValueError):
    """A caller mistake — reported to the agent (becomes an MCP tool error)."""


class QuotaExceeded(Exception):
    def __init__(self, used: int, limit: int, retry_after: int) -> None:
        super().__init__(f"daily simulation fair-use limit reached ({used}/{limit})")
        self.used, self.limit, self.retry_after = used, limit, retry_after


# ── time / small helpers ────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _iso(t: Optional[float] = None) -> str:
    return _dt.datetime.fromtimestamp(t if t is not None else _now(),
                                      _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool) or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise DesignError(f"not a number: {v!r}")
    if math.isnan(x) or math.isinf(x):
        raise DesignError(f"not a finite number: {v!r}")
    return x


def _r(v: Optional[float], nd: int = 3) -> Optional[float]:
    return None if v is None else round(float(v), nd)


def public_base_url() -> str:
    from motor_ai_sim import oauth as _oauth
    return _oauth.base_url()


def configure_url(design_id: str) -> str:
    """The web address that opens a draft in Configure (never auto-loads it
    into the live machine — the engineer clicks "open" there)."""
    return f"{public_base_url()}/?tab=configure&design={design_id}"


# ── storage (the USER's workspace) ──────────────────────────────────────────

def designs_root() -> Path:
    """``<current workspace>/agent_designs`` — call inside ``acting_as`` or a
    request whose workspace is the user's."""
    from motor_ai_sim import workspace as _ws
    return Path(str(_ws.root())) / DESIGNS_DIR


def _check_id(design_id: str) -> str:
    did = str(design_id or "").strip()
    if not _ID_RE.match(did):
        raise DesignError("design_id must look like d-xxxxxxxxxxxx (from start_design)")
    return did


def _path(design_id: str) -> Path:
    return designs_root() / _check_id(design_id) / DESIGN_FILE


def _read(design_id: str, owner: str) -> Dict[str, Any]:
    """The draft, or DesignError("not found") — same answer for "does not
    exist" and "belongs to another account"."""
    p = _path(design_id)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    if not isinstance(d, dict) or str(d.get("owner") or "") != str(owner or ""):
        raise DesignError(f"design '{design_id}' not found")
    return d


def _write(d: Dict[str, Any]) -> None:
    from motor_ai_sim.json_store import mutate_json
    p = _path(d["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    d["updated_at"] = _now()
    mutate_json(p, lambda _old: d, default={})


def _update(design_id: str, owner: str, fn: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    """Read-modify-write under the store lock (a job finishing and an engineer
    editing in Configure may meet here)."""
    from motor_ai_sim.json_store import mutate_json
    p = _path(design_id)
    box: Dict[str, Any] = {}

    def _m(old):
        if not isinstance(old, dict) or str(old.get("owner") or "") != str(owner or ""):
            raise DesignError(f"design '{design_id}' not found")
        fn(old)
        old["updated_at"] = _now()
        box["d"] = old
        return old
    if not p.is_file():
        raise DesignError(f"design '{design_id}' not found")
    mutate_json(p, _m, default={})
    return box["d"]


def list_designs(owner: str) -> List[Dict[str, Any]]:
    root = designs_root()
    out = []
    try:
        dirs = sorted(root.iterdir())
    except OSError:
        return []
    for dd in dirs:
        f = dd / DESIGN_FILE
        if not f.is_file():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and str(d.get("owner") or "") == str(owner or ""):
            out.append(d)
    out.sort(key=lambda d: -(d.get("created_at") or 0))
    return out


def delete_design(design_id: str, owner: str) -> bool:
    import shutil
    _read(design_id, owner)                      # ownership check / 404
    shutil.rmtree(designs_root() / _check_id(design_id), ignore_errors=True)
    return True


# ── ADMIN ONLY: cross-account views ─────────────────────────────────────────
# "Agent activity" in the web Admin tab (2026-09-30, owner: drafts/runs do not
# belong on the Motors catalog page).  These never filter by owner — every
# caller MUST be gated by routes.admin.require_admin, same as every other
# cross-account listing there (usage, sessions, tickets).

def _all_design_roots() -> List[Path]:
    """Every workspace's ``agent_designs`` directory, admin cross-account scan.

    ``WORKSPACES_ROOT`` unset (single-user / local dev, the same condition
    ``workspace.workspace_for_identity`` uses) -> just the one process
    workspace, so a single-user install still shows its own drafts here."""
    from motor_ai_sim import workspace as _ws
    base = _ws.workspaces_root()
    if base is not None:
        try:
            return [p / DESIGNS_DIR for p in sorted(base.iterdir()) if p.is_dir()]
        except OSError:
            return []
    return [Path(str(_ws.process_workspace().root)) / DESIGNS_DIR]


def list_all_designs() -> List[Dict[str, Any]]:
    """ADMIN ONLY — every draft on this server, across every account, newest
    first.  No owner filter: the caller is the gate."""
    out: List[Dict[str, Any]] = []
    for root in _all_design_roots():
        try:
            dirs = sorted(root.iterdir())
        except OSError:
            continue
        for dd in dirs:
            f = dd / DESIGN_FILE
            if not f.is_file():
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict):
                out.append(d)
    out.sort(key=lambda d: -(d.get("created_at") or 0))
    return out


def admin_view(d: Dict[str, Any]) -> Dict[str, Any]:
    """Admin cross-account view: ``web_view`` plus the owner e-mail — the one
    field a normal owner's own view has no reason to carry."""
    out = web_view(d)
    out["owner"] = d.get("owner")
    return out


def admin_delete_design(design_id: str) -> str:
    """ADMIN ONLY — delete a draft in ANY account's workspace, regardless of
    who owns it.  Returns the owner e-mail (for the audit log).  Raises
    DesignError("... not found") if no such draft exists anywhere."""
    import shutil
    did = _check_id(design_id)
    for root in _all_design_roots():
        dd = root / did
        f = dd / DESIGN_FILE
        if not f.is_file():
            continue
        owner = ""
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            owner = str((d or {}).get("owner") or "")
        except (OSError, ValueError):
            pass
        shutil.rmtree(dd, ignore_errors=True)
        return owner
    raise DesignError(f"design '{design_id}' not found")


def revert_design(design_id: str, owner: str) -> Dict[str, Any]:
    """Back to exactly what the agent created (params + estimate); run results
    of later edits are dropped because they describe a different machine."""
    def _fn(d):
        d["params"] = json.loads(json.dumps(d.get("initial_params") or d.get("params")))
        d["estimate"] = json.loads(json.dumps(d.get("initial_estimate") or d.get("estimate")))
        d["results"] = {}
        d["edited_by_user"] = False
        d.setdefault("history", []).append({"at": _now(), "event": "reverted"})
    return _update(design_id, owner, _fn)


# ── requirements: validate loudly, ask when missing ─────────────────────────

REQUIREMENT_FIELDS = ("torque_nm", "power_kw", "speed_rpm", "max_speed_rpm",
                      "dc_bus_v", "max_outer_diameter_mm", "max_length_mm",
                      "max_mass_kg", "cooling", "duty", "ambient_c", "mode",
                      "application")


def normalize_requirements(req: Dict[str, Any]) -> Tuple[Dict[str, Any],
                                                          List[Dict[str, Any]],
                                                          List[str]]:
    """``(normalized, needs_input, assumptions)``.  Raises DesignError for a
    value that is plainly wrong (negative, not a number, unknown option)."""
    if not isinstance(req, dict):
        raise DesignError("requirements must be an object")
    unknown = sorted(set(req) - set(REQUIREMENT_FIELDS))
    if unknown:
        raise DesignError(f"unknown requirement field(s) {unknown}; allowed: "
                          f"{list(REQUIREMENT_FIELDS)}")
    n: Dict[str, Any] = {}
    for k in ("torque_nm", "power_kw", "speed_rpm", "max_speed_rpm", "dc_bus_v",
              "max_outer_diameter_mm", "max_length_mm", "max_mass_kg", "ambient_c"):
        v = _num(req.get(k))
        if v is not None and k != "ambient_c" and v <= 0:
            raise DesignError(f"{k} must be positive (got {v:g})")
        n[k] = v
    if n["ambient_c"] is not None and not AMBIENT_RANGE_C[0] <= n["ambient_c"] <= AMBIENT_RANGE_C[1]:
        raise DesignError("ambient_c must be between -60 and 150 degC")
    if n["dc_bus_v"] is not None and n["dc_bus_v"] > DC_BUS_RANGE_V[1]:
        raise DesignError("dc_bus_v above 1500 V is outside what eMotres simulates")
    for k, allowed in (("cooling", COOLINGS), ("duty", DUTIES), ("mode", MODES)):
        v = req.get(k)
        if v is None or str(v).strip() == "":
            n[k] = None
            continue
        s = str(v).strip()
        match = next((a for a in allowed if a.lower() == s.lower()), None)
        if match is None:
            raise DesignError(f"{k} must be one of {list(allowed)} (got {v!r})")
        n[k] = match
    n["application"] = (str(req.get("application") or "").strip()[:500] or None)

    ask: List[Dict[str, Any]] = []
    assumptions: List[str] = []
    T, P, n_r = n["torque_nm"], n["power_kw"], n["speed_rpm"]
    # derive the third of torque / power / speed when two are given
    if T is None and P is not None and n_r is not None:
        n["torque_nm"] = T = P * 1000.0 / (n_r * math.pi / 30.0)
        assumptions.append(f"torque derived from power and speed: {T:.3g} N*m")
    elif P is None and T is not None and n_r is not None:
        n["power_kw"] = P = T * n_r * math.pi / 30.0 / 1000.0
    elif n_r is None and T is not None and P is not None:
        n["speed_rpm"] = n_r = P * 1000.0 / T * 30.0 / math.pi
        assumptions.append(f"rated speed derived from power and torque: {n_r:.0f} rpm")
    if T is None and P is None:
        ask.append({"field": "torque_nm",
                    "why": "the design target: shaft torque at rated speed "
                           "(or give power_kw instead)",
                    "range": list(TORQUE_RANGE_NM), "unit": "N*m"})
    if n_r is None:
        ask.append({"field": "speed_rpm",
                    "why": "rated speed sets the frequency, the back-EMF and "
                           "therefore the winding", "range": list(SPEED_RANGE_RPM),
                    "unit": "rpm"})
    if T is not None and P is not None and n_r is not None:
        p_calc = T * n_r * math.pi / 30.0 / 1000.0
        if abs(p_calc - P) > POWER_TOLERANCE * max(P, p_calc):
            ask.append({"field": "power_kw",
                        "why": (f"contradiction: torque {T:g} N*m at {n_r:g} rpm is "
                                f"{p_calc:.3g} kW, but power_kw = {P:g}. Which one "
                                "is the requirement?"),
                        "options": [round(p_calc, 3)],
                        "alternative": {"torque_nm": round(P * 1000.0 / (n_r * math.pi / 30.0), 3)}})
    if n["max_speed_rpm"] is not None and n_r is not None and n["max_speed_rpm"] < n_r:
        ask.append({"field": "max_speed_rpm",
                    "why": f"contradiction: max speed {n['max_speed_rpm']:g} rpm is "
                           f"below the rated speed {n_r:g} rpm",
                    "range": [n_r, n_r * 4], "unit": "rpm"})
    if n["dc_bus_v"] is None:
        ask.append({"field": "dc_bus_v",
                    "why": "the winding (turns and parallel paths) is chosen so "
                           "the peak line voltage fits the DC bus",
                    "options": list(DC_BUS_OPTIONS_V), "range": list(DC_BUS_RANGE_V),
                    "unit": "V"})
    if n["cooling"] is None:
        ask.append({"field": "cooling",
                    "why": "the continuous current, hence the size, depends on "
                           "how the heat leaves the machine",
                    "options": list(COOLINGS)})
    if n["duty"] is None:
        ask.append({"field": "duty",
                    "why": "continuous (S1) or short-time / intermittent duty "
                           "sets the allowed current density",
                    "options": list(DUTIES)})
    if n["ambient_c"] is None:
        n["ambient_c"] = AMBIENT_DEFAULT_C
        assumptions.append("ambient 40 degC (not given)")
    if n["mode"] is None:
        n["mode"] = "motor"
        assumptions.append("motor mode (not given)")
    if n["max_speed_rpm"] is None and n_r is not None:
        n["max_speed_rpm"] = n_r
        assumptions.append("max speed = rated speed (not given)")
    for k, what in (("max_outer_diameter_mm", "outer diameter"),
                    ("max_length_mm", "active length"), ("max_mass_kg", "mass")):
        if n[k] is None:
            assumptions.append(f"no limit on {what} (not given)")
    return n, ask, assumptions


# ── starting point: existing dies the user may see, scaled ─────────────────

def _divisors(c: int) -> List[int]:
    return [p for p in range(1, max(1, c) + 1) if c % p == 0]


def _conn_label(c: int, p: int) -> str:
    s = c // p
    return f"{c}S" if p == 1 else (f"{c}P" if s == 1 else f"{s}S-{p}P")


def _winding_choice(k_target: float, c: int, p0: int, n0: int, wp: int
                    ) -> Optional[Tuple[int, int, float]]:
    """``(conductors_per_slot, parallel_paths, series_turns_factor)`` — the
    largest turns factor <= ``k_target`` realisable with an integer number of
    turns (conductors a multiple of the strands in hand ``wp``) and a parallel
    path count that divides the coils per phase ``c``."""
    best = None
    lo, hi = TURNS_FACTOR_RANGE
    for p in _divisors(c):
        turns0 = n0 / wp
        for t in range(1, int(turns0 * hi) + 2):
            k = (t / turns0) * (p0 / p)
            if k > k_target + 1e-9 or not lo - 1e-9 <= t / turns0 <= hi + 1e-9:
                continue
            if best is None or k > best[2] + 1e-9 or (abs(k - best[2]) < 1e-9 and p == p0):
                best = (t * wp, p, k)
    return best


def _scale(req: Dict[str, Any], m: Dict[str, Any], d: Dict[str, Any],
           wind: Optional[Dict[str, Any]], max_rpm_cfg: Optional[float]
           ) -> Dict[str, Any]:
    """Scale one saved duty to the requirements.  Returns
    ``{ok, reasons, warnings, k_stack, k_turns, k_current, ...}``."""
    reasons: List[str] = []
    warnings: List[str] = []
    T_req, n_req = req["torque_nm"], req["speed_rpm"]
    T0, n0, I0 = d.get("torque_nm"), d.get("speed_rpm"), d.get("phase_current_a_rms")
    V0, L0 = d.get("line_voltage_peak_v"), m.get("active_length_mm")
    m0 = d.get("mass_kg") if d.get("mass_kg") is not None else m.get("mass_kg")
    out: Dict[str, Any] = {"ok": False, "reasons": reasons, "warnings": warnings}
    if not (T0 and n0 and I0 and L0) or T0 <= 0 or n0 <= 0 or I0 <= 0 or L0 <= 0:
        reasons.append("saved duty lacks torque / speed / current / stack length")
        return out
    od = m.get("outer_diameter_mm")
    if req.get("max_outer_diameter_mm") and od and od > req["max_outer_diameter_mm"]:
        reasons.append(f"outer diameter {od:g} mm > {req['max_outer_diameter_mm']:g} mm")
    if (m.get("role") or "motor") != req["mode"] and d.get("mode") != req["mode"]:
        warnings.append(f"base machine was characterised as {d.get('mode') or m.get('role')}")
    # 1) stack length carries the torque at the base current
    k_L = T_req / T0
    lo, hi = STACK_FACTOR_RANGE
    if req.get("max_length_mm"):
        hi = min(hi, req["max_length_mm"] / L0)
    k_L = max(lo, min(hi, k_L))
    if hi < lo:
        reasons.append(f"active length {L0 * lo:.3g} mm (minimum stack) > "
                       f"{req['max_length_mm']:g} mm")
    # 2) the rest by current
    k_I = T_req / (T0 * k_L)
    jmax = J_FACTOR_MAX.get(req["duty"] or "S1", 1.3)
    if k_I > jmax:
        reasons.append(f"needs {k_I:.2f}x the base current density "
                       f"(limit {jmax:g}x for {req['duty']})")
    # 3) winding: peak line voltage at the MAX speed must fit the bus
    n_max = max(req.get("max_speed_rpm") or n_req, n_req)
    k_T, cond, par, conn = 1.0, None, None, None
    v_est = None
    if V0 and V0 > 0 and req.get("dc_bus_v") and wind:
        v_allow = BUS_UTILISATION * req["dc_bus_v"]
        v_at = V0 * k_L * (n_req / n0)            # at rated speed, base winding
        k_target = min(TURNS_FACTOR_RANGE[1], v_allow / v_at) if v_at > 0 else 1.0
        choice = _winding_choice(k_target, wind["coils_per_phase"], wind["n_parallel"],
                                 wind["conductors"], wind["strands"])
        if choice is None:
            reasons.append(f"cannot reduce turns enough: peak line voltage "
                           f"{v_at:.0f} V vs {v_allow:.0f} V allowed")
        else:
            cond, par, k_T = choice
            conn = _conn_label(wind["coils_per_phase"], par)
            v_est = v_at * k_T
            if n_max > n_req:
                v_top = v_est * n_max / n_req
                if v_top > req["dc_bus_v"]:
                    warnings.append(
                        f"back-EMF at max speed ~{v_top:.0f} V exceeds the "
                        f"{req['dc_bus_v']:g} V bus: field weakening needed "
                        "(not verified)")
    elif not V0:
        warnings.append("base duty has no recorded line voltage: winding not "
                        "sized to the bus")
    elif req.get("dc_bus_v") and not wind:
        warnings.append("winding of the base machine could not be read: turns "
                        "not sized to the bus")
    # the same torque with fewer series turns needs proportionally more current;
    # at constant slot fill the current DENSITY follows k_I only (J ~ I/p * N)
    I_new = I0 * k_I / k_T
    if max_rpm_cfg and n_max > SPEED_VERIFIED_FACTOR * max_rpm_cfg:
        warnings.append(f"speed {n_max:.0f} rpm is above the fastest verified "
                        f"duty of this machine ({max_rpm_cfg:.0f} rpm): rotor "
                        "stress and bearings are not verified")
    mass = m0 * k_L if m0 else None
    if req.get("max_mass_kg") and mass and mass > req["max_mass_kg"]:
        reasons.append(f"estimated mass {mass:.3g} kg > {req['max_mass_kg']:g} kg")
    out.update(ok=not reasons, k_stack=k_L, k_current=k_I, k_turns=k_T,
               stack_mm=round(L0 * k_L * 2) / 2, current_a=I_new,
               conductors=cond, parallel_paths=par, connection=conn,
               v_peak_est=v_est, mass_est=mass,
               score=abs(math.log(k_L)) + 1.5 * abs(math.log(max(k_I, 1e-6)))
               + 0.5 * abs(math.log(n_req / n0)) + 0.3 * len(warnings))
    return out


def _winding_of(die: str, cfg: str, duty: str) -> Optional[Dict[str, Any]]:
    """Coils per phase / parallel paths / conductors / strands of a config —
    read INTERNALLY, never returned to the agent."""
    try:
        pay = _payload(die, cfg, duty)
    except Exception:                                   # noqa: BLE001
        return None
    geo, w = pay.get("geometry") or {}, pay.get("winding") or {}
    slots = int(geo.get("num_slots") or 0) or int(
        (geo.get("num_seg") or 0) * (geo.get("num_slots_per_segment") or 0))
    c = int(w.get("n_coils_per_phase") or 0) or max(1, slots // 6)
    p0 = int(w.get("n_parallel") or 0)
    if not p0:
        mm = re.search(r"(\d+)\s*P", str(w.get("connection") or pay["sim"].get("connection") or ""))
        p0 = int(mm.group(1)) if mm else 1
    n0 = int(geo.get("num_wires_per_slot") or 0)
    wp = max(1, int(geo.get("wire_parallel") or 1))
    if n0 <= 0:
        return None
    return {"coils_per_phase": c, "n_parallel": p0, "conductors": n0, "strands": wp}


def _payload(die: str, cfg: str, duty: Optional[str]) -> Dict[str, Any]:
    from motor_ai_sim.routes import family as _fam
    return _fam.payload(die, cfg, duty=duty, authorization=None)


def rank_candidates(req: Dict[str, Any],
                    base: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Scale every visible saved duty (inside ``acting_as``)."""
    from motor_ai_sim import mcp_tools as _t
    ms = _t._machines()
    rows = []
    for m in ms:
        if base and (m["die"] != base.get("die") or m["config"] != base.get("config")):
            continue
        rpms = [d["speed_rpm"] for d in m["duties"] if d.get("speed_rpm")]
        max_rpm = max(rpms) if rpms else None
        for d in m["duties"]:
            if base and base.get("duty") and d["duty"] != base["duty"]:
                continue
            wind = None
            if d.get("line_voltage_peak_v") and req.get("dc_bus_v"):
                wind = _winding_of(m["die"], m["config"], d["duty"])
            s = _scale(req, m, d, wind, max_rpm)
            rows.append({"machine": m, "duty": d, "scale": s})
    ok = sorted((r for r in rows if r["scale"]["ok"]), key=lambda r: r["scale"]["score"])
    bad = [r for r in rows if not r["scale"]["ok"]]
    return {"ok": ok, "rejected": bad, "considered": len(rows)}


def _row_summary(r: Dict[str, Any]) -> Dict[str, Any]:
    m, d, s = r["machine"], r["duty"], r["scale"]
    out = {"die": m["die"], "config": m["config"], "duty": d["duty"],
           "outer_diameter_mm": m["outer_diameter_mm"],
           "base_active_length_mm": m["active_length_mm"],
           "base_torque_nm": d["torque_nm"], "base_speed_rpm": d["speed_rpm"]}
    if s.get("reasons"):
        out["reasons"] = s["reasons"]
    if s.get("ok"):
        out.update(stack_factor=_r(s["k_stack"]), current_factor=_r(s["k_current"]),
                   turns_factor=_r(s["k_turns"]), warnings=s["warnings"])
    return out


# ── start_design ─────────────────────────────────────────────────────────────

def start_design(p: Principal, requirements: Dict[str, Any],
                 base: Optional[Dict[str, str]] = None,
                 name: Optional[str] = None) -> Dict[str, Any]:
    from motor_ai_sim import mcp_tools as _t
    req, ask, assumptions = normalize_requirements(requirements or {})
    if base is not None:
        if not isinstance(base, dict) or not base.get("die") or not base.get("config"):
            raise DesignError("base must name {die, config[, duty]} from list_machines")
    if ask:
        return {"status": "needs_input", "needs_input": ask,
                "understood": {k: v for k, v in req.items() if v is not None},
                "assumptions": assumptions,
                "next": "ask the engineer for these fields, then call start_design "
                        "again with the complete requirements"}
    with _t.acting_as(p):
        ranked = rank_candidates(req, base)
        if base is not None and ranked["considered"] == 0:
            raise DesignError(f"machine '{base.get('die')} / {base.get('config')}"
                              f"{' / ' + base['duty'] if base.get('duty') else ''}' not found")
        if not ranked["ok"]:
            return _no_fit(req, ranked, assumptions)
        best = ranked["ok"][0]
        design = _make_draft(p, req, best, ranked, assumptions, name)
        _write(design)
    return _t._checked(public_view(design))


def _no_fit(req: Dict[str, Any], ranked: Dict[str, Any],
            assumptions: List[str]) -> Dict[str, Any]:
    """Nothing in reach: say what blocked it, as questions the engineer can
    answer (relax the envelope, change the bus, allow short duty)."""
    ask: List[Dict[str, Any]] = []
    rej = sorted(ranked["rejected"], key=lambda r: len(r["scale"]["reasons"]))
    blob = " ".join(x for r in rej for x in r["scale"]["reasons"])
    if "outer diameter" in blob and req.get("max_outer_diameter_mm"):
        ods = sorted({r["machine"]["outer_diameter_mm"] for r in rej
                      if r["machine"].get("outer_diameter_mm")})
        ask.append({"field": "max_outer_diameter_mm",
                    "why": "no die you can use is that small",
                    "options": ods[:6], "unit": "mm"})
    if "active length" in blob and req.get("max_length_mm"):
        ask.append({"field": "max_length_mm", "why": "the stack needed does not fit",
                    "unit": "mm"})
    if "current density" in blob:
        ask.append({"field": "duty", "why": "the torque needs more current density "
                    "than continuous duty allows on the machines in reach",
                    "options": ["S2", "S3", "peak"]})
    if "mass" in blob and req.get("max_mass_kg"):
        ask.append({"field": "max_mass_kg", "why": "estimated mass over the limit",
                    "unit": "kg"})
    if "voltage" in blob:
        ask.append({"field": "dc_bus_v", "why": "the back-EMF does not fit the bus "
                    "even with the fewest turns", "unit": "V"})
    return {"status": "no_fit", "needs_input": ask,
            "considered": ranked["considered"],
            "near_misses": [_row_summary(r) for r in rej[:5]],
            "assumptions": assumptions,
            "next": "tell the engineer what blocked it and ask which limit may "
                    "move; new lamination synthesis is not available yet"}


def _make_draft(p: Principal, req: Dict[str, Any], best: Dict[str, Any],
                ranked: Dict[str, Any], assumptions: List[str],
                name: Optional[str]) -> Dict[str, Any]:
    m, d, s = best["machine"], best["duty"], best["scale"]
    existing = list_designs(p.email)
    if len(existing) >= MAX_DRAFTS_PER_USER:
        raise DesignError(f"at most {MAX_DRAFTS_PER_USER} drafts per account — "
                          "delete old ones in Motors -> Agent drafts")
    pay = _payload(m["die"], m["config"], d["duty"])
    duty_raw = pay.get("duty") or {}
    gamma = _num(duty_raw.get("gamma_deg")) or 0.0
    wind = _winding_of(m["die"], m["config"], d["duty"]) or {}
    params = {
        "stack_mm": s["stack_mm"],
        "turns_factor": round(s["k_turns"], 4),
        "parallel_paths": s["parallel_paths"] or wind.get("n_parallel"),
        "connection": s["connection"] or (pay.get("winding") or {}).get("connection")
                      or pay["sim"].get("connection"),
        "current_a_rms": round(s["current_a"], 3),
        "speed_rpm": round(req["speed_rpm"], 1),
        "gamma_deg": gamma,
        "mode": req["mode"],
        "star_delta": pay["sim"].get("star_delta") or "star",
    }
    internal = {"conductors": s["conductors"] or wind.get("conductors"),
                "base_conductors": wind.get("conductors"),
                "strands": wind.get("strands"),
                "coils_per_phase": wind.get("coils_per_phase")}
    T_req = req["torque_nm"]
    estimate = {"torque_nm": _r(T_req), "power_kw": _r(req["power_kw"]),
                "speed_rpm": _r(req["speed_rpm"], 1),
                "line_voltage_peak_v": _r(s["v_peak_est"], 1),
                "mass_kg": _r(s["mass_est"]),
                "current_density_factor": _r(s["k_current"]),
                "method": "analytical scaling of a saved FEM duty (T ~ L*I, "
                          "EMF ~ L*N*n/p) — run simulate() for the FEM answer"}
    why = [f"nearest existing machine you can use: {m['die']} / {m['config']} / "
           f"duty '{d['duty']}' ({d['torque_nm']:g} N*m at {d['speed_rpm']:g} rpm, "
           f"OD {m['outer_diameter_mm']:g} mm)",
           f"stack {m['active_length_mm']:g} -> {s['stack_mm']:g} mm "
           f"(x{s['k_stack']:.2f}, allowed {STACK_FACTOR_RANGE[0]}-{STACK_FACTOR_RANGE[1]})",
           f"current density x{s['k_current']:.2f} of the base duty "
           f"(limit x{J_FACTOR_MAX.get(req['duty'], 1.3):g} for {req['duty']})"]
    if abs(s["k_turns"] - 1.0) > 1e-6:
        why.append(f"series turns x{s['k_turns']:.2f} ({params['connection']}) so the "
                   f"peak line voltage (~{s['v_peak_est'] or 0:.0f} V) fits "
                   f"{BUS_UTILISATION:.0%} of the {req['dc_bus_v']:g} V bus")
    did = "d-" + secrets.token_hex(6)
    now = _now()
    design = {
        "id": did, "version": 1, "owner": p.email, "status": "draft",
        "name": (name or "").strip()[:80] or
                f"{_fmt(T_req)} N*m @ {req['speed_rpm']:.0f} rpm ({m['die']})",
        "created_at": now, "updated_at": now,
        "created_by": {"kind": "agent", "client_name": p.client_name or "agent",
                       "credential_id": p.credential_id, "credential_kind": p.kind},
        "requirements": req, "assumptions": assumptions,
        "base": {"die": m["die"], "config": m["config"], "duty": d["duty"],
                 "outer_diameter_mm": m["outer_diameter_mm"],
                 "base_active_length_mm": m["active_length_mm"],
                 "base_torque_nm": d["torque_nm"], "base_speed_rpm": d["speed_rpm"],
                 "base_current_a_rms": d["phase_current_a_rms"],
                 "slots": m.get("slots"), "poles": m.get("poles"),
                 "magnet": m.get("magnet"), "core_steel": m.get("core_steel")},
        "why": why, "warnings": s["warnings"],
        "alternatives": [_row_summary(r) for r in ranked["ok"][1:4]],
        "params": params, "initial_params": json.loads(json.dumps(params)),
        "estimate": estimate, "initial_estimate": json.loads(json.dumps(estimate)),
        "internal": internal, "results": {}, "runs": [],
        "edited_by_user": False, "history": [{"at": now, "event": "created"}],
    }
    return design


def _fmt(x: float) -> str:
    return f"{x:.3g}"


# ── views ────────────────────────────────────────────────────────────────────

_PUBLIC_KEYS = ("id", "name", "status", "created_by", "requirements",
                "assumptions", "why", "warnings", "alternatives", "estimate",
                "edited_by_user")


def public_view(d: Dict[str, Any]) -> Dict[str, Any]:
    """What an agent may see of a draft — whitelist; ``internal`` never."""
    out = {k: d.get(k) for k in _PUBLIC_KEYS}
    out["design_id"] = d.get("id")
    out["created_at"] = _iso(d.get("created_at"))
    out["updated_at"] = _iso(d.get("updated_at"))
    b = d.get("base") or {}
    out["starting_point"] = {k: b.get(k) for k in (
        "die", "config", "duty", "outer_diameter_mm", "base_active_length_mm",
        "base_torque_nm", "base_speed_rpm", "slots", "poles", "magnet", "core_steel")}
    pr = d.get("params") or {}
    out["parameters"] = {"active_length_mm": pr.get("stack_mm"),
                         "series_turns_factor": pr.get("turns_factor"),
                         "connection": pr.get("connection"),
                         "phase_current_a_rms": pr.get("current_a_rms"),
                         "speed_rpm": pr.get("speed_rpm"),
                         "mode": pr.get("mode"),
                         "terminal_connection": pr.get("star_delta")}
    out["runs"] = [{k: r.get(k) for k in ("job_id", "what", "queued_at")}
                   for r in d.get("runs") or []]
    out["results_available"] = sorted((d.get("results") or {}).keys())
    out["open_in_configure"] = configure_url(str(d.get("id")))
    out["status"] = d.get("status") or "draft"
    out["status_is"] = "draft — nothing in your saved catalog or open machine was changed"
    return out


def web_view(d: Dict[str, Any]) -> Dict[str, Any]:
    """What the OWNER's web sees (same account, so the reference card id and
    the full parameter set are fine; still no ``internal`` block)."""
    out = public_view(d)
    out["params"] = d.get("params")
    out["initial_params"] = d.get("initial_params")
    out["results"] = d.get("results") or {}
    out["reference_motor_id"] = _reference_card(d)
    internal = d.get("internal") or {}
    # the owner's own account: the tuner needs the turn count it starts from
    out["build"] = {"conductors_per_slot": internal.get("conductors"),
                    "base_conductors_per_slot": internal.get("base_conductors")}
    return out


def _reference_card(d: Dict[str, Any]) -> Optional[str]:
    """The catalog card (with a Configure passport) of the draft's base
    machine, matched the way the passport code matches: "<die> <config>"."""
    try:
        from motor_ai_sim.routes import catalog as _cat
        b = d.get("base") or {}
        want = f"{b.get('die')} {b.get('config')}".casefold()
        want_die = str(b.get("die") or "").casefold()
        hit = None
        for m in (_cat._load() or {}).get("motors") or []:
            nm = str(m.get("name") or "").casefold()
            if not m.get("passport"):
                continue
            if nm == want:
                return str(m.get("id"))
            if nm == want_die and hit is None:
                hit = str(m.get("id"))
        return hit
    except Exception:                                   # noqa: BLE001
        return None


# ── the engineer's edits from Configure ─────────────────────────────────────

EDITABLE = {"stack_mm": (1.0, 2000.0), "current_a_rms": (0.01, 5000.0),
            "speed_rpm": (1.0, 100000.0), "gamma_deg": (-90.0, 90.0),
            "turns_factor": TURNS_FACTOR_RANGE}


def patch_params(design_id: str, owner: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    bad = sorted(set(changes or {}) - set(EDITABLE) - {"parallel_paths", "mode"})
    if bad:
        raise DesignError(f"not editable here: {bad}")
    clean: Dict[str, Any] = {}
    for k, v in (changes or {}).items():
        if k == "mode":
            if v not in MODES:
                raise DesignError(f"mode must be one of {list(MODES)}")
            clean[k] = v
            continue
        x = _num(v)
        if x is None:
            continue
        if k == "parallel_paths":
            clean[k] = int(x)
            continue
        lo, hi = EDITABLE[k]
        if not lo <= x <= hi:
            raise DesignError(f"{k} must be within [{lo:g}, {hi:g}]")
        clean[k] = x

    def _fn(d):
        pr = d["params"]
        internal = d.get("internal") or {}
        if "parallel_paths" in clean:
            c = int(internal.get("coils_per_phase") or 0)
            if c and c % clean["parallel_paths"]:
                raise DesignError(f"parallel_paths must divide {c}")
            if c:
                pr["connection"] = _conn_label(c, clean["parallel_paths"])
        if "turns_factor" in clean and internal.get("base_conductors"):
            wp = int(internal.get("strands") or 1)
            n0 = int(internal["base_conductors"])
            cond = max(wp, int(round(n0 * clean["turns_factor"] / wp)) * wp)
            internal["conductors"] = cond
            clean["turns_factor"] = round(cond / n0, 4)
        pr.update(clean)
        d["edited_by_user"] = True
        d["results"] = {}
        d.setdefault("history", []).append({"at": _now(), "event": "edited",
                                            "fields": sorted(clean)})
    return _update(design_id, owner, _fn)


# ── simulate: the SAME queue the web uses ───────────────────────────────────

def daily_limit(p: Principal) -> Optional[int]:
    """``None`` = unlimited (the owner / an admin account, or an email listed
    in ``MCP_SIMULATE_UNLIMITED``); otherwise ``MCP_SIMULATE_PER_DAY`` (5)."""
    from motor_ai_sim import auth as _auth
    email = (p.email or "").strip().lower()
    extra = {e.strip().lower() for e in os.environ.get("MCP_SIMULATE_UNLIMITED", "").split(",")
             if e.strip()}
    if email in extra or email == _auth.ADMIN_OWNER or email in _auth._ADMIN_EMAILS:
        return None
    try:
        return max(0, int(os.environ.get("MCP_SIMULATE_PER_DAY", "5")))
    except ValueError:
        return 5


def _agent_jobs_today(p: Principal) -> int:
    """Agent-submitted runs of this account queued since UTC midnight, counted
    from the job-queue store itself (survives a restart)."""
    from motor_ai_sim import jobs as _jobs
    midnight = (int(_now() // 86400)) * 86400
    try:
        recs = _jobs.queue().list_for_owner(p.email, limit=500)
    except NotImplementedError:
        return 0
    return sum(1 for r in recs if isinstance(r.body, dict) and r.body.get("agent")
               and (r.queued_at or 0) >= midnight)


def check_quota(p: Principal) -> Tuple[bool, int, int, Optional[int]]:
    """``(ok, retry_after_s, used, limit)`` — runs inside ``acting_as``."""
    lim = daily_limit(p)
    if lim is None:
        return True, 0, 0, None
    from motor_ai_sim import mcp_tools as _t
    with _t.acting_as(p):
        used = _agent_jobs_today(p)
    if used >= lim:
        retry = max(1, int((int(_now() // 86400) + 1) * 86400 - _now()))
        return False, retry, used, lim
    return True, 0, used, lim


#: ``what`` -> runner(design, sandbox_ws, steps, run_id) -> solver answer.
#: Module-level so tests replace the solver with a stub (no FEM in the suite).
SIM_RUNNERS: Dict[str, Callable[..., Any]] = {}


def _sandbox_workspace(d: Dict[str, Any]):
    """A Workspace of the draft's own: its root is ``<draft>/sandbox``, its id
    is derived from the user's, and its machine is the DRAFT — never the live
    ``motor_config.yaml``."""
    from motor_ai_sim import workspace as _ws
    user_ws = _ws.workspace()
    root = designs_root() / d["id"] / SANDBOX_DIR
    root.mkdir(parents=True, exist_ok=True)
    return _ws.Workspace(id=f"{user_ws.id}.{d['id']}", email=user_ws.email,
                         root=root, shared_root=user_ws.shared_root)


def build_sandbox_config(d: Dict[str, Any], ws) -> Path:
    """Write ``<draft>/sandbox/motor_config.yaml`` from the draft.

    The template is the user's config file READ ONLY (schema, mesh defaults);
    every machine section is replaced from the draft's base die/config/duty
    plus the draft parameters.  The template file is never written."""
    import yaml
    from motor_ai_sim import workspace as _wsm
    from motor_ai_sim.routes import family as _fam
    src = Path(str(_wsm.config_file()))
    tpl = yaml.safe_load(src.read_text(encoding="utf-8")) if src.is_file() else {}
    tpl = tpl if isinstance(tpl, dict) else {}
    b, pr, internal = d["base"], d["params"], d.get("internal") or {}
    pay = _payload(b["die"], b["config"], b["duty"])
    geo = dict(tpl.get("geometry") or {})
    geo.update(pay.get("geometry") or {})
    geo["motor_length"] = float(pr["stack_mm"])
    n0 = internal.get("base_conductors")
    n1 = internal.get("conductors")
    if n0 and n1 and int(n0) != int(n1):
        geo["num_wires_per_slot"] = int(n1)
        # constant slot fill: fewer conductors -> each proportionally taller
        if geo.get("wire_height"):
            geo["wire_height"] = round(float(geo["wire_height"]) * int(n0) / int(n1), 4)
    wind = dict(tpl.get("winding") or {})
    wind.update(pay.get("winding") or {})
    c = int(internal.get("coils_per_phase") or wind.get("n_coils_per_phase") or 0)
    par = int(pr.get("parallel_paths") or wind.get("n_parallel") or 1)
    if c:
        wind.update(n_coils_per_phase=c, n_parallel=par, n_series=max(1, c // par),
                    connection=pr.get("connection") or _conn_label(c, par))
    sim = dict(tpl.get("simulation") or {})
    poles = int(geo.get("num_poles") or 0)
    rpm = float(pr["speed_rpm"])
    sim.update(current_a=float(pr["current_a_rms"]),
               max_current=float(pr["current_a_rms"]) * math.sqrt(2.0),
               rpm=rpm, gamma_deg=float(pr.get("gamma_deg") or 0.0),
               phase_offset_deg=float(pr.get("gamma_deg") or 0.0),
               mode=pr.get("mode") or "motor", drive="current",
               connection=wind.get("connection"),
               star_delta=pr.get("star_delta") or "star")
    if poles:
        sim["frequency"] = round(rpm * poles / 120.0, 4)
    for k in ("daxis_deg", "end_winding_factor"):
        v = (pay.get("sim") or {}).get(k)
        if v is not None:
            sim[k] = v
    mesh = dict(tpl.get("mesh") or {})
    mesh.update(_fam.duty_mesh_patch(pay.get("duty")))
    doc = dict(tpl)
    doc.update(geometry=geo, winding=wind, simulation=sim, mesh=mesh,
               materials=dict(pay.get("materials") or tpl.get("materials") or {}))
    cf = Path(str(ws.config_file))
    cf.parent.mkdir(parents=True, exist_ok=True)
    tmp = cf.with_name(cf.name + ".tmp")
    tmp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    os.replace(str(tmp), str(cf))
    from motor_ai_sim import config as _cfg
    _cfg.clear_config_cache(cf)
    return cf


def _solve_kwargs(d: Dict[str, Any], ws, steps: Optional[int], run_id: str) -> Dict[str, Any]:
    import yaml
    pr = d["params"]
    doc = yaml.safe_load(Path(str(ws.config_file)).read_text(encoding="utf-8")) or {}
    mesh = doc.get("mesh") or {}
    kw: Dict[str, Any] = {
        "I_phase_rms": float(pr["current_a_rms"]), "rpm": float(pr["speed_rpm"]),
        "gamma_deg": float(pr.get("gamma_deg") or 0.0), "mode": pr.get("mode") or "motor",
        "n_parallel": int(pr.get("parallel_paths") or 1),
        "connection": pr.get("connection"), "star_delta": pr.get("star_delta") or "star",
        "run_id": run_id,
    }
    for src, dst, typ in (("mesh_size_mm", "mesh_size_mm", float),
                          ("min_size_mm", "min_size_mm", float),
                          ("outer_air_factor", "outer_air_factor", float),
                          ("gap_layers", "gap_layers", float),
                          ("n_sectors", "n_sectors", int)):
        if mesh.get(src) is not None:
            try:
                kw[dst] = typ(mesh[src])
            except (TypeError, ValueError):
                pass
    if steps:
        kw["n_steps_per_period"] = int(steps)
    return kw


def _thermal_settings(req: Dict[str, Any]) -> Dict[str, Any]:
    """The Thermal tab's field names for the requested cooling."""
    cool = req.get("cooling") or "air"
    s: Dict[str, Any] = {"coolMode": cool, "ambientT": str(req.get("ambient_c") or 40.0)}
    if cool == "liquid":
        s.update(fluid="water", flowLpm="8")
    if cool == "air":
        s.update(airSpeed="0")
    return s


def _run_em(d, ws, steps, run_id):
    from motor_ai_sim import run_recording as _rr
    from motor_ai_sim.routes import simulation as _sim
    kw = _solve_kwargs(d, ws, steps, run_id)
    kw["ledger"] = False
    with _rr.no_record():
        return _sim.get_fem_transient(**kw)


def _run_coupled(d, ws, steps, run_id, max_iter=None):
    from motor_ai_sim.routes import coupled as _co
    body = _solve_kwargs(d, ws, steps, run_id)
    body["thermal_settings"] = _thermal_settings(d.get("requirements") or {})
    body["fresh"] = True
    if max_iter:
        body["max_iter"] = int(max_iter)
    return _co.run(body=body, authorization=None)


SIM_RUNNERS.update({
    "em": _run_em,
    "thermal": lambda d, ws, steps, rid: _run_coupled(d, ws, steps, rid, max_iter=1),
    "coupled": _run_coupled,
})


def _pick(obj: Any, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if obj.get(k) is not None:
            return obj.get(k)
    return None


def headline(answer: Any, d: Dict[str, Any], what: str) -> Dict[str, Any]:
    """Whitelisted headline of a solver answer (transient or coupled)."""
    a = answer if isinstance(answer, dict) else {}
    s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
    em = a.get("em") if isinstance(a.get("em"), dict) else {}
    src = {**s, **{k: v for k, v in em.items() if v is not None}} if (s or em) else a
    T = _num(_pick(src, "T_em_avg_Nm"))
    rpm = float(d["params"]["speed_rpm"])
    w = rpm * math.pi / 30.0
    p_mech = _num(_pick(src, "P_mech_W")) or ((T or 0) * w if T is not None else None)
    p_loss = _num(_pick(src, "P_loss_total_W"))
    p_extra = _num(_pick(src, "P_mech_extra_W"))
    eff = _num(_pick(src, "efficiency"))
    eff_pct = None if eff is None else (eff * 100.0 if eff <= 1.5 else eff)
    shaft_eta = None
    if p_mech is not None and p_loss is not None:
        p_shaft = p_mech - (p_extra or 0.0)
        denom = p_mech + p_loss if d["params"].get("mode") != "generator" else p_shaft
        if denom and denom > 0:
            shaft_eta = (p_shaft / denom * 100.0 if d["params"].get("mode") != "generator"
                         else (p_mech - p_loss) / p_shaft * 100.0 if p_shaft else None)
    out = {
        "what": what, "computed_at": _iso(),
        "torque_nm": _r(T), "speed_rpm": _r(rpm, 1),
        "power_kw": _r(p_mech / 1000.0 if p_mech is not None else None),
        "efficiency_shaft_pct": _r(shaft_eta, 2),
        "efficiency_electromagnetic_pct": _r(eff_pct, 2),
        "mechanical_losses_included": p_extra is not None,
        "line_voltage_peak_v": _r(_num(_pick(src, "V_line_peak_V")), 1),
        "phase_current_a_rms": _r(_num(_pick(src, "I1_phase_rms_A", "I_phase_rms_A"))
                                  or d["params"]["current_a_rms"]),
        "torque_ripple_pct": _r(_num(_pick(src, "T_ripple_pct")), 2),
        "losses_w": {"copper": _r(_num(_pick(src, "P_stranded_W")), 1),
                     "core": _r(_num(_pick(src, "P_core_W")), 1),
                     "magnets": _r(_num(_pick(src, "P_mag_W")), 1),
                     "solid_parts": _r(_num(_pick(src, "P_solid_W")), 1),
                     "mechanical": _r(p_extra, 1),
                     "total": _r(p_loss, 1)},
        "mass_kg": (d.get("estimate") or {}).get("mass_kg"),
        "mass_is_estimate": True,
    }
    if what in ("thermal", "coupled"):
        out["temperatures_c"] = {"coil": _r(_num(a.get("coil_temp_c")), 1),
                                 "magnet": _r(_num(a.get("magnet_temp_c")), 1),
                                 "magnet_max": _r(_num(a.get("magnet_temp_max_c")), 1),
                                 "bearing": _r(_num(a.get("bearing_temp_c")), 1)}
        out["thermally_converged"] = a.get("converged")
        out["runaway"] = a.get("runaway")
        out["cooling"] = (d.get("requirements") or {}).get("cooling")
        out["ambient_c"] = (d.get("requirements") or {}).get("ambient_c")
        if isinstance(a.get("warning"), str):
            out["warning"] = a["warning"][:300]
        ttl = a.get("time_to_limit")
        if isinstance(ttl, dict):
            out["limits"] = {"within_limits": ttl.get("within_limits"),
                             "time_to_limit_s": _r(_num(ttl.get("cold_s")), 1),
                             "limiting_part": ttl.get("part")}
    return out


def simulate(p: Principal, design_id: str, what: str,
             steps: Optional[int] = None) -> Dict[str, Any]:
    """Queue a solve of the draft on the user's queue; return the job id."""
    from motor_ai_sim import jobs as _jobs
    from motor_ai_sim import mcp_tools as _t
    what = str(what or "").strip().lower()
    if what not in WHATS:
        raise DesignError(f"what must be one of {list(WHATS)}")
    if steps is not None and not STEPS_RANGE[0] <= int(steps) <= STEPS_RANGE[1]:
        raise DesignError("steps (per electrical period) must be within [12, 360]")
    ok, retry, used, lim = check_quota(p)
    if not ok:
        raise QuotaExceeded(used, lim or 0, retry)
    with _t.acting_as(p):
        d = _read(design_id, p.email)
        agent = {"client_name": p.client_name or "agent",
                 "credential_id": p.credential_id, "kind": p.kind}
        body = {"agent": agent, "design_id": d["id"], "what": what,
                "steps": int(steps) if steps else None}
        rec = _jobs.make_record(f"agent.{what}", priority=_jobs.Priority.DUTY,
                                body=body)
        rid = rec.run_id
        principal = p
        did = d["id"]

        def work():
            return _run_job(principal, did, what, steps, rid)

        try:
            _jobs.queue().submit(rec, work, block=False)
        except _jobs.JobAccepted as acc:
            position = acc.record.position
        else:                                           # pragma: no cover
            position = 0

        def _fn(x):
            x.setdefault("runs", []).append({"job_id": rid, "what": what,
                                             "queued_at": _iso(), "agent": agent})
            x["runs"] = x["runs"][-50:]
        _update(did, p.email, _fn)
    return {"job_id": rid, "design_id": did, "what": what, "state": "queued",
            "position": position,
            "quota": {"used_today": used + 1 if lim is not None else None,
                      "per_day": lim},
            "next": "poll get_job(job_id) every 10-30 s; then get_design_result(design_id)"}


def _run_job(p: Principal, design_id: str, what: str, steps: Optional[int],
             run_id: str) -> Dict[str, Any]:
    """The queued work — runs on a queue worker thread."""
    from motor_ai_sim import mcp_tools as _t
    from motor_ai_sim import workspace as _ws
    with _t.acting_as(p):
        d = _read(design_id, p.email)
        sb = _sandbox_workspace(d)
        build_sandbox_config(d, sb)
    with _t.acting_as(p):
        with _ws.use_workspace(sb):
            answer = SIM_RUNNERS[what](d, sb, steps, run_id)
    head = headline(answer, d, what)
    head["job_id"] = run_id
    _t.assert_no_geometry(head)
    with _t.acting_as(p):
        def _fn(x):
            x.setdefault("results", {})[what] = head
        _update(design_id, p.email, _fn)
    return {"ok": True, "design_id": design_id, "what": what}


def get_job(p: Principal, job_id: str) -> Dict[str, Any]:
    from motor_ai_sim import jobs as _jobs
    from motor_ai_sim import mcp_tools as _t
    from motor_ai_sim import progress as _prog
    with _t.acting_as(p):
        rec = _jobs.queue().status(str(job_id or ""))
    if rec is None or str(rec.owner) != str(p.email):
        raise DesignError(f"job '{job_id}' not found")
    body = rec.body if isinstance(rec.body, dict) else {}
    out: Dict[str, Any] = {"job_id": rec.run_id, "kind": rec.kind, "state": rec.state,
                           "position": rec.position or None,
                           "design_id": body.get("design_id"),
                           "what": body.get("what"),
                           "submitted_by_agent": bool(body.get("agent")),
                           "queued_at": _iso(rec.queued_at) if rec.queued_at else None,
                           "started_at": _iso(rec.started_at) if rec.started_at else None,
                           "finished_at": _iso(rec.finished_at) if rec.finished_at else None}
    pub = rec.public()
    out["elapsed_s"] = pub.get("elapsed_s")
    out["waited_s"] = pub.get("waited_s")
    e = _prog.registry().entry(rec.run_id)
    if e is not None:
        snap = e.snapshot() or {}
        tot = snap.get("total") or 0
        out["progress"] = {"step": snap.get("step"), "total": tot,
                           "pct": round(100.0 * (snap.get("step") or 0) / tot, 1) if tot else None,
                           "eta_s": snap.get("eta_s"), "phase": snap.get("phase")}
        if rec.state == "done":
            # the last solver tick is often before post-processing ends
            # (seen 2026-09-29: done at 23/24 = 95.8 %) — a finished job is 100 %
            out["progress"].update(step=tot or snap.get("step"), pct=100.0,
                                   eta_s=0.0, phase="done")
    if rec.error:
        out["error"] = _clean_error(rec.error)
    if rec.state == "done":
        out["next"] = "call get_design_result(design_id)"
    elif rec.state in ("queued", "running"):
        out["next"] = "poll again in 10-30 s"
    return _t._checked(out)


def _clean_error(err: str) -> str:
    s = str(err or "")
    m = re.search(r"detail=['\"]?(.+?)['\"]?\)?$", s)
    s = m.group(1) if m else s
    return s[:400]


def get_design(p: Principal, design_id: str) -> Dict[str, Any]:
    from motor_ai_sim import mcp_tools as _t
    with _t.acting_as(p):
        d = _read(design_id, p.email)
    return _t._checked(public_view(d))


def get_design_result(p: Principal, design_id: str) -> Dict[str, Any]:
    from motor_ai_sim import mcp_tools as _t
    with _t.acting_as(p):
        d = _read(design_id, p.email)
    res = d.get("results") or {}
    out = {"design_id": d["id"], "name": d.get("name"),
           "requirements": d.get("requirements"),
           "estimate": d.get("estimate"),
           "results": res,
           "status": ("simulated" if res else "not simulated yet — call simulate()"),
           "source": "2-D FEM (energy torque) of the draft in its own sandbox; "
                     "mass is the scaled estimate",
           "open_in_configure": configure_url(d["id"])}
    best = res.get("coupled") or res.get("thermal") or res.get("em")
    if best:
        req = d.get("requirements") or {}
        checks = {}
        if best.get("torque_nm") is not None and req.get("torque_nm"):
            checks["torque_met"] = best["torque_nm"] >= 0.98 * req["torque_nm"]
        if best.get("line_voltage_peak_v") is not None and req.get("dc_bus_v"):
            checks["voltage_fits_bus"] = best["line_voltage_peak_v"] <= req["dc_bus_v"]
        out["requirement_checks"] = checks
    return _t._checked(out)


def open_in_configure(p: Principal, design_id: str) -> Dict[str, Any]:
    from motor_ai_sim import mcp_tools as _t
    with _t.acting_as(p):
        d = _read(design_id, p.email)
    return {"design_id": d["id"], "url": configure_url(d["id"]),
            "note": "give this link to the engineer: it opens the draft in "
                    "Configure; his open machine is not replaced until he clicks "
                    "Open there"}
