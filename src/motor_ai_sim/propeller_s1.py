"""Continuous (S1) rating of a motor driving a propeller that is also its only cooling.

WHY THIS MODULE EXISTS
======================
A drone motor cooled only by its own propeller's slipstream has one degree of
freedom: the speed ``n``.  At ``n`` the propeller fixes the load torque Q(n) and
the air over the housing v(n); the motor's torque constant (at its magnet
temperature) fixes the current; the current and the speed fix the losses; the
losses and the air fix the temperatures, which feed back into the resistance and
the flux.  The S1 limit with that propeller is the highest ``n`` at which the
steady winding and magnet temperatures stay below their limits (and the current
and speed stay inside what the passport characterised).

No field is solved here.  Everything comes from a compact per-configuration
MOTOR MODEL extracted from the machine's passport (``scripts/
export_propeller_s1_model.py`` writes ``config/propeller_s1/<die>/<config>.json``;
``<shared>/propeller_s1/`` on the server wins) and from ``motor_ai_sim.propeller``
(catalogue, coefficients, slipstream).  Nothing here writes anything.

THE EQUATIONS (one operating speed n, iterated to a thermal fixed point)
=======================================================================
::

    Q_em      = Q_prop(n) + P_mech(n) / ω                 (bearings + windage on top of the prop)
    k_ψ(T_m)  = 1 + c_ψ · (T_m − T_m,ref)                  (flux vs magnet temperature, from the passport)
    I         : k_T · T_grid(n, I) · k_ψ(T_m) = Q_em        (bisection; T_grid = settled FEM torque map)
    R(T_w)    = R_ref · (1 + α(T_w − 20)) / (1 + α(T_ref − 20)),  α = 0.00393 /K (copper)
    P_cu,dc   = 3 I² R(T_w)
    P_cu,ac   = P_ac,grid(n, I) · ρ(T_ref)/ρ(T_w)          (resistance-limited eddy ∝ σ)
    P_fe,s, P_fe,r, P_rotor-eddy, P_pwm  from the grid at (n, I)
    v_air     = factor · sqrt(2 T(n) / (ρ A))               (propeller.operating_point)
    h         = cooling_models.outer_air(v_air, T_amb, D_housing)
    T_housing = T_amb + P_motor / (h · A_housing)
    T_winding = T_housing + P_cu · (R_slot + R_liner + R_yoke)
    T_magnet  = T_housing + P_rotor · R_gap                 (rotor iron + magnet + shaft eddy)

The thermal network is Configure's lumped estimate (``web/src/lib/thermalEstimate.ts``,
the same constants) so the backend agrees with the tiles the user already sees;
the rotor heat crossing the gap includes the rotor iron loss (Configure's tile
puts only the magnet/shaft eddy there).  Interpolation in the loss grid follows the
passport's own rule: linear in current, power law ``P = a·n^k`` between
neighbouring speeds; below the first speed the first interval's exponent is
continued down (flagged ``below_loss_grid``); above the last speed or the last
current the model refuses.

CONTROLLER LOSSES (inverter + board copper of the passport's drive variant) are
added to the battery power only: the controller is not inside the motor's
thermal model.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from motor_ai_sim import propeller as pp

log = logging.getLogger(__name__)

SCHEMA = "propeller-s1-model-1"
ALPHA_CU = 0.00393                    # /K, copper resistivity at 20 °C reference
T_CU_REF_C = 20.0
RUNAWAY_C = 600.0                     # a fixed point above this is a thermal runaway
FIXED_POINT_TOL_K = 0.01
FIXED_POINT_MAX_IT = 400

# Configure's lumped conductivities (web/src/lib/thermalEstimate.ts) [W/m·K]
K_IRON = 25.0
K_SLOT = 1.4
K_INS = 0.2
K_GAP = 0.06

_REPO_DIR = Path(__file__).resolve().parents[2] / "config" / "propeller_s1"
#: Overridden by tests; a moved path wins outright (the passport-store rule).
_DIR: Path = _REPO_DIR

LOSS_GROUPS = ("cu_ac", "fe_stator", "fe_rotor", "rotor_eddy", "pwm_extra")


class S1Error(ValueError):
    """The model or the request cannot give an S1 answer."""


class S1ModelNotFound(S1Error, KeyError):
    def __str__(self) -> str:                                  # KeyError would repr() it
        return str(self.args[0]) if self.args else "no propeller S1 model"


# ---------------------------------------------------------------------------
# Model files
# ---------------------------------------------------------------------------

def model_dirs() -> List[Path]:
    """Repo folder first, then ``<shared>/propeller_s1`` (which wins per file)."""
    dirs = [_DIR]
    if _DIR == _REPO_DIR:
        try:
            from motor_ai_sim.workspace import shared_root
            p = Path(str(shared_root())) / "propeller_s1"
            if p.is_dir() and p.resolve() != _REPO_DIR.resolve():
                dirs.append(p)
        except Exception:                                       # noqa: BLE001
            pass
    return dirs


def _safe_name(s: str) -> str:
    s = str(s or "").strip()
    if not s or s in (".", "..") or any(c in s for c in "/\\\x00"):
        raise S1Error(f"bad die/config name {s!r}")
    return s


def load_model(die: str, config: str) -> Dict[str, Any]:
    """The validated S1 model of ``die`` / ``config`` (``S1ModelNotFound`` if none)."""
    d, c = _safe_name(die), _safe_name(config)
    found: Optional[Path] = None
    for root in model_dirs():
        f = root / d / f"{c}.json"
        if f.is_file():
            found = f                                           # later layer (server) wins
    if found is None:
        raise S1ModelNotFound(f"no propeller S1 model for {die} {config}")
    try:
        raw = json.loads(found.read_text(encoding="utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        raise S1Error(f"{found}: unreadable ({exc})")
    m = validate_model(raw)
    m["_path"] = str(found)
    return m


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _axis(v: Any, name: str) -> List[float]:
    if not isinstance(v, list) or len(v) < 2:
        raise S1Error(f"model: {name} needs at least 2 values")
    xs = [_num(x) for x in v]
    if any(x is None or x < 0 for x in xs) or any(b <= a for a, b in zip(xs, xs[1:])):
        raise S1Error(f"model: {name} must be increasing non-negative numbers")
    return xs  # type: ignore[return-value]


def _table(v: Any, n_rpm: int, n_i: int, name: str) -> List[List[Optional[float]]]:
    if not isinstance(v, list) or len(v) != n_rpm or any(
            not isinstance(r, list) or len(r) != n_i for r in v):
        raise S1Error(f"model: {name} must be a {n_rpm} x {n_i} table (rpm rows)")
    return [[_num(x) for x in r] for r in v]


def validate_model(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Check a model dict and return a normalised copy (``S1Error`` on anything wrong)."""
    if not isinstance(raw, Mapping) or raw.get("schema") != SCHEMA:
        raise S1Error(f"model: schema must be {SCHEMA!r}")
    m: Dict[str, Any] = {k: raw[k] for k in raw}
    for k in ("R_phase_ohm", "R_temp_C", "magnet_ref_temp_C"):
        if _num(raw.get(k)) is None:
            raise S1Error(f"model: missing {k}")
    if not (_num(raw["R_phase_ohm"]) > 0):
        raise S1Error("model: R_phase_ohm must be positive")
    g = raw.get("grid") or {}
    rpm = _axis(g.get("rpm"), "grid.rpm")
    cur = _axis(g.get("I_A"), "grid.I_A")
    grid = {"rpm": rpm, "I_A": cur, "T_Nm": _table(g.get("T_Nm"), len(rpm), len(cur), "grid.T_Nm"),
            "loss_W": {}}
    for k, t in (g.get("loss_W") or {}).items():
        if k not in LOSS_GROUPS:
            raise S1Error(f"model: unknown loss group {k!r} (use {LOSS_GROUPS})")
        grid["loss_W"][k] = _table(t, len(rpm), len(cur), f"grid.loss_W.{k}")
    fl = g.get("I_floor_A")
    if fl is not None:
        if not isinstance(fl, list) or len(fl) != len(rpm):
            raise S1Error("model: grid.I_floor_A must list one current per grid speed")
        vals = [0.0 if x is None else _num(x) for x in fl]
        if any(v is None or v < 0 for v in vals):
            raise S1Error("model: grid.I_floor_A must be non-negative numbers (or null)")
        grid["I_floor_A"] = vals
    m["grid"] = grid
    mech = raw.get("mech_W") or {}
    if mech:
        m["mech_W"] = {"rpm": _axis(mech.get("rpm"), "mech_W.rpm"),
                       "W": [_num(x) or 0.0 for x in (mech.get("W") or [])]}
        if len(m["mech_W"]["W"]) != len(m["mech_W"]["rpm"]):
            raise S1Error("model: mech_W.W must match mech_W.rpm")
    ctrl = raw.get("controller")
    if ctrl:
        crpm = _axis(ctrl.get("rpm"), "controller.rpm")
        ci = _axis(ctrl.get("I_A"), "controller.I_A")
        m["controller"] = {**ctrl, "rpm": crpm, "I_A": ci,
                           "loss_W": _table(ctrl.get("loss_W"), len(crpm), len(ci), "controller.loss_W")}
    th = raw.get("thermal_geom") or {}
    need = ("statorOD_mm", "stackLength_mm", "numSlots", "slotHeight_mm", "slotWidth_mm",
            "insulation_mm", "coreThickness_mm", "airGap_mm", "magnetOD_mm")
    if any(_num(th.get(k)) is None for k in need):
        raise S1Error(f"model: thermal_geom needs {need}")
    m["thermal_geom"] = {k: float(th[k]) for k in need}
    lim = dict(raw.get("limits") or {})
    m["limits"] = lim
    m["torque_factor"] = _num(raw.get("torque_factor")) or 1.0
    if raw.get("thermal_calibration") is not None:
        if not isinstance(raw.get("thermal_calibration"), Mapping):
            raise S1Error("model: thermal_calibration must be a mapping")
        calibration_of(raw)
    m["flux_temp_coeff_per_K"] = _num(raw.get("flux_temp_coeff_per_K")) or 0.0
    return m


