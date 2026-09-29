"""MCP Stage 1 tools — read-only views for external AI agents.

Pure functions over the stores the web already reads; the MCP transport and
auth live in ``mcp_app``.  Every function takes the caller's ``Principal`` and
runs inside :func:`acting_as`, which makes every in-process permission check
(``motor_access.catalog_access``, ``routes.family._require_die_access``, the
per-user workspace) answer exactly as the owner's own web session would.

PUBLIC-DATASHEET RULE: a machine is described by what a datasheet prints —
ratings, efficiency, loss split, temperatures, outer diameter, active length,
mass, slot/pole count, materials by name.  NEVER stator/rotor dimensions,
winding geometry, thumbnails or build signatures.  Every machine payload is
built from an explicit whitelist, and ``assert_no_geometry`` re-checks the
result against ``GEOMETRY_DENYLIST`` before it leaves the server.
"""
from __future__ import annotations

import math
from contextlib import ExitStack, contextmanager
from typing import Any, Dict, Iterator, List, Optional

from motor_ai_sim.agent_keys import Principal

CATALOG_KINDS = ("magnets", "steels", "wires", "bearings", "devices", "dies")

#: Keys that must never appear anywhere in a tool answer (die geometry, winding
#: build, drawings, internal signatures, material curves).
GEOMETRY_DENYLIST = frozenset({
    "geometry", "geometry_overrides", "thumb_svg", "package_svg", "svg",
    "build_sig", "assignment_sig", "geometry_fingerprint",
    "slot_height", "core_thickness", "air_gap", "tooth_width", "tooth2_width",
    "cut_width", "insulation_thickness", "wire_width", "wire_height",
    "wire_width_mm", "wire_height_mm", "wire_spacing_x", "wire_spacing_y",
    "num_wires_per_slot", "turns", "wire_split", "slot_hs", "magnet_height",
    "rotor_house_height", "shaft_height", "magnet_fill_down", "magnet_fill_up",
    "magnet_fill_radius", "magnet_up_gap", "rotor_hole", "magnet_down_height",
    "magnet_lamination", "stator_fillet_r", "stator_fillet_r1", "rotor_fill_r",
    "stator_inner_radius", "rotor_outer_radius", "rotor_inner_radius",
    "slot_width", "shaft_diameter", "slot_pitch", "pole_pitch", "angle_slot",
    "angle_pole", "daxis_deg", "bh_curve", "core_loss_curves", "payload",
    "payload_file", "gamma_deg", "file", "dir",
})


class ToolError(ValueError):
    """A caller mistake (unknown kind/id, not visible) — reported to the agent."""


# ── identity ─────────────────────────────────────────────────────────────────

@contextmanager
def acting_as(principal: Principal) -> Iterator[None]:
    from motor_ai_sim import auth as _auth
    from motor_ai_sim import workspace as _ws
    user = _auth.agent_user_for(principal.email)
    if user is None:
        raise ToolError("account disabled")
    with ExitStack() as st:
        st.enter_context(_auth.use_agent_user(user))
        ident = _auth.caller_identity(None)
        if _ws.workspaces_root() is not None:
            who = str(ident.get("id") or "")
            ws = (_ws.process_workspace()
                  if who in (_auth.ANON_OWNER, _auth.ADMIN_OWNER, "")
                  else _ws.workspace_for_identity(who))
            st.enter_context(_ws.use_workspace(ws))
        st.enter_context(_ws.use_caller(ident))
        yield


# ── helpers ──────────────────────────────────────────────────────────────────

def _f(v: Any, nd: int = 3) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def _match(q: Optional[str], *texts: Any) -> bool:
    if not q:
        return True
    ql = q.strip().lower()
    return any(ql in str(t or "").lower() for t in texts)


