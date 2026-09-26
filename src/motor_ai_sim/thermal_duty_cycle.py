"""The DUTY CYCLE — a lumped transient network fitted to one steady 2-D map.

A robot joint is not an S1 machine.  It holds a pose, it swings, it waits; the
question its designer asks is not "how hot does it get" but "how long may it
pull 46 A before the winding reaches its class, and at what duty may it repeat
that for ever".  Answering it needs two things the steady solver does not have —
heat CAPACITY (``thermal_capacities``) and TIME — and one thing it does have and
nothing else does: the actual conductances of this machine, which are read off a
converged 2-D map rather than guessed from formulas.

    G_ws = P_copper / (T̄_winding − T̄_stator)          both from the map
    G_rs = gap_W    / (T̄_rotor   − T̄_stator)
    G_mr = P_magnet / (T̄_magnet  − T̄_rotor)
    G_bore, G_shaft = the map's own bore / shaft-end watts over their ΔT

so the network reproduces the calibration point EXACTLY by construction, and
everything it then says about other operating points is an interpolation of a
real solve rather than a thermal-resistance sketch.

WHEN A LINK CANNOT BE FITTED (user 2026-09-15).  On a machine whose rotor makes
almost nothing — the L13 robot joint is one — the calibration map puts the
rotor, the magnets and the stator within a kelvin of each other, and a watt
divided by a kelvin of numerical noise (or by a mean-to-mean drop pointing the
wrong way) is not a conductance.  The first answer was to MERGE those nodes into
one lump; it is wrong in both directions at once, so the link now keeps the
interface's own PHYSICAL conductance instead::

    G_rs = k_eff·A/δ + h_rad·A       A = π·D_gap·L, δ = air gap − sleeve
    G_mr = k_magnet·A_root/(h_mag/2) A_root = π·D_root·L

``Network.links`` says of each of the three internal links whether it is
``calibrated`` (fitted to the map), ``physical`` (the formulas above) or
``merged`` (only when there is no geometry to build one from, or when an old
record is read back with ``allow_merge=True``).

WHAT THE NETWORK LOOKS LIKE (robotics mode, user 2026-09-14)::

        ambient ─ still-air housing film ─┐   ┌─ mount (G, T_mount)
                                          │   │
      winding ──G_ws── stator ────────────┴───┘
         │                │
         │ end faces      │ end faces          (still air + radiation,
         ↓ (still air)    ↓ (still air)         h re-evaluated every step)
       ambient          ambient
                          │
                        G_rs
                          │
       magnet ──G_mr── rotor ── bore ── ambient
         │                │
         │ end faces      ├─ shaft ends ── ambient
         ↓                ↓ end faces
       ambient          ambient

The four SIDE-FACE paths are the user's correction to the first cut of this
model: on this machine the coils stand proud of the core on both ends and the
core and magnet end faces are open to the room, so the axial paths are not a
refinement — on the winding they are comparable to everything the core carries.
They are INPUTS here (areas from the geometry, h from the still-air
correlations), never fitted from the 2-D map: the map is a cross-section and has
no end faces to fit them to.

STATED APPROXIMATIONS (all of them, in one place):

  * four lumped nodes and a CONSTANT hot-spot offset (max − mean of the winding
    at the calibration point) — the limit is judged on the hot spot;
  * the conductances are fitted at ONE operating point.  A cycle whose segments
    run at different speeds is refused (``duty_cycle_mixed_speed``) rather than
    solved with the wrong gap and bore films; locked rotor (0 rpm) is refused
    (``duty_cycle_locked_rotor``) for the same reason;
  * losses are cycle-averaged over an electrical period — a segment shorter than
    a few periods is not resolved by this model;
  * ONLY copper resistivity feeds back on temperature (the same ρ(T) law the
    loss solver uses).  Iron, magnet and mechanical losses are held at their
    solved values, which is conservative for the iron and optimistic for the
    magnets;
  * radiation sees the air temperature with a view factor of 1, and the mount is
    an infinite sink at its own temperature.

Pure: no FastAPI, no route, no I/O.  Every refusal is a :class:`DutyCycleError`
carrying the ``error_code`` the route will put in its 422.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from motor_ai_sim.thermal_capacities import NODES

#: Short node names, as the stored record spells the conductances (B.3).
SHORT: Dict[str, str] = {"winding": "w", "stator": "s", "rotor": "r",
                         "magnet": "m"}

#: The project's insulation class, in one place and NOT 180 °C (user
#: 2026-09-14).  Mirrors ``report.PROJECT_INSULATION_C`` — imported lazily below
#: so this module never drags the report in, and kept here as the literal the
#: import falls back to.
PROJECT_INSULATION_C = 200.0

#: Past this, there is no machine left to have an equilibrium — the same ceiling
#: the coupled EM↔thermal loop calls runaway (``routes.thermal.RUNAWAY_C``).
RUNAWAY_C = 400.0

#: Stefan-Boltzmann [W/m²·K⁴] and the default surface emissivity of a machined /
#: anodised housing.  Both are the A-group's numbers; they live here only for as
#: long as the adapter below has to stand in for ``cooling_models.outer_still``.
STEFAN_BOLTZMANN = 5.670374419e-8
EMISSIVITY_DEFAULT = 0.9

#: PROVISIONAL still-air film on an end face [W/m²·K], convection + radiation
#: together, used until ``cooling_models.end_face_still`` exists (user
#: 2026-09-14: "keep a thin adapter with a documented constant h ≈ 12 W/m²K and
#: mark it provisional").  It is the right order for a 60 K plate in a room —
#: ~6 convective + ~6 radiative — and every payload that uses it says
#: ``"provisional"`` so no report can quote it as a correlation.
END_FACE_H_PROVISIONAL = 12.0

#: Copper resistivity slope, from the one place the solver defines it.
try:                                                   # one source of truth
    from motor_ai_sim.simulation.field_ops import ALPHA_CU as ALPHA_CU_PER_K
except Exception:                                      # noqa: BLE001
    ALPHA_CU_PER_K = 0.00393
T_CU_REF_C = 20.0

#: Cycle-map convergence: the periodic state is reached when no node's
#: cycle-start temperature moves by more than this between two cycles.
PERIODIC_TOL_K = 0.05

#: Below this ΔT two nodes are not distinguishable and the conductance between
#: them is a division by noise — the CALIBRATED fit is refused and a PHYSICAL
#: conductance is used instead (``allow_merge=True`` restores the old merge).
MERGE_TOL_K = 0.5

#: Through-thickness conductivity of a sintered NdFeB magnet [W/m·K], used when
#: the card carries none.  7.6 is the middle of the 6-9 the grades quote and the
#: number the L13 study (2026-09-15) used; every payload that falls back to it
#: says ``"default"``.
MAGNET_K_DEFAULT = 7.6

#: The cycle lengths the ED-vs-cycle-length curve is evaluated at, seconds.
#: A duty ratio means nothing without the period it is a ratio OF — 25 % of 10 s
#: and 25 % of 300 s are different machines — so the tool that FINDS the regime
#: answers over a span of periods rather than at one (user 2026-09-15).
ED_CYCLE_LENGTHS_S: Tuple[float, ...] = (10.0, 30.0, 60.0, 120.0, 300.0)

#: The split an S3 whose ED was NOT stated is BUILT at, before the search moves
#: it.  It is a seed and never an answer: a profile with ``ed_given=False``
#: reports ``ed_requested_pct: None`` and is integrated at the ALLOWABLE ED.
ED_SEED_PCT = 50.0


class DutyCycleError(ValueError):
    """A duty cycle this model refuses to answer, by name.

    ``code`` is the ``error_code`` the route reports (B.4): one of
    ``no_steady_thermal_map``, ``no_electromagnetic_run``,
    ``duty_cycle_locked_rotor``, ``duty_cycle_mixed_speed``,
    ``duty_cycle_no_periodic_state`` — plus the plain validation refusals a
    malformed spec earns.  ``remedy`` is what to DO about it, and it is never
    empty for a physics refusal.
    """

    def __init__(self, code: str, message: str, remedy: str = ""):
        self.code = str(code)
        self.remedy = str(remedy)
        super().__init__(message + (("  " + remedy) if remedy else ""))
        self.message = message


# ---------------------------------------------------------------------------
# ρ_Cu(T) — the same law the loss solver and the coupled loop use
# ---------------------------------------------------------------------------

def cu_rho_ratio(t_c: float, t_ref_c: float = T_CU_REF_C) -> float:
    """ρ_Cu(T)/ρ_Cu(T_ref), both referred to the card's 20 °C resistivity.

    Identical to ``routes.thermal._cu_rho_ratio`` (re-spelled rather than
    imported so this module stays free of FastAPI); the note there explains why
    the linearised ``1 + α(T − T_ref)`` is wrong by ~6 % over a 60 K step.
    """
    num = 1.0 + float(ALPHA_CU_PER_K) * (float(t_c) - T_CU_REF_C)
    den = 1.0 + float(ALPHA_CU_PER_K) * (float(t_ref_c) - T_CU_REF_C)
    return float(num) / float(den if abs(den) > 1e-12 else 1e-12)


# ---------------------------------------------------------------------------
# Still-air films — the adapters
# ---------------------------------------------------------------------------

def _churchill_chu_h(t_wall_c: float, t_env_c: float, d_m: float) -> Tuple[float, float]:
    """(h_conv, Ra) for a horizontal cylinder in still air — Churchill-Chu.

    FALLBACK ONLY.  It stands in for ``cooling_models.outer_still`` while the
    A-group is adding it, and the payloads that use it are marked
    ``h_source: "adapter"``; delete this function the moment the correlation
    lands in ``cooling_models`` (the call site below prefers it already).
    """
    from motor_ai_sim.simulation.cooling_models import (
        NATURAL_CONVECTION_H, air_properties)

    d = max(float(d_m), 1e-4)
    dt = float(t_wall_c) - float(t_env_c)
    if abs(dt) < 1e-6:
        return NATURAL_CONVECTION_H, 0.0
    t_film = 0.5 * (float(t_wall_c) + float(t_env_c))
    p = air_properties(t_film)
    beta = 1.0 / (t_film + 273.15)
    alpha = p.nu / max(p.pr, 1e-9)
    ra = 9.80665 * beta * abs(dt) * d ** 3 / max(p.nu * alpha, 1e-30)
    nu_d = (0.60 + 0.387 * ra ** (1.0 / 6.0)
            / (1.0 + (0.559 / p.pr) ** (9.0 / 16.0)) ** (8.0 / 27.0)) ** 2
    return max(nu_d * p.k / d, NATURAL_CONVECTION_H), ra


def radiation_h(t_wall_c: float, t_env_c: float,
                emissivity: float = EMISSIVITY_DEFAULT) -> float:
    """Linearised radiation film εσ(T_w²+T_∞²)(T_w+T_∞) [W/m²·K], in kelvin.

    Exact at every ΔT (it is the identity T⁴−T_∞⁴ = (T²+T_∞²)(T+T_∞)(T−T_∞)),
    so the "linearisation" costs nothing but the view factor, which is 1.
    """
    tw = float(t_wall_c) + 273.15
    te = float(t_env_c) + 273.15
    e = max(0.0, min(float(emissivity), 1.0))
    return e * STEFAN_BOLTZMANN * (tw * tw + te * te) * (tw + te)


def housing_h_total(t_wall_c: float, t_env_c: float, d_housing_m: float,
                    emissivity: float = EMISSIVITY_DEFAULT,
                    area_m2: float = 0.0) -> Tuple[float, str, Dict[str, float]]:
    """``(h_total, source, detail)`` for the housing in still air.

    Prefers ``cooling_models.outer_still`` — the A-group's function, which is
    the one a report is allowed to quote — and falls back to the adapter above
    while that function does not exist yet.  ``source`` says which ran.
    """
    try:
        from motor_ai_sim.simulation import cooling_models as cm
        rep = cm.outer_still(t_wall_c=float(t_wall_c),           # type: ignore[attr-defined]
                             t_ambient_c=float(t_env_c),
                             d_housing_m=float(d_housing_m),
                             emissivity=float(emissivity),
                             area_m2=float(area_m2), heat_w=0.0)
        return (float(rep["h_total"]), "cooling_models.outer_still",
                {"h_conv": float(rep.get("h_conv") or 0.0),
                 "h_rad": float(rep.get("h_rad") or 0.0),
                 "ra": float(rep.get("ra") or 0.0)})
    except (ImportError, AttributeError, KeyError, TypeError):
        pass
    h_c, ra = _churchill_chu_h(t_wall_c, t_env_c, d_housing_m)
    h_r = radiation_h(t_wall_c, t_env_c, emissivity)
    return (h_c + h_r, "adapter (cooling_models.outer_still not available yet)",
            {"h_conv": h_c, "h_rad": h_r, "ra": ra})


def end_face_h_total(t_wall_c: float, t_env_c: float,
                     emissivity: float = EMISSIVITY_DEFAULT,
                     char_len_m: float = 0.0,
                     area_m2: float = 0.0) -> Tuple[float, str, Dict[str, float]]:
    """``(h_total, source, detail)`` for an END FACE in still air.

    Prefers ``cooling_models.end_face_still``; until that lands, the PROVISIONAL
    constant :data:`END_FACE_H_PROVISIONAL` is returned and ``source`` says
    ``provisional`` so nothing downstream can present it as a correlation.
    """
    try:
        from motor_ai_sim.simulation import cooling_models as cm
        rep = cm.end_face_still(t_wall_c=float(t_wall_c),         # type: ignore[attr-defined]
                                t_ambient_c=float(t_env_c),
                                char_len_m=float(char_len_m),
                                emissivity=float(emissivity),
                                area_m2=float(area_m2), n_faces=1)
        return (float(rep["h_total"]), "cooling_models.end_face_still",
                {"h_conv": float(rep.get("h_conv") or 0.0),
                 "h_rad": float(rep.get("h_rad") or 0.0)})
    except (ImportError, AttributeError, KeyError, TypeError):
        pass
    return (END_FACE_H_PROVISIONAL,
            "provisional constant (cooling_models.end_face_still not available yet)",
            {"h_conv": END_FACE_H_PROVISIONAL - radiation_h(t_wall_c, t_env_c,
                                                            emissivity),
             "h_rad": radiation_h(t_wall_c, t_env_c, emissivity)})


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    """One stretch of the cycle at ONE operating point.

    ``losses`` are machine watts per node at ``coil_ref_c`` — the coil
    temperature the electromagnetic run was solved at, which is what the copper
    feedback re-references.  ``name`` is ``None`` for an unpowered pause, and
    that is a real segment: the machine cools, it does not vanish.
    """
    name: Optional[str]
    t_s: float
    losses: Dict[str, float]
    coil_ref_c: float = 120.0
    rpm: float = 0.0
    note: str = ""
    #: Let ρ_Cu(T) move the copper loss with the winding temperature.  Off only
    #: for a like-for-like comparison against a 2-D map, which was itself solved
    #: at ONE fixed coil temperature.
    copper_feedback: bool = True

    @property
    def total_W(self) -> float:
        return float(sum(self.losses.get(n, 0.0) for n in NODES))


@dataclass(frozen=True)
class Profile:
    """A normalised duty cycle: what runs, for how long, starting where."""
    kind: str
    segments: Tuple[Segment, ...]
    cycle_s: float
    t_start_c: Optional[float] = None
    n_cycles_max: int = 200
    calibration_duty: Optional[str] = None
    ed_pct: Optional[float] = None
    t_on_s: Optional[float] = None
    rest_duty: Optional[str] = None
    note: str = ""
    #: Did the REQUEST state the duty ratio?  ``False`` is the 2026-09-15
    #: reframe: the user asks the tool to FIND the regime, the block carries no
    #: ``ed_pct``, and the segments below are built at :data:`ED_SEED_PCT` only
    #: so that there is something to vary.  Nothing may report that seed as the
    #: cycle the user asked for — ``ed_requested_pct`` is ``None`` then, and the
    #: cycle that gets integrated is the one at the ALLOWABLE ED.
    ed_given: bool = True

    @property
    def duration_s(self) -> float:
        return float(sum(s.t_s for s in self.segments))

    @property
    def is_periodic(self) -> bool:
        return self.kind in ("S3", "segments")

    def with_ed(self, ed_pct: float) -> "Profile":
        """The same S3 cycle at a different ED — the map ``allowable_ed`` walks.

        Only the SPLIT of the cycle moves: the same on-segment, the same rest
        segment, the same cycle time.  Anything else would make the bisection
        compare two different machines.
        """
        if self.kind != "S3":
            raise DutyCycleError(
                "duty_cycle_not_s3",
                "the duty ratio can only be varied on an S3 (intermittent) "
                "cycle; this one is %s." % self.kind,
                "Set kind: S3 with an ed_pct and a cycle_s first.")
        ed = float(ed_pct)
        if not (0.0 < ed <= 100.0):
            raise DutyCycleError("duty_cycle_bad_ed",
                                 "ED must be >0 and ≤100 %%, got %g." % ed)
        on_s = self.cycle_s * ed / 100.0
        off_s = self.cycle_s - on_s
        segs = [replace(self.segments[0], t_s=on_s)]
        if off_s > 1e-12:
            segs.append(replace(self.segments[-1], t_s=off_s))
        return replace(self, segments=tuple(segs), ed_pct=ed)


# ---------------------------------------------------------------------------
# Losses per node
# ---------------------------------------------------------------------------

def losses_by_node(summary: Mapping[str, Any],
                   thermal: Optional[Mapping[str, Any]] = None,
                   *, shaft_ends_open: Optional[bool] = None,
                   ) -> Dict[str, Any]:
    """Where this operating point's watts are MADE — one number per node.

    ``summary`` is the duty's own summary (the run that solved the point);
    ``thermal`` is a steady thermal payload for the SAME point when there is
    one, and it wins wherever it is more exact:

      * winding — ``P_cu_exact_W`` (the un-rounded copper the map was built
        from), else ``P_stranded_W``;
      * stator  — the Bertotti terms of ``P_core_terms.stator``;
      * rotor   — ``P_core_terms.rotor`` + the SOLID loss that is not in the
        magnets (shaft eddy), + bearing friction when the shaft-end path is
        open (it enters at the shaft seats, ``routes.thermal`` :3317) + half the
        gap windage;
      * magnet  — ``P_mag_eddy_W``; with no thermal payload the whole
        ``P_solid_W`` is put on the magnets and the note SAYS so, because that
        is the conservative half of the ambiguity (the magnets are the part with
        a demagnetisation limit).

    Returns ``{winding, stator, rotor, magnet, total_W, coil_ref_c, rpm, notes,
    basis}``.
    """
    s = dict(summary or {})
    th = dict(thermal or {})
    notes: List[str] = []

    p_w = th.get("P_cu_exact_W")
    if p_w is None:
        p_w = s.get("P_stranded_W")
    p_w = float(p_w or 0.0)
    if p_w <= 0.0:
        raise DutyCycleError(
            "no_electromagnetic_run",
            "this duty's summary carries no copper loss (P_stranded_W), so "
            "there is nothing to integrate.",
            "Re-run the operating point on the Simulation tab and save the duty "
            "again.")

    terms = dict(s.get("P_core_terms") or {})

    def _core(side: str) -> float:
        blk = dict(terms.get(side) or {})
        return float(sum(float(blk.get(k) or 0.0)
                         for k in ("hysteresis_W", "eddy_W", "excess_W")))

    p_s = _core("stator")
    p_r = _core("rotor")
    if not terms:
        # No split available — all of the iron on the STATOR, which is where
        # 90-95 % of it is on every machine this solver has seen, and say so.
        p_s = float(s.get("P_core_W") or 0.0)
        notes.append("this summary carries no P_core_terms split, so the whole "
                     "iron loss %.2f W is put on the stator" % p_s)

    p_solid = float(s.get("P_solid_W") or 0.0)
    p_mag = th.get("P_mag_eddy_W")
    if p_mag is None:
        p_mag = p_solid
        if p_solid > 0.0:
            notes.append("no thermal map to split the solid loss, so all "
                         "%.2f W of it is put on the MAGNETS (conservative: "
                         "the magnets are the part with a limit)" % p_solid)
    else:
        p_mag = float(p_mag or 0.0)
        p_shaft = max(p_solid - p_mag, 0.0)
        if p_shaft > 0.0:
            p_r += p_shaft
            notes.append("solid loss split from the map: %.2f W in the magnets, "
                         "%.2f W in the shaft (on the rotor node)"
                         % (p_mag, p_shaft))
    p_mag = float(p_mag)

    # Mechanical watts, exactly where the steady map puts them.
    mech = dict((th.get("cooling") or {}).get("mech_losses") or {})
    if mech:
        open_ends = (bool(mech.get("shaft_ends_open")) if shaft_ends_open is None
                     else bool(shaft_ends_open))
        p_brg = float(mech.get("P_bearings_W") or 0.0)
        if p_brg > 0.0 and open_ends:
            p_r += p_brg
            notes.append("bearing friction %.2f W enters at the shaft (rotor "
                         "node) — the shaft-ends path is open" % p_brg)
        elif p_brg > 0.0:
            notes.append("bearing friction %.2f W is NOT in this network: the "
                         "shaft-ends path is closed, so the steady map does not "
                         "carry it either" % p_brg)
        p_wind = float(mech.get("P_windage_gap_W") or 0.0)
        if p_wind > 0.0:
            p_r += 0.5 * p_wind
            p_s += 0.5 * p_wind
            notes.append("gap windage %.3f W split 50/50 between rotor and "
                         "stator — it is sheared in the air between them"
                         % p_wind)

    out = {
        "winding": round(p_w, 4),
        "stator": round(p_s, 4),
        "rotor": round(p_r, 4),
        "magnet": round(p_mag, 4),
        "coil_ref_c": float(s.get("coil_temp_C") or th.get("coil_temp_c") or 20.0),
        "rpm": float(s.get("rpm") or th.get("rpm") or 0.0),
        "notes": notes,
        "basis": {"P_cu_source": ("thermal map (P_cu_exact_W)"
                                  if th.get("P_cu_exact_W") is not None
                                  else "summary (P_stranded_W)"),
                  "P_magnet_source": ("thermal map (P_mag_eddy_W)"
                                      if th.get("P_mag_eddy_W") is not None
                                      else "summary (all of P_solid_W)")},
    }
    out["total_W"] = round(sum(float(out[n]) for n in NODES), 4)
    return out


def node_watts(losses: Mapping[str, Any]) -> Dict[str, float]:
    """Just the four numbers, out of what :func:`losses_by_node` returned."""
    return {n: float(losses.get(n, 0.0) or 0.0) for n in NODES}


# ---------------------------------------------------------------------------
# normalise_spec
# ---------------------------------------------------------------------------

_KINDS = ("S1", "S2", "S3", "segments")


def _duty_map(duties: Any) -> Dict[str, Mapping[str, Any]]:
    if isinstance(duties, Mapping):
        return {str(k): v for k, v in duties.items()}
    return {str((d or {}).get("name")): d for d in (duties or ())}


def normalise_spec(block: Mapping[str, Any], duties: Any, *,
                   thermal_by_duty: Optional[Mapping[str, Mapping[str, Any]]] = None,
                   default_duty: Optional[str] = None,
                   structure_only: bool = False) -> Profile:
    """Validate a ``duty_cycle:`` block and turn it into a :class:`Profile`.

    ``duties`` is the configuration's duty list (or ``{name: duty}``); every
    segment names one of them or is ``null`` (an unpowered pause).  Every
    refusal is by NAME and says what to change — a duty cycle that quietly
    became a different cycle is the whole class of bug this project refuses to
    ship.

    ``structure_only`` checks the SHAPE of the block and the NAMES it points at,
    and stops there: no losses are read off the duties and no speed is resolved,
    so the returned profile carries zero watts and must never be integrated.
    That is what the CATALOG needs (``routes.family`` validates the block on
    every duty save): a cycle may legitimately name a duty that has not been run
    yet, and refusing to SAVE it for that would make the editor unusable.  The
    solve refuses then — by name, with the codes above.
    """
    blk = dict(block or {})
    known = _duty_map(duties)
    th_by = dict(thermal_by_duty or {})

    kind = str(blk.get("kind") or "S1").strip()
    if kind not in _KINDS:
        raise DutyCycleError(
            "duty_cycle_bad_kind",
            "unknown duty-cycle kind %r." % kind,
            "Use one of: %s." % ", ".join(_KINDS))

    calib = blk.get("calibration_duty") or default_duty

    def _segment(name: Optional[str], t_s: float) -> Segment:
        t = float(t_s)
        if not (t > 0.0):
            raise DutyCycleError(
                "duty_cycle_bad_segment",
                "a segment of %g s has no duration." % t,
                "Every segment needs t_s > 0.")
        if name is None:
            return Segment(None, t, {n: 0.0 for n in NODES}, coil_ref_c=20.0,
                           rpm=0.0, note="unpowered pause — the machine cools")
        nm = str(name)
        if nm not in known:
            raise DutyCycleError(
                "duty_cycle_unknown_duty",
                "duty %r is not one of this configuration's duties (%s)."
                % (nm, ", ".join(sorted(known)) or "none saved"),
                "Save the duty first, or name one of the duties above.")
        duty = known[nm] or {}
        if structure_only:
            # The name exists and the duration is positive — everything this
            # block can be judged on without a solved machine behind it.
            return Segment(nm, t, {n: 0.0 for n in NODES}, coil_ref_c=20.0,
                           rpm=0.0,
                           note="names checked only — no losses resolved")
        summary = duty.get("summary") or {}
        if not summary:
            raise DutyCycleError(
                "no_electromagnetic_run",
                "duty %r has no solved summary, so its losses are unknown." % nm,
                "Run the operating point on the Simulation tab and save the "
                "duty again.")
        loss = losses_by_node(summary, th_by.get(nm))
        rpm = float(loss.get("rpm") or duty.get("rpm") or 0.0)
        if rpm <= 0.0:
            raise DutyCycleError(
                "duty_cycle_locked_rotor",
                "duty %r runs at %g rpm.  A locked rotor is out of scope for "
                "this model: the air-gap and bore films, and the bearing and "
                "windage terms, are all fitted at a turning speed." % (nm, rpm),
                "Model the holding case on the Thermal tab as a steady point, "
                "or pick a duty with rpm > 0.")
        return Segment(nm, t, node_watts(loss),
                       coil_ref_c=float(loss["coil_ref_c"]), rpm=rpm,
                       note="; ".join(loss["notes"]))

    if kind == "S1":
        nm = blk.get("duty") or calib
        seg = _segment(nm, float(blk.get("t_on_s") or 1.0))
        prof = Profile("S1", (seg,), cycle_s=seg.t_s, calibration_duty=calib,
                       note="continuous duty — the steady point, integrated to it")
    elif kind == "S2":
        t_on = float(blk.get("t_on_s") or 0.0)
        if not (t_on > 0.0):
            raise DutyCycleError(
                "duty_cycle_bad_s2", "S2 needs a run time: t_on_s = %r."
                % blk.get("t_on_s"),
                "Set t_on_s to the length of the pull, in seconds.")
        seg = _segment(blk.get("duty") or calib, t_on)
        prof = Profile("S2", (seg,), cycle_s=t_on, t_on_s=t_on,
                       calibration_duty=calib,
                       note="short-time duty — one pull from the start "
                            "temperature, never repeated")
    elif kind == "S3":
        # ED is OPTIONAL since 2026-09-15: an S3 that states no duty ratio is
        # the user asking the tool to FIND one, and the profile is built at the
        # seed so the search has something to vary.  A STATED ED still has to be
        # a duty ratio — a typed 0 is a cycle that runs for no time at all, and
        # reading it as "find me one" would be the request quietly becoming a
        # different request.
        _raw_ed = blk.get("ed_pct")
        ed_given = not (_raw_ed is None or _raw_ed == "")
        try:
            ed = float(_raw_ed) if ed_given else float(ED_SEED_PCT)
        except (TypeError, ValueError):
            raise DutyCycleError(
                "duty_cycle_bad_ed",
                "S3 needs a duty ratio 0 < ED ≤ 100 %%; got %r." % _raw_ed,
                "ED is the powered share of each cycle, in percent — or leave "
                "it blank and the allowable one is found for you.")
        cyc = float(blk.get("cycle_s") or 0.0)
        if ed_given and not (0.0 < ed <= 100.0):
            raise DutyCycleError(
                "duty_cycle_bad_ed",
                "S3 needs a duty ratio 0 < ED ≤ 100 %%; got %r."
                % blk.get("ed_pct"),
                "ED is the powered share of each cycle, in percent — or leave "
                "it blank and the allowable one is found for you.")
        if not (cyc > 0.0):
            raise DutyCycleError(
                "duty_cycle_bad_cycle",
                "S3 needs a cycle time; got %r." % blk.get("cycle_s"),
                "cycle_s is one ON+OFF period, in seconds.")
        on = _segment(blk.get("duty") or calib, cyc * ed / 100.0)
        rest_name = blk.get("rest_duty")          # null = unpowered pause
        segs = [on]
        if ed < 100.0:
            segs.append(_segment(rest_name, cyc * (100.0 - ed) / 100.0))
        prof = Profile("S3", tuple(segs), cycle_s=cyc, ed_pct=ed,
                       rest_duty=(None if rest_name is None else str(rest_name)),
                       calibration_duty=calib, ed_given=ed_given,
                       note=("intermittent duty — repeated until the cycle "
                             "repeats itself" if ed_given else
                             "intermittent duty with NO duty ratio stated — "
                             "the allowable one is found, and the %g %% split "
                             "below is only the seed the search starts from"
                             % ED_SEED_PCT))
    else:                                          # explicit segments
        raw = list(blk.get("segments") or ())
        if not raw:
            raise DutyCycleError(
                "duty_cycle_bad_segments",
                "kind: segments needs a segments list.",
                "Each entry is {duty: <name or null>, t_s: <seconds>}.")
        segs = [_segment(e.get("duty"), e.get("t_s")) for e in raw]
        prof = Profile("segments", tuple(segs),
                       cycle_s=float(sum(s.t_s for s in segs)),
                       calibration_duty=calib,
                       note="explicit segment list, repeated as one cycle")

    # One speed per cycle — the conductances are fitted at one point.  Nothing
    # resolved a speed under ``structure_only``, so there is nothing to compare.
    speeds = {round(s.rpm, 3) for s in prof.segments if s.name is not None}
    if len(speeds) > 1 and not structure_only:
        raise DutyCycleError(
            "duty_cycle_mixed_speed",
            "this cycle mixes speeds (%s rpm).  The gap, bore and shaft films "
            "and the windage are all fitted at ONE speed, so a mixed-speed "
            "cycle would be solved with the wrong ones."
            % ", ".join("%g" % v for v in sorted(speeds)),
            "Split the cycle into one per speed, or pick duties that share a "
            "speed.")

    t0 = blk.get("t_start_c")
    # ABSENT (or blank, which is what an empty form field sends) is the default;
    # a STATED 0 is a cycle budget of nothing, and reading it as 200 would be
    # the spec quietly becoming a different spec.
    _nraw = blk.get("n_cycles_max")
    n_max = 200 if _nraw is None or _nraw == "" else int(_nraw)
    if n_max < 1:
        raise DutyCycleError("duty_cycle_bad_n_cycles",
                             "n_cycles_max must be at least 1; got %r."
                             % blk.get("n_cycles_max"))
    return replace(prof, t_start_c=(None if t0 is None else float(t0)),
                   n_cycles_max=n_max)


# ---------------------------------------------------------------------------
# The network
# ---------------------------------------------------------------------------

#: The two-node conductances, as ``(node_a, node_b, record key)``.
_PAIRS: Tuple[Tuple[str, str, str], ...] = (
    ("winding", "stator", "w_s"),
    ("rotor", "stator", "r_s"),
    ("magnet", "rotor", "m_r"),
)

#: The side (axial end-face) paths, per node.
_SIDE_KEY: Dict[str, str] = {"winding": "winding_ends", "stator": "stator_ends",
                             "rotor": "rotor_ends", "magnet": "magnet_ends"}


# ---------------------------------------------------------------------------
# PHYSICAL conductances — what a link gets when the map cannot fit one
# ---------------------------------------------------------------------------
# A 2-D map whose rotor and stator MEANS sit a kelvin apart (or whose gap watt
# points the wrong way) cannot be divided into a conductance.  Merging the two
# nodes was the first answer and it is wrong in both directions at once
# (L13 study, 2026-09-15):
#
#   * for the WINDING it is not conservative — the merged lump hands the coil
#     the rotor's 71 J/K to lean on, and the S2 pull to 200 °C from cold reads
#     35.65 s merged against 26.59 s with the rotor kept separate;
#   * for the MAGNETS it is pessimistic by 23 K — nailed to the stator's PEAK
#     they read 143.8 °C on the 25 %/60 s cycle against 120.4 °C when their own
#     capacity is allowed to float.
#
# So the link keeps a conductance; it is simply not fitted.  Both are textbook
# series paths with no free parameter:
#
#   G_rs = k_eff·A/δ + h_rad·A          across the air gap
#   G_mr = k_magnet·A_root / (h_mag/2)  magnet mid-height to the rotor iron
#
# and every number in them is printed in the note the network carries.

def gap_radiation_h(t_a_c: float, t_b_c: float,
                    eps_a: float = EMISSIVITY_DEFAULT,
                    eps_b: float = EMISSIVITY_DEFAULT) -> float:
    """Linearised radiation film between the two GAP faces [W/m²·K].

    Two long coaxial grey surfaces, so the room is not in it and the emissivity
    is the pair's: ``σ(T_a²+T_b²)(T_a+T_b) / (1/ε_a + 1/ε_b − 1)``.  The area
    ratio of the two cylinders is 1 to within the gap-to-diameter ratio (0.9 %
    on the L13), so it is left out and said so here rather than carried as a
    fourth digit.  Unlike :func:`radiation_h` this one does NOT see ambient: a
    gap face looks at the other gap face.
    """
    ta = float(t_a_c) + 273.15
    tb = float(t_b_c) + 273.15
    ea = max(1e-6, min(float(eps_a), 1.0))
    eb = max(1e-6, min(float(eps_b), 1.0))
    denom = 1.0 / ea + 1.0 / eb - 1.0
    return STEFAN_BOLTZMANN * (ta * ta + tb * tb) * (ta + tb) / max(denom, 1e-9)


def gap_conductance(*, stack_m: float, delta_m: float, d_mean_m: float,
                    t_rotor_c: float, t_stator_c: float,
                    k_eff: Optional[float] = None,
                    eps_rotor: float = EMISSIVITY_DEFAULT,
                    eps_stator: float = EMISSIVITY_DEFAULT) -> Dict[str, Any]:
    """rotor ↔ stator across the air gap, from the geometry — still-air
    conduction plus gap radiation, ``G = k_eff·A/δ + h_rad·A``.

    ``k_eff`` is the calibration map's OWN gap conductivity when it has one
    (``cooling.gap.k_eff``, the Becker-Kaye value the 2-D solve ran with, Nu = 1
    on a laminar gap); with none it is plain air at the film temperature, and
    the payload says which.  ``δ`` is the MECHANICAL clearance — air gap minus
    any retaining sleeve — and ``A = π·D·L`` at the mean gap diameter.
    """
    L = float(stack_m)
    d = float(d_mean_m)
    delta = float(delta_m)
    if not (L > 0.0 and d > 0.0 and delta > 0.0):
        raise DutyCycleError(
            "duty_cycle_no_gap_geometry",
            "the gap conductance needs a stack length, a mean gap diameter and "
            "a clearance; got L %g m, D %g m, δ %g m." % (L, d, delta))
    area = math.pi * d * L
    if k_eff is not None and float(k_eff) > 0.0:
        k, k_src = float(k_eff), "the calibration map's own gap block (k_eff)"
    else:
        from motor_ai_sim.simulation.cooling_models import air_properties
        k = float(air_properties(0.5 * (float(t_rotor_c)
                                        + float(t_stator_c))).k)
        k_src = "air at the gap film temperature (the map carries no gap block)"
    g_cond = k * area / delta
    h_rad = gap_radiation_h(t_rotor_c, t_stator_c, eps_rotor, eps_stator)
    g_rad = h_rad * area
    return {
        "G_W_per_K": g_cond + g_rad,
        "G_conduction_W_per_K": g_cond,
        "G_radiation_W_per_K": g_rad,
        "area_m2": area, "delta_m": delta, "d_mean_m": d, "stack_m": L,
        "k_eff_W_per_mK": k, "k_source": k_src,
        "h_rad_W_per_m2K": h_rad,
        "emissivity": [float(eps_rotor), float(eps_stator)],
        "formula": ("G_rs = k_eff·A/δ + h_rad·A with A = π·D·L = π·%.2f mm·"
                    "%.2f mm = %.2f cm², δ = air gap − sleeve = %.3f mm, "
                    "k_eff = %.4f W/m·K (%s), h_rad = %.2f W/m²·K "
                    "(ε %.2f/%.2f at %.1f/%.1f °C) → %.4f + %.4f = %.4f W/K"
                    % (d * 1e3, L * 1e3, area * 1e4, delta * 1e3, k, k_src,
                       h_rad, eps_rotor, eps_stator, t_rotor_c, t_stator_c,
                       g_cond, g_rad, g_cond + g_rad)),
    }


def magnet_root_conductance(*, stack_m: float, d_root_m: float,
                            magnet_height_m: float,
                            k_magnet: Optional[float] = None,
                            ) -> Dict[str, Any]:
    """magnet ↔ rotor iron, ``G = k·A_root / (h_mag/2)``.

    The magnet's ONLY conductive route in a surface-mounted topology: half the
    magnet height (mid-height to root, the lumped node being the mean) through
    the root area ``A = π·D_root·L``.  ``k_magnet`` is the card's; with none the
    :data:`MAGNET_K_DEFAULT` is used and the payload says ``default``.

    Stated approximation: a bond line is NOT modelled.  A 0.1 mm epoxy joint in
    series would take the L13's 4.58 W/K to 2.65 and its magnet peak by 0.2 K
    (L13 study, 2026-09-15) — far inside what this model claims.
    """
    L = float(stack_m)
    d = float(d_root_m)
    h = float(magnet_height_m)
    if not (L > 0.0 and d > 0.0 and h > 0.0):
        raise DutyCycleError(
            "duty_cycle_no_magnet_geometry",
            "the magnet conductance needs a stack length, a root diameter and a "
            "magnet height; got L %g m, D %g m, h %g m." % (L, d, h))
    k = float(k_magnet) if (k_magnet is not None and float(k_magnet) > 0.0) \
        else MAGNET_K_DEFAULT
    k_src = ("the magnet card" if (k_magnet is not None
                                   and float(k_magnet) > 0.0)
             else "default %.1f W/m·K (the card carries none)"
                  % MAGNET_K_DEFAULT)
    area = math.pi * d * L
    g = k * area / (0.5 * h)
    return {
        "G_W_per_K": g, "area_m2": area, "d_root_m": d, "stack_m": L,
        "magnet_height_m": h, "k_W_per_mK": k, "k_source": k_src,
        "formula": ("G_mr = k·A_root/(h_mag/2) with A_root = π·D·L = π·%.2f mm·"
                    "%.2f mm = %.2f cm², h_mag/2 = %.2f mm, k = %.2f W/m·K "
                    "(%s) → %.4f W/K"
                    % (d * 1e3, L * 1e3, area * 1e4, 0.5 * h * 1e3, k, k_src,
                       g)),
    }


def _gap_geometry(geometry: Optional[Mapping[str, Any]],
                  thermal_result: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    """``{stack_m, delta_m, d_mean_m}`` for the air gap, or ``None``.

    The calibration map's own gap block wins where it has the numbers (the live
    payload carries ``delta_mm`` and ``r_mean_mm`` straight out of
    ``cooling_models.taylor_couette_gap``); a COMPACTED record carries only
    ``k_eff``/``Ta``/``Nu``, and then the radii come from the geometry.
    """
    g = dict(geometry or {})
    gap = dict((dict(thermal_result or {}).get("cooling") or {}).get("gap") or {})

    def _mm(src: Mapping[str, Any], key: str) -> float:
        try:
            return float(src.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    stack_m = _mm(g, "motor_length") * 1e-3
    if stack_m <= 0.0:
        try:
            stack_m = float(thermal_result.get("stack_m") or 0.0)
        except (TypeError, ValueError):
            stack_m = 0.0

    delta_m = _mm(gap, "delta_mm") * 1e-3
    d_mean_m = 2.0 * _mm(gap, "r_mean_mm") * 1e-3
    if delta_m <= 0.0 or d_mean_m <= 0.0:
        r_rot = _mm(g, "rotor_outer_radius") + _mm(g, "sleeve_thickness")
        r_bore = _mm(g, "stator_inner_radius")
        if r_bore <= 0.0 and r_rot > 0.0:
            r_bore = r_rot + _mm(g, "air_gap") - _mm(g, "sleeve_thickness")
        delta_m = max(r_bore - r_rot, 0.0) * 1e-3
        d_mean_m = (r_bore + r_rot) * 1e-3
    if stack_m > 0.0 and delta_m > 0.0 and d_mean_m > 0.0:
        return {"stack_m": stack_m, "delta_m": delta_m, "d_mean_m": d_mean_m}
    return None


def _magnet_geometry(geometry: Optional[Mapping[str, Any]],
                     thermal_result: Mapping[str, Any]
                     ) -> Optional[Dict[str, float]]:
    """``{stack_m, d_root_m, magnet_height_m}`` for the magnet root, or ``None``."""
    g = dict(geometry or {})

    def _f(key: str) -> float:
        try:
            return float(g.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    stack_m = _f("motor_length") * 1e-3
    if stack_m <= 0.0:
        try:
            stack_m = float(thermal_result.get("stack_m") or 0.0)
        except (TypeError, ValueError):
            stack_m = 0.0
    h = _f("magnet_height") * 1e-3
    r_root = _f("rotor_outer_radius") * 1e-3 - h
    if stack_m > 0.0 and h > 0.0 and r_root > 0.0:
        return {"stack_m": stack_m, "d_root_m": 2.0 * r_root,
                "magnet_height_m": h}
    return None


@dataclass
class Network:
    """Conductances, areas and sinks — everything the integrator needs.

    ``G`` holds the FIXED conductances in W/K under the short keys of the stored
    record (``w_s``, ``r_s``, ``m_r``, ``s_mount``, ``r_bore``, ``r_shaft``).
    The housing film and the four end-face films are NOT in it: they depend on
    the wall temperature and are re-evaluated every step through
    :func:`still_air_G`.
    """
    G: Dict[str, float]
    areas: Dict[str, float] = field(default_factory=dict)
    char_len_m: Dict[str, float] = field(default_factory=dict)
    t_ambient_c: float = 25.0
    t_mount_c: float = 25.0
    emissivity: float = EMISSIVITY_DEFAULT
    d_housing_m: float = 0.0
    hot_spot_offset_k: float = 0.0
    node_of: Dict[str, str] = field(default_factory=lambda: {n: n for n in NODES})
    merged: Tuple[Tuple[str, str], ...] = ()
    #: ``{record key: "calibrated" | "physical" | "merged"}`` — WHERE each
    #: internal conductance came from.  A report that prints a gap conductance
    #: has to be able to say whether it was divided out of the map or computed
    #: from the geometry, and a merged link is a missing conductance, not a
    #: small one.
    links: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    housing_G_fixed: Optional[float] = None
    #: WHAT THE CALIBRATION MAP SAYS each SURFACE path is worth (2026-09-20):
    #: ``{path: W/K}`` at ``t_fit_c[path]``, with ``film_kind[path]`` saying
    #: whether that film moves with the wall temperature (``"natural"``) or not
    #: (``"forced"`` — blown air, a jacket, a typed h).  See :func:`still_air_G`
    #: for why a fitted conductance beats a re-evaluated correlation.
    G_fit: Dict[str, float] = field(default_factory=dict)
    t_fit_c: Dict[str, float] = field(default_factory=dict)
    film_kind: Dict[str, str] = field(default_factory=dict)
    calibration: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    h_sources: Dict[str, str] = field(default_factory=dict)

    # -- the active (post-merge) node set ----------------------------------
    @property
    def active_nodes(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for n in NODES:
            rep = self.node_of.get(n, n)
            if rep not in seen:
                seen.append(rep)
        return tuple(seen)

    def rep(self, node: str) -> str:
        return self.node_of.get(node, node)

    def link_kinds(self) -> Dict[str, str]:
        """``{w_s|r_s|m_r: kind}`` — one word per internal link."""
        return {k: str(v.get("kind") or "") for k, v in self.links.items()}

    def as_record(self) -> Dict[str, Any]:
        """The ``network`` block of the stored duty-cycle record (B.3)."""
        return {
            "G_W_per_K": {k: round(float(v), 6) for k, v in self.G.items()},
            "areas_m2": {k: round(float(v), 6) for k, v in self.areas.items()},
            "t_ambient_c": round(self.t_ambient_c, 2),
            "t_mount_c": round(self.t_mount_c, 2),
            "emissivity": self.emissivity,
            "d_housing_m": round(self.d_housing_m, 5),
            "hot_spot_offset_K": round(self.hot_spot_offset_k, 2),
            "merged": [list(p) for p in self.merged],
            # WHICH of the three internal links is a fit, which is a formula and
            # which (backward compatibility only) is a merge — with the numbers
            # behind each one.  ``merged`` above stays for the records written
            # before this block existed.
            "links": {k: dict(v) for k, v in self.links.items()},
            "link_kinds": self.link_kinds(),
            "h_sources": dict(self.h_sources),
            "calibration": dict(self.calibration),
            "notes": list(self.notes),
        }


def end_face_areas(summary: Mapping[str, Any], *, stack_m: float,
                   geometry: Optional[Mapping[str, Any]] = None,
                   n_coils: Optional[int] = None,
                   k_end: Optional[float] = None,
                   perimeter_mode: str = "library") -> Dict[str, Any]:
    """Axial end-face areas [m²] per node, from the run's own numbers.

    * **winding end faces** — ``cooling_models.end_winding_area``, the SAME
      derivation the open frame's forced-air path uses (``n_coils · 2 sides ·
      P_exposed · ℓ_end`` with ``ℓ_end = (k_end − 1)·L_stack/2``, the end-turn
      length the copper loss was billed at, and ``P_exposed = 2t + w`` because
      one wide face looks at the tooth).  One derivation and two films, so the
      still-air machine and the open one cannot disagree about how much copper
      is out there.  ``perimeter_mode="full"`` bills the whole ``2(t + w)``
      instead, and with no wire geometry at all the bundle is taken as square
      off the copper section (``4·√(A_slot_copper)``, within ~15 % on this
      machine) — both are REPORTED as the coarser basis.
    * **core / magnet end faces** — the part's own SECTION area × 2 sides, taken
      straight off the mass row (``volume_cm3 / stack``), so it is the area the
      CAD polygons actually have.

    Approximation, stated: the end faces are treated as flat plates in still
    air, ignoring that the rotor ones turn (which can only help) and that a
    fraction of each core face is covered by the end winding it carries.
    """
    s = dict(summary or {})
    g = dict(geometry or {})
    L = float(stack_m)
    if not (L > 0.0):
        raise DutyCycleError("duty_cycle_no_stack",
                             "the stack length is needed for the end-face "
                             "areas; got %r m." % stack_m)
    rows = list(s.get("mass_components") or ())
    if not rows:
        raise DutyCycleError(
            "no_electromagnetic_run",
            "this duty's summary carries no mass_components, so the end-face "
            "areas cannot be measured.")

    def _section_m2(prefix: str) -> float:
        for r in rows:
            if str(r.get("name") or "").strip().lower().startswith(prefix):
                v = float(r.get("volume_cm3") or 0.0) * 1e-6      # m³
                return v / L
        return 0.0

    a_stator = _section_m2("stator core")
    a_rotor = _section_m2("rotor back-iron") + _section_m2("shaft")
    a_magnet = _section_m2("magnets")

    kend = float(k_end if k_end is not None
                 else (s.get("end_winding_factor") or 0.0))
    a_cu_m2 = float(s.get("A_copper_slotted_mm2") or 0.0) * 1e-6
    n_c = int(n_coils or 0)
    if not n_c and g:
        n_c = int(float(g.get("num_seg") or 0)
                  * float(g.get("num_slots_per_segment") or 0))
    ell_m = max(kend - 1.0, 0.0) * L / 2.0
    basis = "wire geometry"
    a_ew = 0.0
    if g.get("wire_width") and g.get("num_wires_per_slot"):
        t_m = (float(g["num_wires_per_slot"])
               * (float(g.get("wire_height") or 0.0)
                  + float(g.get("wire_spacing_y") or 0.0))) * 1e-3
        w_m = float(g["wire_width"]) * 1e-3 * max(int(g.get("wire_split") or 1), 1)
        d_char = 4.0 * (t_m * w_m) / max(2.0 * (t_m + w_m), 1e-9)
        if perimeter_mode == "library":
            from motor_ai_sim.simulation.cooling_models import end_winding_area
            rep = end_winding_area(n_coils=n_c, bar_thickness_m=t_m,
                                   bar_width_m=w_m, end_turn_length_m=ell_m,
                                   n_sides=2)
            a_ew = float(rep["area_m2"])
            per_m = float(rep["perimeter_m"])
            d_char = float(rep.get("d_equiv_m") or d_char)
            basis = ("cooling_models.end_winding_area (shielded perimeter "
                     "2t + w, the open frame's own convention)")
        else:
            per_m = 2.0 * (t_m + w_m)
            basis = "wire geometry, FULL bundle perimeter 2(t + w)"
    else:
        if not (a_cu_m2 > 0.0 and n_c > 0):
            raise DutyCycleError(
                "duty_cycle_no_winding_area",
                "the end-winding area needs either the wire geometry or the "
                "slotted copper section (A_copper_slotted_mm2) and the coil "
                "count.")
        a_coil = a_cu_m2 / n_c
        per_m = 4.0 * math.sqrt(a_coil)
        d_char = math.sqrt(a_coil)
        basis = "copper section, bundle taken as square (±15 %)"
    if a_ew <= 0.0:
        a_ew = n_c * 2.0 * per_m * ell_m

    return {
        "winding_ends": a_ew,
        "stator_ends": 2.0 * a_stator,
        "rotor_ends": 2.0 * a_rotor,
        "magnet_ends": 2.0 * a_magnet,
        "char_len_m": {"winding_ends": d_char,
                       "stator_ends": math.sqrt(max(a_stator, 1e-12)),
                       "rotor_ends": math.sqrt(max(a_rotor, 1e-12)),
                       "magnet_ends": math.sqrt(max(a_magnet, 1e-12))},
        "basis": {"winding_ends": basis, "k_end": kend, "n_coils": n_c,
                  "end_turn_length_m": ell_m,
                  "perimeter_mode": perimeter_mode,
                  "cores": "mass-row section area (volume / stack) × 2 sides"},
    }


def network_from_steady(thermal_result: Mapping[str, Any], *,
                        mount_g_w_per_k: float = 0.0,
                        mount_temp_c: Optional[float] = None,
                        t_ambient_c: Optional[float] = None,
                        side_areas: Optional[Mapping[str, Any]] = None,
                        d_housing_m: Optional[float] = None,
                        emissivity: Optional[float] = None,
                        merge_tol_k: float = MERGE_TOL_K,
                        calibration_duty: Optional[str] = None,
                        geometry: Optional[Mapping[str, Any]] = None,
                        magnet_k_w_per_mk: Optional[float] = None,
                        allow_merge: bool = False,
                        surface_fit: bool = True,
                        ) -> Network:
    """Fit the lumped network to ONE converged steady map.

    ``thermal_result`` is a ``routes.thermal.solve_thermal_field`` payload:
    ``components{winding,stator,rotor,magnet}{max,avg}``,
    ``cooling.heat_budget{gap_W, bore_W, shaft_ends_W, housing_W, losses_W}``,
    ``cooling.outer{area_m2, t_sink_c}``, ``P_cu_exact_W``, ``P_mag_eddy_W``.

    The side-face conductances are NOT fitted here — a 2-D cross-section has no
    end faces to fit them to.  They are inputs: pass ``side_areas`` (from
    :func:`end_face_areas`) and they are evaluated as ``h_still(ΔT)·A`` at every
    step of the integration.

    Two nodes whose mean temperatures are closer than ``merge_tol_k`` (or whose
    fit comes out negative) cannot have a conductance DIVIDED out of the map —
    a watt over 0.2 K of numerical noise is not a number.  Since 2026-09-15 that
    link is not merged either: it is given the PHYSICAL conductance of the
    interface (:func:`gap_conductance`, :func:`magnet_root_conductance`), which
    needs ``geometry`` (the merged geometry dict — ``motor_length``,
    ``rotor_outer_radius``, ``air_gap``, ``sleeve_thickness``,
    ``magnet_height``) and, for the magnets, the card's conductivity.
    ``Network.links`` says of every link whether it is ``calibrated``,
    ``physical`` or ``merged``.

    ``surface_fit`` (2026-09-20, **the DEFAULT since 2026-09-21** — owner:
    *«давай включай все»*) takes each SURFACE path's conductance from
    the map as well — the housing film the map actually used (forced air, a
    jacket, a typed h) instead of a natural-convection correlation, the axial
    end faces from the watts the map's heat budget closes on, and the open
    frame's end-turn and slot-channel paths, which the network had no key for at
    all.  It also takes every watt a node sends STRAIGHT to the room off that
    node's internal drive, so the winding→stator link is fitted to what actually
    crosses into the iron.  It shipped OFF for one day, so the owner could see
    what it moves before it moved anything; ``surface_fit=False`` still restores
    the pre-2026-09-21 network exactly, which is how a record written before
    that date has to be read back.  ``docs/CONTINUOUS_RATING_2026-09-20.md``
    carries the measured sizes.

    WHAT TURNING IT ON COST, and it was not what it looked like: the L13 test
    set went from 60 s to over 14 minutes without finishing, and the cause was
    NOT this fit.  A 4 % change in one conductance was enough to tip the S3
    search into an unguarded Aitken extrapolation that threw the rotor to
    −2822 °C, and LSODA then ground for ever on air properties below absolute
    zero.  :func:`_aitken` now has a lower bound; with it the same set runs in
    34 s — an order of magnitude FASTER than before either change.

    ``allow_merge=True`` restores the old behaviour (capacities and losses
    summed, no conductance carried) and is there for reading an OLD record back,
    not for solving: the merge is non-conservative for the winding — it hands
    the coil the rotor's heat capacity, which on the L13 turns a 26.6 s S2 pull
    into a 35.7 s one — and 23 K pessimistic on the magnets.  With no geometry
    to build a physical conductance from, the merge is all there is and the
    network's notes say so.
    """
    res = dict(thermal_result or {})
    comps = dict(res.get("components") or {})
    cooling = dict(res.get("cooling") or {})
    budget = dict(cooling.get("heat_budget") or {})
    outer = dict(cooling.get("outer") or {})
    if not comps or not budget:
        raise DutyCycleError(
            "no_steady_thermal_map",
            "the calibration needs a solved steady thermal map (components and "
            "heat budget), and this payload has none.",
            "Solve the Thermal tab at the calibration duty first.")

    def _avg(name: str) -> float:
        blk = comps.get(name) or {}
        v = blk.get("avg")
        if v is None:
            raise DutyCycleError(
                "no_steady_thermal_map",
                "the steady map carries no mean temperature for the %s, so the "
                "conductances cannot be fitted." % name,
                "Re-solve the thermal map on a mesh that contains this part.")
        return float(v)

    t_amb = float(t_ambient_c if t_ambient_c is not None
                  else res.get("ambient_temp", outer.get("t_sink_c", 25.0)))
    t_mount = float(mount_temp_c if mount_temp_c is not None else t_amb)
    eps = float(emissivity if emissivity is not None
                else outer.get("emissivity", EMISSIVITY_DEFAULT))

    means = {n: _avg(n) for n in NODES}
    notes: List[str] = []

    P_cu = float(res.get("P_cu_exact_W") or budget.get("coil_W") or 0.0)
    P_mag = float(res.get("P_mag_eddy_W") or 0.0)
    gap_w = float(budget.get("gap_W") or 0.0)
    bore_w = float(budget.get("bore_W") or 0.0)
    shaft_w = float(budget.get("shaft_ends_W") or 0.0)
    # THE BEARINGS (2026-09-26, the robotics heat path 'shaft' / 'both'): the
    # rotor's conduction path out through the shaft into the housing /
    # structure.  0 on every map before it.
    bearings_w = float(budget.get("bearings_W") or 0.0)
    housing_w = float(budget.get("housing_W") or 0.0)
    # ── THE OPEN FRAME's two winding-side paths (2026-09-20) ────────────────
    # On a machine built WITHOUT a housing the map cools the end turns in the
    # airflow and the ventilated slot channels directly to the room, and those
    # two are not the housing, not the bore and not an axial end face — so
    # until today the network had nowhere to put them and simply did not carry
    # them.  On the Ø50 joint that is 207 W of the 262 W the machine makes: the
    # fitted network could reject 55 W, every transient ran away, and the
    # continuous rating came out `feasible: false` under a cooling the machine
    # actually holds (measured while writing this).  Worse, the winding→stator
    # fit `P_cu / ΔT` assumed ALL the copper crossed into the iron when only a
    # seventh of it does, so `w_s` came out 7× too stiff.
    #
    # Both are fitted here as ONE conductance from the WINDING node to the
    # ambient — the end turns are copper and the slot channels blow over the
    # slot the copper sits in — and the copper they take is removed from the
    # winding→stator drive.  Zero on every housed machine, where the map
    # reports both as `mode: housed` with no watts, so nothing that has been
    # computed before this line existed moves.
    #
    # OPT-IN, and that is not squeamishness (``surface_fit``, default False).
    # Switching it on moves every number this network has ever produced on a
    # machine that is not a still-air housed one — the L13's time to its
    # insulation class among them, which is printed in reports already
    # delivered.  The project's rule is that live answers do not move without
    # the owner's word, so the correction ships switched OFF, the continuous
    # rating (a NEW feature, with no records to disturb) asks for it, and the
    # finding goes to the owner with the numbers above.  Turning it on for the
    # loop and the duty cycle is a one-word change once he has seen them.
    ew_w = float((dict(cooling.get("end_windings") or {})
                  ).get("heat_removed_W") or 0.0)
    ch_w = float((dict(cooling.get("slot_channels") or {})
                  ).get("heat_removed_W") or 0.0)
    open_w = max(ew_w + ch_w, 0.0) if surface_fit else 0.0
    # …and the AXIAL END FACES, which are the robotics machine's version of the
    # same thing: 192 of the 263 W this Ø50 joint makes leave through them.  Any
    # watt a node sends straight to the room is a watt that does NOT cross into
    # the iron, and the internal conductances are fitted to what does.
    _efw = {n: (float((dict((cooling.get("end_faces") or {}).get(n) or {})
                       ).get("heat_removed_W") or 0.0) if surface_fit else 0.0)
            for n in NODES}
    # ── THE VENTILATED AIR GAP (2026-09-21) ────────────────────────────────
    # An OPEN machine's clearance is a duct the wash blows through, and the map
    # reports what each half of it carries: the rotor half (inside the slip
    # radius, plus any open magnet recess) and the stator half.  They are the
    # rotor's and the stator's, not the winding's, so they get their own two
    # conductances to ambient below — without them the network hands the rotor
    # back the watts the map took off it and every transient runs the magnets
    # hot.  Zero on a housed machine, where the block reads `mode: off`.
    _gapf = dict(cooling.get("gap_flow") or {})
    gf_rot_w = (max(float(_gapf.get("rotor_side_W") or 0.0), 0.0)
                if surface_fit else 0.0)
    gf_sta_w = (max(float(_gapf.get("stator_side_W") or 0.0), 0.0)
                if surface_fit else 0.0)
    drive = {"w_s": max(P_cu - open_w - _efw["winding"], 0.0),
             "r_s": gap_w,
             "m_r": max(P_mag - _efw["magnet"], 0.0)}
    if open_w > 0.0 or _efw["winding"] > 0.0:
        notes.append(
            "%.2f W leave the winding DIRECTLY to the room (%.2f W from the end "
            "turns in the airflow, %.2f W through the ventilated slot channels, "
            "%.2f W off the axial end face), so only %.2f W of the %.2f W of "
            "copper crosses into the iron and the winding→stator conductance is "
            "fitted to that"
            % (open_w + _efw["winding"], ew_w, ch_w, _efw["winding"],
               drive["w_s"], P_cu))
    # The map's own gap block (``k_eff``, and on a live payload the clearance and
    # the mean radius too) and the emissivity the gap faces radiate at.
    gap_blk = dict(cooling.get("gap") or {})
    # The GAP faces are machined steel and magnet, not the painted housing, so
    # they radiate at the default 0.9/0.9 and not at the housing's ``emissivity``
    # (which the panel lets the user set for the outer surface alone).
    eps_gap = EMISSIVITY_DEFAULT

    # ── fit each pair where the map can, use the PHYSICS where it cannot ────
    # Two ways a fit is not a conductance:
    #   * the two means are within `merge_tol_k` — a watt divided by numerical
    #     noise;
    #   * the fit comes out NEGATIVE, i.e. the map says the heat crosses this
    #     interface the other way.  That is not a sign error, it is the lumped
    #     model's own limit: the gap is driven by (tooth tip − magnet face) and
    #     not by the two MEANS, so on a machine whose rotor makes almost nothing
    #     and whose stator is cooled from its outer surface the mean-to-mean
    #     drop can point the wrong way.
    # Either way the MAP has nothing to say about this conductance — but the
    # GEOMETRY does, and since 2026-09-15 that is what the link gets: the air
    # gap and the magnet root both have a closed form (``gap_conductance``,
    # ``magnet_root_conductance``).  Merging the two nodes instead, which is
    # what this function used to do, is wrong in both directions at once — it
    # lends the winding a heat capacity it is not in contact with (L13: S2 to
    # 200 °C reads 35.6 s merged against 26.6 s), and it nails the magnets to
    # the stator's peak (143.8 °C against 120.4).  A winding↔stator link the map
    # cannot resolve has no closed form to fall back on, and neither has
    # anything when no geometry was given, so the merge stays as the last
    # resort — and says, in the note, that it is not conservative.
    node_of = {n: n for n in NODES}
    merged: List[Tuple[str, str]] = []
    raw_fits: Dict[str, Optional[float]] = {}
    links: Dict[str, Dict[str, Any]] = {}
    G: Dict[str, float] = {}
    k_mag = (float(magnet_k_w_per_mk) if magnet_k_w_per_mk is not None
             else (float(res["k_magnet"]) if res.get("k_magnet") else None))

    def _physical(key: str) -> Optional[Dict[str, Any]]:
        """The interface's own conductance for this link, or ``None``."""
        try:
            if key == "r_s":
                geo = _gap_geometry(geometry, res)
                if geo is None:
                    return None
                return gap_conductance(t_rotor_c=means["rotor"],
                                       t_stator_c=means["stator"],
                                       k_eff=(float(gap_blk["k_eff"])
                                              if gap_blk.get("k_eff") else None),
                                       eps_rotor=eps_gap, eps_stator=eps_gap,
                                       **geo)
            if key == "m_r":
                geo = _magnet_geometry(geometry, res)
                if geo is None:
                    return None
                return magnet_root_conductance(k_magnet=k_mag, **geo)
        except DutyCycleError:
            return None
        return None

    for a, b, key in _PAIRS:
        ra, rb = node_of[a], node_of[b]
        if ra == rb:                       # already merged through another pair
            G[key] = 0.0
            raw_fits[key] = None
            links[key] = {"kind": "merged", "nodes": [a, b], "G_W_per_K": 0.0,
                          "basis": "both nodes are already one lump"}
            continue
        dt = means[a] - means[b]
        p_w = float(drive[key])
        fit = (p_w / dt) if abs(dt) > 1e-9 else float("inf")
        raw_fits[key] = (None if abs(dt) <= 1e-9 else round(fit, 5))
        unresolved = abs(dt) < float(merge_tol_k) or not (fit > 0.0)
        why = ("below the %.2f K the model resolves" % merge_tol_k
               if abs(dt) < float(merge_tol_k)
               else "the wrong sign: mean-to-mean is not the driving "
                    "temperature difference across this interface")
        if not unresolved:
            G[key] = fit
            links[key] = {
                "kind": "calibrated", "nodes": [a, b],
                "G_W_per_K": round(float(fit), 6),
                "basis": ("fitted to the calibration map: %.3f W over the "
                          "%.2f K between the two means (%.2f / %.2f °C)"
                          % (p_w, dt, means[a], means[b])),
                "dt_K": round(dt, 3), "P_W": round(p_w, 4)}
            continue
        phys = None if allow_merge else _physical(key)
        if phys is not None:
            G[key] = float(phys["G_W_per_K"])
            links[key] = {
                "kind": "physical", "nodes": [a, b],
                "G_W_per_K": round(float(phys["G_W_per_K"]), 6),
                "basis": phys["formula"], "dt_K": round(dt, 3),
                "P_W": round(p_w, 4),
                "why_not_calibrated": why,
                **{k: (round(v, 8) if isinstance(v, float) else v)
                   for k, v in phys.items() if k not in ("formula",)},
            }
            notes.append(
                "%s and %s keep a PHYSICAL conductance rather than being "
                "merged: the calibration map puts them at %.2f / %.2f °C "
                "(Δ %.2f K) with %.3f W crossing, which is %s, so the fit is "
                "refused.  %s"
                % (a, b, means[a], means[b], dt, p_w, why, phys["formula"]))
            continue
        # Nothing to build a conductance from — the old merge, and it is loud.
        for n, r in list(node_of.items()):
            if r == ra:
                node_of[n] = rb
        merged.append((a, b))
        G[key] = 0.0
        links[key] = {"kind": "merged", "nodes": [a, b], "G_W_per_K": 0.0,
                      "dt_K": round(dt, 3), "P_W": round(p_w, 4),
                      "basis": ("no conductance: the fit is %s and %s"
                                % (why, ("no geometry was given to build the "
                                         "physical one from"
                                         if not allow_merge else
                                         "allow_merge was asked for")))}
        notes.append(
            "%s and %s are MERGED: the calibration map puts them at "
            "%.2f / %.2f °C (Δ %.2f K) with %.3f W crossing, which fits a "
            "conductance of %s — %s.  Their capacities and losses are "
            "summed and no conductance is carried between them.%s"
            % (a, b, means[a], means[b], dt, p_w,
               ("%.3f W/K" % fit) if math.isfinite(fit) else "∞", why,
               ("" if allow_merge else
                "  A physical conductance could not be built (no geometry), "
                "and the merge is NOT conservative for the winding: it hands "
                "the coil the other node's heat capacity.")))

    G["s_mount"] = max(float(mount_g_w_per_k or 0.0), 0.0)
    dt_rotor = means["rotor"] - t_amb
    G["r_bore"] = bore_w / dt_rotor if abs(dt_rotor) > 1e-6 else 0.0
    G["r_shaft"] = shaft_w / dt_rotor if abs(dt_rotor) > 1e-6 else 0.0
    G["r_bearings"] = bearings_w / dt_rotor if abs(dt_rotor) > 1e-6 else 0.0
    _hp = dict(cooling.get("heat_path") or {})
    if _hp.get("body"):
        # The housing / structure is a NODE of the 2-D solve but not of this
        # network: its path is carried as the map's own watts over the
        # stator's / rotor's ΔT to the room (the housing film, `r_bearings`).
        # Right at the calibration point; its heat capacity is left out, so a
        # pull from cold heats the machine FASTER than the real one would.
        notes.append(
            "the %s (heat_path %r, %.1f °C on the map) is folded into the "
            "fitted conductances to the room — its own heat capacity is not a "
            "node here, so a transient from cold is pessimistic (fast)"
            % (_hp.get("body"), _hp.get("option"),
               float(_hp.get("t_body_c") or t_amb)))
    # THE VENTILATED GAP, one conductance per side, fitted the same way every
    # surface path here is: the WATTS the map removed over the node's own ΔT.
    G["r_gap_flow"] = (gf_rot_w / dt_rotor
                       if (gf_rot_w > 0.0 and abs(dt_rotor) > 1e-6) else 0.0)
    dt_stator = means["stator"] - t_amb
    G["s_gap_flow"] = (gf_sta_w / dt_stator
                       if (gf_sta_w > 0.0 and abs(dt_stator) > 1e-6) else 0.0)
    for _k, _w, _dt, _nd in (("r_gap_flow", gf_rot_w, dt_rotor, "rotor"),
                             ("s_gap_flow", gf_sta_w, dt_stator, "stator")):
        if G[_k] > 0.0:
            links[_k] = {
                "kind": "calibrated", "nodes": [_nd, "ambient"],
                "G_W_per_K": round(float(G[_k]), 6),
                "basis": ("fitted to the calibration map: %.3f W blown out of "
                          "the %s half of the air gap at %.2f m/s through-flow, "
                          "against the %s node at %.2f °C and the %.2f °C room"
                          % (_w, _nd,
                             float(_gapf.get("gap_speed_mps") or 0.0),
                             _nd, means[_nd], t_amb)),
                "dt_K": round(_dt, 3), "P_W": round(_w, 4)}
    if gf_rot_w > 0.0 or gf_sta_w > 0.0:
        notes.append(
            "the air gap is VENTILATED on this map (%.2f m/s through the "
            "clearance): %.2f W leave the rotor half and %.2f W the stator half "
            "straight into the room, so neither is available to cross the gap "
            "and both are carried as their own conductance"
            % (float(_gapf.get("gap_speed_mps") or 0.0), gf_rot_w, gf_sta_w))
    dt_wind = means["winding"] - t_amb
    G["w_open"] = (open_w / dt_wind
                   if (open_w > 0.0 and abs(dt_wind) > 1e-6) else 0.0)
    if G["w_open"] > 0.0:
        links["w_open"] = {
            "kind": "calibrated", "nodes": ["winding", "ambient"],
            "G_W_per_K": round(float(G["w_open"]), 6),
            "basis": ("fitted to the calibration map: %.3f W off the winding "
                      "node at %.2f °C into the %.2f °C room (end turns %.3f W "
                      "+ slot channels %.3f W)"
                      % (open_w, means["winding"], t_amb, ew_w, ch_w)),
            "dt_K": round(dt_wind, 3), "P_W": round(open_w, 4)}

    areas: Dict[str, float] = {"housing": float(outer.get("area_m2") or 0.0)}
    chars: Dict[str, float] = {}
    if side_areas:
        for key in ("winding_ends", "stator_ends", "rotor_ends", "magnet_ends"):
            areas[key] = float(side_areas.get(key) or 0.0)
        chars.update({k: float(v) for k, v in
                      (side_areas.get("char_len_m") or {}).items()})
    d_h = float(d_housing_m if d_housing_m is not None else 0.0)
    if d_h <= 0.0 and areas["housing"] > 0.0 and res.get("stack_m"):
        d_h = areas["housing"] / (math.pi * float(res["stack_m"]))

    hot_off = 0.0
    wblk = comps.get("winding") or {}
    if wblk.get("max") is not None and wblk.get("avg") is not None:
        hot_off = float(wblk["max"]) - float(wblk["avg"])

    housing_G = (housing_w / (means["stator"] - t_amb)
                 if abs(means["stator"] - t_amb) > 1e-6 else None)

    # ── WHAT THE MAP SAYS EACH SURFACE PATH IS WORTH (2026-09-20) ───────────
    # Until today every surface path was re-computed from a NATURAL-convection
    # correlation at solve time — the housing whenever a diameter was known, the
    # four end faces always, and the end faces from a flat provisional
    # `END_FACE_H_PROVISIONAL` at that.  Both were wrong wherever the map was
    # not a still-air one, and wrong by a lot: on this Ø50 joint the 40 m/s
    # housing film is 132 W/m²K against the correlation's 8, and the robotics
    # end-winding film the 2-D solve computes from its own Rayleigh number is
    # 30.8 W/m²K against the provisional 12 — so the network could reject 10 W
    # of the 192 W the map takes off the end faces, and EVERY transient on such
    # a machine ran away (measured 2026-09-20 while building the continuous
    # rating: still air 152 °C in the map, 230 °C and climbing in the network).
    #
    # The fix is the module's own principle applied to the surfaces as well as
    # to the internal links: the LEVEL comes from the map that was solved, and
    # only the SHAPE — how a natural film moves with the wall temperature —
    # comes from a correlation.  A forced film (blown air, a jacket, a typed h)
    # does not move with the wall at all and is held.
    G_fit: Dict[str, float] = {}
    t_fit: Dict[str, float] = {}
    film: Dict[str, str] = {}
    _o_mode = str(outer.get("mode") or "").strip().lower()
    _o_reg = str(outer.get("regime") or "").strip().lower()
    if surface_fit and housing_G is not None and housing_G > 0.0:
        G_fit["housing"] = float(housing_G)
        t_fit["housing"] = float(means["stator"])
        film["housing"] = ("natural" if (_o_mode == "robotics"
                                         or "natural" in _o_reg) else "forced")
    _ef = dict(cooling.get("end_faces") or {}) if surface_fit else {}
    for _node, _key in _SIDE_KEY.items():
        _blk = dict(_ef.get(_node) or {})
        # THE WATTS, not the stated conductance.  The map carries both, and on a
        # robotics map they do not agree: the 2-D solve's end-face sinks remove
        # the watts the heat budget closes on (192.3 W on this Ø50 joint) while
        # the block's own `G_W_per_K` (the closed form re-evaluated at the
        # converged wall) accounts for 11 W of them — a factor of 17, reported
        # to the owner as a finding.  What a lumped network has to reproduce is
        # the map that was SOLVED, and that is the watts; taking the stated G
        # instead would fit the network to a number the temperature field never
        # came from.  The housing fit above has always worked this way.
        _w = _efw.get(_node, 0.0)
        _dt = float(means[_node]) - t_amb
        _g = (_w / _dt) if (_w > 0.0 and abs(_dt) > 1e-6) else 0.0
        if _g <= 0.0:
            continue
        G_fit[_key] = _g
        t_fit[_key] = float(means[_node])
        if abs(_g - float(_blk.get("G_W_per_K") or 0.0)) > 0.1 * _g:
            notes.append(
                "the %s end face is fitted to the %.2f W the map's heat budget "
                "actually removes through it (%.4f W/K over %.2f K), not to the "
                "%.5f W/K the map's own end-face block states — the two "
                "disagree in the payload"
                % (_node, _w, _g, _dt, float(_blk.get("G_W_per_K") or 0.0)))
        # WHICH FILM this face is on comes from the MAP (2026-09-21), not from
        # this module's idea of what an end face is.  The robotics mode's faces
        # are natural convection plus radiation and move with the wall; the open
        # frame's rotor faces (since 2026-09-21) are forced convection in the
        # propeller wash and do not move with it at all, so re-evaluating them
        # against a Rayleigh number would walk a 143 W/m²·K film down to 8.
        # Absent (every map solved before today) reads "natural", which is
        # exactly what those maps' faces were.
        film[_key] = ("forced"
                      if str(_blk.get("film_kind") or "natural").strip().lower()
                      == "forced" else "natural")
        if not float(areas.get(_key) or 0.0) > 0.0:
            areas[_key] = float(_blk.get("area_m2") or 0.0)
        if not float(chars.get(_key) or 0.0) > 0.0:
            chars[_key] = float(_blk.get("char_len_mm") or 0.0) * 1e-3

    if d_h <= 0.0:
        notes.append("no housing diameter given, so the still-air film is not "
                     "re-evaluated with temperature: the calibration map's own "
                     "housing conductance %.4f W/K is held constant."
                     % (housing_G or 0.0))

    net = Network(
        G=G, areas=areas, char_len_m=chars, t_ambient_c=t_amb,
        t_mount_c=t_mount, emissivity=eps, d_housing_m=d_h,
        hot_spot_offset_k=hot_off, node_of=node_of, merged=tuple(merged),
        links=links, housing_G_fixed=housing_G, notes=notes,
        G_fit=G_fit, t_fit_c=t_fit, film_kind=film,
        calibration={
            "duty": calibration_duty,
            "means_c": {n: round(means[n], 2) for n in NODES},
            "winding_max_c": (None if wblk.get("max") is None
                              else float(wblk["max"])),
            "raw_fits_W_per_K": raw_fits,
            "P_cu_W": round(P_cu, 3), "P_mag_W": round(P_mag, 3),
            "gap_W": round(gap_w, 3), "bore_W": round(bore_w, 3),
            "shaft_ends_W": round(shaft_w, 3), "housing_W": round(housing_w, 3),
            "bearings_W": round(bearings_w, 3),
            "end_windings_W": round(ew_w, 3), "slot_channels_W": round(ch_w, 3),
            "gap_flow_rotor_W": round(gf_rot_w, 3),
            "gap_flow_stator_W": round(gf_sta_w, 3),
            "losses_W": float(budget.get("losses_W") or 0.0),
            "housing_G_W_per_K": (None if housing_G is None
                                  else round(housing_G, 5)),
            "surface_G_W_per_K": {k: round(float(v), 6)
                                  for k, v in G_fit.items()},
            "surface_film_kind": dict(film),
            "surface_t_fit_c": {k: round(float(v), 2)
                                for k, v in t_fit.items()},
        })
    # Record which film each still-air path will actually use.
    net.h_sources["housing"] = housing_h_total(
        means["stator"], t_amb, max(d_h, 1e-3), eps, areas["housing"])[1]
    net.h_sources["end_faces"] = end_face_h_total(
        means["winding"], t_amb, eps)[1]
    return net