# ---------------------------------------------------------------------------
# Grid interpolation (the passport's rule)
# ---------------------------------------------------------------------------

def _bracket(xs: Sequence[float], x: float) -> Tuple[int, int]:
    """Indices (lo, hi) around ``x`` (xs[0] <= x <= xs[-1])."""
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    return lo, hi


def _along_current(row: Sequence[Optional[float]], cur: Sequence[float], I: float,
                   zero_at_zero: bool, floor: float = 0.0) -> Optional[float]:
    """Value of one speed row at current ``I`` (linear).  Below the first current:
    towards 0 at I = 0 (``zero_at_zero``, the torque) or the first interval's line
    continued to I = 0 (losses, clamped at 0).  Above the last current: None.

    ``floor`` > 0 (a speed above the no-load voltage limit): no current below it
    can hold the voltage (None there), the row's first FEASIBLE cell takes the
    place of the first current, and the torque goes to 0 at the floor instead of
    at I = 0 (conservative: at the floor the current is nearly all d-axis)."""
    if I > cur[-1] + 1e-9:
        return None
    k0 = 0
    base = 0.0
    if floor > 0.0:
        if I < floor - 1e-9:
            return None
        k0 = next((k for k, y in enumerate(row) if y is not None), len(row))
        if k0 >= len(row):
            return None
        base = floor
    if I <= cur[k0]:
        y0 = row[k0]
        if y0 is None:
            return None
        if zero_at_zero:
            return y0 * (I - base) / (cur[k0] - base) if cur[k0] > base else y0
        y1 = row[k0 + 1] if k0 + 1 < len(row) else None
        if y1 is None:
            return max(y0, 0.0)
        return max(y0 + (y1 - y0) * (I - cur[k0]) / (cur[k0 + 1] - cur[k0]), 0.0)
    lo, hi = _bracket(cur, I)
    a, b = row[lo], row[hi]
    if a is None or b is None:
        return None
    w = (I - cur[lo]) / (cur[hi] - cur[lo])
    return a + (b - a) * w