def assert_no_geometry(obj: Any, path: str = "$") -> None:
    """Raise if any key of `obj` (recursively) is on the denylist."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in GEOMETRY_DENYLIST:
                raise AssertionError(f"geometry field leaked: {path}.{k}")
            assert_no_geometry(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_geometry(v, f"{path}[{i}]")


def _checked(obj: Any) -> Any:
    assert_no_geometry(obj)
    return obj


# ── catalog: materials, wires, bearings, devices ────────────────────────────

_MAGNET_KEYS = {"grade": "grade", "description": "description",
                "Br": "br_t", "Hc": "hc_a_per_m", "mu_rec": "mu_rec",
                "temperature_c": "card_temperature_c",
                "max_working_temp_c": "max_working_temp_c",
                "alpha_br_pct_per_k": "alpha_br_pct_per_k",
                "beta_hcj_pct_per_k": "beta_hcj_pct_per_k",
                "density": "density_kg_m3", "sigma": "conductivity_s_per_m",
                "tensile_strength_mpa": "tensile_strength_mpa",
                "thermal_conductivity": "thermal_conductivity_w_mk"}
_STEEL_KEYS = {"description": "description", "form": "form",
               "density": "density_kg_m3", "sigma": "conductivity_s_per_m",
               "stacking_factor": "stacking_factor",
               "thickness_mm": "lamination_thickness_mm",
               "yield_strength_mpa": "yield_strength_mpa",
               "youngs_modulus_gpa": "youngs_modulus_gpa",
               "core_loss_model": "core_loss_model",
               "thermal_conductivity": "thermal_conductivity_w_mk"}


def _materials(category: str) -> Dict[str, dict]:
    from motor_ai_sim import materials as _m
    lib = _m._load() or {}
    builtin = {c: dict(v or {}) for c, v in lib.items() if isinstance(v, dict)}
    try:
        from motor_ai_sim import materials_store as _ms
        merged = _ms.merge_library(builtin)
    except Exception:                                   # noqa: BLE001
        merged = builtin
    return {n: (d or {}) for n, d in (merged.get(category) or {}).items()
            if not (d or {}).get("hidden")}


def _mat_row(name: str, raw: dict, keymap: Dict[str, str]) -> dict:
    row: Dict[str, Any] = {"id": name}
    for src, dst in keymap.items():
        v = raw.get(src)
        if v is None:
            continue
        row[dst] = v if isinstance(v, str) else _f(v, 6)
    row["provenance"] = {"source": "eMotres materials library",
                         "has_bh_curve": bool(raw.get("bh_curve")),
                         "has_core_loss_curves": bool(raw.get("core_loss_curves"))}
    return row


def _wires() -> List[dict]:
    import yaml
    from motor_ai_sim import workspace as _ws
    p = _ws.shared_root() / "wire_stock.yaml"
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:                                   # noqa: BLE001
        doc = {}
    out = []
    for w in doc.get("wires") or []:
        out.append({"id": str(w.get("code")), "spec": w.get("spec"),
                    "thickness_mm": _f(w.get("thickness_mm")),
                    "width_mm": _f(w.get("width_mm")),
                    "insulation": w.get("insulation"),
                    "self_bonding": bool(w.get("self_bonding")),
                    "in_stock": bool((w.get("stock_kg") or 0) > 0),
                    "provenance": {"source": "warehouse stock table",
                                   "updated": doc.get("updated"),
                                   "label_ambiguous": bool(w.get("check"))}})
    return out


_BEARING_KEYS = ("description", "type", "d", "D", "B", "contact_angle_deg",
                 "balls", "seals", "default_lubricant", "n_limit_grease_rpm",
                 "n_limit_oil_air_rpm", "C_kn", "C0_kn")
_BEARING_RENAME = {"d": "bore_mm", "D": "outer_diameter_mm", "B": "width_mm",
                   "C_kn": "dynamic_load_rating_kn", "C0_kn": "static_load_rating_kn"}


def _bearings() -> List[dict]:
    from motor_ai_sim import bearings as _b
    lib = _b.library()
    out = []
    for n, card in (lib.get("bearings") or {}).items():
        row: Dict[str, Any] = {"id": n}
        for k in _BEARING_KEYS:
            if card.get(k) is not None:
                row[_BEARING_RENAME.get(k, k)] = card.get(k)
        row["provenance"] = {"source": lib.get("source"),
                             "note": card.get("note")}
        out.append(row)
    return out


_DEVICE_KEYS = ("manufacturer", "family", "technology", "package",
                "package_common_name", "cooling", "v_dss_V", "i_d_100c_A",
                "i_d_25c_A", "t_j_max_c", "r_ds_on_25c_mohm",
                "r_ds_on_175c_mohm", "r_th_jc_k_w")


def _devices() -> List[dict]:
    from motor_ai_sim.inverter import devices as _d
    out = []
    for r in _d.list_devices():
        row: Dict[str, Any] = {"id": r.get("part")}
        for k in _DEVICE_KEYS:
            if r.get(k) is not None:
                row[k] = r.get(k)
        prov: Dict[str, Any] = {}
        try:
            p = _d.get_device(str(r.get("part"))).provenance() or {}
            prov = {k: p.get(k) for k in ("datasheet", "datasheet_revision")
                    if p.get(k)}
        except Exception:                               # noqa: BLE001
            pass
        spice = r.get("spice") if isinstance(r.get("spice"), dict) else {}
        prov["spice_model_status"] = spice.get("model_status")
        row["provenance"] = prov
        out.append(row)
    return out


def _visible_tree() -> List[dict]:
    """The caller's catalog tree — the SAME function the web's Motors tab calls."""
    from fastapi import Response
    from motor_ai_sim.routes import family as _fam
    return list((_fam.tree(Response(), authorization=None) or {}).get("dies") or [])


def _ownership(die: dict, me: str) -> str:
    layer = die.get("layer")
    if layer is None or layer == "shared":
        return "catalog"
    if layer == "published":
        return "own_published" if die.get("owner") == me else "published"
    return "own"


def _dies_rows() -> List[dict]:
    rows = []
    for d in _visible_tree():
        rows.append({"id": d.get("name"),
                     "outer_diameter_mm": _f(d.get("stator_diameter")),
                     "slots": d.get("slots"), "poles": d.get("poles"),
                     "configurations": [c.get("name") for c in d.get("configs") or []],
                     "provenance": {"source": "eMotres die catalog",
                                    "layer": d.get("layer") or "catalog"}})
    return rows


def list_catalog(p: Principal, kind: str, query: Optional[str] = None) -> Dict[str, Any]:
    kind = (kind or "").strip().lower()
    if kind not in CATALOG_KINDS:
        raise ToolError(f"kind must be one of {list(CATALOG_KINDS)}")
    with acting_as(p):
        if kind == "magnets":
            rows = [_mat_row(n, r, _MAGNET_KEYS) for n, r in _materials("magnet").items()]
        elif kind == "steels":
            rows = [_mat_row(n, r, _STEEL_KEYS) for n, r in _materials("steel").items()]
        elif kind == "wires":
            rows = _wires()
        elif kind == "bearings":
            rows = _bearings()
        elif kind == "devices":
            rows = _devices()
        else:
            rows = _dies_rows()
    rows = [r for r in rows if _match(query, r.get("id"), r.get("description"),
                                      r.get("spec"), r.get("grade"),
                                      r.get("manufacturer"), r.get("family"),
                                      r.get("type"))]
    return _checked({"kind": kind, "count": len(rows), "entries": rows})


def get_catalog_entry(p: Principal, kind: str, id: str) -> Dict[str, Any]:
    res = list_catalog(p, kind)
    for r in res["entries"]:
        if str(r.get("id")) == str(id):
            return _checked({"kind": res["kind"], "entry": r})
    raise ToolError(f"no {res['kind']} entry '{id}' visible to this key")


# ── machines ─────────────────────────────────────────────────────────────────

def _battery_v(cfg: dict) -> Dict[str, Optional[float]]:
    b = cfg.get("battery") if isinstance(cfg.get("battery"), dict) else {}
    return {"dc_bus_nominal_v": _f(b.get("v_nom")), "dc_bus_min_v": _f(b.get("v_min")),
            "dc_bus_max_v": _f(b.get("v_max"))}


def _duty_headline(d: dict) -> Dict[str, Any]:
    r = d.get("result") if isinstance(d.get("result"), dict) else {}
    return {"duty": d.get("name"), "mode": d.get("mode") or "motor",
            "speed_rpm": _f(d.get("rpm"), 1),
            "torque_nm": _f(d.get("torque_nm")), "power_kw": _f(d.get("power_kw")),
            "phase_current_a_rms": _f(d.get("current_arms")),
            "efficiency_pct": _f(r.get("efficiency_pct"), 2),
            "line_voltage_peak_v": _f(r.get("v_ll_peak_v"), 1),
            "mass_kg": _f(r.get("mass_kg")),
            "connection": d.get("star_delta") or None,
            "saved_at": d.get("saved_at")}


def _machines() -> List[Dict[str, Any]]:
    from motor_ai_sim import auth as _auth
    me = str(_auth.caller_identity(None).get("id") or "")
    out = []
    for die in _visible_tree():
        for c in die.get("configs") or []:
            duties = [_duty_headline(d) for d in c.get("duties") or []]
            masses = [x["mass_kg"] for x in duties if x["mass_kg"] is not None]
            out.append({
                "die": die.get("name"), "config": c.get("name"),
                "ownership": _ownership(die, me),
                "role": c.get("role"),
                "outer_diameter_mm": _f(die.get("stator_diameter")),
                "active_length_mm": _f(c.get("stack_mm")),
                "mass_kg": masses[0] if masses else None,
                "slots": die.get("slots"), "poles": die.get("poles"),
                "magnet": c.get("magnet"), "core_steel": c.get("steel"),
                **_battery_v(c),
                "duties": duties,
            })
    return out


def list_machines(p: Principal) -> Dict[str, Any]:
    with acting_as(p):
        ms = _machines()
    return _checked({"count": len(ms), "machines": ms,
                     "note": "headline numbers are saved simulation results; "
                             "call get_machine_performance for one duty"})


def _find_cooling(obj: Any, depth: int = 0) -> Optional[str]:
    if depth > 4:
        return None
    if isinstance(obj, dict):
        v = obj.get("cooling_mode")
        if isinstance(v, str) and v:
            return v
        for x in obj.values():
            r = _find_cooling(x, depth + 1)
            if r:
                return r
    return None


def _performance(die_name: str, cfg_name: str, duty_name: str) -> Dict[str, Any]:
    from motor_ai_sim import auth as _auth
    me = str(_auth.caller_identity(None).get("id") or "")
    die = next((d for d in _visible_tree() if d.get("name") == die_name), None)
    cfg = next((c for c in (die or {}).get("configs") or []
                if c.get("name") == cfg_name), None)
    duty = next((d for d in (cfg or {}).get("duties") or []
                 if d.get("name") == duty_name), None)
    if duty is None:
        # Same answer for "does not exist" and "not yours" — as the web's 404.
        raise ToolError(f"machine '{die_name} / {cfg_name} / {duty_name}' not found")
    r = duty.get("result") if isinstance(duty.get("result"), dict) else {}
    from motor_ai_sim import duty_results as _dr
    stored = ((_dr.index(die_name).get(cfg_name) or {}).get(duty_name) or {})
    coupled = stored.get("coupled") if isinstance(stored.get("coupled"), dict) else {}
    head = _duty_headline(duty)
    losses = {"total_electromagnetic_w": _f(r.get("loss_w"), 1),
              "core_w": _f(r.get("p_core_w"), 1),
              "copper_w": _f(r.get("p_stranded_w"), 1),
              "solid_parts_eddy_w": _f(r.get("p_solid_w"), 1),
              "mechanical_w": _f(r.get("loss_mech_w"), 1)}
    temps = {"coil_c": _f(coupled.get("coil_temp_c"), 1),
             "magnet_c": _f(coupled.get("magnet_temp_c"), 1),
             "magnet_max_c": _f(coupled.get("magnet_temp_max_c"), 1),
             "bearing_c": _f(coupled.get("bearing_temp_c"), 1),
             "thermally_converged": coupled.get("converged"),
             "cooling": _find_cooling(stored)}
    cr = duty.get("continuous_rating") or {}
    ttl = duty.get("time_to_limit") or {}
    ratings = {
        "continuous_current_a_rms": _f(cr.get("i_cont_A")),
        "continuous_torque_nm": _f(cr.get("torque_Nm")),
        "continuous_limiting_part": cr.get("part"),
        "continuous_cooling": cr.get("cooling_label"),
        "time_to_limit_from_cold_s": _f(ttl.get("cold_s"), 1),
        "time_to_limit_from_rated_s": _f(ttl.get("rated_s"), 1),
        "time_to_limit_part": ttl.get("part"),
        "kv_rpm_per_v": _f(r.get("kv_rpm_per_v"), 2),
        "kv_is_noload": (None if r.get("kv_is_noload") is None
                         else bool(r.get("kv_is_noload"))),
        "torque_ripple_pct": _f(r.get("ripple_pct"), 2),
        "current_density_a_mm2": _f(r.get("j_coil_a_mm2"), 2),
        "end_effect_3d_factor": _f(r.get("end3d_k"), 4),
    }
    return {
        "die": die_name, "config": cfg_name, "duty": duty_name,
        "ownership": _ownership(die, me),
        "outer_diameter_mm": _f(die.get("stator_diameter")),
        "active_length_mm": _f(cfg.get("stack_mm")),
        "slots": die.get("slots"), "poles": die.get("poles"),
        "magnet": cfg.get("magnet"), "core_steel": cfg.get("steel"),
        **_battery_v(cfg),
        "operating_point": head,
        "losses": losses, "temperatures": temps, "ratings": ratings,
        "duty_cycle": duty.get("duty_cycle") if isinstance(duty.get("duty_cycle"), dict) else None,
        "recorded_at": r.get("recorded_at"),
        "source": "saved simulation results (2-D FEM, energy torque); nothing "
                  "is solved by this call",
    }


def get_machine_performance(p: Principal, die: str, config: str, duty: str) -> Dict[str, Any]:
    with acting_as(p):
        out = _performance(die, config, duty)
    return _checked(out)


# ── check_fit ────────────────────────────────────────────────────────────────

def check_fit(p: Principal, torque_nm: float, speed_rpm: float,
              voltage_v: Optional[float] = None,
              max_diameter_mm: Optional[float] = None,
              max_length_mm: Optional[float] = None,
              max_mass_kg: Optional[float] = None,
              cooling: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    if not (torque_nm and torque_nm > 0 and speed_rpm and speed_rpm > 0):
        raise ToolError("torque_nm and speed_rpm must be positive")
    with acting_as(p):
        ms = _machines()
        cool_of: Dict[tuple, Optional[str]] = {}
        from motor_ai_sim import duty_results as _dr
        for m in ms:
            idx = _dr.index(str(m["die"])).get(str(m["config"])) or {}
            for d in m["duties"]:
                cool_of[(m["die"], m["config"], d["duty"])] = _find_cooling(
                    idx.get(str(d["duty"])) or {})
    fits, rejected = [], []
    for m in ms:
        for d in m["duties"]:
            reasons, unverified, margins = [], [], {}

            def _chk(name, have, need, *, at_least: bool):
                if need is None:
                    return
                if have is None:
                    unverified.append(f"{name}: not recorded")
                    return
                mg = (have - need) / need * 100.0 if at_least else (need - have) / need * 100.0
                margins[f"{name}_margin_pct"] = round(mg, 1)
                if mg < 0:
                    reasons.append(f"{name} {have:g} {'<' if at_least else '>'} {need:g}")

            _chk("torque_nm", d["torque_nm"], torque_nm, at_least=True)
            _chk("speed_rpm", d["speed_rpm"], speed_rpm, at_least=True)
            _chk("line_voltage_peak_v", d["line_voltage_peak_v"], voltage_v, at_least=False)
            _chk("outer_diameter_mm", m["outer_diameter_mm"], max_diameter_mm, at_least=False)
            _chk("active_length_mm", m["active_length_mm"], max_length_mm, at_least=False)
            _chk("mass_kg", d["mass_kg"] if d["mass_kg"] is not None else m["mass_kg"],
                 max_mass_kg, at_least=False)
            if cooling:
                have_c = cool_of.get((m["die"], m["config"], d["duty"]))
                if not have_c:
                    unverified.append("cooling: not recorded")
                elif cooling.strip().lower() not in have_c.lower():
                    reasons.append(f"cooling '{have_c}' != '{cooling}'")
            row = {"die": m["die"], "config": m["config"], "duty": d["duty"],
                   "ownership": m["ownership"], "mode": d["mode"],
                   "torque_nm": d["torque_nm"], "speed_rpm": d["speed_rpm"],
                   "power_kw": d["power_kw"], "efficiency_pct": d["efficiency_pct"],
                   "mass_kg": d["mass_kg"], "outer_diameter_mm": m["outer_diameter_mm"],
                   "active_length_mm": m["active_length_mm"], **margins}
            if reasons:
                rejected.append({**row, "reasons": reasons})
            else:
                fits.append({**row, "unverified": unverified})
    fits.sort(key=lambda r: (len(r["unverified"]),
                             r["mass_kg"] if r["mass_kg"] is not None else 1e9,
                             -(r["efficiency_pct"] or 0)))
    rejected.sort(key=lambda r: len(r["reasons"]))
    lim = max(1, min(int(limit or 10), 50))
    return _checked({
        "requirements": {"torque_nm": torque_nm, "speed_rpm": speed_rpm,
                         "voltage_v": voltage_v, "max_diameter_mm": max_diameter_mm,
                         "max_length_mm": max_length_mm, "max_mass_kg": max_mass_kg,
                         "cooling": cooling},
        "rule": ("a saved duty fits when it delivers >= torque at >= speed, its "
                 "peak line voltage <= voltage_v (DC bus), and OD / active length "
                 "/ mass are within the limits; ranked by fewest unverified "
                 "checks, then lightest, then most efficient"),
        "fits": fits[:lim], "fit_count": len(fits),
        "rejected": rejected[:lim], "rejected_count": len(rejected),
    })


GUIDE = """# eMotres MCP — how to use (Stages 1-3)