def _film_h(t_wall_c: float, network: Network, path: str) -> float:
    """The correlation's ``h_total`` for one surface path at a wall temperature.

    The SHAPE, not the level: it is used as a ratio against the same call at the
    calibration temperature, so the coefficients it carries cancel and only how
    strongly a natural film moves with ΔT survives.
    """
    area = float(network.areas.get(path) or 0.0)
    if path == "housing":
        return housing_h_total(t_wall_c, network.t_ambient_c,
                               max(network.d_housing_m, 1e-3),
                               network.emissivity, area)[0]
    return end_face_h_total(t_wall_c, network.t_ambient_c, network.emissivity,
                            float(network.char_len_m.get(path) or 0.0),
                            area)[0]


def still_air_G(t_wall_c: float, network: Network,
                path: str = "housing") -> float:
    """``W/K`` for one SURFACE path at this wall temperature.

    ``path`` is ``"housing"`` or one of the four end faces
    (``winding_ends``, ``stator_ends``, ``rotor_ends``, ``magnet_ends``).

    THE MAP WINS (2026-09-20).  When the calibration map states what this path
    is worth — every map does for the housing, and a robotics map does for each
    end face — that number is the level, because it is the conductance the
    solved temperatures actually came from.  What the correlation is still
    asked for is how a NATURAL film moves with the wall:

        G(T) = G_map · h_still(T) / h_still(T_map)

    so a machine 80 K hotter than its calibration point gets the extra film it
    earns, and a machine cooled by 40 m/s of air or by a jacket keeps the
    coefficient somebody blew or pumped (``film_kind = "forced"`` → held).  The
    old behaviour — a natural-convection correlation re-evaluated from scratch,
    and ``END_FACE_H_PROVISIONAL`` for the end faces — is the fallback for a
    map that states nothing, and nothing else.

    Returns 0 for a path with no conductance and no area — "this machine has no
    exposed magnet end face" is an answer.
    """
    fit = network.G_fit.get(path)
    if fit is not None and float(fit) > 0.0:
        if str(network.film_kind.get(path) or "natural") == "forced":
            return float(fit)
        t0 = float(network.t_fit_c.get(path, t_wall_c))
        h0 = _film_h(t0, network, path)
        if not (h0 > 1e-12):
            return float(fit)
        return float(fit) * _film_h(t_wall_c, network, path) / h0
    area = float(network.areas.get(path) or 0.0)
    if area <= 0.0:
        return 0.0
    if path == "housing":
        if network.d_housing_m <= 0.0:
            return float(network.housing_G_fixed or 0.0)
        h, _src, _d = housing_h_total(t_wall_c, network.t_ambient_c,
                                      network.d_housing_m, network.emissivity,
                                      area)
        return h * area
    h, _src, _d = end_face_h_total(t_wall_c, network.t_ambient_c,
                                   network.emissivity,
                                   float(network.char_len_m.get(path) or 0.0),
                                   area)
    return h * area