def _along_speed(x0: float, y0: float, x1: float, y1: float, x: float, power_law: bool) -> float:
    if power_law and x0 > 0 and x1 > 0 and y0 > 0 and y1 > 0 and x > 0:
        k = math.log(y1 / y0) / math.log(x1 / x0)
        return y0 * (x / x0) ** k
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def grid_at(rpm_ax: Sequence[float], cur_ax: Sequence[float], table: Sequence[Sequence[Optional[float]]],
            rpm: float, I: float, *, kind: str, floors: Optional[Sequence[float]] = None
            ) -> Tuple[Optional[float], bool]:
    """``(value, below_grid_speed)`` at (rpm, I); value None = outside / infeasible.

    ``kind`` = ``torque`` (linear in rpm, held below the first speed, 0 at I = 0)
    or ``loss`` (power law between speeds, the first interval's law continued below
    the first speed, line to I = 0 below the first current).  ``floors`` = the
    model's ``I_floor_A`` (one per speed row, see ``_along_current``).  At a grid
    speed exactly, that row alone answers."""
    torque = kind == "torque"
    if rpm > rpm_ax[-1] + 1e-6 or rpm < 0:
        return None, False
    f = list(floors) if floors else [0.0] * len(rpm_ax)
    below = rpm < rpm_ax[0]
    if below:
        i0, i1 = 0, 1
    else:
        i0, i1 = _bracket(rpm_ax, rpm)
    y0 = _along_current(table[i0], cur_ax, I, torque, f[i0])
    if below and torque:
        return y0, True
    if not below and rpm == rpm_ax[i0]:
        return (y0 if (torque or y0 is None) else max(y0, 0.0)), below
    y1 = _along_current(table[i1], cur_ax, I, torque, f[i1])
    if y0 is None or y1 is None:
        return None, below
    v = _along_speed(rpm_ax[i0], y0, rpm_ax[i1], y1, rpm, power_law=not torque)
    return (max(v, 0.0) if not torque else v), below


def _mech_W(m: Mapping[str, Any], rpm: float) -> float:
    t = m.get("mech_W")
    if not t:
        return 0.0
    xs, ys = t["rpm"], t["W"]
    if rpm <= 0:
        return 0.0
    if rpm <= xs[0]:
        return ys[0] * rpm / xs[0] if xs[0] > 0 else ys[0]
    if rpm >= xs[-1]:
        n = len(xs)
        return _along_speed(xs[n - 2], ys[n - 2], xs[n - 1], ys[n - 1], rpm, power_law=True)
    lo, hi = _bracket(xs, rpm)
    return _along_speed(xs[lo], ys[lo], xs[hi], ys[hi], rpm, power_law=True)


# ---------------------------------------------------------------------------
# Motor side
# ---------------------------------------------------------------------------

