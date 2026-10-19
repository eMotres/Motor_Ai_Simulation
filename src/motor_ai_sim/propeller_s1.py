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

A SYSTEM RATING, NOT A MOTOR-ONLY ONE (2026-10-11 review)
=========================================================
At every speed the converged point must also hold, besides the two temperatures:

* the CONTROLLER's continuous current of the selected drive (a computed PWM
  variant's board limit, or the die's controller block for a sine drive) at the
  chosen board cooling class -> ``controller``;
* the phase VOLTAGE at the MINIMUM pack voltage and the operating magnet
  temperature (the passport's hot psi-map, its PM flux shifted by k_psi(T_m),
  R(T_w)): at the grid's operating angle, else at a deeper field-weakening angle
  with more current (the losses then taken at that current), else ``voltage``;
* the DEMAGNETISATION current limit at the operating magnet temperature (the
  passport's limit at its probe temperature, scaled by the magnet card's knee
  margin HcJ(T) vs the self-demagnetising field; ``demag_basis`` says when only
  the reference-temperature probe exists) -> ``demag``;
* the BATTERY's continuous current at the minimum pack voltage, when the caller
  gives it -> ``battery_current``.

A constraint the model cannot check is reported ``unverified`` (``flags``), never
passed silently; with no controller data the answer is the motor-only limit with
``drive_feasibility = "unknown"``.  A loss group the grid lacks at a point
(``null`` cell) refuses the point (``loss_data``) instead of counting 0; a fixed
point that does not settle is ``nonconverged``, not ``runaway``.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from motor_ai_sim import propeller as pp

log = logging.getLogger(__name__)

SCHEMA = "propeller-s1-model-2"
#: schema 1 (before the system checks) still loads: its limits come out ``unknown``
SCHEMAS = ("propeller-s1-model-1", SCHEMA)
ALPHA_CU = 0.00393                    # /K, copper resistivity at 20 °C reference
T_CU_REF_C = 20.0
RUNAWAY_C = 600.0                     # a fixed point above this is a thermal runaway
FIXED_POINT_TOL_K = 0.01
FIXED_POINT_MAX_IT = 400
MU0 = 4e-7 * math.pi
SQ2, SQ3 = math.sqrt(2.0), math.sqrt(3.0)

SINE = "sine"
#: an ESC that is not one of the passport's computed boards (the one on the bench, a
#: commercial FOC ESC): no board model, its continuous current is the user's / the bench's
EXTERNAL_ESC = "external_esc"
#: the board cooling classes the passport rates its controllers for, plus the
#: ``slipstream`` class — the board in the propeller wash: needs the model's
#: board block and an air speed, so its limit is evaluated per operating point
BOARD_COOLING = ("favourable", "weak", "hot_motor", "slipstream")
DEFAULT_BOARD_COOLING = "favourable"

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
        # S1 models of the closed motors live in the private data repository (2026-10-19).
        from motor_ai_sim.private_data import private_path
        q = private_path("config", "propeller_s1")
        if q is not None and q.is_dir():
            dirs.append(q)
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
    if not isinstance(raw, Mapping) or raw.get("schema") not in SCHEMAS:
        raise S1Error(f"model: schema must be one of {SCHEMAS}")
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
    if g.get("gamma_deg") is not None:
        grid["gamma_deg"] = _table(g.get("gamma_deg"), len(rpm), len(cur), "grid.gamma_deg")
    m["grid"] = grid
    m["drives"] = {str(k): _drive_of(d, str(k)) for k, d in (raw.get("drives") or {}).items()}
    if raw.get("sine_controller") is not None:
        m["sine_controller"] = _controller_block(raw.get("sine_controller"), "sine_controller")
    if raw.get("external_esc") is not None:
        m["external_esc"] = _external_esc_block(raw.get("external_esc"))
    if raw.get("psi_map") is not None:
        m["psi_map"] = _psi_map_of(raw.get("psi_map"))
    dm = raw.get("demag")
    if dm is not None:
        if not isinstance(dm, Mapping) or _num(dm.get("I_limit_A")) is None or _num(dm.get("ref_magnet_C")) is None:
            raise S1Error("model: demag needs I_limit_A and ref_magnet_C")
        m["demag"] = dict(dm)
    bat = raw.get("battery")
    if bat is not None:
        if not isinstance(bat, Mapping) or not (0 < (_num(bat.get("v_min")) or 0) <= (_num(bat.get("v_max")) or 0)):
            raise S1Error("model: battery needs 0 < v_min <= v_max")
        m["battery"] = dict(bat)
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
        sf = raw["thermal_calibration"].get("slipstream_factor")
        if sf is not None and not (0.0 < (_num(sf) or 0.0) <= 1.5):
            raise S1Error("model: thermal_calibration.slipstream_factor must be in (0, 1.5]")
    m["flux_temp_coeff_per_K"] = _num(raw.get("flux_temp_coeff_per_K")) or 0.0
    return m


def _bus(v: Any, name: str) -> Optional[Dict[str, float]]:
    if v is None:
        return None
    lo, hi = _num((v or {}).get("min")), _num((v or {}).get("max"))
    if lo is None or hi is None or not (0 < lo <= hi):
        raise S1Error(f"model: {name}.bus_v needs 0 < min <= max")
    return {"min": lo, "nom": _num(v.get("nom")), "max": hi}


def _board_block(b: Any, name: str) -> Dict[str, Any]:
    """The board's thermal parameters + per-current inverter loss table (the
    passport's ``stage3.Board.export_block``): everything the S1 needs to
    re-evaluate the board limit at its own air temperature and wash speed."""
    if not isinstance(b, Mapping):
        raise S1Error(f"model: {name} must be a mapping")
    out = dict(b)
    for k in ("T_amb_cal_C", "T_hs_limit_C", "T_j_derate_C", "R_jhs_K_per_W", "K_board_cu_W_per_A2"):
        v = _num(b.get(k))
        if v is None or v < 0 or (v == 0 and k != "K_board_cu_W_per_A2"):
            raise S1Error(f"model: {name}.{k} must be a non-negative number (positive for the temperatures and R)")
    cls = b.get("classes")
    if not isinstance(cls, Mapping) or not cls:
        raise S1Error(f"model: {name}.classes must map cooling classes to Q_budget_W / R_sys_K_per_W")
    classes: Dict[str, Any] = {}
    for k, c in cls.items():
        q, r = _num((c or {}).get("Q_budget_W")), _num((c or {}).get("R_sys_K_per_W"))
        if q is None or r is None or q <= 0 or r <= 0:
            raise S1Error(f"model: {name}.classes.{k} needs positive Q_budget_W and R_sys_K_per_W")
        classes[str(k)] = {"Q_budget_W": q, "R_sys_K_per_W": r}
    tab = b.get("loss_table")
    if not isinstance(tab, Mapping):
        raise S1Error(f"model: {name}.loss_table must be a mapping")
    ii = tab.get("I_A")
    ok = isinstance(ii, list) and len(ii) >= 2 and all(_num(x) is not None for x in ii)
    if ok:
        xs = [float(x) for x in ii]
        ok = all(0.0 <= xs[i] < xs[i + 1] for i in range(len(xs) - 1))
    if not ok:
        raise S1Error(f"model: {name}.loss_table.I_A must be increasing non-negative numbers")
    table: Dict[str, Any] = {"I_A": xs}
    for k in ("P_inverter_W", "P_device_W"):
        v = tab.get(k)
        if not isinstance(v, list) or len(v) != len(xs) or any(_num(x) is None for x in v):
            raise S1Error(f"model: {name}.loss_table.{k} must have one value per loss_table.I_A")
        table[k] = [float(x) for x in v]
    out["classes"], out["loss_table"] = classes, table
    return out


def _controller_block(c: Any, name: str) -> Dict[str, Any]:
    if not isinstance(c, Mapping):
        raise S1Error(f"model: {name} must be a mapping")
    out = dict(c)
    lim = c.get("i_board_limit_A")
    if lim is not None:
        if not isinstance(lim, Mapping) or any(_num(x) is None or _num(x) <= 0 for x in lim.values()):
            raise S1Error(f"model: {name}.i_board_limit_A must map cooling classes to positive currents")
        out["i_board_limit_A"] = {str(k): float(x) for k, x in lim.items()}
    if c.get("board") is not None:
        out["board"] = _board_block(c.get("board"), f"{name}.board")
    out["bus_v"] = _bus(c.get("bus_v"), name)
    return out


def _external_esc_block(c: Any) -> Dict[str, Any]:
    """The model's default external ESC (the one the configuration ran on the bench):
    ``i_bus_cont_A`` / ``i_phase_cont_A`` (either may be absent), ``name``, ``basis``,
    ``bus_v`` (optional, the ESC's rated bus range)."""
    if not isinstance(c, Mapping):
        raise S1Error("model: external_esc must be a mapping")
    out = dict(c)
    for k in ("i_bus_cont_A", "i_phase_cont_A"):
        if c.get(k) is not None and not ((_num(c.get(k)) or 0) > 0):
            raise S1Error(f"model: external_esc.{k} must be a positive current")
        out[k] = _num(c.get(k))
    out["bus_v"] = _bus(c.get("bus_v"), "external_esc")
    return out


def _drive_of(d: Any, key: str) -> Dict[str, Any]:
    """One drive variant: its own (rpm, I) axes and tables (controller loss, motor
    PWM extra loss; null = not computed there) and the board's continuous current."""
    name = f"drives.{key}"
    out = _controller_block(d, name)
    rpm, cur = _axis(d.get("rpm"), f"{name}.rpm"), _axis(d.get("I_A"), f"{name}.I_A")
    out.update(rpm=rpm, I_A=cur)
    for k in ("controller_loss_W", "pwm_extra_W"):
        out[k] = None if d.get(k) is None else _table(d.get(k), len(rpm), len(cur), f"{name}.{k}")
    return out