# ---------------------------------------------------------------------------
# The integrator
# ---------------------------------------------------------------------------

@dataclass
class Trace:
    """What the integration produced: time, temperatures, powers, flows."""
    t_s: List[float]
    T_c: Dict[str, List[float]]
    P_W: Dict[str, List[float]]
    flows_W: Dict[str, List[float]]
    hot_spot_c: List[float]
    energy_in_J: float = 0.0
    energy_out_J: float = 0.0
    stored_J: float = 0.0
    segments: Tuple[Tuple[float, float, Optional[str]], ...] = ()

    def peak(self, node: str) -> float:
        return max(self.T_c[node])

    @property
    def closure_pct(self) -> float:
        """|E_in − E_out − ΔU| / E_in — the first law, as a number."""
        if self.energy_in_J <= 0.0:
            return 0.0
        return abs(self.energy_in_J - self.energy_out_J
                   - self.stored_J) / self.energy_in_J * 100.0

    def final_state(self) -> Dict[str, float]:
        return {n: self.T_c[n][-1] for n in self.T_c}


def _seg_powers(seg: Segment, T: Mapping[str, float],
                network: Network) -> Dict[str, float]:
    """Losses at the CURRENT temperatures — only copper moves (stated)."""
    out = {n: float(seg.losses.get(n, 0.0)) for n in NODES}
    if out["winding"] and seg.copper_feedback:
        tw = float(T[network.rep("winding")])
        out["winding"] *= (cu_rho_ratio(tw) / cu_rho_ratio(seg.coil_ref_c))
    return out