def resistance(m: Mapping[str, Any], t_winding_c: float) -> float:
    r_ref, t_ref = float(m["R_phase_ohm"]), float(m["R_temp_C"])
    return r_ref * (1.0 + ALPHA_CU * (t_winding_c - T_CU_REF_C)) / (1.0 + ALPHA_CU * (t_ref - T_CU_REF_C))


def flux_factor(m: Mapping[str, Any], t_magnet_c: float) -> float:
    return max(1.0 + float(m["flux_temp_coeff_per_K"]) * (t_magnet_c - float(m["magnet_ref_temp_C"])), 0.05)


def current_cap(m: Mapping[str, Any]) -> float:
    """The largest current the S1 search may use: the passport's current limit
    (demagnetisation) and the top of the torque grid, whichever is lower."""
    top = float(m["grid"]["I_A"][-1])
    lim = _num((m.get("limits") or {}).get("I_max_A"))
    return min(top, lim) if lim else top


def speed_cap(m: Mapping[str, Any]) -> float:
    top = float(m["grid"]["rpm"][-1])
    lim = _num((m.get("limits") or {}).get("rpm_max"))
    return min(top, lim) if lim else top


def floor_current(m: Mapping[str, Any], rpm: float) -> float:
    """The smallest current the drive can run at ``rpm`` (``grid.I_floor_A``; 0
    without one): the larger floor of the two speed rows the point lies between,
    the row's own at a grid speed, the first row's below the grid."""
    g = m["grid"]
    fl = g.get("I_floor_A")
    if not fl:
        return 0.0
    ax = g["rpm"]
    if rpm <= ax[0]:
        return float(fl[0])
    if rpm >= ax[-1]:
        return float(fl[-1])
    i0, i1 = _bracket(ax, rpm)
    return float(fl[i0]) if rpm == ax[i0] else max(float(fl[i0]), float(fl[i1]))


def em_torque(m: Mapping[str, Any], rpm: float, I: float, t_magnet_c: float) -> Optional[float]:
    g = m["grid"]
    t, _ = grid_at(g["rpm"], g["I_A"], g["T_Nm"], rpm, I, kind="torque", floors=g.get("I_floor_A"))
    if t is None:
        return None
    return float(m["torque_factor"]) * t * flux_factor(m, t_magnet_c)


def current_for_torque(m: Mapping[str, Any], rpm: float, need_Nm: float, t_magnet_c: float
                       ) -> Tuple[Optional[float], str]:
    """``(I, status)``: the current that makes ``need_Nm`` at ``rpm``.

    ``status`` is ``ok``; ``current`` when the torque still rises at the current
    cap and falls short there; ``voltage`` when the torque the speed allows peaks
    below ``need_Nm`` (the grid's field-weakening cells at high speed collapse
    the torque at high current) or the needed current lies in cells the passport
    marks infeasible.  The SMALLEST current that makes the torque is returned
    (the grid is walked segment by segment: torque is linear in I inside one).
    Above the no-load voltage limit (``floor_current`` > 0) the walk starts at
    the floor; ``fw_floor`` = the speed alone needs more current than the torque
    does, and the floor current is returned (the d-axis current that holds the
    voltage)."""
    cap = current_cap(m)
    fl = floor_current(m, rpm)
    if need_Nm <= 0 and fl <= 0:
        return 0.0, "ok"
    if fl >= cap:
        return None, "voltage"
    pts = [fl] + [float(i) for i in m["grid"]["I_A"] if fl < i < cap] + [cap]
    vals = [em_torque(m, rpm, fl, t_magnet_c) if fl > 0 else 0.0] \
        + [em_torque(m, rpm, i, t_magnet_c) for i in pts[1:]]
    if fl > 0 and vals[0] is not None and need_Nm <= vals[0]:
        return fl, "fw_floor"
    best_i, best_t = 0, 0.0
    for k in range(1, len(pts)):
        a, b = vals[k - 1], vals[k]
        if b is not None and b > best_t:
            best_i, best_t = k, b
        if a is None or b is None or not (a < need_Nm <= b):
            continue
        lo, hi = pts[k - 1], pts[k]
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            t = em_torque(m, rpm, mid, t_magnet_c)
            if t is None or t < need_Nm:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi), "ok"
    return None, ("current" if best_i == len(pts) - 1 else "voltage")


def losses(m: Mapping[str, Any], rpm: float, I: float, t_winding_c: float) -> Dict[str, Any]:
    """Motor losses by group [W] at (rpm, I, winding temperature)."""
    g = m["grid"]
    r = resistance(m, t_winding_c)
    out: Dict[str, Any] = {"cu_dc": 3.0 * I * I * r}
    below = False
    for k in LOSS_GROUPS:
        t = g["loss_W"].get(k)
        if t is None:
            out[k] = 0.0
            continue
        v, b = grid_at(g["rpm"], g["I_A"], t, rpm, I, kind="loss", floors=g.get("I_floor_A"))
        below = below or b
        out[k] = 0.0 if v is None else v
    # resistance-limited eddy in the conductors goes with the conductivity
    out["cu_ac"] *= resistance(m, float(m["R_temp_C"])) / r
    out["mech"] = _mech_W(m, rpm)
    out["motor_total"] = sum(out[k] for k in ("cu_dc", "cu_ac", "fe_stator", "fe_rotor",
                                               "rotor_eddy", "pwm_extra", "mech"))
    out["R_phase_ohm"] = r
    out["below_loss_grid"] = below
    return out


