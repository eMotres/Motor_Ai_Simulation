"""Generate an analytical PASSPORT of a motor from FEM.

Merges the two extractor scripts into one reusable function so the admin can
characterise any catalog motor and publish a passport the Configurator scales
INSTANTLY (no FEM):

  • scaling base — 3 transient solves of the active config
      A) loaded  (I0, gamma, eddy ON) -> T0, R0, Pfe0, Pmag0, mass0
      B) no-load (I=0)                -> Vemf0_peak
      C) loaded @1.5*L0               -> R split (R_active prop. L + R_end const)
    (mirrors extract_passport.py)
  • speed curves — Pfe / Pmag vs rpm sweep (mirrors extract_speed_curves.py)

The returned dict is { passport, fit, geo, poles, slots } and matches
ReferenceMotor in web/src/lib/referencePassports.ts.  It operates on the ACTIVE
config, so the caller loads the target motor's preset first (apply_preset).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

DEFAULT_RPMS: List[float] = [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]


def _slot_fill_measured(geo: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Winding window + copper section of this cross-section, or None if the
    CAD cannot build it.  Pure geometry (no FEM), so it never costs a solve."""
    try:
        from motor_ai_sim.masses import slot_fill_from_cad
        return slot_fill_from_cad(dict(geo))
    except Exception:      # noqa: BLE001 — a passport must survive without it
        return None