def _flows(T: Mapping[str, float], network: Network) -> Dict[str, float]:
    """Every heat flow OUT of the machine plus the internal ones, in W.

    Keys: ``housing``, ``mount``, ``bore``, ``shaft_ends``, the four
    ``*_ends`` side faces, the open frame's ``winding_open`` and (since
    2026-09-21) ``rotor_gap_flow`` / ``stator_gap_flow``, and the internal
    ``w_s``/``r_s``/``m_r`` (positive = from the hotter node to the colder one,
    in the order of :data:`_PAIRS`).
    """
    amb, mnt = network.t_ambient_c, network.t_mount_c
    ts = float(T[network.rep("stator")])
    tr = float(T[network.rep("rotor")])
    tw = float(T[network.rep("winding")])
    out: Dict[str, float] = {
        "housing": still_air_G(ts, network, "housing") * (ts - amb),
        "mount": float(network.G.get("s_mount") or 0.0) * (ts - mnt),
        "bore": float(network.G.get("r_bore") or 0.0) * (tr - amb),
        "shaft_ends": float(network.G.get("r_shaft") or 0.0) * (tr - amb),
        "bearings": float(network.G.get("r_bearings") or 0.0) * (tr - amb),
        # THE OPEN FRAME (2026-09-20): the end turns in the airflow and the
        # ventilated slot channels.  Zero on every housed machine, and on this
        # Ø50 open-frame joint 207 of the 262 W the machine makes — see
        # ``network_from_steady``.  A FIXED conductance and not a still-air
        # film: it is forced convection at a stated air speed, which does not
        # move with the wall temperature the way natural convection does.
        "winding_open": float(network.G.get("w_open") or 0.0) * (tw - amb),
        # THE VENTILATED GAP (2026-09-21), one per side and both FIXED for the
        # same reason `winding_open` is: a blown film does not move with the
        # wall the way natural convection does.
        "rotor_gap_flow": float(network.G.get("r_gap_flow") or 0.0) * (tr - amb),
        "stator_gap_flow": float(network.G.get("s_gap_flow") or 0.0) * (ts - amb),
    }
    for node, key in _SIDE_KEY.items():
        tn = float(T[network.rep(node)])
        out[key] = still_air_G(tn, network, key) * (tn - amb)
    for a, b, key in _PAIRS:
        ra, rb = network.rep(a), network.rep(b)
        out[key] = (0.0 if ra == rb
                    else float(network.G.get(key) or 0.0) * (T[ra] - T[rb]))
    return out