def controller_loss(m: Mapping[str, Any], rpm: float, I: float) -> Optional[float]:
    c = m.get("controller")
    if not c:
        return None
    v, _ = grid_at(c["rpm"], c["I_A"], c["loss_W"], rpm, I, kind="loss")
    return v


# ---------------------------------------------------------------------------
# Thermal (Configure's lumped estimate, thermalEstimate.ts)
# ---------------------------------------------------------------------------

def housing_film(air_speed_ms: float, ambient_c: float, d_housing_m: float) -> float:
    from motor_ai_sim.simulation import cooling_models as cm
    return float(cm.outer_air(air_speed_mps=air_speed_ms, t_ambient_c=ambient_c,
                              d_housing_m=d_housing_m)["h_conv"])


#: The calibration constants a model may carry (``thermal_calibration``), with
#: the value that leaves Configure's network exactly as it is.
CALIBRATION_KEYS = {"housing_film_factor": 1.0, "winding_air_area_m2": 0.0, "rotor_air_area_m2": 0.0,
                    "gap_resistance_factor": 1.0}


def calibration_of(m: Mapping[str, Any]) -> Dict[str, float]:
    """The model's thermal calibration (``thermal_calibration``; absent = Configure's
    network unchanged).  Every value must be finite and non-negative, the housing
    factor positive."""
    raw = m.get("thermal_calibration") or {}
    out = dict(CALIBRATION_KEYS)
    for k in CALIBRATION_KEYS:
        if raw.get(k) is not None:
            v = _num(raw.get(k))
            if v is None or v < 0 or (k in ("housing_film_factor", "gap_resistance_factor") and v <= 0):
                raise S1Error(f"model: thermal_calibration.{k} must be a finite number >= 0 "
                              "(the housing and gap factors > 0)")
            out[k] = v
    return out


#: A calibrated network is taken as calibrated within this fraction outside the
#: slipstream speeds its FEM anchors ran at (the Ø40 L12 validation anchor sat
#: 17 % above its highest fitted speed and landed within 1.5 K).
CALIBRATION_RANGE_MARGIN = 0.2


def calibration_covers(m: Mapping[str, Any], air_speed_ms: Optional[float]) -> Optional[bool]:
    """Whether ``air_speed_ms`` lies inside the calibration's anchor slipstream range
    (``thermal_calibration.anchor_air_speed_ms`` ± ``CALIBRATION_RANGE_MARGIN``);
    None when the model is uncalibrated or its calibration states no range."""
    rng = (m.get("thermal_calibration") or {}).get("anchor_air_speed_ms")
    v = _num(air_speed_ms)
    if not rng or v is None:
        return None
    lo, hi = float(rng[0]), float(rng[1])
    return (1.0 - CALIBRATION_RANGE_MARGIN) * lo <= v <= (1.0 + CALIBRATION_RANGE_MARGIN) * hi