def _psi_map_of(p: Any) -> Dict[str, Any]:
    if not isinstance(p, Mapping):
        raise S1Error("model: psi_map must be a mapping")
    cur, gam = p.get("I_A"), p.get("gamma_deg")
    if not isinstance(gam, list) or len(gam) < 2 or any(_num(x) is None for x in gam) \
            or any(b <= a for a, b in zip(gam, gam[1:])):
        raise S1Error("model: psi_map.gamma_deg must be increasing numbers")
    out = dict(p)
    out["I_A"] = _axis(cur, "psi_map.I_A")
    out["gamma_deg"] = [float(x) for x in gam]
    for k in ("psi_d_Wb", "psi_q_Wb", "T_Nm"):
        out[k] = _table(p.get(k), len(out["I_A"]), len(gam), f"psi_map.{k}")
    for k in ("pole_pairs", "psi_pm_Wb", "magnet_C"):
        if _num(p.get(k)) is None:
            raise S1Error(f"model: psi_map.{k} missing")
    out["gamma_max_deg"] = _num(p.get("gamma_max_deg")) or float(gam[-1])
    return out


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


def _first_current(row: Sequence[Optional[float]], cur: Sequence[float]) -> Optional[float]:
    return next((c for c, y in zip(cur, row) if y is not None), None)


def grid_eval(rpm_ax: Sequence[float], cur_ax: Sequence[float], table: Sequence[Sequence[Optional[float]]],
              rpm: float, I: float, *, kind: str, floors: Optional[Sequence[float]] = None
              ) -> Tuple[Optional[float], bool, bool]:
    """``grid_at`` plus whether the current lies below the first FEASIBLE cell of a
    row it reads (the first interval's line continued down: an extrapolation in
    current, flagged like the one in speed)."""
    v, below = grid_at(rpm_ax, cur_ax, table, rpm, I, kind=kind, floors=floors)
    if v is None or kind == "torque":
        return v, below, False
    if below or rpm <= rpm_ax[0]:
        rows = [0]
    elif rpm >= rpm_ax[-1]:
        rows = [len(rpm_ax) - 1]
    else:
        i0, i1 = _bracket(rpm_ax, rpm)
        rows = [i0] if rpm == rpm_ax[i0] else [i0, i1]
    below_i = False
    for r in rows:
        c0 = _first_current(table[r], cur_ax)
        if c0 is not None and I < c0 - 1e-9:
            below_i = True
    return v, below, below_i


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