#: Which node each external flow leaves FROM.
_FLOW_NODE: Dict[str, str] = {
    "housing": "stator", "mount": "stator", "bore": "rotor",
    "shaft_ends": "rotor", "bearings": "rotor", "winding_ends": "winding",
    "stator_ends": "stator", "rotor_ends": "rotor", "magnet_ends": "magnet",
    "winding_open": "winding",
    "rotor_gap_flow": "rotor", "stator_gap_flow": "stator",
}
EXTERNAL_FLOWS: Tuple[str, ...] = tuple(_FLOW_NODE)
#: Which side of the machine each external path belongs to (for the split).
STATOR_SIDE_FLOWS = ("housing", "mount", "winding_ends", "stator_ends",
                     "winding_open", "stator_gap_flow")
ROTOR_SIDE_FLOWS = ("bore", "shaft_ends", "bearings", "rotor_ends",
                    "magnet_ends",
                    "rotor_gap_flow")


def _derivatives(seg: Segment, T: Mapping[str, float], network: Network,
                 caps: Mapping[str, float]) -> Tuple[Dict[str, float],
                                                     Dict[str, float],
                                                     Dict[str, float]]:
    """``(dT/dt per active node, powers per node, flows)``."""
    P = _seg_powers(seg, T, network)
    F = _flows(T, network)
    net_w: Dict[str, float] = {r: 0.0 for r in network.active_nodes}
    for n in NODES:
        net_w[network.rep(n)] += P[n]
    for key, node in _FLOW_NODE.items():
        net_w[network.rep(node)] -= F[key]
    for a, b, key in _PAIRS:
        ra, rb = network.rep(a), network.rep(b)
        if ra != rb:
            net_w[ra] -= F[key]
            net_w[rb] += F[key]
    return ({r: net_w[r] / max(float(caps[r]), 1e-9)
             for r in network.active_nodes}, P, F)