def generate_passport(
    *,
    I0: Optional[float] = None,
    gamma_deg: Optional[float] = None,
    rpms: Optional[List[float]] = None,
    base_steps: int = 12,
    sweep_steps: int = 12,
    mesh_size_mm: float = 5.0,
    machine: Optional[Dict[str, Any]] = None,
    rpm0: Optional[float] = None,
    daxis_deg: Optional[float] = None,
    # "motor" | "generator".  EXPLICIT on purpose: the solver falls back to the
    # shared config's mode when nobody passes one, and that made a passport
    # depend on which way the engineer had left the Simulation tab — a
    # generator characterised while the panel said "motor" is a different
    # operating point (measured 2026-08-30 on CILN28 G2-L40: 58.74 -> 57.18 N·m,
    # terminal 175.2 -> 219.2 V, iron +20 %, magnets +53 %).
    mode: str = "motor",
    # The pack on the DC link, as the family yaml stores it ({chemistry, cells,
    # v_nom, v_min, v_max, n_parallel, r_int_mohm, capacity_ah,
    # i_charge_max_a}).  Two things ride on it: the PWM block needs a bus to
    # switch against, and a GENERATOR's Configure tab needs the pack as a
    # circuit to compute what actually reaches it.  None = the machine has no
    # declared pack, and the passport says so instead of inventing one.
    battery: Optional[Dict[str, Any]] = None,
    # "off" | "quick" | "full" — see passport_pwm.measure_pwm.  Default quick:
    # 3 sine + 4 PWM solves on top of the 2-D passport.
    pwm: str = "quick",
    # "motor" | "generator" as the CONFIGURATION declares it — carried onto the
    # passport so Configure knows whether to show the charging block at all.
    role: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the FEM passport extraction → full passport dict.

    Two modes:
      • `machine={"geometry": full-dict, "connection": str|None,
                  "materials": {part: name}}` — SOLVES THAT MACHINE without
        touching the live config: the geometry rides every request as a
        `geo` override, the connection as an explicit parameter, the
        materials through the per-request material context, and the rpm as
        an argument.  This is the catalog path — a passport queue must never
        fight the engineer for the shared live machine (measured live
        2026-08-25: a background generation switched the geometry under the
        user's edits, twice).
      • no `machine` (legacy) — the active config, as before.

    I0 / gamma_deg default to the machine's own operating point.
    """
    import json
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes.simulation import get_fem_transient, _BACKGROUND_RUN

    # Every solve below is BACKGROUND work: it must never persist as the
    # user's last transient nor claim the field-snapshot store, even though it
    # solves the same geometry the user has on screen (incident 2026-08-25 —
    # a loss-grid point replaced the card mid-session).  ContextVar token is
    # reset in the finally at the end.
    _bg_token = _BACKGROUND_RUN.set(True)

    cfg = get_config()
    # The passport REPORTS slot_width and the four radii (the `fit` / `geo`
    # blocks below).  Those are DERIVED fields that the config STORES, and a
    # stored derived value can lag the primaries it comes from — HEAD's config
    # carried slot_width 2.5 next to a 2.3 mm wire pitch.  A passport is a
    # client-facing datasheet, so it must publish the machine the config
    # DESCRIBES, not whatever number was last written next to it.  No override
    # here: merge_geo_override(g, None) is exactly "recompute this dict's own
    # derived fields from its own primaries".
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    _mgeo = dict((machine or {}).get("geometry") or {}) or None
    _mconn = str((machine or {}).get("connection") or "") or None
    # TERMINAL connection.  Every solve below is told explicitly (never the
    # panel's), the route drives the winding at I_line/√3 in delta, and the
    # passport is then stored as the EQUIVALENT STAR (see the transform at
    # the end): line current on its I axis, winding voltage/√3 as its phase
    # voltage, winding R and L over 3.  charge_analytic's voltage law and
    # modulation index are the star ones and stay untouched — they are now
    # exact for both connections.
    _msd = ("delta" if str((machine or {}).get("star_delta") or "")
            .lower().startswith("d") else "star")
    _sq3 = math.sqrt(3.0)
    _mmats = dict((machine or {}).get("materials") or {})
    # Measured end-winding factor of the real (hand-)wound machine — rides
    # every solve so the passport's R and copper losses describe the motor as
    # BUILT, not the idealized ends (0/absent = solver auto).
    _mkend = float((machine or {}).get("end_winding_factor") or 0.0)
    g = merge_geo_override(_mgeo if _mgeo else (cfg.get("geometry", {}) or {}),
                           None)
    wind = cfg.get("winding", {}) or {}
    sim = cfg.setdefault("simulation", {})
    # Operating point from the motor itself (its loaded preset), not hard-coded:
    # max_current = phase RMS [A], phase_offset_deg = load angle gamma [deg].
    if I0 is None:
        I0 = float(sim.get("max_current", 110.0) or 110.0)
    if gamma_deg is None:
        gamma_deg = float(sim.get("phase_offset_deg", 28.0) or 28.0)

    # The machine's materials ride the per-request material context — the same
    # mechanism ?mat= uses — so the solves below use THEM, not the live
    # assignment.  ContextVars are per-request; the route's context dies with
    # the request, so no restore is needed on the exception path.
    if _mmats:
        from motor_ai_sim.material_context import (get_request_materials,
                                                   set_request_materials)
        # Carry the per-part ACCOUNTING states (included/reference/excluded)
        # through: they ride this same payload, and a passport generated with a
        # frameless machine's shaft silently put back would quote a mass — and
        # a torque density — for a shaft the customer supplies.
        _keep = (get_request_materials() or {}).get("parts")
        set_request_materials({"assignment": _mmats, "materials": {},
                               **({"parts": _keep} if _keep else {})})

    N0 = float(g.get("num_wires_per_slot", 0) or 0)
    # STRANDS IN HAND and STRIPS PER ROW the passport was MEASURED at.  N0 stays
    # the PHYSICAL wire rows per slot (that is what the Configure knob edits and
    # what the wire coating is built on); the ELECTRICAL turns are
    # (N0 / wire_parallel0) × wire_split0 = winding.turns_per_coil, and every
    # measured number below (T0, Vemf0, R0, the loss grid) already carries that
    # scale.  Both are recorded so a consumer can tell a 24-turn coil from 24
    # wires wound 2-in-hand (the same slot, a factor 2 apart in EMF) and from 24
    # rows split in two (the same rows, a factor 2 THE OTHER WAY).  A passport
    # written before these fields existed carries neither; ABSENT MEANS 1 —
    # which is what the unsplit, one-in-hand machine they were all measured on
    # actually was.
    from motor_ai_sim.winding import (wire_parallel_from_geo as _wp_geo,
                                      wire_split_from_geo as _ws_geo)
    wireParallel0 = float(_wp_geo(g))
    wireSplit0 = float(_ws_geo(g))
    L0 = float(g.get("motor_length", 0) or 0)
    wireH0 = float(g.get("wire_height", 0.0) or 0.0)
    if _mconn:
        from motor_ai_sim.winding import parse_connection as _pc
        nP0 = float(_pc(_mconn)[0])
    else:
        nP0 = float(wind.get("n_parallel", 2) or 2)
    # Sector count from the MACHINE, not a constant: 4 was hardcoded here, and
    # a passport is generated for every motor — on a 12s/14p (gcd 2) a 1/4
    # model holds 3.5 poles and the solver now (correctly) refuses it.  The
    # largest divisor of gcd(slots, poles) capped at 4 keeps the old speed on
    # 24s/28p machines (gcd 4 → 4) and gives every other machine a symmetry
    # it actually has.
    import math as _math
    _sym = _math.gcd(int(g.get("num_slots", 24) or 24),
                     int(g.get("num_poles", 28) or 28))
    NSECT = next((d for d in (4, 3, 2) if _sym % d == 0), 1)

    def run(I: float, length: Optional[float] = None, eddy: bool = False,
            steps: int = 12, demag: bool = False, rpm: Optional[float] = None):
        ov: Dict[str, Any] = dict(_mgeo) if _mgeo else {}
        if length is not None:
            ov["motor_length"] = round(length, 3)
        return get_fem_transient(
            n_steps_per_period=steps, n_periods=1.0, gamma_deg=gamma_deg,
            # never inherited from the panel — see the `mode` argument
            mode=str(mode or "motor"),
            # NSECT, not 4: the hardcoded 4 refused every 12s/14p machine
            # (gcd 2) — the sector count was computed above and then unused
            # (measured overnight 2026-08-24: three 40 mm-class passports
            # failed on exactly this).
            I_phase_rms=I, mesh_size_mm=mesh_size_mm, n_sectors=NSECT,
            sliding_band=True, rotor_eddy=eddy, demag=demag,
            geo=(json.dumps(ov) if ov else None),
            rpm=(rpm if rpm is not None else rpm0),
            star_delta=_msd,
            **({"connection": _mconn} if _mconn else {}),
            **({"end_winding_factor": _mkend} if _mkend > 0 else {}),
            # The MACHINE'S calibrated d-axis (from its die), not a per-run
            # auto-calibration: without it the solver can settle on the other
            # branch and the passport torque comes out with the wrong sign
            # (measured 2026-08-26 on the CILN28 series).
            **({"daxis_deg": float(daxis_deg)} if daxis_deg is not None else {}),
        )

    # ── scaling base: A loaded, B no-load, C @1.5L ──────────────────────────
    # demag=True: the loaded base doubles as the 1.0·I0 point of the current
    # sweep below, and that point must carry a MEASURED retention like its
    # neighbours (not a demag-off 100 %).
    # steps ≥ 48 on the BASE point only (user 2026-08-25: "на 12 точках не
    # поймаешь пульсаций"): the passport quotes the rated-point torque ripple,
    # and ripple needs the fine grid; the sweeps stay coarse — they feed only
    # AVERAGED quantities, where 12 steps sit within ~0.2 % of 40 (measured).
    A = run(I0, eddy=True, steps=max(48, base_steps), demag=True)
    B = run(0.0, eddy=False, steps=base_steps)
    C = run(I0, length=L0 * 1.5, eddy=False, steps=max(4, base_steps // 3))

    sa = A.get("summary", {}) or {}
    T0 = float(A.get("T_avg_Nm", 0.0) or 0.0)
    # STAR-EQUIVALENT from here on when the machine is delta: the winding's
    # R over 3 and its voltage over √3 describe, at the terminals, exactly the
    # star every formula below assumes — with the I axis already the LINE
    # current (the route took the √3 on the way in).  3·I_line²·R_w/3 is the
    # winding's own 3·I_w²·R_w, and V_w/√3 against V_bus/2 is the modulation
    # index of a winding that sees the full line voltage.
    _kR = (1.0 / 3.0) if _msd == "delta" else 1.0
    _kV = (1.0 / _sq3) if _msd == "delta" else 1.0
    R_A = float(A.get("R_phase_ohm", 0.0) or 0.0) * _kR
    R_C = float(C.get("R_phase_ohm", 0.0) or 0.0) * _kR
    Vemf0 = float(B.get("V_peak", 0.0) or 0.0) * _kV
    rpm0 = float(A.get("rpm", 0.0) or 0.0)
    # R(L) = R_active*(L/L0) + R_end ; R_C @1.5L0 -> R_C - R_A = 0.5*R_active
    R_active = 2.0 * (R_C - R_A)
    R_end = max(0.0, R_A - R_active)
    endWindFrac = min(1.0, max(0.0, R_end / R_A)) if R_A > 0 else 0.0

    # ── current sweep: saturation + demagnetisation calibration ──────────────
    # Sampled in AMPERE-TURNS: tooth saturation is set by MMF = turns × coil
    # current, so one I-sweep at base turns calibrates the tuner's current AND
    # turns AND connection knobs at once (NI_frac = fN·fI·fConn).  Each point
    # also stamps the demag retention (Br kept), so the same points locate the
    # demagnetisation current limit — no separate sweep.
    #
    # Point count is ADAPTIVE (user's spec 2026-08-24): start with
    # {0.25, 0.5, 1.0, 1.5}·I0; extend upward (×1.4) until the retention drops
    # below 99.5 % (the demag knee is IN the data, not extrapolated) or 3·I0;
    # then a leave-one-out check — any interior point that linear interpolation
    # of its neighbours misses by >0.5 % of T earns a midpoint neighbour.
    cur: Dict[str, List[float]] = {"I_A": [], "T_Nm": [], "V_peak_V": [],
                                   "demag_keep_pct": []}

    def _cur_point(I: float) -> float:
        d = run(I, eddy=False, steps=max(6, base_steps // 2), demag=True)
        s = d.get("summary", {}) or {}
        dm = s.get("demag") or {}
        keep = 100.0 - float(dm.get("loss_pct") or 0.0)
        cur["I_A"].append(round(I, 2))
        cur["T_Nm"].append(round(abs(float(d.get("T_avg_Nm") or 0.0)), 3))
        cur["V_peak_V"].append(round(float(d.get("V_peak") or 0.0) * _kV, 2))
        cur["demag_keep_pct"].append(round(keep, 2))
        return keep

    try:
        for f in (0.25, 0.5, 1.5):
            _cur_point(I0 * f)
        # the loaded base solve IS the 1.0·I0 point — reuse, no extra solve
        _dm0 = (sa.get("demag") or {})
        cur["I_A"].append(round(I0, 2))
        cur["T_Nm"].append(round(abs(T0), 3))
        cur["V_peak_V"].append(round(float(A.get("V_peak") or 0.0) * _kV, 2))
        cur["demag_keep_pct"].append(round(100.0 - float(_dm0.get("loss_pct")
                                                         or 0.0), 2))
        # demag knee: extend until retention < 99.5 % or 3·I0
        _Imax, _keep = 1.5 * I0, cur["demag_keep_pct"][cur["I_A"].index(round(1.5 * I0, 2))]
        while _keep > 99.5 and _Imax < 3.0 * I0:
            _Imax = min(3.0 * I0, _Imax * 1.4)
            _keep = _cur_point(_Imax)
        # sort by I, then one adaptive refinement round (max 2 added points)
        _order = sorted(range(len(cur["I_A"])), key=lambda i: cur["I_A"][i])
        for k in cur:
            cur[k] = [cur[k][i] for i in _order]
        _added = 0
        for i in range(1, len(cur["I_A"]) - 1):
            if _added >= 2:
                break
            x0, x1, x2 = cur["I_A"][i - 1], cur["I_A"][i], cur["I_A"][i + 1]
            y0, y2 = cur["T_Nm"][i - 1], cur["T_Nm"][i + 1]
            y_pred = y0 + (y2 - y0) * (x1 - x0) / max(x2 - x0, 1e-9)
            if cur["T_Nm"][i] > 1e-9 and abs(y_pred - cur["T_Nm"][i]) \
                    / cur["T_Nm"][i] > 0.005:
                _cur_point((x1 + x2) / 2.0)
                _added += 1
        if _added:
            _order = sorted(range(len(cur["I_A"])), key=lambda i: cur["I_A"][i])
            for k in cur:
                cur[k] = [cur[k][i] for i in _order]
    except Exception:  # noqa: BLE001 — the linear passport must survive a sweep hiccup
        import logging
        logging.getLogger(__name__).exception(
            "passport current sweep failed — passport ships without saturation/"
            "demag calibration (linear Kt)")
        cur = {"I_A": [], "T_Nm": [], "V_peak_V": [], "demag_keep_pct": []}

    # ── speed sweep: Pfe / Pmag vs rpm (rpm read from cfg; save+restore) ─────
    # The speed axis has to BRACKET the machine's own rated speed.  A fixed
    # 1000-6000 rpm list left every high-speed build (13 000-25 000 rpm) with a
    # loss surface that stops long before its operating point, so the tuner and
    # the datasheet were extrapolating iron and magnet loss — both grow faster
    # than linearly with frequency, so the extrapolation understates them.
    # Fractions of rpm0 keep the rated point inside the measured range.
    if not rpms:
        rpms = ([round(f * rpm0) for f in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5)]
                if rpm0 and rpm0 > 0 else DEFAULT_RPMS)
    rpm_saved = sim.get("rpm")
    speed: Dict[str, List[float]] = {"rpm": [], "Pfe_W": [], "Pmag_W": []}
    # 2-D LOSS TABLE over current × rpm (user's spec 2026-08-25): iron loss
    # depends on BOTH the flux level (current → saturation) and the frequency,
    # and two 1-D slices cannot reproduce that surface.  Rows are currents
    # (read by the tuner in ampere-turn space, like the torque curve); the
    # I0 row doubles as the legacy `speed` curve for old frontends.
    grid_I = [0.5 * I0, I0, 1.5 * I0]
    loss_grid: Dict[str, Any] = {"I_A": [round(i, 2) for i in grid_I],
                                 "rpm": [], "Pfe_W": [], "Pmag_W": [],
                                 # AC copper factor = solved copper / DC I^2R
                                 # at that point.  Proximity/skin losses are
                                 # +25-30 % on a concentrated winding, and
                                 # without them the tuner under-reported loss
                                 # and over-reported efficiency (user
                                 # 2026-08-26: "цифры расходятся").
                                 "cuAC": []}
    try:
        for gi, Ig in enumerate(grid_I):
            row_fe: List[float] = []
            row_mag: List[float] = []
            row_ac: List[float] = []
            for r in rpms:
                # rpm as an ARGUMENT, never sim["rpm"] mutation — the sweep
                # used to write the shared config's rpm (live-state leak).
                d = get_fem_transient(
                    n_steps_per_period=sweep_steps, n_periods=1.0,
                    gamma_deg=gamma_deg, I_phase_rms=Ig, rpm=r,
                    mode=str(mode or "motor"),
                    mesh_size_mm=mesh_size_mm + 1.0, n_sectors=NSECT,
                    sliding_band=True, rotor_eddy=True,
                    geo=(json.dumps(_mgeo) if _mgeo else None),
                    **({"connection": _mconn} if _mconn else {}),
                    **({"end_winding_factor": _mkend} if _mkend > 0 else {}),
                    **({"daxis_deg": float(daxis_deg)} if daxis_deg is not None else {}),
                )

                def gv(k: str) -> float:
                    v = d.get(k)
                    if v is None:
                        return 0.0
                    if isinstance(v, (list, tuple)):
                        return float(sum(v) / len(v)) if v else 0.0
                    return float(v)

                if gi == 0:
                    loss_grid["rpm"].append(round(float(d.get("rpm") or r)))
                row_fe.append(round(gv("P_fe_W"), 1))
                row_mag.append(round(gv("P_mag_eddy_W") + gv("P_shaft_eddy_W"), 1))
                _rr = float(d.get("R_phase_ohm") or 0.0)
                _dc = 3.0 * Ig * Ig * _rr
                row_ac.append(round(gv("P_cu_W") / _dc, 4) if _dc > 1e-9 else 1.0)
                if abs(Ig - I0) < 1e-9:
                    speed["rpm"].append(round(float(d.get("rpm") or r)))
                    speed["Pfe_W"].append(row_fe[-1])
                    speed["Pmag_W"].append(row_mag[-1])
            loss_grid["Pfe_W"].append(row_fe)
            loss_grid["Pmag_W"].append(row_mag)
            loss_grid["cuAC"].append(row_ac)
    finally:
        # rpm rides as an argument now; nothing was mutated, nothing to
        # restore (rpm_saved kept only so a legacy caller's expectations —
        # config untouched — hold trivially).
        del rpm_saved

    # ── PWM block: the deltas a real inverter adds to the sine numbers ───────
    # Solved on the SAME machine and the same mesh as everything above, so the
    # deltas are switching physics and not a mesh study.  A failure here is
    # logged and returns None — the 2-D passport must never be lost to it (the
    # same rule the 3-D and bench blocks follow in routes/catalog.py).
    def _solve_pt(**over: Any) -> Dict[str, Any]:
        """One transient of THIS machine at an arbitrary operating point and
        excitation.  Same overrides as `run` above (geometry, connection,
        materials context, measured end-winding, calibrated d-axis); the
        caller supplies the drive."""
        kw: Dict[str, Any] = dict(
            n_periods=1.0, gamma_deg=gamma_deg, mode=str(mode or "motor"),
            mesh_size_mm=mesh_size_mm, n_sectors=NSECT, sliding_band=True,
            # rotor_eddy ON for both halves of every delta: the magnet eddy
            # loss is the term the carrier moves most, and a baseline solved
            # without it would report the whole magnet loss as a PWM delta.
            rotor_eddy=True, demag=False,
            geo=(json.dumps(_mgeo) if _mgeo else None),
            **({"connection": _mconn} if _mconn else {}),
            **({"end_winding_factor": _mkend} if _mkend > 0 else {}),
            **({"daxis_deg": float(daxis_deg)} if daxis_deg is not None else {}),
        )
        kw.update(over)
        return get_fem_transient(**kw)

    pwm_block: Optional[Dict[str, Any]] = None
    # Why a point produced no PWM measurement, kept even when the whole block
    # comes back None: "this machine has no PWM block" used to be a silence,
    # and the datasheet printed it as one.  Every record carries a `code`.
    pwm_skipped: List[Dict[str, Any]] = []
    _batt = dict(battery or {})
    _vnom = float(_batt.get("v_nom") or 0.0)
    if str(pwm or "").strip().lower() not in ("", "off", "none", "0", "false"):
        from motor_ai_sim.passport_pwm import measure_pwm
        pwm_block = measure_pwm(
            solve=_solve_pt, I0_A=I0, rpm0=rpm0,
            rpms=(loss_grid["rpm"] or rpms),
            pole_pairs=max(1, int(g.get("num_poles", 0) or 0) // 2),
            v_bus_V=_vnom, v_nom_V=(_vnom or None),
            fidelity=str(pwm), sine_steps=(24 if base_steps <= 6 else 40),
            skipped_out=pwm_skipped)

    passport = {
        "N0": N0, "L0_mm": L0, "wireH0_mm": wireH0,
        "I0_A": I0, "rpm0": round(rpm0), "nP0": nP0,
        # Strands in hand and strips per wire row at the measured point (1 = one
        # wire per turn / one solid bar).  N0 is PHYSICAL wire ROWS per slot, so
        # the SERIES turns are (N0 / wire_parallel0) × wire_split0 — the split's
        # strips are consecutive turns.  A passport without these keys was
        # measured on a machine that had neither: read them as 1.
        "wire_parallel0": wireParallel0,
        "wire_split0": wireSplit0,
        # The convention every number below was measured in.  A generator
        # solved as a motor is a different point, so the passport says which.
        "mode0": str(mode or "motor"),
        # MAGNITUDE: a passport is a datasheet ("this machine makes 58.9 N·m
        # at that point").  The sign only says which way power flows — a
        # generator solved in generator mode reports negative torque, and a
        # client card showing "−58.7 N·m" is noise, not information (the
        # catalog rows have always recorded |T| for the same reason).
        "T0_Nm": round(abs(T0), 3),
        # Terminal connection the machine was measured in, and the convention
        # the numbers below are stored in: a delta machine's passport is its
        # EQUIVALENT STAR (I = line current, V = winding/√3, R and L = /3) so
        # that Configure, charging and the datasheet read it like any other.
        "star_delta": _msd,
        "stored_as": ("equivalent star" if _msd == "delta" else "star"),
        "Vemf0_peak_V": round(Vemf0, 2),
        "Vload0_peak_V": round(float(sa.get("V_phase_peak_V") or 0.0) * _kV, 2),
        "R0_ohm": round(R_A, 5),
        "endWindFrac": round(endWindFrac, 3),
        "Pfe0_W": round(float(sa.get("P_core_W") or 0.0), 2),
        # The BASE POINT's iron loss per half.  Configure scales one core-loss
        # number off the loss grid and has no way to know how much of it sits
        # in the stator teeth and how much in the rotor back iron — this pair
        # is the ratio it splits the scaled value by, so the tuner can quote
        # the heat each side has to shed.  Absent on a passport generated
        # before the split existed; the tuner then puts the whole iron loss on
        # the stator and says the split is unknown.
        **({} if sa.get("P_core_stator_W") is None else {
            "P_fe_stator0_W": round(float(sa.get("P_core_stator_W") or 0.0), 2),
            "P_fe_rotor0_W": round(float(sa.get("P_core_rotor_W") or 0.0), 2),
        }),
        "Pmag0_W": round(float(sa.get("P_solid_W") or 0.0), 2),
        "mass0_kg": round(float(sa.get("mass_total_kg") or 0.0), 3),
        # Winding window measured on the CAD polygons.  Geometry only — the
        # tuner scales the COPPER with turns and wire height and divides by
        # this, so it can tell the user when a variant stops being windable.
        **((lambda _sf: ({} if not _sf else {
            "A_slot_mm2": round(_sf["A_slot_mm2"], 2),
            "A_cu0_mm2": round(_sf["A_cu_mm2"], 2),
            "slot_fill0_pct": round(100.0 * _sf["fill"], 2),
        }))(_slot_fill_measured(g))),
        # Rated-point torque ripple off the FINE base solve (48 steps) — a
        # datasheet figure of THIS operating point; the tuner shows it as-is
        # and never rescales it (ripple does not follow the linear knob laws).
        "ripple0_pct": (round(float(sa.get("T_ripple_pct")), 2)
                        if sa.get("T_ripple_pct") is not None else None),
        "speed": speed,
        # Saturation + demag calibration: T/V/retention over phase current at
        # base turns — the tuner reads it in ampere-turn space (see current
        # sweep above).  Empty when the sweep failed (linear Kt fallback).
        "current": (cur if cur["I_A"] else None),
        # 2-D loss surface over current × rpm — rows follow current["I_A"]-style
        # ampere-turn reading; the tuner bilinearly interpolates it.
        "loss_grid": (loss_grid if loss_grid["rpm"] else None),
        # MEASURED PWM deltas (see passport_pwm.py).  None = not measured, and
        # Configure then has no PWM toggle for this machine rather than a
        # toggle backed by an assumption.
        "pwm": pwm_block,
        # WHY, when there is no block (or when individual points are missing
        # from one).  Machine-readable: each record has a `code`
        # (overmodulation / step_budget / pwm_solve_failed / baseline_failed /
        # seed_failed / no_v1_seed / no_bus / no_speed) and the numbers behind
        # it.  Absent on a passport that measured everything it asked for.
        **({} if not pwm_skipped else {"pwm_skipped": pwm_skipped}),
        # The pack this machine is wired to, verbatim from the configuration.
        # A generator's Configure tab computes the charge current against it;
        # the PWM block switched against its v_nom.  None = no declared pack.
        "battery": (_batt or None),
        # "motor" / "generator" as the CONFIGURATION declares it.  mode0 above
        # says which convention the numbers were SOLVED in; this says what the
        # machine IS, and only a generator gets the charging block.
        "role": (str(role).strip().lower()
                 if str(role or "").strip().lower() in ("motor", "generator")
                 else str(mode or "motor")),
    }
    fit = {
        "slotHeight_mm": float(g.get("slot_height", 0.0) or 0.0),
        "insulation_mm": float(g.get("insulation_thickness", 0.0) or 0.0),
        "wireSpacingY_mm": float(g.get("wire_spacing_y", 0.0) or 0.0),
        "slotWidth_mm": float(g.get("slot_width", 0.0) or 0.0),
        "wireWidth_mm": float(g.get("wire_width", 0.0) or 0.0),
    }
    geo = {
        "statorOR_mm": float(g.get("stator_outer_radius", 0.0) or 0.0),
        "statorIR_mm": float(g.get("stator_inner_radius", 0.0) or 0.0),
        "rotorOR_mm": float(g.get("rotor_outer_radius", 0.0) or 0.0),
        "rotorIR_mm": float(g.get("rotor_inner_radius", 0.0) or 0.0),
        "numSlots": int(g.get("num_slots", 0) or 0),
        "numPoles": int(g.get("num_poles", 0) or 0),
        "magnetHeight_mm": float(g.get("magnet_height", 0.0) or 0.0),
    }
    _BACKGROUND_RUN.reset(_bg_token)
    return {"passport": passport, "fit": fit, "geo": geo,
            "poles": geo["numPoles"], "slots": geo["numSlots"]}