def temperatures(geom: Mapping[str, float], p_cu: float, p_rotor: float, p_total: float,
                 h: float, ambient_c: float, cal: Optional[Mapping[str, float]] = None
                 ) -> Dict[str, float]:
    """Steady temperatures of Configure's lumped network, optionally calibrated.

    Uncalibrated (``cal`` None or the defaults) every watt leaves through the
    housing surface (cylinder + both end discs) at the film ``h``: the housing
    sits at ``T_amb + P/(h·A)``, the winding ``P_cu·R_w`` above it and the rotor
    ``P_rotor·R_gap`` above it.  The calibration (fitted to coupled FEM anchors)
    adds the two paths an open, propeller-washed machine has besides its outer
    surface — the end turns / slot channels straight to the air
    (``G_wa = h·A_w``) and the rotor faces / ventilated gap straight to the air
    (``G_ra = h·A_r``) — and scales the housing film (``f_h``).  A HOUSED frame has
    no rotor-to-air path (its rotor heat leaves only across the gap), so its fit
    scales the gap resistance instead (``gap_resistance_factor`` f_g, default 1).
    The three nodes
    (housing, winding, rotor) are then one linear system; with A_w = A_r = 0 and
    f_h = f_g = 1 it is the uncalibrated network exactly."""
    mm = lambda x: max(0.0, float(x or 0.0)) / 1000.0           # noqa: E731
    D = mm(geom["statorOD_mm"]) or 0.1
    L = mm(geom["stackLength_mm"]) or 0.03
    h = max(1.0, h)
    a_cyl = math.pi * D * L
    a_surf = a_cyl + 2.0 * math.pi * (D / 2.0) ** 2
    a_slot = max(1e-4, (geom["numSlots"] or 12) * 2.0 * mm(geom["slotHeight_mm"]) * L)
    r_w = ((mm(geom["slotWidth_mm"]) * 0.5) / (K_SLOT * a_slot)
           + mm(geom["insulation_mm"]) / (K_INS * a_slot)
           + mm(geom["coreThickness_mm"]) / (K_IRON * max(1e-4, a_cyl)))
    a_gap = max(1e-4, math.pi * mm(geom["magnetOD_mm"]) * L)
    c = dict(CALIBRATION_KEYS)
    c.update({k: float(v) for k, v in (cal or {}).items() if k in CALIBRATION_KEYS})
    r_gap = c["gap_resistance_factor"] * mm(geom["airGap_mm"]) / (K_GAP * a_gap)
    g_h = c["housing_film_factor"] * h * a_surf
    g_wa = c["winding_air_area_m2"] * h
    g_ra = c["rotor_air_area_m2"] * h
    g_w, g_r = 1.0 / r_w, 1.0 / r_gap
    p_h = p_total - p_cu - p_rotor                              # stator iron, PWM extra, bearings
    # unknowns x = (T_h, T_w, T_r) - T_amb:  [housing; winding; rotor] balances
    a11, a12, a13 = g_h + g_w + g_r, -g_w, -g_r
    a21, a22 = -g_w, g_w + g_wa
    a31, a33 = -g_r, g_r + g_ra
    # eliminate the winding and rotor nodes into the housing balance
    s = a11 - a12 * a21 / a22 - a13 * a31 / a33
    x_h = (p_h - a12 * p_cu / a22 - a13 * p_rotor / a33) / s
    x_w = (p_cu - a21 * x_h) / a22
    x_r = (p_rotor - a31 * x_h) / a33
    return {"housing": ambient_c + x_h, "winding": ambient_c + x_w, "magnet": ambient_c + x_r,
            "A_surface_m2": a_surf, "R_winding_K_W": r_w, "R_gap_K_W": r_gap,
            "G_winding_air_W_K": g_wa, "G_rotor_air_W_K": g_ra, "G_housing_W_K": g_h}


# ---------------------------------------------------------------------------
# One speed: the coupled fixed point
# ---------------------------------------------------------------------------

def limits_of(m: Mapping[str, Any], winding_limit_c: Optional[float] = None,
              magnet_limit_c: Optional[float] = None) -> Dict[str, Any]:
    lim = m.get("limits") or {}
    w = _num(winding_limit_c) or _num(lim.get("winding_C"))
    w_basis = str(lim.get("winding_basis") or "model limit") if w is not None and winding_limit_c is None else "given by the caller"
    if w is None:                      # the project's ONE insulation class (report.PROJECT_INSULATION_C)
        from motor_ai_sim.coupled_time_to_limit import winding_limit
        w = winding_limit()[0]
        w_basis = "project insulation class N (%g °C)" % w
    mg = _num(magnet_limit_c) or _num(lim.get("magnet_C"))
    if mg is None:
        raise S1Error("model: no magnet temperature limit (limits.magnet_C)")
    return {"winding_C": w, "winding_basis": w_basis,
            "magnet_C": mg, "magnet_basis": str(lim.get("magnet_basis") or "magnet card"),
            "I_max_A": current_cap(m), "I_max_basis": str(lim.get("I_max_basis") or "top of the passport current grid"),
            "rpm_max": speed_cap(m), "rpm_max_basis": str(lim.get("rpm_max_basis") or "top of the passport speed grid")}