def merged_capacities(caps: Mapping[str, Any],
                      network: Network) -> Dict[str, float]:
    """``{active node: ΣC}`` — capacities summed over merged nodes.

    Accepts either the ``part_capacities`` mapping or a plain ``{node: C}``.
    """
    out = {r: 0.0 for r in network.active_nodes}
    for n in NODES:
        blk = caps.get(n)
        c = float(blk["C_J_per_K"] if isinstance(blk, Mapping) else (blk or 0.0))
        out[network.rep(n)] += c
    for r, c in out.items():
        if c <= 0.0:
            raise DutyCycleError(
                "duty_cycle_no_capacity",
                "node %r has no heat capacity, so its transient would be "
                "instantaneous." % r)
    return out


def integrate_profile(profile: Profile, network: Network,
                      caps: Mapping[str, Any],
                      T0: Optional[Mapping[str, float]] = None,
                      *, n_cycles: int = 1, samples_per_segment: int = 60,
                      stop_above_c: Optional[float] = None) -> Trace:
    """Integrate ``n_cycles`` of the profile from ``T0``.

    ``T0`` may be ``{node: °C}`` (missing nodes take the profile's start
    temperature, else ambient) or ``None``.  Each segment is integrated
    SEPARATELY — LSODA restarted at every boundary — because the loss step at a
    segment boundary is a discontinuity no adaptive integrator should be asked
    to step over.

    ``stop_above_c`` aborts as soon as any node passes it; used by the runaway
    guard, not by the physics.
    """
    C = merged_capacities(caps, network)
    active = network.active_nodes
    t_start = (profile.t_start_c if profile.t_start_c is not None
               else network.t_ambient_c)
    state = {r: float(t_start) for r in active}
    if T0:
        for n, v in T0.items():
            r = network.rep(n)
            if r in state:
                state[r] = float(v)

    t_all: List[float] = []
    T_all: Dict[str, List[float]] = {r: [] for r in active}
    P_all: Dict[str, List[float]] = {n: [] for n in NODES}
    F_all: Dict[str, List[float]] = {}
    seg_marks: List[Tuple[float, float, Optional[str]]] = []
    t_now = 0.0
    u0 = sum(C[r] * state[r] for r in active)

    for _cycle in range(max(int(n_cycles), 1)):
        for seg in profile.segments:
            t_end = t_now + seg.t_s
            ts, ys = _solve_segment(seg, network, C, active, state, seg.t_s,
                                    samples_per_segment)
            for i, tt in enumerate(ts):
                # The boundary point is written TWICE — once with the segment
                # that ends there and once with the one that starts — because
                # the loss step is a real discontinuity.  Collapsing the two
                # makes the trapezoid ramp the power across one sample interval
                # and invents energy: on a 15 s pull sampled every 0.25 s that
                # is 84 J per boundary, i.e. ~3 W of a 150 W cycle average, and
                # it shows up as a heat budget that does not close.
                t_all.append(t_now + tt)
                st = {r: ys[j][i] for j, r in enumerate(active)}
                for r in active:
                    T_all[r].append(st[r])
                _d, P, F = _derivatives(seg, st, network, C)
                for n in NODES:
                    P_all[n].append(P[n])
                for k, v in F.items():
                    F_all.setdefault(k, []).append(v)
            state = {r: ys[j][-1] for j, r in enumerate(active)}
            seg_marks.append((t_now, t_end, seg.name))
            t_now = t_end
            if stop_above_c is not None and max(state.values()) > stop_above_c:
                break
        else:
            continue
        break

    # Energy bookkeeping — the first law, as a number.
    e_in = _trapz(t_all, [sum(P_all[n][i] for n in NODES)
                          for i in range(len(t_all))])
    e_out = _trapz(t_all, [sum(F_all[k][i] for k in EXTERNAL_FLOWS if k in F_all)
                           for i in range(len(t_all))])
    u1 = sum(C[r] * T_all[r][-1] for r in active)

    # Expand merged nodes back out so a caller always sees four series.
    T_out = {n: T_all[network.rep(n)] for n in NODES}
    hot = [v + network.hot_spot_offset_k for v in T_out["winding"]]
    return Trace(t_s=t_all, T_c=T_out, P_W=P_all, flows_W=F_all, hot_spot_c=hot,
                 energy_in_J=e_in, energy_out_J=e_out, stored_J=(u1 - u0),
                 segments=tuple(seg_marks))


def _solve_segment(seg: Segment, network: Network, C: Mapping[str, float],
                   active: Sequence[str], state: Mapping[str, float],
                   t_s: float, samples: int):
    """One segment, LSODA, restarted at the boundary."""
    from scipy.integrate import solve_ivp
    import numpy as _np

    def rhs(_t, y):
        st = {r: float(y[i]) for i, r in enumerate(active)}
        d, _P, _F = _derivatives(seg, st, network, C)
        return [d[r] for r in active]

    n = max(int(samples), 2)
    t_eval = _np.linspace(0.0, float(t_s), n)
    sol = solve_ivp(rhs, (0.0, float(t_s)), [state[r] for r in active],
                    method="LSODA", t_eval=t_eval, rtol=1e-8, atol=1e-8)
    if not sol.success:
        raise DutyCycleError("duty_cycle_integration_failed",
                             "the transient integration failed: %s" % sol.message)
    # Plain floats, not numpy scalars: these land in a JSON record and in a
    # report, and `np.float64(185.77)` is not a number a reader should meet.
    return [float(v) for v in sol.t], [[float(v) for v in row] for row in sol.y]


def _trapz(t: Sequence[float], y: Sequence[float]) -> float:
    return float(sum(0.5 * (y[i] + y[i - 1]) * (t[i] - t[i - 1])
                     for i in range(1, len(t))))


def steady_state(segment: Segment, network: Network, caps: Mapping[str, Any],
                 *, T0: Optional[Mapping[str, float]] = None,
                 tol_k: float = 1e-3, max_chunks: int = 40,
                 ) -> Dict[str, float]:
    """Where this operating point SETTLES — the network's own S1 answer.

    Integrated rather than solved algebraically: the housing and end-face films
    depend on the wall temperature, so the steady state is a nonlinear system
    and marching to it is both shorter and safer than a Newton solve nobody
    would trust at first sight.  Chunks of ~2τ until nothing moves by ``tol_k``.
    """
    C = merged_capacities(caps, network)
    tau = _time_constant(network, C)
    state = dict(T0 or {})
    prof = Profile("S1", (replace(segment, t_s=max(2.0 * tau, 1.0)),),
                   cycle_s=max(2.0 * tau, 1.0),
                   t_start_c=(network.t_ambient_c if not state else None))
    last: Optional[Dict[str, float]] = None
    for _ in range(max(int(max_chunks), 2)):
        tr = integrate_profile(prof, network, caps, state or None, n_cycles=1,
                               samples_per_segment=12)
        end = tr.final_state()
        if last is not None and max(abs(end[n] - last[n]) for n in NODES) < tol_k:
            return {n: float(end[n]) for n in NODES}
        last = dict(end)
        state = dict(end)
    return {n: float(last[n]) for n in NODES}      # type: ignore[index]


# ---------------------------------------------------------------------------
# The periodic state
# ---------------------------------------------------------------------------