eMotres designs permanent-magnet motors/generators and keeps a catalog of
simulated machines (2-D FEM, energy-method torque, coupled thermal).

Hierarchy: a *die* (lamination) -> *configurations* (stack length, winding,
materials) -> *duties* (operating points with saved results).

Tools:
- list_catalog(kind, query?) — magnets | steels | wires | bearings | devices | dies
- get_catalog_entry(kind, id)
- list_machines() — machines this key's owner may see, with headline ratings
- get_machine_performance(die, config, duty) — one duty's saved results
- check_fit(torque_nm, speed_rpm, ...) — ranked machines meeting requirements

Stage 3 (scopes designs:write + simulate) — design a motor for the engineer:
- start_design(requirements, base?, name?) — a DRAFT from the nearest existing
  machine, scaled by stack length / turns / parallel paths.  If the answer says
  status 'needs_input', ASK THE ENGINEER each listed field (why + options are
  written for him), then call again.  'no_fit' says which limit blocks it.
- simulate(design_id, what=em|thermal|coupled, steps?) -> job_id (queued on the
  user's own queue; daily fair-use limit)
- get_job(job_id) — state, position, progress, ETA, error
- get_design_result(design_id) — torque, power, shaft efficiency, losses,
  temperatures, limits, mass; requirement checks
- open_in_configure(design_id) — link for the engineer to tune it interactively

Example: "I need 12 N*m at 3000 rpm, 48 V" -> start_design -> needs_input
[cooling, duty] -> ask -> start_design again -> simulate(em) -> get_job until
done -> get_design_result -> open_in_configure link to the engineer.

Rules: read tools solve nothing; numbers are the last saved simulation.  Drafts
never change the user's saved machines or the machine he has open.
Units are in field names (_nm, _rpm, _kw, _v, _mm, _kg, _c, _pct).
Only public-datasheet data is returned — no internal dimensions or drawings.
"""