def losses(m: Mapping[str, Any], rpm: float, I: float, t_winding_c: float,
           drive: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Motor losses by group [W] at (rpm, I, winding temperature) with ``drive``
    (``resolve_drive``; None = the model's own groups only).

    A group the grid HAS but cannot give at this point (a null cell) is listed in
    ``unavailable`` (the caller refuses the point; its 0 here is a placeholder, not
    a loss).  The drive's PWM extra loss comes from the drive's own grid; where
    that grid has no value it is listed in ``drive_unavailable`` and counted 0
    (flagged).  ``below_loss_grid_speed`` / ``_current``: a value read below the
    grid's first speed / first feasible current (extrapolated)."""
    g = m["grid"]
    r = resistance(m, t_winding_c)
    out: Dict[str, Any] = {"cu_dc": 3.0 * I * I * r}
    below_n = below_i = False
    unavailable: List[str] = []
    drive_unavailable: List[str] = []
    for k in LOSS_GROUPS:
        t = g["loss_W"].get(k)
        if t is None:
            out[k] = 0.0
            continue
        v, bn, bi = grid_eval(g["rpm"], g["I_A"], t, rpm, I, kind="loss", floors=g.get("I_floor_A"))
        below_n, below_i = below_n or bn, below_i or bi
        if v is None:
            unavailable.append(k)
        out[k] = 0.0 if v is None else v
    pw = (drive or {}).get("pwm_extra_W")
    if g["loss_W"].get("pwm_extra") is None and pw is not None:
        v, _, _ = grid_eval(drive["rpm"], drive["I_A"], pw, rpm, I, kind="loss")
        if v is None:
            drive_unavailable.append("pwm_extra")
        out["pwm_extra"] = 0.0 if v is None else v
    # resistance-limited eddy in the conductors goes with the conductivity
    out["cu_ac"] *= resistance(m, float(m["R_temp_C"])) / r
    out["mech"] = _mech_W(m, rpm)
    out["motor_total"] = sum(out[k] for k in ("cu_dc", "cu_ac", "fe_stator", "fe_rotor",
                                               "rotor_eddy", "pwm_extra", "mech"))
    out["R_phase_ohm"] = r
    out["below_loss_grid_speed"] = below_n
    out["below_loss_grid_current"] = below_i
    out["below_loss_grid"] = below_n or below_i
    out["unavailable"] = unavailable
    out["drive_unavailable"] = drive_unavailable
    return out


def controller_loss(m: Mapping[str, Any], rpm: float, I: float,
                    drive: Optional[Mapping[str, Any]] = None) -> Optional[float]:
    """Inverter + board copper loss of ``drive`` at (rpm, I); None = not computed
    there (or a sine drive / a model without one)."""
    if drive is not None:
        t = drive.get("controller_loss_W")
        if t is None:
            return None
        v, _ = grid_at(drive["rpm"], drive["I_A"], t, rpm, I, kind="loss")
        return v
    c = m.get("controller")                                     # schema 1
    if not c:
        return None
    v, _ = grid_at(c["rpm"], c["I_A"], c["loss_W"], rpm, I, kind="loss")
    return v


# ---------------------------------------------------------------------------
# The drive: controller envelope, pack
# ---------------------------------------------------------------------------

def _slipstream_block_of(ctrl: Mapping[str, Any], pre_basis: Any) -> Tuple[None, str]:
    """``slipstream`` has no precomputed limit: it is evaluated per operating
    point from the controller's ``board`` block (see :func:`board_limit_A`)."""
    if not ctrl.get("board"):
        raise S1Error("board cooling 'slipstream' needs the board's thermal parameters (the "
                      "controller's `board` block) to evaluate its limit at the S1 air "
                      "temperature and the propeller's wash speed: this model carries only "
                      "precomputed limits at the passport's cooling classes")
    return None, ("the slipstream class is evaluated per operating point, at the S1 air "
                  "temperature and the propeller's wash speed (board model): %s" % pre_basis)


def board_cooling_classes(ctrl: Optional[Mapping[str, Any]]) -> List[str]:
    """The board cooling classes a controller block supports: the classes its
    precomputed limits (``i_board_limit_A``) or its ``board`` block carry, plus
    ``slipstream`` where the block exists (that class has no precomputed limit —
    it is evaluated per operating point from the block, :func:`board_limit_A`).
    Without a ``board`` block ``slipstream`` is NOT offered: the route refuses it
    (:func:`_slipstream_block_of`), and the UI must not offer what cannot be run."""
    c = ctrl or {}
    have = set(c.get("i_board_limit_A") or {}) | set((c.get("board") or {}).get("classes") or {})
    blk = bool(c.get("board"))
    return [x for x in BOARD_COOLING if x in have or (x == "slipstream" and blk)]


def _controller_block_of(m: Mapping[str, Any], drive: Optional[str]) -> Optional[Mapping[str, Any]]:
    """The controller block a drive id selects (``sine`` -> ``sine_controller``, a PWM
    variant id -> its ``drives`` entry); None for ``external_esc`` / an unknown id."""
    did = str(drive or SINE)
    if did == EXTERNAL_ESC:
        return None
    if did == SINE:
        return m.get("sine_controller") or {}
    return (m.get("drives") or {}).get(did) or {}


def effective_board_cooling(m: Mapping[str, Any], drive: Optional[str],
                            cooling_context: Optional[str] = None) -> Tuple[Optional[str], str]:
    """THE board cooling class of a drive, decided by the machine - never by the
    user (owner 2026-10-15: the airflow must be unambiguous).  Returns
    ``(class, basis)``; the class is None for an external ESC (no board of ours).

    * ``cooling_context == "propeller"`` (the motor is cooled by the propeller wash
      - the S1 of a propeller) and the drive's controller block carries a ``board``
      block: ``slipstream`` - the board sits in the wash, its limit evaluated per
      operating point from the selected propeller's wash speed.
    * Otherwise the one class the controller is calibrated for: the only class the
      block carries, else ``favourable`` - the passport's rated airflow, the basis of
      its datasheet board limit (fullcard "Controller continuous").
    """
    if str(drive or SINE) == EXTERNAL_ESC:
        return None, "external ESC: no board of ours, its current is given by the user / bench"
    ctrl = _controller_block_of(m, drive)
    if cooling_context == "propeller" and (ctrl or {}).get("board"):
        return "slipstream", ("board in the propeller wash: limit evaluated at each operating "
                              "point from the selected propeller's wash speed")
    own = [c for c in board_cooling_classes(ctrl) if c != "slipstream"]
    if len(own) == 1:
        return own[0], f"the controller's one calibrated cooling class ({own[0]})"
    return DEFAULT_BOARD_COOLING, "board limit at the passport's rated airflow class (favourable)"


def board_limit_A(drive: Mapping[str, Any], *, ambient_c: float,
                  air_speed_ms: Optional[float] = None) -> Tuple[Optional[float], str]:
    """The drive's continuous board current at the S1's own air temperature:
    re-evaluated from the model's ``board`` block (the ``slipstream`` class at the
    operating point's wash speed), else the precomputed limit the model carries
    (calibrated at 45 °C ambient)."""
    blk = drive.get("board")
    if not blk:
        return drive.get("I_cont_A"), str(drive.get("I_cont_basis")
                        or "the model's precomputed board limit")
    from motor_ai_sim.passport_v1 import stage3 as S3
    bc = str(drive.get("board_cooling") or DEFAULT_BOARD_COOLING)
    try:
        i = S3.board_block_limit(blk, t_amb_c=float(ambient_c), cls=bc,
                                 air_speed_ms=air_speed_ms)
    except ValueError as exc:
        raise S1Error(str(exc))
    wash = (", wash %.2f m/s" % air_speed_ms) if (
        bc == "slipstream" and air_speed_ms is not None) else ""
    basis = ("board model re-evaluated at the S1 air temperature %.0f °C (class %s%s) from "
             "the model's board parameters" % (ambient_c, bc, wash))
    return i, basis


def resolve_drive(m: Mapping[str, Any], drive: Optional[str] = None,
                  board_cooling: Optional[str] = None, esc_i_bus_A: Optional[float] = None,
                  esc_i_phase_A: Optional[float] = None) -> Dict[str, Any]:
    """The selected drive: ``sine`` (default; the die's controller block bounds its
    current), one computed PWM variant id of ``m["drives"]`` (its board limit,
    its controller-loss and motor-PWM-extra grids) or ``external_esc``.  ``I_cont_A``
    None = the controller's continuous (phase) current is not known for this model.

    ``board_cooling``: the board's cooling class (``slipstream`` = the board in the
    propeller wash).  ``I_cont_A`` is the model's precomputed limit for the class
    (calibrated at 45 °C ambient); a model with a ``board`` block is re-evaluated
    at the S1's own air temperature per operating point (``board_limit_A``), and
    ``slipstream`` is always per-point (``I_cont_A`` stays None here).  The
    returned ``board_cooling_classes`` lists the classes this controller block
    supports (``board_cooling_classes``) — the UI offers only those.

    ``external_esc``: an ESC that is none of the passport's boards (a commercial FOC
    ESC, the one on the bench).  No board model and no board current from the
    passport: its continuous BUS current ``I_bus_cont_A`` (and/or phase current) is
    the caller's (``esc_i_*``), else the model's ``external_esc`` block (what the bench
    demonstrated).  Its losses and the motor's PWM extra loss are not known (flagged,
    not counted)."""
    bc = board_cooling or DEFAULT_BOARD_COOLING
    if bc not in BOARD_COOLING:
        raise S1Error(f"board cooling must be one of {BOARD_COOLING}")
    did = str(drive or SINE)
    if did == EXTERNAL_ESC:
        blk = m.get("external_esc") or {}
        for v, nm in ((esc_i_bus_A, "ESC bus current"), (esc_i_phase_A, "ESC phase current")):
            if v is not None and not ((_num(v) or 0) > 0):
                raise S1Error(f"{nm} must be a positive current")
        given = esc_i_bus_A is not None or esc_i_phase_A is not None
        ibus = _num(esc_i_bus_A) if given else blk.get("i_bus_cont_A")
        iph = _num(esc_i_phase_A) if given else blk.get("i_phase_cont_A")
        if given:
            basis = "given by the user (external ESC)"
        elif ibus is not None or iph is not None:
            basis = str(blk.get("basis") or "the model's external ESC")
        else:
            basis = "no ESC current given and the model carries no external ESC"
        return {"id": EXTERNAL_ESC, "kind": "external", "board_cooling": bc, "I_cont_A": iph,
                "I_bus_cont_A": ibus, "I_cont_basis": basis,
                "board_cooling_classes": [],     # an external ESC has no board of ours
                "name": (None if given else blk.get("name")) or "external ESC",
                "source": "caller" if given else ("model" if (ibus or iph) else None),
                "bus_v": None if given else blk.get("bus_v"), "controller_loss_W": None,
                "pwm_extra_W": None, "device": None, "carrier_hz": None, "n_parallel": None}
    if did == SINE:
        sc = m.get("sine_controller") or {}
        lim = (sc.get("i_board_limit_A") or {}).get(bc)
        basis = (sc.get("i_board_basis") if lim is not None
                 else "the model carries no controller block for a sine drive")
        if bc == "slipstream":
            lim, basis = _slipstream_block_of(sc, basis)
        return {"id": SINE, "kind": "sine", "board_cooling": bc, "I_cont_A": lim,
                "I_cont_basis": basis, "board": sc.get("board"),
                "board_cooling_classes": board_cooling_classes(sc),
                "bus_v": sc.get("bus_v"), "controller_loss_W": None, "pwm_extra_W": None,
                "device": None, "carrier_hz": None, "n_parallel": None}
    drives = m.get("drives") or {}
    if did not in drives:
        raise S1Error(f"unknown drive {did!r}: use {SINE!r} or one of {sorted(drives)}")
    d = dict(drives[did])
    lim = (d.get("i_board_limit_A") or {}).get(bc)
    basis = (d.get("i_board_basis") if lim is not None
             else "the variant carries no board continuous current")
    if bc == "slipstream":
        lim, basis = _slipstream_block_of(d, basis)
    d.update(id=did, kind="pwm", board_cooling=bc, I_cont_A=lim, I_cont_basis=basis,
             board_cooling_classes=board_cooling_classes(d))
    return d


def resolve_pack(m: Mapping[str, Any], v_min: Optional[float] = None, v_max: Optional[float] = None,
                 i_max_A: Optional[float] = None) -> Dict[str, Any]:
    """The battery the S1 point must run from: the caller's window, else the
    passport's pack.  ``i_max_A`` = its continuous current (None = not known)."""
    b = m.get("battery") or {}
    lo = _num(v_min) if v_min is not None else _num(b.get("v_min"))
    hi = _num(v_max) if v_max is not None else _num(b.get("v_max"))
    if lo is not None and hi is not None and not (0 < lo <= hi):
        raise S1Error("pack voltage window must satisfy 0 < v_min <= v_max")
    src = "caller" if (v_min is not None or v_max is not None) else ("passport battery" if b else None)
    return {"v_min": lo, "v_max": hi, "i_max_A": _num(i_max_A), "source": src}


def pack_fits_drive(drive: Mapping[str, Any], pack: Mapping[str, Any]) -> Optional[bool]:
    """Whether the pack's window lies inside the bus range the drive was computed
    over (Configure's rule, ``configuratorDrive.limitProblems``); None = unknown."""
    bus = drive.get("bus_v")
    if not bus or pack.get("v_min") is None or pack.get("v_max") is None:
        return None
    return pack["v_max"] <= bus["max"] * 1.001 and pack["v_min"] >= bus["min"] * 0.999


# ---------------------------------------------------------------------------
# Demagnetisation current limit at the magnet temperature
# ---------------------------------------------------------------------------

def demag_limit_at(m: Mapping[str, Any], t_magnet_c: float) -> Tuple[Optional[float], str]:
    """``(I_limit, basis)``: the demagnetisation current limit at ``t_magnet_c``.

    The passport probes demagnetisation at ONE magnet temperature.  Above it the
    limit is scaled by the knee margin of the magnet card: the armature field at
    the worst magnet point grows with the current, the margin it may use is the
    knee field minus the magnet's own demagnetising field,
    ``H_k(T) - H_self(T)``, ``H_k = knee_fraction · HcJ(T)``,
    ``H_self = Br(T) / (mu0 (mu_rec + Pc))`` (Pc = the permeance coefficient
    behind the grade's working temperature).  Below the probe temperature the
    probe's own limit is held (NdFeB is stronger cold: conservative).  Without a
    magnet card the probe's limit stands only AT its temperature: above it the
    answer is None (unverified)."""
    dm = m.get("demag")
    if not dm:
        return None, "unknown (the model has no demagnetisation limit)"
    i_ref, t_ref = float(dm["I_limit_A"]), float(dm["ref_magnet_C"])
    if t_magnet_c <= t_ref:
        return i_ref, f"passport demagnetisation probe at {t_ref:g} C (held below it)"
    mg = dm.get("magnet")
    if not mg:
        return None, f"reference temperature only ({t_ref:g} C), no magnet card to scale it"
    pc = float(dm.get("permeance_coeff") or 2.0)

    def margin(t: float) -> float:
        dt = t - float(mg["T_ref_C"])
        hcj = float(mg["Hcj_ref_A_m"]) * (1.0 + float(mg["beta_hcj_pct_per_K"]) / 100.0 * dt)
        br = float(mg["Br_ref_T"]) * (1.0 + float(mg["alpha_br_pct_per_K"]) / 100.0 * dt)
        return float(mg.get("knee_fraction") or 0.97) * hcj - br / (MU0 * (float(mg["mu_rec"]) + pc))
    m_ref = margin(t_ref)
    if m_ref <= 0:
        return None, "reference temperature only: the card's knee margin is not positive at the probe temperature"
    return (i_ref * max(0.0, margin(t_magnet_c)) / m_ref,
            f"reference temperature only: probe at {t_ref:g} C scaled by the knee margin of {mg.get('name')} "
            f"(Pc {pc:g})")


# ---------------------------------------------------------------------------
# Voltage at the minimum pack voltage and the operating magnet temperature
# ---------------------------------------------------------------------------

def _bilinear(xs: Sequence[float], ys: Sequence[float], tabs: Sequence[Sequence[Sequence[Optional[float]]]],
              x: float, y: float) -> Optional[List[float]]:
    """Bilinear values of every table in ``tabs`` at (x, y); None outside / at a null cell."""
    if not (xs[0] - 1e-9 <= x <= xs[-1] + 1e-9 and ys[0] - 1e-9 <= y <= ys[-1] + 1e-9):
        return None
    i0, i1 = _bracket(xs, min(max(x, xs[0]), xs[-1]))
    j0, j1 = _bracket(ys, min(max(y, ys[0]), ys[-1]))
    wx = (x - xs[i0]) / (xs[i1] - xs[i0]) if xs[i1] > xs[i0] else 0.0
    wy = (y - ys[j0]) / (ys[j1] - ys[j0]) if ys[j1] > ys[j0] else 0.0
    wx, wy = min(max(wx, 0.0), 1.0), min(max(wy, 0.0), 1.0)
    out = []
    for tab in tabs:
        a, b, c, d = tab[i0][j0], tab[i0][j1], tab[i1][j0], tab[i1][j1]
        if a is None or b is None or c is None or d is None:
            return None
        out.append(a * (1 - wx) * (1 - wy) + b * (1 - wx) * wy + c * wx * (1 - wy) + d * wx * wy)
    return out


def dq_state(m: Mapping[str, Any], I: float, gamma_deg: float, t_magnet_c: float) -> Optional[Dict[str, float]]:
    """psi_d, psi_q, torque of the hot psi-map at (I rms, gamma) with the PM flux
    moved to ``t_magnet_c`` (psi_d + (k_psi(T)/k_psi(T_map) - 1)·psi_PM; the torque
    by the same shift, 3/2·p·dpsi·i_q).  None outside the characterised map."""
    pm = m["psi_map"]
    v = _bilinear(pm["I_A"], pm["gamma_deg"], (pm["psi_d_Wb"], pm["psi_q_Wb"], pm["T_Nm"]), I, gamma_deg)
    if v is None:
        return None
    pd, pq, tq = v
    dpsi = (flux_factor(m, t_magnet_c) / flux_factor(m, float(pm["magnet_C"])) - 1.0) * float(pm["psi_pm_Wb"])
    g = math.radians(gamma_deg)
    i_d, i_q = -SQ2 * I * math.sin(g), SQ2 * I * math.cos(g)
    return {"psi_d": pd + dpsi, "psi_q": pq, "i_d": i_d, "i_q": i_q,
            "T": tq + 1.5 * float(pm["pole_pairs"]) * dpsi * i_q}


def phase_voltage(m: Mapping[str, Any], rpm: float, I: float, gamma_deg: float, t_magnet_c: float,
                  r_phase: float) -> Optional[float]:
    """Phase-voltage PEAK |R·i + j·w·psi| (the passport's rule) [V]; None off the map."""
    s = dq_state(m, I, gamma_deg, t_magnet_c)
    if s is None:
        return None
    w = 2.0 * math.pi * rpm / 60.0 * float(m["psi_map"]["pole_pairs"])
    return math.hypot(r_phase * s["i_d"] - w * s["psi_q"], r_phase * s["i_q"] + w * s["psi_d"])


def _mtpa_gamma(m: Mapping[str, Any], I: float) -> float:
    """The operating line below the voltage limit: the loss grid's angles at its
    lowest speed (MTPA or the fixed operating angle), linear in I, 0 at I = 0."""
    ga = (m["grid"].get("gamma_deg") or [None])[0]
    pts = [(c, g) for c, g in zip(m["grid"]["I_A"], ga or []) if g is not None]
    if not pts:
        return 0.0
    if I >= pts[-1][0]:
        return float(pts[-1][1])
    xs, ys = [0.0] + [p[0] for p in pts], [0.0] + [p[1] for p in pts]
    lo, hi = _bracket(xs, I)
    return ys[lo] + (ys[hi] - ys[lo]) * (I - xs[lo]) / (xs[hi] - xs[lo])


def operating_angle(m: Mapping[str, Any], rpm: float, I: float, v_limit: float, t_magnet_c: float,
                    r_phase: float) -> Optional[float]:
    """The passport's operating-angle rule at another voltage / magnet temperature:
    the operating line when its voltage fits, else the smallest angle up to the
    characterised field weakening (``gamma_max_deg``) that fits; None beyond it."""
    g0 = _mtpa_gamma(m, I)
    gmax = float(m["psi_map"]["gamma_max_deg"])
    fits = lambda g: (lambda v: v is not None and v <= v_limit)(          # noqa: E731
        phase_voltage(m, rpm, I, g, t_magnet_c, r_phase))
    if fits(g0):
        return g0
    if not fits(gmax):
        return None
    lo, hi = g0, gmax
    while hi - lo > 0.02:
        mid = 0.5 * (lo + hi)
        if fits(mid):
            hi = mid
        else:
            lo = mid
    return hi


def voltage_check(m: Mapping[str, Any], drive: Mapping[str, Any], rpm: float, I: float,
                  t_magnet_c: float, t_winding_c: float, v_dc: Optional[float],
                  i_hint: Optional[float] = None) -> Dict[str, Any]:
    """Re-check the voltage of the grid's operating point at ``v_dc`` (the minimum
    pack voltage) with the magnets at ``t_magnet_c`` and the winding at ``t_winding_c``.

    ``status``: ``ok`` (the grid's angle fits), ``deeper_fw`` (only a deeper field-
    weakening angle fits; ``I_A`` is then the smallest current that still makes the
    point's torque there - more than the grid's), ``infeasible`` (no current up to
    the cap makes it) or ``unverified`` (no psi-map / pack voltage / modulation
    limit, or the point lies off the characterised map)."""
    vm = m.get("voltage") or {}
    mod = _num(drive.get("m_max")) or _num(vm.get("m"))
    v_grid = _num(vm.get("v_dc_grid"))
    if not m.get("psi_map") or v_dc is None or not mod or not v_grid:
        return {"status": "unverified", "I_A": I}
    r = resistance(m, t_winding_c)
    v_lim = mod * float(v_dc) / SQ3
    # the grid's operating angle: the passport's rule at its own bus and temperatures
    g_ref = operating_angle(m, rpm, I, mod * v_grid / SQ3, float(m["magnet_ref_temp_C"]), float(m["R_phase_ohm"]))
    if g_ref is None:
        g_ref = _mtpa_gamma(m, I)
    ref = dq_state(m, I, g_ref, t_magnet_c)
    v0 = phase_voltage(m, rpm, I, g_ref, t_magnet_c, r)
    base = {"v_dc": float(v_dc), "V_limit_V": v_lim, "modulation": mod}
    if ref is None or v0 is None:
        return {"status": "unverified", "I_A": I, **base}
    if v0 <= v_lim:
        return {"status": "ok", "I_A": I, "gamma_deg": g_ref, "V_V": v0, **base}
    target = ref["T"]
    cap = min(current_cap(m), float(m["psi_map"]["I_A"][-1]))

    def ok(i: float) -> Optional[float]:
        g = operating_angle(m, rpm, i, v_lim, t_magnet_c, r)
        if g is None:
            return None
        s = dq_state(m, i, g, t_magnet_c)
        return g if (s is not None and s["T"] >= target) else None
    prev, hit = I, None
    if i_hint is not None and I < i_hint <= cap:
        # warm start (the fixed point's previous answer): the edge is usually close by
        if ok(i_hint) is not None:
            hit = i_hint
            near = max(I, i_hint - 0.3)
            if near > I and ok(near) is None:
                prev = near
            prev = i_hint
    if hit is None:
        start = prev
        for k in range(1, 25):
            i = start + (cap - start) * k / 24.0
            if ok(i) is not None:
                hit = i
                break
            prev = i
    if hit is None:
        return {"status": "infeasible", "I_A": None, "gamma_deg": None, "V_V": v0, **base}
    lo, hi = prev, hit
    while hi - lo > 0.01:
        mid = 0.5 * (lo + hi)
        if ok(mid) is not None:
            hi = mid
        else:
            lo = mid
    return {"status": "deeper_fw", "I_A": hi, "gamma_deg": ok(hi), "V_V": v_lim, "I_grid_A": I, **base}


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


def model_slipstream(m: Mapping[str, Any], position: Optional[str] = None,
                     factor: Optional[float] = None) -> Tuple[Optional[str], Optional[float], str]:
    """``(position, factor, source)`` the S1 uses for the air at the motor: the caller's
    (``caller``), else the model's calibrated factor (``model``: fitted to bench runs of
    this configuration, or transferred from a sibling with the same frame -
    ``thermal_calibration.slipstream_factor_status``), else the propeller module's
    default position (``default``, behind_hub 0.4, an assumption)."""
    if factor is not None or position is not None:
        return position, factor, "caller"
    sf = _num((m.get("thermal_calibration") or {}).get("slipstream_factor"))
    if sf:
        return None, sf, "model"
    return None, None, "default"


def thermal_domain(m: Mapping[str, Any], air_speed_ms: Optional[float]) -> Optional[str]:
    """Where ``air_speed_ms`` lies against the calibration's anchors: ``fit`` (inside
    the slipstream speeds the network was fitted at), ``validated`` (outside them
    but inside the span the held-out check anchors extend it to), ``extrapolated``
    (outside both); None when the model is uncalibrated or states no range.  No
    margin: a point beyond the last anchor is an extrapolation."""
    cal = m.get("thermal_calibration") or {}
    fit = (cal.get("fit_domain") or {}).get("air_speed_ms") or cal.get("anchor_air_speed_ms")
    v = _num(air_speed_ms)
    if not fit or v is None:
        return None
    lo, hi = float(fit[0]), float(fit[1])
    if lo - 1e-9 <= v <= hi + 1e-9:
        return "fit"
    val = (cal.get("validation_domain") or {}).get("air_speed_ms")
    if val and min(lo, float(val[0])) - 1e-9 <= v <= max(hi, float(val[1])) + 1e-9:
        return "validated"
    return "extrapolated"


def calibration_covers(m: Mapping[str, Any], air_speed_ms: Optional[float]) -> Optional[bool]:
    """Whether ``air_speed_ms`` lies inside the calibration's fitted or validated
    slipstream range (``thermal_domain``); None when the model is uncalibrated or
    its calibration states no range."""
    d = thermal_domain(m, air_speed_ms)
    return None if d is None else d != "extrapolated"


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
# Configure's thermal tiles = THIS model (owner 2026-10-19: "one model only, the server is the truth")
# ---------------------------------------------------------------------------

def thermal_at(m: Mapping[str, Any], rpm: float, I: float, h: float, ambient_c: float,
               drive: Optional[Mapping[str, Any]] = None, max_it: int = FIXED_POINT_MAX_IT
               ) -> Optional[Dict[str, Any]]:
    """The model's steady temperatures at a GIVEN speed and current (Configure's knobs): the
    passport's losses at (rpm, I) with the copper at its own temperature, through the calibrated
    network (``temperatures``), iterated in the winding temperature exactly like ``_fixed_point``
    (which also re-derives the current from the torque; here the current is the caller's).  At a
    converged S1 point (rpm, I) it returns that point's temperatures.  ``drive`` = a mapping with
    the variant's ``rpm``/``I_A``/``pwm_extra_W`` (``resolve_drive`` or ``configure_thermal_model``'s
    ``drives``), None = sine.  None when a loss group has no value here; ``state`` runaway /
    nonconverged otherwise as the S1 fixed point says it.
    ``web/src/lib/s1Thermal.ts`` is the same function in the browser (parity: tests/test_configure_thermal_parity.py)."""
    cal = calibration_of(m)
    t_w = float(ambient_c)
    damping, last_res = 1.0, float("inf")
    for it in range(1, max_it + 1):
        ls = losses(m, rpm, I, t_w, drive)
        if ls["unavailable"]:
            return None
        th = temperatures(m["thermal_geom"], ls["cu_dc"] + ls["cu_ac"], ls["fe_rotor"] + ls["rotor_eddy"],
                          ls["motor_total"], h, ambient_c, cal)
        res = abs(th["winding"] - t_w)
        if res > last_res * 0.999 and it > 3:
            damping = max(0.05, damping * 0.5)
        last_res = res
        t_w += damping * (th["winding"] - t_w)
        if t_w > RUNAWAY_C:
            return {"state": "runaway", "iterations": it}
        if res < FIXED_POINT_TOL_K:
            ls = losses(m, rpm, I, t_w, drive)
            th = temperatures(m["thermal_geom"], ls["cu_dc"] + ls["cu_ac"], ls["fe_rotor"] + ls["rotor_eddy"],
                              ls["motor_total"], h, ambient_c, cal)
            return {"state": "converged", "iterations": it, "T_winding_C": th["winding"],
                    "T_magnet_C": th["magnet"], "T_housing_C": th["housing"],
                    "losses_W": {k: ls[k] for k in ("cu_dc", "cu_ac", "fe_stator", "fe_rotor", "rotor_eddy",
                                                    "pwm_extra", "mech", "motor_total")}}
    return {"state": "nonconverged", "iterations": max_it}


def load_point(m: Mapping[str, Any], rpm: float, q_load_Nm: float, h: float, ambient_c: float,
               drive: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The S1 fixed point at ONE speed for a load torque already known (the propeller's at that
    speed): the current from the torque map at the magnets' own temperature, the losses, the
    calibrated network - ``_fixed_point`` itself, with the bearing + windage torque on top as
    ``solve_point`` adds it.  The pack's voltage re-check (deeper field weakening at the minimum pack
    voltage) is NOT applied here (no pack): where it would raise the current the S1 row carries more.
    ``web/src/lib/s1Thermal.ts`` evaluates the same for every knob position (Configure's current and
    temperatures in propeller mode).  None = no current makes the torque / no loss data."""
    w = 2.0 * math.pi * rpm / 60.0
    q_em = float(q_load_Nm) + (_mech_W(m, rpm) / w if w > 0 else 0.0)
    fp = _fixed_point(m, rpm, q_em, h, ambient_c, calibration_of(m), dict(drive or {}), {"v_min": None},
                      1.0, FIXED_POINT_MAX_IT)
    if fp["state"] == "nonconverged":
        fp2 = _fixed_point(m, rpm, q_em, h, ambient_c, calibration_of(m), dict(drive or {}), {"v_min": None},
                           0.2, 3 * FIXED_POINT_MAX_IT)
        fp = fp2 if fp2["state"] != "nonconverged" else fp
    if fp["state"] != "converged":
        return {"state": fp["state"], "status": fp.get("status")}
    I = fp["I"]
    t = thermal_at(m, rpm, I, h, ambient_c, drive)
    return {**(t or {}), "state": "converged", "current_A": I, "torque_em_Nm": q_em}


def configure_thermal_model(m: Mapping[str, Any]) -> Dict[str, Any]:
    """What Configure needs to evaluate ``thermal_at`` itself for every knob position (no request per
    slider move): the loss grid, the copper resistance law, bearings + windage, each drive variant's
    PWM extra-loss grid, the thermal geometry, the fitted calibration and slipstream factor, the
    limits, and the torque map + flux law ``load_point`` finds the current with.  Nothing else of the
    model (voltage / psi-map, demagnetisation, controller) leaves the server here."""
    g = m["grid"]
    grid: Dict[str, Any] = {"rpm": list(g["rpm"]), "I_A": list(g["I_A"]), "T_Nm": g["T_Nm"],
                            "loss_W": dict(g["loss_W"])}
    if g.get("I_floor_A"):
        grid["I_floor_A"] = list(g["I_floor_A"])
    lims = limits_of(m)
    cal_raw = m.get("thermal_calibration") or {}
    return {
        "schema": "configure-thermal-1",
        "die": m.get("die"), "config": m.get("config"),
        "R_phase_ohm": float(m["R_phase_ohm"]), "R_temp_C": float(m["R_temp_C"]),
        # the load side of ``load_point``: the torque map at the magnets' own temperature
        "torque_factor": float(m["torque_factor"]), "flux_temp_coeff_per_K": float(m["flux_temp_coeff_per_K"]),
        "magnet_ref_temp_C": float(m["magnet_ref_temp_C"]), "I_cap_A": current_cap(m),
        "grid": grid, "mech_W": m.get("mech_W"),
        "drives": {k: {"rpm": d["rpm"], "I_A": d["I_A"], "pwm_extra_W": d.get("pwm_extra_W")}
                   for k, d in (m.get("drives") or {}).items()},
        "thermal_geom": dict(m["thermal_geom"]),
        "calibration": calibration_of(m), "calibrated": bool(cal_raw),
        "slipstream_factor": model_slipstream(m)[1],
        "fit_domain": (cal_raw.get("fit_domain") or {}).get("air_speed_ms") or cal_raw.get("anchor_air_speed_ms"),
        "limits": {"winding_C": lims["winding_C"], "magnet_C": lims["magnet_C"],
                   # the RATED point (owner 2026-10-19): coils at 180 °C
                   "rated_winding_C": _rated_winding_c()},
    }


def _rated_winding_c() -> float:
    """The rated winding temperature of a PROPELLER machine (this model only
    exists for one), from its one place (``configure_limits.rated_winding``)."""
    from motor_ai_sim.configure_limits import rated_winding
    return rated_winding("propeller")[0]


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
    dm = m.get("demag") or {}
    return {"winding_C": w, "winding_basis": w_basis,
            "magnet_C": mg, "magnet_basis": str(lim.get("magnet_basis") or "magnet card"),
            "I_max_A": current_cap(m), "I_max_basis": str(lim.get("I_max_basis") or "top of the passport current grid"),
            "rpm_max": speed_cap(m), "rpm_max_basis": str(lim.get("rpm_max_basis") or "top of the passport speed grid"),
            "demag_ref_A": _num(dm.get("I_limit_A")), "demag_ref_magnet_C": _num(dm.get("ref_magnet_C"))}


def _fixed_point(m: Mapping[str, Any], rpm: float, q_em: float, h: float, ambient_c: float,
                 cal: Mapping[str, float], drive: Mapping[str, Any], pack: Mapping[str, Any],
                 damping0: float, max_it: int) -> Dict[str, Any]:
    """Iterate current -> voltage re-check -> losses -> temperatures to the fixed point.

    ``state``: ``converged``, ``no_current`` (``status`` says why: current cap,
    voltage at the grid's bus, voltage at the minimum pack voltage), ``loss_data``
    (a required loss group has no value here), ``runaway`` (the iterate left any
    physical temperature: no steady state) or ``nonconverged`` (the iteration did
    not settle within ``max_it`` - NOT a proof that no steady state exists)."""
    t_w = t_m = float(ambient_c)
    damping, last_res = damping0, float("inf")
    vc: Optional[Dict[str, Any]] = None
    I = I_t = None
    for it in range(1, max_it + 1):
        I_t, status = current_for_torque(m, rpm, q_em, t_m)
        if I_t is None:
            return {"state": "no_current", "status": status, "t_w": t_w, "t_m": t_m, "it": it, "voltage": vc}
        hint = vc["I_A"] if (vc and vc.get("status") == "deeper_fw") else None
        vc = voltage_check(m, drive, rpm, I_t, t_m, t_w, pack.get("v_min"), hint)
        if vc["status"] == "infeasible":
            return {"state": "no_current", "status": "voltage", "t_w": t_w, "t_m": t_m, "it": it, "voltage": vc}
        I = max(I_t, float(vc["I_A"]))
        if I > current_cap(m) + 1e-9:
            return {"state": "no_current", "status": "current", "t_w": t_w, "t_m": t_m, "it": it, "voltage": vc}
        ls = losses(m, rpm, I, t_w, drive)
        if ls["unavailable"]:
            return {"state": "loss_data", "groups": ls["unavailable"], "I": I, "it": it, "voltage": vc}
        th = temperatures(m["thermal_geom"], ls["cu_dc"] + ls["cu_ac"], ls["fe_rotor"] + ls["rotor_eddy"],
                          ls["motor_total"], h, ambient_c, cal)
        res = max(abs(th["winding"] - t_w), abs(th["magnet"] - t_m))
        if res > last_res * 0.999 and it > 3:
            damping = max(0.05, damping * 0.5)
        last_res = res
        t_w += damping * (th["winding"] - t_w)
        t_m += damping * (th["magnet"] - t_m)
        if t_w > RUNAWAY_C or t_m > RUNAWAY_C:
            return {"state": "runaway", "I": I, "it": it, "voltage": vc}
        if res < FIXED_POINT_TOL_K:
            return {"state": "converged", "I": I, "I_grid": I_t, "t_w": t_w, "t_m": t_m, "it": it, "voltage": vc}
    return {"state": "nonconverged", "I": I, "I_grid": I_t, "t_w": t_w, "t_m": t_m, "it": max_it,
            "residual_K": last_res, "voltage": vc}


def _exceed(value: float, limit: float, span: float) -> float:
    return (value - limit) / max(span, 1e-9)


def solve_point(m: Mapping[str, Any], prop: pp.Propeller, rpm: float, *, ambient_c: float,
                rho: float, lims: Mapping[str, Any], position: Optional[str] = None,
                factor: Optional[float] = None, drive: Any = None,
                pack: Optional[Mapping[str, Any]] = None,
                load: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Everything at one propeller speed: load, current, losses, air, temperatures,
    margins and what (if anything) stops it.  ``drive`` = a ``resolve_drive`` dict
    or id (default sine), ``pack`` = a ``resolve_pack`` dict (default the passport's).

    ``violations`` lists every limit the converged point breaks, worst first
    (temperatures by their share of the limit's rise over ambient, currents by
    their share of the limit); ``binding`` is the first of them.

    ``load`` (default None = the propeller, unchanged): a callable
    ``load(rpm, rho, position, factor)`` returning the ``propeller.operating_point``
    keys for the load seen at the MOTOR shaft - used by the drive-train system solve
    (modules/drivetrain) to put a transmission between motor and propeller."""
    drv = drive if isinstance(drive, Mapping) else resolve_drive(m, drive)
    pk = pack if pack is not None else resolve_pack(m)
    position, factor, ss_src = model_slipstream(m, position, factor)
    op = (load(rpm, rho, position, factor) if load is not None
          else pp.operating_point(prop, rpm, rho, position=position, factor=factor))
    if ss_src == "model":
        op = {**op, "slipstream_position": "calibrated"}
    w = 2.0 * math.pi * rpm / 60.0
    p_mech = _mech_W(m, rpm)
    q_em = op["torque_Nm"] + (p_mech / w if w > 0 else 0.0)
    D = float(m["thermal_geom"]["statorOD_mm"]) / 1000.0
    h = housing_film(op["air_speed_ms"], ambient_c, D)
    # the board limit at this speed's air: the S1 air temperature (and, for the
    # slipstream class, the wash speed) — re-evaluated per point, not precomputed
    i_cont, i_cont_basis = board_limit_A(drv, ambient_c=ambient_c,
                                         air_speed_ms=op.get("air_speed_ms"))
    row: Dict[str, Any] = {
        "rpm": float(rpm), "thrust_N": op["thrust_N"], "torque_prop_Nm": op["torque_Nm"],
        "torque_em_Nm": q_em, "shaft_power_W": op["shaft_power_W"],
        "air_speed_ms": op["air_speed_ms"], "h_W_m2K": h,
        "slipstream_factor": op.get("slipstream_factor"), "slipstream_position": op.get("slipstream_position"),
        "slipstream_source": ss_src,
        "extrapolated": bool(op["extrapolated"]), "power_estimated": bool(op["power_estimated"]),
        "current_A": None, "losses_W": None, "T_winding_C": None, "T_magnet_C": None,
        "T_housing_C": None, "margin_winding_K": None, "margin_magnet_K": None,
        "electrical_power_W": None, "battery_power_W": None, "controller_loss_W": None,
        "battery_current_A": None, "controller_limit_A": i_cont,
        "controller_limit_basis": i_cont_basis,
        "controller_bus_limit_A": drv.get("I_bus_cont_A"),
        "demag_limit_A": None, "demag_basis": None, "voltage": None, "current_grid_A": None,
        "converged": None, "iterations": 0, "below_loss_grid": False, "below_loss_grid_speed": False,
        "below_loss_grid_current": False, "losses_unavailable": [], "drive_losses_unavailable": [],
        "fw_floor": False, "ok": False, "binding": None, "violations": [],
    }
    if rpm > lims["rpm_max"] + 1e-6:
        row["binding"] = "speed"
        return row
    cal = calibration_of(m)
    # above the no-load voltage limit the drive carries d-axis current at any load
    row["fw_floor"] = floor_current(m, rpm) > 0.0
    fp = _fixed_point(m, rpm, q_em, h, ambient_c, cal, drv, pk, 1.0, FIXED_POINT_MAX_IT)
    if fp["state"] == "nonconverged":
        # one more, heavily damped, before calling it: a slow orbit is not a runaway
        fp2 = _fixed_point(m, rpm, q_em, h, ambient_c, cal, drv, pk, 0.2, 3 * FIXED_POINT_MAX_IT)
        fp = fp2 if fp2["state"] != "nonconverged" else fp
    row["iterations"] = fp["it"]
    row["voltage"] = fp.get("voltage")
    if fp["state"] == "no_current":
        # the torque is out of reach (at the current cap or the voltage limit).  When the
        # iterate is already past a temperature limit the motor overheats on the way there
        # (the hot magnets lost the flux): that limit binds, not the current
        ow, om = fp["t_w"] - lims["winding_C"], fp["t_m"] - lims["magnet_C"]
        row["binding"] = fp["status"] if max(ow, om) <= 0 else ("winding" if ow >= om else "magnet")
        row["violations"] = [row["binding"]]
        return row
    if fp["state"] == "loss_data":
        row.update(binding="loss_data", violations=["loss_data"], losses_unavailable=fp["groups"],
                   current_A=fp["I"])
        return row
    if fp["state"] in ("runaway", "nonconverged"):
        # no steady state found: the temperatures are not printed (they are not a state of the motor)
        row.update(binding=fp["state"], violations=[fp["state"]], converged=False, current_A=fp["I"])
        return row
    I, t_w = fp["I"], fp["t_w"]
    ls = losses(m, rpm, I, t_w, drv)
    th = temperatures(m["thermal_geom"], ls["cu_dc"] + ls["cu_ac"], ls["fe_rotor"] + ls["rotor_eddy"],
                      ls["motor_total"], h, ambient_c, cal)
    p_in = op["shaft_power_W"] + ls["motor_total"]
    pc = (controller_loss(m, rpm, I, drv if drv.get("kind") == "pwm" else None)
          if (drv.get("kind") == "pwm" or (m.get("controller") and drv.get("kind") == "sine")) else None)
    p_batt = (p_in + pc) if pc is not None else None
    i_batt = ((p_batt if p_batt is not None else p_in) / pk["v_min"]) if pk.get("v_min") else None
    i_dm, dm_basis = demag_limit_at(m, th["magnet"])
    row.update(converged=True, current_A=I, current_grid_A=fp["I_grid"],
               T_winding_C=th["winding"], T_magnet_C=th["magnet"], T_housing_C=th["housing"],
               losses_W={k: ls[k] for k in ("cu_dc", "cu_ac", "fe_stator", "fe_rotor", "rotor_eddy",
                                             "pwm_extra", "mech", "motor_total")},
               R_phase_ohm=ls["R_phase_ohm"], below_loss_grid=bool(ls["below_loss_grid"]),
               below_loss_grid_speed=bool(ls["below_loss_grid_speed"]),
               below_loss_grid_current=bool(ls["below_loss_grid_current"]),
               drive_losses_unavailable=list(ls["drive_unavailable"]),
               electrical_power_W=p_in, controller_loss_W=pc, battery_power_W=p_batt,
               battery_current_A=i_batt, demag_limit_A=i_dm, demag_basis=dm_basis,
               margin_winding_K=lims["winding_C"] - th["winding"],
               margin_magnet_K=lims["magnet_C"] - th["magnet"])
    viol: List[Tuple[str, float]] = []
    if th["winding"] > lims["winding_C"]:
        viol.append(("winding", _exceed(th["winding"], lims["winding_C"], lims["winding_C"] - ambient_c)))
    if th["magnet"] > lims["magnet_C"]:
        viol.append(("magnet", _exceed(th["magnet"], lims["magnet_C"], lims["magnet_C"] - ambient_c)))
    if i_dm is not None and I > i_dm + 1e-9:
        viol.append(("demag", _exceed(I, i_dm, i_dm) if i_dm > 0 else float("inf")))
    if row["controller_limit_A"] is not None and I > float(row["controller_limit_A"]) + 1e-9:
        viol.append(("controller", _exceed(I, float(row["controller_limit_A"]),
                                           float(row["controller_limit_A"]))))
    ib = drv.get("I_bus_cont_A")
    if ib is not None and i_batt is not None and i_batt > float(ib) + 1e-9:
        # an external ESC rated by its continuous BUS current (at the pack's minimum voltage)
        e = _exceed(i_batt, float(ib), float(ib))
        if not any(k == "controller" for k, _ in viol):
            viol.append(("controller", e))
        else:
            viol = [(k, max(v, e) if k == "controller" else v) for k, v in viol]
    if pk.get("i_max_A") is not None and i_batt is not None and i_batt > float(pk["i_max_A"]) + 1e-9:
        viol.append(("battery_current", _exceed(i_batt, float(pk["i_max_A"]), float(pk["i_max_A"]))))
    viol.sort(key=lambda kv: -kv[1])
    row["violations"] = [k for k, _ in viol]
    if viol:
        row["binding"] = viol[0][0]
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
    """``(row at lo side, row at hi side)`` of the ok / not-ok edge between ``lo``
    and ``hi`` (either side may be the passing one), bisected to ``tol_rpm``."""
    r_lo, r_hi = f(lo), f(hi)
    want = r_lo["ok"]
    while hi - lo > tol_rpm:
        mid = 0.5 * (lo + hi)
        r = f(mid)
        if r["ok"] == want:
            lo, r_lo = mid, r
        else:
            hi, r_hi = mid, r
    return r_lo, r_hi


# The supply side of the system (the drive's continuous current, the ESC bus current, the pack's current): the
# load chart draws its limit as a separate line, the motor's own limit (winding, magnet, demagnetisation,
# voltage, data) as another (owner 2026-10-18: "the controller must not cut the motor's S1 line").
SUPPLY_VIOLATIONS = ("controller", "battery_current")


def _view(row: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    """``row`` judged by one side only: ``motor`` drops the supply violations, ``supply`` keeps only them (a
    point the motor model cannot solve has no supply verdict, so it passes on that side)."""
    v = list(row.get("violations") or [])
    keep = ([k for k in v if k not in SUPPLY_VIOLATIONS] if kind == "motor"
            else [k for k in v if k in SUPPLY_VIOLATIONS])
    if keep == v and (kind == "motor" or row["ok"] == (not keep)):
        return row
    out = dict(row)
    out.update(violations=keep, ok=not keep, binding=keep[0] if keep else None)
    return out


def _side_limit(f: Callable[[float], Dict[str, Any]], cache: Mapping[float, Dict[str, Any]], kind: str,
                tol_rpm: float) -> Optional[Dict[str, Any]]:
    """The top of the passing speeds judged by one side (``_view``), its edge bisected like the system limit.
    None when that side never fails in the scan (no supply limit: a sine drive without a board limit) or for
    the supply side when it fails from the lowest speed on (``rpm`` None, ``binding`` set)."""
    g = lambda r: _view(f(r), kind)  # noqa: E731
    for _ in range(2):
        allv = sorted((_view(r, kind) for r in list(cache.values())), key=lambda r: r["rpm"])
        for a, b in zip(allv, allv[1:]):
            if b["rpm"] - a["rpm"] > tol_rpm and a["ok"] != b["ok"]:
                _bisect_edge(g, a["rpm"], b["rpm"], tol_rpm)
    allv = sorted((_view(r, kind) for r in cache.values()), key=lambda r: r["rpm"])
    oks = [i for i, r in enumerate(allv) if r["ok"]]
    if not any(not r["ok"] for r in allv):
        return None if kind == "supply" else {"rpm": allv[-1]["rpm"], "binding": "speed_range",
                                              "lower_bound": True, "violations_above": []}
    if not oks:
        return {"rpm": None, "binding": allv[0]["binding"], "lower_bound": False,
                "violations_above": list(allv[0]["violations"])}
    top = max(oks)
    best = allv[top]
    if top == len(allv) - 1:
        nxt, binding = None, "speed_range"
    else:
        nxt = allv[top + 1]
        binding = nxt["binding"]
    return {"rpm": best["rpm"], "binding": binding, "lower_bound": binding == "speed_range",
            "violations_above": list((nxt or {}).get("violations") or []),
            "current_A": best.get("current_A"), "battery_current_A": best.get("battery_current_A"),
            "T_winding_C": best.get("T_winding_C"), "T_magnet_C": best.get("T_magnet_C"),
            "controller_limit_A": best.get("controller_limit_A"),
            "controller_bus_limit_A": best.get("controller_bus_limit_A")}


def _breakpoints(m: Mapping[str, Any], drive: Mapping[str, Any], prop: pp.Propeller,
                 lo: float, hi: float) -> List[float]:
    """Speeds where the model's answer can jump (a loss-grid / drive-grid speed row,
    where the field-weakening floor and the feasible cells change; the ends of the
    propeller's tested range): scanned on both sides, so a narrow band between two
    coarse samples is not missed."""
    xs = list(m["grid"]["rpm"]) + list(drive.get("rpm") or [])
    if prop.rpm_range:
        xs += [float(prop.rpm_range[0]), float(prop.rpm_range[1])]
    out = []
    for x in xs:
        for d in (-0.5, 0.5):
            if lo < x + d < hi:
                out.append(x + d)
    return out


def _flags(m: Mapping[str, Any], drv: Mapping[str, Any], pk: Mapping[str, Any],
           row: Mapping[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """``(flags, qualification)`` of one answered speed: what the number rests on,
    what it omits and which limits could not be checked (never a silent pass)."""
    cal = m.get("thermal_calibration")
    td = thermal_domain(m, row.get("air_speed_ms")) if cal else None
    base_pwm = m["grid"]["loss_W"].get("pwm_extra") is not None
    external = drv.get("kind") == "external"
    if drv.get("kind") == "sine":
        pwm = "included (passport drive variant)" if base_pwm else "zero (sine drive)"
    elif external:
        pwm = "included (passport drive variant)" if base_pwm else "omitted"
    elif base_pwm:
        pwm = "included (passport drive variant)"
    elif drv.get("pwm_extra_W") is None:
        pwm = "omitted"
    elif "pwm_extra" in (row.get("drive_losses_unavailable") or []):
        pwm = "unavailable at this point"
    else:
        pwm = "included"
    mech = "included" if m.get("mech_W") else "omitted"
    ctrl = "included" if row.get("controller_loss_W") is not None else "omitted"
    v = (row.get("voltage") or {}).get("status") or "unverified"
    fits = pack_fits_drive(drv, pk)
    dm_basis = str(row.get("demag_basis") or "")
    demag = ("unknown" if row.get("demag_limit_A") is None
             else "reference temperature only" if dm_basis.startswith("reference temperature only")
             else "at temperature")
    bus_lim = drv.get("I_bus_cont_A") is not None and pk.get("v_min") is not None
    checks = {"controller": "checked" if (row.get("controller_limit_A") is not None or bus_lim)
              else "unknown",
              "voltage_min_pack": "checked" if v in ("ok", "deeper_fw") else "unverified",
              "pack_bus_range": {True: "checked", False: "failed", None: "unknown"}[fits],
              "battery_current": "checked" if pk.get("i_max_A") is not None else "unknown",
              "demag": demag}
    if fits is False:
        feas = "failed"
    elif "unknown" in (checks["controller"], checks["pack_bus_range"]) or checks["voltage_min_pack"] != "checked":
        feas = "unknown"
    else:
        feas = "checked"
    ss_model = row.get("slipstream_source") == "model"
    ss_status = str((cal or {}).get("slipstream_factor_status") or "bench") if ss_model else None
    q = {"thermal": "calibrated" if cal else "uncalibrated", "thermal_domain": td,
         "slipstream": {"factor": row.get("slipstream_factor"), "position": row.get("slipstream_position"),
                        "calibrated": ss_model, "status": ss_status or (
                            "caller" if row.get("slipstream_source") == "caller" else "assumed"),
                        "basis": ((cal or {}).get("slipstream_factor_basis") if ss_model else None)
                        or ("given by the caller" if row.get("slipstream_source") == "caller"
                            else "assumed (propeller.py)")},
         "propeller": {"extrapolated": bool(row.get("extrapolated")),
                       "power_estimated": bool(row.get("power_estimated"))},
         "losses": {"pwm_extra": pwm, "mech": mech,
                    "controller": "unknown (external ESC)" if external else ctrl},
         "loss_grid_extrapolated": {"speed": bool(row.get("below_loss_grid_speed")),
                                    "current": bool(row.get("below_loss_grid_current"))},
         "checks": checks, "drive_feasibility": feas,
         "unverified": [k for k, s in checks.items() if s in ("unknown", "unverified")]}
    f: List[str] = []
    if not cal:
        f.append("thermal_uncalibrated")
    elif td == "extrapolated":
        f.append("thermal_extrapolated")
    if ss_status == "transferred":
        f.append("slipstream_transferred")
    elif ss_model:
        f.append("slipstream_bench")
    else:
        f.append("slipstream_uncalibrated")
    if external:
        f.append("esc_external")
        f.append("esc_loss_omitted")
    if row.get("extrapolated"):
        f.append("prop_extrapolated")
    if row.get("power_estimated"):
        f.append("prop_power_estimated")
    if row.get("below_loss_grid_speed"):
        f.append("below_loss_grid_speed")
    if row.get("below_loss_grid_current"):
        f.append("below_loss_grid_current")
    if row.get("fw_floor"):
        f.append("fw_floor")
    if pwm == "omitted":
        f.append("pwm_omitted")
    elif pwm == "unavailable at this point":
        f.append("pwm_unavailable")
    if mech == "omitted":
        f.append("mech_omitted")
    if v == "deeper_fw":
        f.append("voltage_deeper_fw")
    elif v != "ok":
        f.append("voltage_unverified")
    if demag == "reference temperature only":
        f.append("demag_reference_only")
    elif demag == "unknown":
        f.append("demag_unknown")
    if feas == "failed":
        f.append("drive_failed")
    elif checks["controller"] == "unknown":
        f.append("drive_unknown")
    if checks["battery_current"] == "unknown":
        f.append("battery_current_unknown")
    return f, q


def s1_rating(m: Mapping[str, Any], prop: pp.Propeller, *, ambient_c: float = 25.0,
              altitude_m: float = 0.0, position: Optional[str] = None,
              factor: Optional[float] = None, n_points: int = 41,
              rpm_min: Optional[float] = None, rpm_max: Optional[float] = None,
              winding_limit_c: Optional[float] = None, magnet_limit_c: Optional[float] = None,
              tol_rpm: float = 1.0, drive: Optional[str] = None,
              pack_v_min: Optional[float] = None, pack_v_max: Optional[float] = None,
              pack_i_max_A: Optional[float] = None, esc_i_bus_A: Optional[float] = None,
              esc_i_phase_A: Optional[float] = None) -> Dict[str, Any]:
    """Rows over speed and the continuous limit with propeller ``prop``, driven by
    ``drive`` (``sine``, a PWM variant id - the board at its one cooling class
    (:func:`effective_board_cooling`; never a caller's choice) - or
    ``external_esc`` with its continuous bus / phase current ``esc_i_*``, default the
    model's bench ESC) from the pack (``pack_*``; default the passport's battery).
    With no ``factor`` / ``position`` the model's calibrated slipstream factor is used
    when it has one (``model_slipstream``).

    The scan runs from ``rpm_min`` (default 10 % of the top) to ``rpm_max``
    (default: the motor's speed cap — the top of its characterised grid / voltage
    limit — but not more than 1.5 x the propeller's highest tested rpm), plus both
    sides of every speed where the model can jump (``_breakpoints``) and every edge
    between a passing and a failing speed (bisected to ``tol_rpm``).  The limit is
    the upper edge of the highest passing band.  ``monotone`` is False when a
    failing speed lies below a passing one (all bands are listed then).  When the
    scan ends still passing, the limit is a LOWER bound (``lower_bound``,
    ``range_end`` says why the scan stopped)."""
    if not prop.selectable:
        raise S1Error(f"{prop.id} has no usable thrust/power data")
    bc, bc_basis = effective_board_cooling(m, drive, "propeller")
    drv = resolve_drive(m, drive, bc, esc_i_bus_A, esc_i_phase_A)
    drv["board_cooling_basis"] = bc_basis
    pk = resolve_pack(m, pack_v_min, pack_v_max, pack_i_max_A)
    lims = limits_of(m, winding_limit_c, magnet_limit_c)
    rho = pp.air_density(temp_c=ambient_c, altitude_m=altitude_m)
    prop_end = 1.5 * prop.rpm_range[1]
    hi = float(rpm_max) if rpm_max else min(lims["rpm_max"], prop_end)
    lo = float(rpm_min) if rpm_min else 0.1 * hi
    if not (0 < lo < hi):
        raise S1Error("rpm range must satisfy 0 < rpm_min < rpm_max")
    n = max(5, min(int(n_points), 201))
    cache: Dict[float, Dict[str, Any]] = {}

    def f(r: float) -> Dict[str, Any]:
        k = round(float(r), 6)
        if k not in cache:
            cache[k] = solve_point(m, prop, r, ambient_c=ambient_c, rho=rho, lims=lims,
                                   position=position, factor=factor, drive=drv, pack=pk)
        return cache[k]
    rows = [f(lo + (hi - lo) * i / (n - 1)) for i in range(n)]
    for x in _breakpoints(m, drv, prop, lo, hi):
        f(x)
    # refine: every ok / not-ok edge bisected; between two failing samples that fail for
    # different reasons (a voltage floor below, a temperature above ...) look inside once
    for _ in range(2):
        allr = sorted(cache.values(), key=lambda r: r["rpm"])
        for a, b in zip(allr, allr[1:]):
            if b["rpm"] - a["rpm"] <= tol_rpm:
                continue
            if a["ok"] != b["ok"]:
                _bisect_edge(f, a["rpm"], b["rpm"], tol_rpm)
            elif not a["ok"] and a["binding"] != b["binding"]:
                for k in (1, 2, 3):
                    f(a["rpm"] + (b["rpm"] - a["rpm"]) * k / 4.0)
    allr = sorted(cache.values(), key=lambda r: r["rpm"])
    bands = _bands(allr)
    first_fail = next((i for i, r in enumerate(allr) if not r["ok"]), None)
    monotone = first_fail is None or not any(r["ok"] for r in allr[first_fail:])
    limit: Optional[Dict[str, Any]] = None
    if bands:
        top_ok = max(i for i, r in enumerate(allr) if r["ok"])
        best = allr[top_ok]
        if top_ok == len(allr) - 1:
            binding, nxt = "speed_range", None             # passes up to the end of the scanned range
        else:
            nxt = allr[top_ok + 1]
            binding = nxt["binding"]
        flags, qual = _flags(m, drv, pk, best)
        limit = {"rpm": best["rpm"], "thrust_N": best["thrust_N"], "thrust_g": best["thrust_N"] / pp.G0 * 1000.0,
                 "shaft_power_W": best["shaft_power_W"], "electrical_power_W": best["electrical_power_W"],
                 "battery_power_W": best["battery_power_W"], "battery_current_A": best["battery_current_A"],
                 "current_A": best["current_A"], "current_grid_A": best["current_grid_A"],
                 "T_winding_C": best["T_winding_C"], "T_magnet_C": best["T_magnet_C"],
                 "T_housing_C": best["T_housing_C"], "air_speed_ms": best["air_speed_ms"],
                 "binding": binding, "violations_above": (nxt or {}).get("violations") or [],
                 "lower_bound": binding == "speed_range",
                 "extrapolated": best["extrapolated"],
                 "power_estimated": best["power_estimated"], "below_loss_grid": best["below_loss_grid"],
                 "below_loss_grid_speed": best["below_loss_grid_speed"],
                 "below_loss_grid_current": best["below_loss_grid_current"],
                 "fw_floor": bool(best.get("fw_floor")),
                 "controller_limit_A": best["controller_limit_A"],
                 "controller_limit_basis": best.get("controller_limit_basis"),
                 "controller_bus_limit_A": best.get("controller_bus_limit_A"),
                 "demag_limit_A": best["demag_limit_A"],
                 "demag_basis": best["demag_basis"], "voltage": best["voltage"],
                 "cooling": {"slipstream_factor": best["slipstream_factor"],
                             "slipstream_position": best["slipstream_position"],
                             "slipstream_source": best.get("slipstream_source"),
                             "air_speed_ms": best["air_speed_ms"], "h_W_m2K": best["h_W_m2K"]},
                 "thermal_domain": thermal_domain(m, best["air_speed_ms"]),
                 "calibration_in_range": calibration_covers(m, best["air_speed_ms"]),
                 "drive_feasibility": qual["drive_feasibility"],
                 "flags": flags, "qualification": qual}
        if binding == "speed_range":
            if rpm_max:
                limit["range_end"], limit["range_end_basis"] = "requested", "the requested top of the scan"
            elif hi >= lims["rpm_max"] - 1e-6:
                limit["range_end"], limit["range_end_basis"] = "motor_speed_grid", lims["rpm_max_basis"]
            else:
                limit["range_end"] = "propeller_data"
                limit["range_end_basis"] = "1.5 x the propeller's highest tested rpm"
    # the two sides of the system apart (the load chart's two lines); the rows say which side they pass
    limit_motor = _side_limit(f, cache, "motor", tol_rpm)
    limit_supply = _side_limit(f, cache, "supply", tol_rpm)
    for r in rows:
        r["ok_motor"] = bool(_view(r, "motor")["ok"])
    return {
        "propeller_id": prop.id, "propeller": prop.model,
        "die": m.get("die"), "config": m.get("config"),
        "ambient_c": float(ambient_c), "altitude_m": float(altitude_m), "rho_kg_m3": rho,
        "rpm_range_tested": list(prop.rpm_range) if prop.rpm_range else None,
        "power_data": prop.power_data,
        "drive": {"id": drv["id"], "kind": drv["kind"], "device": drv.get("device"),
                  "technology": drv.get("technology"), "carrier_hz": drv.get("carrier_hz"),
                  "n_parallel": drv.get("n_parallel"),
                  # the ONE cooling class the machine decides (no user choice) and why
                  "board_cooling": None if drv["kind"] == "external" else drv["board_cooling"],
                  "board_cooling_basis": drv["board_cooling_basis"],
                  "I_cont_A": drv.get("I_cont_A"), "I_cont_basis": drv.get("I_cont_basis"),
                  "I_bus_cont_A": drv.get("I_bus_cont_A"), "name": drv.get("name"),
                  "source": drv.get("source"),
                  "bus_v": drv.get("bus_v"), "pack_fits": pack_fits_drive(drv, pk)},
        "drives_available": [SINE] + sorted(m.get("drives") or {}) + [EXTERNAL_ESC],
        "external_esc_default": m.get("external_esc"),
        "pack": pk,
        "limits": lims, "rows": rows,
        "refined": [{"rpm": r["rpm"], "ok": r["ok"], "binding": r["binding"]} for r in allr
                    if not any(r is x for x in rows)],
        "bands": [list(b) for b in bands], "monotone": monotone,
        "nonconverged_rpm": [r["rpm"] for r in allr if r["binding"] == "nonconverged"],
        "limit": limit,
        "limit_motor": limit_motor,
        "limit_supply": limit_supply,
        "no_continuous_point": limit is None,
        "first_failure": (None if first_fail is None else allr[first_fail]["binding"]),
        "model": {"source": m.get("source"), "labels": m.get("labels") or [], "schema": m.get("schema"),
                  "magnet_ref_temp_C": m.get("magnet_ref_temp_C"), "R_temp_C": m.get("R_temp_C"),
                  "flux_temp_coeff_per_K": m.get("flux_temp_coeff_per_K"),
                  "flux_coeff_basis": m.get("flux_coeff_basis"),
                  "torque_factor": m.get("torque_factor"),
                  "controller": (m.get("controller") or {}).get("variant") or drv["id"],
                  "loss_groups": m.get("loss_groups"),
                  "thermal_calibration": ({k: v for k, v in m["thermal_calibration"].items() if k != "anchors"}
                                          if m.get("thermal_calibration") else None)},
        "method": "propeller Q(n) + bearing/windage -> current from the passport torque map at the magnet "
                  "temperature -> voltage re-check at the minimum pack voltage (hot psi-map, PM flux at the "
                  "magnet temperature) -> passport loss grid with R(T) + the drive's PWM extra -> slipstream "
                  "film (outer_air) -> Configure's lumped thermal estimate (calibrated where anchored), iterated "
                  "to the fixed point at each speed; the point must also hold the controller's continuous "
                  "current, the demagnetisation limit at the magnet temperature and (if given) the pack's "
                  "continuous current; limit by bisection",
    }