def solve_point(m: Mapping[str, Any], prop: pp.Propeller, rpm: float, *, ambient_c: float,
                rho: float, lims: Mapping[str, Any], position: Optional[str] = None,
                factor: Optional[float] = None) -> Dict[str, Any]:
    """Everything at one propeller speed: load, current, losses, air, temperatures,
    margins and what (if anything) stops it."""
    op = pp.operating_point(prop, rpm, rho, position=position, factor=factor)
    w = 2.0 * math.pi * rpm / 60.0
    p_mech = _mech_W(m, rpm)
    q_em = op["torque_Nm"] + (p_mech / w if w > 0 else 0.0)
    D = float(m["thermal_geom"]["statorOD_mm"]) / 1000.0
    h = housing_film(op["air_speed_ms"], ambient_c, D)
    row: Dict[str, Any] = {
        "rpm": float(rpm), "thrust_N": op["thrust_N"], "torque_prop_Nm": op["torque_Nm"],
        "torque_em_Nm": q_em, "shaft_power_W": op["shaft_power_W"],
        "air_speed_ms": op["air_speed_ms"], "h_W_m2K": h,
        "extrapolated": bool(op["extrapolated"]), "power_estimated": bool(op["power_estimated"]),
        "current_A": None, "losses_W": None, "T_winding_C": None, "T_magnet_C": None,
        "T_housing_C": None, "margin_winding_K": None, "margin_magnet_K": None,
        "electrical_power_W": None, "battery_power_W": None, "controller_loss_W": None,
        "converged": None, "iterations": 0, "below_loss_grid": False, "fw_floor": False, "ok": False,
        "binding": None,
    }
    if rpm > lims["rpm_max"] + 1e-6:
        row["binding"] = "speed"
        return row
    cal = calibration_of(m)
    # above the no-load voltage limit the drive carries d-axis current at any load
    row["fw_floor"] = floor_current(m, rpm) > 0.0
    t_w = t_m = float(ambient_c)
    damping, last_res = 1.0, float("inf")
    I = None
    for it in range(1, FIXED_POINT_MAX_IT + 1):
        I, status = current_for_torque(m, rpm, q_em, t_m)

        if I is None:
            # the torque is out of reach (at the current cap or the voltage limit).  When the
            # iterate is already past a temperature limit the motor overheats on the way there
            # (the hot magnets lost the flux): that limit binds, not the current
            ow, om = t_w - lims["winding_C"], t_m - lims["magnet_C"]
            row["binding"] = status if max(ow, om) <= 0 else ("winding" if ow >= om else "magnet")
            row["iterations"] = it
            return row
        ls = losses(m, rpm, I, t_w)
        p_rotor = ls["fe_rotor"] + ls["rotor_eddy"]
        th = temperatures(m["thermal_geom"], ls["cu_dc"] + ls["cu_ac"], p_rotor, ls["motor_total"], h,
                          ambient_c, cal)
        res = max(abs(th["winding"] - t_w), abs(th["magnet"] - t_m))
        if res > last_res * 0.999 and it > 3:
            damping = max(0.1, damping * 0.5)
        last_res = res
        t_w += damping * (th["winding"] - t_w)
        t_m += damping * (th["magnet"] - t_m)
        row["iterations"] = it
        if t_w > RUNAWAY_C or t_m > RUNAWAY_C:
            # no steady state: the temperatures are not printed (they are not a state of the motor)
            row.update(binding="runaway", converged=False, current_A=I)
            return row
        if res < FIXED_POINT_TOL_K:
            row["converged"] = True
            break
    else:
        row["converged"] = False
    ls = losses(m, rpm, I, t_w)
    th = temperatures(m["thermal_geom"], ls["cu_dc"] + ls["cu_ac"], ls["fe_rotor"] + ls["rotor_eddy"],
                      ls["motor_total"], h, ambient_c, cal)
    p_in = op["shaft_power_W"] + ls["motor_total"]
    pc = controller_loss(m, rpm, I)
    row.update(current_A=I, T_winding_C=th["winding"], T_magnet_C=th["magnet"], T_housing_C=th["housing"],
               losses_W={k: ls[k] for k in ("cu_dc", "cu_ac", "fe_stator", "fe_rotor", "rotor_eddy",
                                             "pwm_extra", "mech", "motor_total")},
               R_phase_ohm=ls["R_phase_ohm"], below_loss_grid=bool(ls["below_loss_grid"]),
               electrical_power_W=p_in, controller_loss_W=pc,
               battery_power_W=(p_in + pc) if pc is not None else None,
               margin_winding_K=lims["winding_C"] - th["winding"],
               margin_magnet_K=lims["magnet_C"] - th["magnet"])
    if not row["converged"] or th["winding"] > RUNAWAY_C or th["magnet"] > RUNAWAY_C:
        row.update(binding="runaway", T_winding_C=None, T_magnet_C=None, T_housing_C=None,
                   margin_winding_K=None, margin_magnet_K=None)
        return row
    mw, mm_ = row["margin_winding_K"], row["margin_magnet_K"]
    if mw < 0 or mm_ < 0:
        row["binding"] = "winding" if mw <= mm_ else "magnet"
    else:
        row["ok"] = True
    return row


# ---------------------------------------------------------------------------
# The S1 limit
# ---------------------------------------------------------------------------

def _bands(rows: Sequence[Mapping[str, Any]]) -> List[Tuple[float, float]]:
    """``[(first rpm, last rpm)]`` of every run of passing scan rows."""
    out: List[Tuple[float, float]] = []
    start: Optional[float] = None
    prev = 0.0
    for r in rows:
        if r["ok"] and start is None:
            start = r["rpm"]
        if not r["ok"] and start is not None:
            out.append((start, prev))
            start = None
        prev = r["rpm"]
    if start is not None:
        out.append((start, prev))
    return out