def periodic_steady_state(profile: Profile, network: Network,
                          caps: Mapping[str, Any], *,
                          T0: Optional[Mapping[str, float]] = None,
                          tol_k: float = PERIODIC_TOL_K,
                          n_cycles_max: Optional[int] = None,
                          samples_per_segment: int = 60,
                          aitken: bool = True) -> Dict[str, Any]:
    """Iterate the CYCLE MAP until the cycle repeats itself.

    One cycle is a map from the start state to the end state; the periodic
    state is its fixed point.  Aitken Δ² accelerates it (the map is close to
    linear once the fastest node has settled, so three iterates give the limit
    to within a fraction of a kelvin), and the acceleration is verified by one
    more real cycle rather than trusted.

    Refuses ``duty_cycle_no_periodic_state`` when the temperature runs away or
    the cycle cap is reached while still rising: an answer that says "185 °C"
    for a machine that is heading for 600 °C is the worst possible output.
    """
    if not profile.is_periodic and profile.kind != "S1":
        raise DutyCycleError(
            "duty_cycle_not_periodic",
            "kind %s has no periodic state (it runs once)." % profile.kind,
            "Use time_to_limit() for an S2 pull.")
    n_max = int(n_cycles_max or profile.n_cycles_max or 200)
    t_start = (profile.t_start_c if profile.t_start_c is not None
               else network.t_ambient_c)
    state = dict(T0 or {n: float(t_start) for n in NODES})
    history: List[Dict[str, float]] = []
    residual = float("inf")
    peaks: List[float] = []
    n_done = 0

    for i in range(n_max):
        tr = integrate_profile(profile, network, caps, state, n_cycles=1,
                               samples_per_segment=samples_per_segment,
                               stop_above_c=RUNAWAY_C)
        end = tr.final_state()
        n_done = i + 1
        peak = max(tr.hot_spot_c)
        peaks.append(peak)
        if max(max(v) for v in tr.T_c.values()) > RUNAWAY_C:
            raise DutyCycleError(
                "duty_cycle_no_periodic_state",
                "this cycle has no periodic state: the machine passes %.0f °C "
                "on cycle %d and is still rising." % (RUNAWAY_C, n_done),
                "Lower the ED, shorten t_on, or cool the machine harder — a "
                "robotics heat path (housing / shaft) or a mount conductance.")
        residual = max(abs(end[n] - state[n]) for n in NODES)
        history.append(dict(end))
        state = dict(end)
        if residual < float(tol_k):
            break
        if aitken and len(history) >= 3:
            # The coldest thing this machine touches: an accelerated state below
            # it is an extrapolation and not a temperature (see `_aitken`).
            acc = _aitken(history[-3], history[-2], history[-1],
                          floor_c=min(float(network.t_ambient_c),
                                      float(network.t_mount_c)) - 1.0)
            if acc is not None:
                state = acc
    else:
        rising = len(peaks) >= 2 and peaks[-1] - peaks[-2] > float(tol_k)
        if rising:
            raise DutyCycleError(
                "duty_cycle_no_periodic_state",
                "the cycle had not repeated itself after %d cycles and the "
                "peak is still rising by %.2f K per cycle."
                % (n_max, peaks[-1] - peaks[-2]),
                "Lower the ED, shorten t_on, cool the machine harder (a "
                "robotics heat path or a mount conductance), or raise "
                "n_cycles_max if the machine is merely slow.")

    # One last cycle FROM the converged state — the series that is reported is
    # a real cycle, never an extrapolated one.
    last = integrate_profile(profile, network, caps, state, n_cycles=1,
                             samples_per_segment=samples_per_segment)
    return {
        "converged": bool(residual < float(tol_k)),
        "n_cycles": n_done,
        "residual_K": round(float(residual), 4),
        "start_state_c": {n: round(float(state[n]), 3) for n in NODES},
        "peak_c": {n: round(max(last.T_c[n]), 2) for n in NODES},
        "min_c": {n: round(min(last.T_c[n]), 2) for n in NODES},
        "mean_c": {n: round(sum(last.T_c[n]) / len(last.T_c[n]), 2)
                   for n in NODES},
        "winding_hot_peak_c": round(max(last.hot_spot_c), 2),
        "winding_hot_mean_c": round(sum(last.hot_spot_c)
                                    / len(last.hot_spot_c), 2),
        "closure_pct": round(last.closure_pct, 4),
        "trace": last,
    }


def _aitken(a: Mapping[str, float], b: Mapping[str, float],
            c: Mapping[str, float], floor_c: Optional[float] = None
            ) -> Optional[Dict[str, float]]:
    """Aitken Δ² on the cycle map, per node — ``None`` if it is not usable.

    BOUNDED AT BOTH ENDS (2026-09-21).  The upper guard has always been here;
    the lower one was missing, and an acceleration is not a physical step: on a
    node whose two increments nearly cancel, ``d2²/den`` can throw the state
    hundreds of degrees the wrong way.  Measured while turning ``surface_fit``
    on — a 4 % change in one conductance was enough to tip the 40 mm S3 search
    into it — the rotor was extrapolated to **−2822 °C**, and the next cycle was
    then integrated through air properties evaluated below absolute zero, where
    LSODA cuts its step down and never comes back: one ``allowable_ed`` call
    that costs 14 s stopped finishing at all.

    A cycle map's fixed point cannot be colder than the coldest sink the machine
    touches, so ``floor_c`` (the ambient / mount, whichever is lower) is the
    bound, with absolute zero as the backstop when no floor is given.  Rejecting
    the step costs one ordinary iteration and nothing else — the plain iteration
    converges to the same fixed point, only slower.
    """
    lo = (float(floor_c) if floor_c is not None else -273.15)
    out: Dict[str, float] = {}
    for n in NODES:
        d1 = b[n] - a[n]
        d2 = c[n] - b[n]
        den = d2 - d1
        if abs(den) < 1e-12 or abs(d2) > abs(d1):      # diverging: don't push
            return None
        out[n] = c[n] - d2 * d2 / den
        if not math.isfinite(out[n]) or out[n] > RUNAWAY_C or out[n] < lo:
            return None
    return out


# ---------------------------------------------------------------------------
# S2 — how long may it pull?
# ---------------------------------------------------------------------------

def default_limits(magnet_limit_c: Optional[float] = None) -> Dict[str, float]:
    """The limits a duty cycle is judged against.

    The winding is the PROJECT's class — 200 °C, ``report.PROJECT_INSULATION_C``
    (user 2026-09-14: 200 everywhere, never 180) — and it is judged on the HOT
    SPOT, not the mean.  The magnet limit is the card's, so it is passed in.
    """
    try:
        from motor_ai_sim.report import PROJECT_INSULATION_C as _lim
    except Exception:                                    # noqa: BLE001
        _lim = PROJECT_INSULATION_C
    out = {"winding": float(_lim)}
    if magnet_limit_c is not None:
        out["magnet"] = float(magnet_limit_c)
    return out


def _runaway_stop_c(limits) -> float:
    """Where a step response is stopped as a RUNAWAY: ``RUNAWAY_C``, or 100 K
    past the highest limit being judged if that is higher — so the stop can
    never fire before a part it is asked about has had its chance to cross."""
    vals = [float(v) for v in limits]
    return max([RUNAWAY_C] + [v + 100.0 for v in vals])


def time_to_limit(profile: Profile, network: Network, caps: Mapping[str, Any],
                  *, limits: Optional[Mapping[str, float]] = None,
                  T0: Optional[Mapping[str, float]] = None,
                  t_max_s: Optional[float] = None,
                  ) -> Dict[str, Any]:
    """How long the first segment may run before a part reaches its limit.

    The S2 question.  The winding is judged on its HOT SPOT (mean + the
    calibration map's constant offset); every other node on its own mean.
    ``None`` with ``"this point is S1"`` when nothing is reached — the machine
    settles below every limit and may run for ever.
    """
    from scipy.integrate import solve_ivp

    lims = dict(limits or default_limits())
    C = merged_capacities(caps, network)
    active = network.active_nodes
    seg = profile.segments[0]
    t_start = (profile.t_start_c if profile.t_start_c is not None
               else network.t_ambient_c)
    state = {r: float(t_start) for r in active}
    for n, v in (T0 or {}).items():
        if network.rep(n) in state:
            state[network.rep(n)] = float(v)
    t_end = float(t_max_s if t_max_s is not None
                  else max(seg.t_s, 10.0 * _time_constant(network, C)))

    def rhs(_t, y):
        st = {r: float(y[i]) for i, r in enumerate(active)}
        d, _P, _F = _derivatives(seg, st, network, C)
        return [d[r] for r in active]

    events = []
    names: List[str] = []
    for node, lim in lims.items():
        off = network.hot_spot_offset_k if node == "winding" else 0.0
        idx = active.index(network.rep(node))

        def _ev(_t, y, _i=idx, _l=float(lim), _o=off):
            return y[_i] + _o - _l
        _ev.terminal = False
        _ev.direction = 1.0
        events.append(_ev)
        names.append(node)
    # THE RUNAWAY STOP (2026-09-26), the same RUNAWAY_C the periodic solver
    # refuses at.  With the copper feedback on, a weakly cooled machine at a
    # hot point makes more watts per kelvin than it sheds (the L13 at its
    # 686 W peak behind a still-air housing: ~1.6 W/K of copper against
    # ~1.2 W/K to the room), so there is no equilibrium below ~800 °C — and
    # LSODA stalls there for good (measured: > 500 000 evaluations without
    # advancing).  Every limit is far below 400 °C, so the answer this
    # function exists for is already in hand when a node passes it; the
    # integration ENDS there and the result says so.  Nothing is clamped.
    _stop_c = _runaway_stop_c(lims.values())

    def _runaway(_t, y):
        return max(float(v) for v in y) - _stop_c
    _runaway.terminal = True
    _runaway.direction = 1.0

    sol = solve_ivp(rhs, (0.0, t_end), [state[r] for r in active],
                    method="LSODA", rtol=1e-9, atol=1e-9, events=events + [_runaway],
                    dense_output=True, max_step=max(t_end / 200.0, 1e-6))
    if not sol.success:
        raise DutyCycleError("duty_cycle_integration_failed",
                             "the S2 integration failed: %s" % sol.message)
    runaway_t = (float(sol.t_events[-1][0]) if len(sol.t_events[-1])
                 else None)
    hits = [(float(te[0]), names[i]) for i, te in
            enumerate(sol.t_events[:-1]) if len(te)]
    end_state = {r: float(sol.y[i][-1]) for i, r in enumerate(active)}
    out = {
        "s2_time_to_limit_s": None,
        "s2_limiting_part": None,
        "t_horizon_s": round(t_end, 3),
        # When the runaway stop fired: the end state is the machine at that
        # instant, not a settled one.
        "runaway_at_s": (None if runaway_t is None else round(runaway_t, 3)),
        "end_state_c": {n: round(end_state[network.rep(n)], 2) for n in NODES},
        "winding_hot_end_c": round(end_state[network.rep("winding")]
                                   + network.hot_spot_offset_k, 2),
        "limits_c": {k: float(v) for k, v in lims.items()},
        "note": "",
    }
    if hits:
        t_hit, part = min(hits)
        out["s2_time_to_limit_s"] = round(t_hit, 3)
        out["s2_limiting_part"] = part
        out["note"] = ("%s reaches %.0f °C after %.1f s from %.0f °C"
                       % (part, lims[part], t_hit, t_start))
        if runaway_t is not None:
            out["note"] += ("; the machine has no equilibrium at this point — "
                            "it passes %.0f °C after %.1f s and the "
                            "integration stops there" % (_stop_c, runaway_t))
    else:
        out["note"] = ("no part reaches its limit within %.0f s — at this "
                       "operating point the machine settles below every limit, "
                       "i.e. this point is S1 (continuous)." % t_end)
    return out


#: One thing that may be judged against a limit: a LABEL, the network node whose
#: transient it rides, the limit in °C, and the CONSTANT offset from that node's
#: temperature to the quantity being judged (the winding hot spot is the winding
#: node plus the calibration map's own max − mean; the bearing seat is the rotor
#: node plus the map's own seat − rotor mean).  A label rather than a node key
#: because two parts may ride ONE node — the seat and the rotor iron do — and a
#: dict keyed by node could not carry both.
Target = Tuple[str, str, float, float]      # (label, node, limit_c, offset_k)


def time_to_limits(segment: Segment, network: Network, caps: Mapping[str, Any],
                   targets: Sequence[Target], *,
                   T0: Optional[Mapping[str, float]] = None,
                   t_max_s: Optional[float] = None,
                   ) -> Dict[str, Any]:
    """The STEP RESPONSE of this operating point, and when each target is hit.

    :func:`time_to_limit` answers the S2 question for the two nodes a duty cycle
    judges; this one answers it for an arbitrary list of ``(label, node, limit,
    offset)`` targets, which is what the coupled loop needs — it judges the
    winding HOT SPOT, the hottest MAGNET element and the BEARING SEAT, and the
    last two are a node-plus-offset rather than a node.

    The machine is switched on at ``T0`` (missing nodes take the profile's start
    temperature, i.e. ambient) and held at this point for ever; each target
    reports the first time its own quantity crosses its limit, or says that it
    never does.  ``reaches: False`` with an ``asymptote_c`` below the limit is a
    real answer and not a failure — it means the step response of THIS network
    settles under the limit, so a map that is over it is over it for a reason the
    four-node network does not represent, and inventing a number would be worse
    than saying so.

    Returns ``{"targets": {label: {...}}, "t_horizon_s", "end_state_c",
    "settled": bool}``.
    """
    from scipy.integrate import solve_ivp

    C = merged_capacities(caps, network)
    active = network.active_nodes
    state = {r: float(network.t_ambient_c) for r in active}
    for n, v in (T0 or {}).items():
        if network.rep(n) in state:
            state[network.rep(n)] = float(v)
    t_end = float(t_max_s if t_max_s is not None
                  else 10.0 * _time_constant(network, C))
    t_end = max(t_end, 1e-3)

    def rhs(_t, y):
        st = {r: float(y[i]) for i, r in enumerate(active)}
        d, _P, _F = _derivatives(segment, st, network, C)
        return [d[r] for r in active]

    events = []
    labels: List[str] = []
    for label, node, lim, off in targets:
        idx = active.index(network.rep(node))

        def _ev(_t, y, _i=idx, _l=float(lim), _o=float(off)):
            return y[_i] + _o - _l
        _ev.terminal = False
        _ev.direction = 1.0
        events.append(_ev)
        labels.append(str(label))
    # THE RUNAWAY STOP (2026-09-26) — see ``time_to_limit``: a machine with
    # no equilibrium below ~800 °C stalls LSODA for good.  Here EVERY target's
    # own crossing time is reported, so the stop fires only once every target
    # is past its limit AND a node is past the stop — no crossing is lost.
    _stop_c = _runaway_stop_c(t[2] for t in targets)
    _tg_idx = [(active.index(network.rep(node)), float(lim), float(off))
               for _l, node, lim, off in targets]

    def _runaway(_t, y):
        margin = min((float(y[i]) + o - l for i, l, o in _tg_idx),
                     default=0.0)
        return min(margin, max(float(v) for v in y) - _stop_c)
    _runaway.terminal = True
    _runaway.direction = 1.0

    sol = solve_ivp(rhs, (0.0, t_end), [state[r] for r in active],
                    method="LSODA", rtol=1e-9, atol=1e-9,
                    events=events + [_runaway],
                    dense_output=False, max_step=max(t_end / 200.0, 1e-6))
    runaway_t = (float(sol.t_events[-1][0]) if len(sol.t_events[-1])
                 else None)
    # NOTE ON `y_events`: SciPy hands back the WHOLE state vector at each event,
    # which is what makes "the machine AT the limit" a state and not a single
    # temperature — every other node is read off the same instant of the same
    # trajectory rather than interpolated afterwards.
    if not sol.success:
        raise DutyCycleError(
            "duty_cycle_integration_failed",
            "the step response could not be integrated: %s" % sol.message)

    end_state = {r: float(sol.y[i][-1]) for i, r in enumerate(active)}
    # SETTLED?  The last derivative, in kelvin per hour — under a kelvin an hour
    # nothing is going to move again, and an asymptote may be quoted.
    d_end, _P, _F = _derivatives(segment, end_state, network, C)
    drift_k_per_h = max(abs(v) for v in d_end.values()) * 3600.0

    out: Dict[str, Any] = {
        "t_horizon_s": round(t_end, 3),
        # When the runaway stop fired the end state is that instant, and the
        # machine is (by construction) NOT settled.
        "runaway_at_s": (None if runaway_t is None else round(runaway_t, 3)),
        "end_state_c": {n: round(end_state[network.rep(n)], 2) for n in NODES},
        "settled": bool(drift_k_per_h < 1.0 and runaway_t is None),
        "drift_K_per_h": round(drift_k_per_h, 4),
        "start_state_c": {n: round(state[network.rep(n)], 2) for n in NODES},
        "targets": {},
    }
    for i, (label, node, lim, off) in enumerate(targets):
        te = sol.t_events[i]
        end_c = end_state[network.rep(node)] + float(off)
        blk: Dict[str, Any] = {
            "node": str(node), "limit_c": float(lim),
            "offset_K": round(float(off), 3),
            "end_c": round(end_c, 2),
        }
        if len(te):
            blk["reaches"] = True
            blk["time_s"] = round(float(te[0]), 3)
            # …AND THE STATE THE MACHINE IS IN AT THAT INSTANT (owner
            # 2026-09-18).  The time alone answers "how long"; the answer the
            # owner asked for is the MACHINE at that moment, so every node is
            # read off the same crossing — this part at its limit by
            # construction, the others wherever the trajectory has put them.
            ye = sol.y_events[i]
            if ye is not None and len(ye):
                blk["state_c"] = {
                    n: round(float(ye[0][active.index(network.rep(n))]), 2)
                    for n in NODES}
        else:
            blk["reaches"] = False
            blk["time_s"] = None
            blk["asymptote_c"] = (round(end_c, 2) if out["settled"] else None)
        out["targets"][str(label)] = blk
    return out