def _bisect_edge(f: Callable[[float], Dict[str, Any]], lo: float, hi: float, tol_rpm: float
                 ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(last ok row, first failing row)`` between an ok ``lo`` and a failing ``hi``."""
    r_lo, r_hi = f(lo), f(hi)
    while hi - lo > tol_rpm:
        mid = 0.5 * (lo + hi)
        r = f(mid)
        if r["ok"]:
            lo, r_lo = mid, r
        else:
            hi, r_hi = mid, r
    return r_lo, r_hi


def s1_rating(m: Mapping[str, Any], prop: pp.Propeller, *, ambient_c: float = 25.0,
              altitude_m: float = 0.0, position: Optional[str] = None,
              factor: Optional[float] = None, n_points: int = 41,
              rpm_min: Optional[float] = None, rpm_max: Optional[float] = None,
              winding_limit_c: Optional[float] = None, magnet_limit_c: Optional[float] = None,
              tol_rpm: float = 1.0) -> Dict[str, Any]:
    """Rows over speed and the continuous limit with propeller ``prop``.

    The scan runs from ``rpm_min`` (default 10 % of the top) to ``rpm_max``
    (default: the motor's speed cap — the top of its characterised grid / voltage
    limit — but not more than 1.5 x the propeller's highest tested rpm).  The limit
    is the upper edge of the highest band of speeds that pass, bisected to
    ``tol_rpm``.  ``monotone`` is False when a failing speed lies below a passing
    one (all bands are listed then)."""
    if not prop.selectable:
        raise S1Error(f"{prop.id} has no usable thrust/power data")
    lims = limits_of(m, winding_limit_c, magnet_limit_c)
    rho = pp.air_density(temp_c=ambient_c, altitude_m=altitude_m)
    hi = float(rpm_max) if rpm_max else min(lims["rpm_max"], 1.5 * prop.rpm_range[1])
    lo = float(rpm_min) if rpm_min else 0.1 * hi
    if not (0 < lo < hi):
        raise S1Error("rpm range must satisfy 0 < rpm_min < rpm_max")
    n = max(5, min(int(n_points), 201))
    f = lambda r: solve_point(m, prop, r, ambient_c=ambient_c, rho=rho, lims=lims,  # noqa: E731
                              position=position, factor=factor)
    rows = [f(lo + (hi - lo) * i / (n - 1)) for i in range(n)]
    bands = _bands(rows)
    first_fail = next((i for i, r in enumerate(rows) if not r["ok"]), None)
    monotone = first_fail is None or not any(r["ok"] for r in rows[first_fail:])
    limit: Optional[Dict[str, Any]] = None
    if bands:
        top_ok = max(i for i, r in enumerate(rows) if r["ok"])
        if top_ok == n - 1:
            best, nxt = rows[-1], None
            binding = "speed_range"            # passes up to the end of the scanned range
        else:
            best, nxt = _bisect_edge(f, rows[top_ok]["rpm"], rows[top_ok + 1]["rpm"], tol_rpm)
            binding = nxt["binding"]
        limit = {"rpm": best["rpm"], "thrust_N": best["thrust_N"], "thrust_g": best["thrust_N"] / pp.G0 * 1000.0,
                 "shaft_power_W": best["shaft_power_W"], "electrical_power_W": best["electrical_power_W"],
                 "battery_power_W": best["battery_power_W"], "current_A": best["current_A"],
                 "T_winding_C": best["T_winding_C"], "T_magnet_C": best["T_magnet_C"],
                 "T_housing_C": best["T_housing_C"], "air_speed_ms": best["air_speed_ms"],
                 "binding": binding, "extrapolated": best["extrapolated"],
                 "power_estimated": best["power_estimated"], "below_loss_grid": best["below_loss_grid"],
                 "fw_floor": bool(best.get("fw_floor")),
                 "calibration_in_range": calibration_covers(m, best["air_speed_ms"])}
        if binding == "speed_range":
            limit["range_end_basis"] = (lims["rpm_max_basis"] if hi >= lims["rpm_max"] - 1e-6
                                        else "1.5 x the propeller's highest tested rpm")
    return {
        "propeller_id": prop.id, "propeller": prop.model,
        "die": m.get("die"), "config": m.get("config"),
        "ambient_c": float(ambient_c), "altitude_m": float(altitude_m), "rho_kg_m3": rho,
        "rpm_range_tested": list(prop.rpm_range) if prop.rpm_range else None,
        "power_data": prop.power_data,
        "limits": lims, "rows": rows, "bands": [list(b) for b in bands], "monotone": monotone,
        "limit": limit,
        "no_continuous_point": limit is None,
        "first_failure": (None if first_fail is None else rows[first_fail]["binding"]),
        "model": {"source": m.get("source"), "labels": m.get("labels") or [],
                  "magnet_ref_temp_C": m.get("magnet_ref_temp_C"), "R_temp_C": m.get("R_temp_C"),
                  "flux_temp_coeff_per_K": m.get("flux_temp_coeff_per_K"),
                  "flux_coeff_basis": m.get("flux_coeff_basis"),
                  "torque_factor": m.get("torque_factor"), "controller": (m.get("controller") or {}).get("variant"),
                  "thermal_calibration": ({k: v for k, v in m["thermal_calibration"].items() if k != "anchors"}
                                          if m.get("thermal_calibration") else None)},
        "method": "propeller Q(n) + bearing/windage -> current from the passport torque map at the magnet "
                  "temperature -> passport loss grid with R(T) -> slipstream film (outer_air) -> Configure's "
                  "lumped thermal estimate, iterated to the fixed point at each speed; limit by bisection",
    }