def _time_constant(network: Network, C: Mapping[str, float]) -> float:
    """A crude ΣC/ΣG — only used to pick an integration horizon."""
    g = (float(network.G.get("s_mount") or 0.0)
         + float(network.G.get("r_bore") or 0.0)
         + float(network.G.get("r_shaft") or 0.0)
         + float(network.G.get("r_bearings") or 0.0)
         + still_air_G(network.t_ambient_c + 60.0, network, "housing")
         + sum(still_air_G(network.t_ambient_c + 60.0, network, k)
               for k in _SIDE_KEY.values()))
    return float(sum(C.values())) / max(g, 1e-6)


# ---------------------------------------------------------------------------
# Allowable ED
# ---------------------------------------------------------------------------

def _at_point(rec: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The node temperatures of one periodic record, as a panel reads them.

    The answer to "and how hot is it THERE?" — the question that follows every
    found regime.  ``winding_hot_peak_c`` and ``magnet_peak_c`` are spelled out
    beside the per-node blocks because they are the two numbers the headline
    line prints and a report quotes.
    """
    if not rec:
        return None
    peak = dict(rec.get("peak_c") or {})
    mean = dict(rec.get("mean_c") or {})
    return {
        "winding_hot_peak_c": rec.get("winding_hot_peak_c"),
        "winding_hot_mean_c": rec.get("winding_hot_mean_c"),
        "magnet_peak_c": peak.get("magnet"),
        "peak_c": {n: peak.get(n) for n in NODES},
        "mean_c": {n: mean.get(n) for n in NODES},
    }


def allowable_ed(profile: Profile, network: Network, caps: Mapping[str, Any],
                 *, limits: Optional[Mapping[str, float]] = None,
                 iters: int = 12, curve_step_pct: float = 5.0,
                 samples_per_segment: int = 40,
                 with_curve: bool = True,
                 warm_start: bool = False) -> Dict[str, Any]:
    """The highest ED whose PERIODIC peak still respects the limits.

    THE ANSWER THIS TOOL EXISTS FOR (user 2026-09-15): the duty cycle does not
    grade a duty ratio somebody typed, it FINDS the one the machine can hold.
    Bisection on the duty ratio (12 steps = 0.02 % on a 0-100 % bracket), with
    the seed from the steady balance ``ED ≈ (G_tot·(T_lim−T_∞) − P_rest) /
    (P_peak − P_rest)`` so the first bracket is already near the answer.  The
    ``ed_curve`` is the same evaluation on a coarse grid, which is what a panel
    plots and what makes the single number checkable; ``with_curve=False``
    skips it, which is what :func:`ed_vs_cycle` wants (it is walking the
    allowable ED over PERIODS and every inner curve would be paid for five
    times over).

    ``at_allowable`` is every node's temperature AT the found ED — the machine
    the answer describes, not the one that was asked about.  It is ``None``
    only when no ED is feasible.

    ``warm_start`` (2026-09-16, for the coupled loop) starts each cycle-map
    iteration from the START STATE of the last evaluation that converged instead
    of from ambient.  The fixed point is the same one — a cycle map is a
    contraction here and the tolerance that stops it is 0.05 K either way — but
    the PATH to it is shorter, and on a period far below the machine's time
    constant that path is most of the cost (a 10 s cycle needs ~60 cycles from
    cold and a handful from the neighbouring ED).  OFF by default, so the
    Thermal tab's answer is the one it has always been; the coupled loop, which
    runs this search inside every pass, asks for it.  A seeded solve that comes
    back "no periodic state" is retried COLD before it is believed — the same
    rule the sweep applies to a seeded Newton failure, and for the same reason:
    a seed is an accelerator and must never be able to invent a refusal.
    """
    if profile.kind != "S3":
        raise DutyCycleError(
            "duty_cycle_not_s3",
            "an allowable ED only means something for an S3 cycle.",
            "Set kind: S3 with ed_pct and cycle_s.")
    lims = dict(limits or default_limits())
    #: the ED the request ASKED about — ``None`` when it asked for none
    asked = profile.ed_pct if profile.ed_given else None

    seed: Dict[str, float] = {}

    def _periodic(ed: float) -> Dict[str, Any]:
        if not (warm_start and seed):
            return periodic_steady_state(profile.with_ed(ed), network, caps,
                                         samples_per_segment=samples_per_segment)
        try:
            rec = periodic_steady_state(profile.with_ed(ed), network, caps,
                                        T0=dict(seed),
                                        samples_per_segment=samples_per_segment)
        except DutyCycleError as exc:
            if exc.code != "duty_cycle_no_periodic_state":
                raise
            # COLD RETRY: the seed is an accelerator, never a verdict.
            rec = periodic_steady_state(profile.with_ed(ed), network, caps,
                                        samples_per_segment=samples_per_segment)
        seed.update({k: float(v) for k, v in (rec.get("start_state_c")
                                              or {}).items()})
        return rec

    def _peak(ed: float) -> Tuple[float, str, Dict[str, Any]]:
        """(worst margin K, part, the record) at this ED — >0 is over."""
        rec = _periodic(ed)
        worst, who = -1e9, ""
        for node, lim in lims.items():
            t = (rec["winding_hot_peak_c"] if node == "winding"
                 else rec["peak_c"][node])
            if t - float(lim) > worst:
                worst, who = t - float(lim), node
        return worst, who, rec

    lo, hi = 0.5, 100.0
    curve: List[List[float]] = []
    if with_curve:
        step = max(float(curve_step_pct), 1.0)
        ed = step
        while ed <= 100.0 + 1e-9:
            try:
                rec = _periodic(ed)
                curve.append([round(ed, 2), round(rec["winding_hot_peak_c"], 2)])
            except DutyCycleError as exc:
                if exc.code != "duty_cycle_no_periodic_state":
                    raise
                curve.append([round(ed, 2), None])     # type: ignore[list-item]
            ed += step

    lo_margin, _who, lo_rec = _peak(lo)
    if lo_margin > 0.0:
        return {"ed_allowable_pct": 0.0, "ed_requested_pct": asked,
                "ed_curve": curve, "limits_c": lims,
                "limiting_part": _who, "at_allowable": None,
                "note": ("even a %.1f %% duty ratio exceeds the limit at this "
                         "operating point — the cycle is not feasible at any "
                         "ED." % lo)}
    best: Dict[str, Any] = lo_rec
    try:
        hi_margin, hi_who, hi_rec = _peak(hi)
    except DutyCycleError as exc:
        if exc.code != "duty_cycle_no_periodic_state":
            raise
        hi_margin, hi_who, hi_rec = 1e9, "winding", {}
    if hi_margin <= 0.0:
        return {"ed_allowable_pct": 100.0, "ed_requested_pct": asked,
                "ed_curve": curve, "limits_c": lims, "limiting_part": None,
                "at_allowable": _at_point(hi_rec),
                "note": "continuous duty is within every limit — this point "
                        "is S1."}
    who = hi_who
    for _ in range(max(int(iters), 4)):
        mid = 0.5 * (lo + hi)
        try:
            m, who_m, rec_m = _peak(mid)
        except DutyCycleError as exc:
            if exc.code != "duty_cycle_no_periodic_state":
                raise
            m, who_m, rec_m = 1e9, who, {}
        if m > 0.0:
            hi, who = mid, who_m
        else:
            lo, best = mid, rec_m
    return {
        "ed_allowable_pct": round(lo, 2),
        "ed_requested_pct": asked,
        "ed_curve": curve,
        "limits_c": lims,
        "limiting_part": who,
        # the FEASIBLE side of the bracket: the cycle that is actually allowed,
        # never the one half a bisection step over the limit
        "at_allowable": _at_point(best),
        "note": ("at %.1f %% duty the %s peak sits on its limit; above it the "
                 "cycle exceeds it." % (lo, who)),
    }


def ed_vs_cycle(profile: Profile, network: Network, caps: Mapping[str, Any],
                *, cycle_lengths: Sequence[float] = ED_CYCLE_LENGTHS_S,
                limits: Optional[Mapping[str, float]] = None,
                iters: int = 8, samples_per_segment: int = 24,
                warm_start: bool = False,
                ) -> List[Dict[str, Any]]:
    """The allowable ED as a function of the CYCLE LENGTH — the found regime.

    A duty ratio is a ratio of something, and the something matters: on a short
    period the machine rides its own heat capacity and a high ED is allowed; on
    a long one it has to be in thermal balance and the allowable ED falls
    towards the continuous answer.  One curve says that; a single number at one
    period hides it.

    The same rest duty and the same on-point throughout — only the period moves
    — and each row carries what the machine reaches at its own allowable point
    (``winding_hot_peak_c``, ``magnet_peak_c``), so nothing has to be re-solved
    to plot a limit beside it.  A period with NO feasible ED is a row with
    ``ed_allowable_pct: 0.0`` and its own note, never a gap in the curve.
    """
    if profile.kind != "S3":
        raise DutyCycleError(
            "duty_cycle_not_s3",
            "the ED-vs-cycle-length curve only means something for an S3 "
            "cycle.",
            "Set kind: S3 with a cycle_s (the ED may be left blank).")
    out: List[Dict[str, Any]] = []
    for raw in cycle_lengths:
        try:
            length = float(raw)
        except (TypeError, ValueError):
            continue
        if not (length > 0.0):
            continue
        row: Dict[str, Any] = {"cycle_s": round(length, 3)}
        try:
            rec = allowable_ed(replace(profile, cycle_s=length), network, caps,
                               limits=limits, iters=iters,
                               samples_per_segment=samples_per_segment,
                               with_curve=False, warm_start=warm_start)
        except DutyCycleError as exc:
            # A period the network cannot hold at ANY duty is an answer about
            # this machine, not a failure of the request — it is reported in
            # the row and the rest of the curve still gets drawn.
            if exc.code not in ("duty_cycle_no_periodic_state",
                                "duty_cycle_integration_failed"):
                raise
            out.append(dict(row, ed_allowable_pct=None, t_on_s=None,
                            limiting_part=None, winding_hot_peak_c=None,
                            magnet_peak_c=None, note=exc.message))
            continue
        ed = rec["ed_allowable_pct"]
        at = rec.get("at_allowable") or {}
        out.append(dict(
            row,
            ed_allowable_pct=ed,
            t_on_s=(None if ed is None else round(length * float(ed) / 100.0, 3)),
            limiting_part=rec.get("limiting_part"),
            winding_hot_peak_c=at.get("winding_hot_peak_c"),
            magnet_peak_c=at.get("magnet_peak_c"),
            note=rec.get("note", "")))
    return out


def map_state_c(thermal_result: Mapping[str, Any]) -> Dict[str, float]:
    """One steady map's node MEANS, as a start state for an integration.

    "How long may it pull from RATED?" is a different question from "how long
    may it pull from cold", and the difference is entirely in where the machine
    starts.  The calibration map already holds that state — it is the settled
    machine at the duty the network was fitted to — so the S2-from-rated answer
    costs no extra solve, only this read.

    Returns ``{}`` for a payload with no component means rather than raising:
    the caller reports one fewer number, it does not lose the cycle.
    """
    comps = dict((thermal_result or {}).get("components") or {})
    out: Dict[str, float] = {}
    for n in NODES:
        blk = comps.get(n)
        v = blk.get("avg") if isinstance(blk, dict) else None
        if v is None:
            return {}
        try:
            out[n] = float(v)
        except (TypeError, ValueError):
            return {}
    return out


# ---------------------------------------------------------------------------
# Where the heat went, averaged over the cycle
# ---------------------------------------------------------------------------

def average_split(trace: Trace, network: Network) -> Dict[str, Any]:
    """Time-averaged heat flows over the trace — the stator/rotor split.

    Every external path is its own line, the four END FACES included (user
    2026-09-14: they are a real share on this machine, not a correction), and
    the two sides are the sums of those lines:

        stator side = housing + mount + winding end faces + stator end faces
        rotor  side = bore + shaft ends + rotor end faces + magnet end faces

    ``closure_W`` is what the generation leaves over against the outflows: zero
    in a periodic state, and the number that says so.
    """
    t = trace.t_s
    span = max(t[-1] - t[0], 1e-12)

    def _avg(series: Sequence[float]) -> float:
        return _trapz(t, series) / span

    flows = {k: _avg(v) for k, v in trace.flows_W.items()}
    gen = {n: _avg(trace.P_W[n]) for n in NODES}
    stator_side = sum(flows.get(k, 0.0) for k in STATOR_SIDE_FLOWS)
    rotor_side = sum(flows.get(k, 0.0) for k in ROTOR_SIDE_FLOWS)
    total_out = stator_side + rotor_side
    total_in = sum(gen.values())
    return {
        "basis": "time-averaged over %.3f s (%s)" % (span, "one cycle"),
        "generated_W": {n: round(gen[n], 3) for n in NODES},
        "generated_total_W": round(total_in, 3),
        "stator_side_W": round(stator_side, 3),
        "rotor_side_W": round(rotor_side, 3),
        "stator_pct": (round(100.0 * stator_side / total_out, 1)
                       if total_out > 1e-9 else None),
        "rotor_pct": (round(100.0 * rotor_side / total_out, 1)
                      if total_out > 1e-9 else None),
        "housing_W": round(flows.get("housing", 0.0), 3),
        "mount_W": round(flows.get("mount", 0.0), 3),
        "winding_end_faces_W": round(flows.get("winding_ends", 0.0), 3),
        "stator_end_faces_W": round(flows.get("stator_ends", 0.0), 3),
        "rotor_end_faces_W": round(flows.get("rotor_ends", 0.0), 3),
        "magnet_end_faces_W": round(flows.get("magnet_ends", 0.0), 3),
        "bore_W": round(flows.get("bore", 0.0), 3),
        "shaft_ends_W": round(flows.get("shaft_ends", 0.0), 3),
        "bearings_W": round(flows.get("bearings", 0.0), 3),
        # The OPEN frame's three forced paths (end turns + slot channels are one
        # line, the ventilated gap is two).  0 on every housed machine.
        "winding_open_W": round(flows.get("winding_open", 0.0), 3),
        "rotor_gap_flow_W": round(flows.get("rotor_gap_flow", 0.0), 3),
        "stator_gap_flow_W": round(flows.get("stator_gap_flow", 0.0), 3),
        "gap_W": round(flows.get("r_s", 0.0), 3),
        "winding_to_core_W": round(flows.get("w_s", 0.0), 3),
        "closure_W": round(total_in - total_out, 4),
        "closure_pct": (round(100.0 * abs(total_in - total_out) / total_in, 3)
                        if total_in > 1e-9 else None),
        "note": ("the four end-face lines are the AXIAL paths: still air (plus "
                 "radiation) on the exposed coil ends and on the core and "
                 "magnet end faces.  They are inputs to the network, not fitted "
                 "from the 2-D map, which has no end faces."),
    }


def decimate(trace: Trace, max_samples: int = 400) -> Dict[str, Any]:
    """The trace, thinned to ``max_samples`` points per series (B.3).

    ``max_samples`` is a CEILING and not an approximate one: the last sample is
    always kept — a decimated chart that lost the end of the cycle would be a
    truncated one — and it REPLACES the last strided point rather than being
    appended to it, so 24 000 points come back as 400 and never as 401.  The
    store downstream (``duty_results.compact_duty_cycle``) applies the same rule
    to the same cap, and an off-by-one here would make it decimate a second time
    and halve the resolution of every stored cycle.
    """
    n = len(trace.t_s)
    cap = max(int(max_samples), 2)
    stride = max(1, int(math.ceil(n / cap)))
    idx = list(range(0, n, stride))
    if idx[-1] != n - 1:
        if len(idx) >= cap:
            idx[-1] = n - 1
        else:
            idx.append(n - 1)
    return {
        "t_s": [round(trace.t_s[i], 4) for i in idx],
        "T_c": {n_: [round(trace.T_c[n_][i], 3) for i in idx] for n_ in NODES},
        "winding_hot_c": [round(trace.hot_spot_c[i], 3) for i in idx],
        "P_W": {n_: [round(trace.P_W[n_][i], 3) for i in idx] for n_ in NODES},
        "n_samples": len(idx),
        "n_solved": n,
    }
