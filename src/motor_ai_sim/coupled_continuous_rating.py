"""HOW MUCH MAY IT PULL FOR EVER? — the S1 rating under a stated cooling.

WHY THIS MODULE EXISTS (owner, 2026-09-20: *"let's also add a continuous
power calculation for different cooling conditions"*)
=============================================================================
:mod:`coupled_time_to_limit` answers the question the other way round: the
current is given, the steady state is over a limit, and what is asked is how
long the machine may pull before it gets there.  The CONTINUOUS rating is the
complement — the largest current this machine may hold **for ever** at the
reference speed with every part inside its own limit, and the torque, shaft
power, efficiency and temperatures at that current.  It is the number a robot
joint, a pump and a catalogue row are all actually specified by, and it moves
by a factor of three between a joint in still air and the same machine under a
water jacket.  So it is answered PER COOLING CONDITION.

WHAT IS JUDGED, AND ON WHICH QUANTITY.  Exactly what ``coupled_time_to_limit``
judges, through the very same :func:`~coupled_time_to_limit.part_limits`: each
part rides ONE network node plus a constant offset frozen at the calibration
map's own shape — the winding HOT SPOT against the insulation class, the
HOTTEST magnet element against its card, the bearing SEAT against the
lubricant.  One rule for the time to a limit and for the rating, so the two can
never disagree about what "at the limit" means.

THE METHOD, and every approximation in it, stated
-------------------------------------------------
For ONE cooling condition, given the reference electromagnetic run (its summary
and the thermal map solved from its loss field at ``I_ref``, ``rpm_ref``,
``γ_ref``, ``coil_ref_c``):

1. **Fit.**  The four-node network is fitted to the reference map
   (:func:`thermal_duty_cycle.network_from_steady`), the segment is built by the
   duty cycle's own loss split (:func:`thermal_duty_cycle.losses_by_node`), and
   the limits come off the machine's own cards.

2. **Scale and settle.**  With ``s = I / I_ref`` the copper goes as ``s²`` and
   the iron, magnet-eddy and mechanical watts are HELD at their reference
   values.  That is the first approximation and it is a real one: at a fixed
   speed the iron loss moves with the flux (armature reaction, and saturation
   under it) and the magnet eddy loss with the slot-harmonic field, so both
   drift with current — weakly, because the magnet field dominates both on a
   surface-magnet machine, but not by nothing.  Nothing here feeds Br(T) back
   either.  The copper's OWN temperature dependence is not held: the segment
   carries ``copper_feedback``, so the whole copper loss is re-referenced by
   ``ρ_Cu(T_w)/ρ_Cu(coil_ref)`` at every step of the settle, which makes the
   steady state nonlinear and is why it is marched to rather than solved.  (The
   DC/AC split that :func:`routes.thermal._scaled_copper_map` applies — the
   proximity share going as 1/ρ — is NOT applied to the segment: the network
   bills one copper number.  On this machine the solved AC share is 7 % of the
   copper, so the two laws differ by a per cent at 250 °C; the difference is
   reported as ``copper_law``.)

3. **Bisect.**  ``s`` is bisected until the first part's judged quantity sits on
   its limit within :data:`TOL_K`.  The function is monotone — more current is
   more copper is a hotter machine — so a bracket is all the bisection needs.

4. **Make it real.**  The network fitted to the reference map carries that map's
   conductances: its gap ``k_eff``, its wall films, its hot-spot offset, all
   evaluated at the reference temperatures.  At ``s* = 1.6`` those are the
   conductances of a machine 100 K cooler than the one being reported.  So the
   loss map is REBUILT with the copper scaled by ``s*²·ρ_Cu(T_w)/ρ_Cu(coil_ref)``,
   the 2-D thermal FEM is re-solved under the same cooling, the network is
   re-fitted to THAT map and ``s*`` is searched again — until it moves by less
   than :data:`REL_TOL`.  Two or three FEM thermal solves per condition, and the
   final map is what the temperatures are read off.

5. **Torque and power.**  ``T_em(s*) = T_em_ref · s*``, LINEAR IN CURRENT and
   labelled so: saturation makes it optimistic above the reference point and
   conservative below it, and this module never re-solves the electromagnetics.
   The power balance is the project's one balance (``report.shaft_view``): the
   rotor power times the 3-D end-effect factor, the mechanical losses off the
   shaft, ONE efficiency and it is at the shaft.

Pure: no FastAPI, no I/O, no store.  The 2-D thermal FEM enters through the
``resolve`` callback the caller passes — this module never imports a solver, and
with no callback at all it answers on the reference map alone and says so.
Every refusal is a :class:`thermal_duty_cycle.DutyCycleError`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from motor_ai_sim import coupled_time_to_limit as _ttl
from motor_ai_sim import thermal_duty_cycle as tdc
from motor_ai_sim.thermal_capacities import NODES
from motor_ai_sim.thermal_duty_cycle import DutyCycleError

#: How close to the limit the answer has to sit.  Half a kelvin on a 200 °C
#: class is the rounding of the number that produced it — the same tolerance
#: ``coupled_time_to_limit`` calls "over" with, so a rating found here and a
#: point judged there cannot straddle the same limit.
TOL_K = 0.5

#: When the outer (FEM) iteration has converged: ``s*`` moving by less than this
#: fraction of itself.  One per cent of a current is a tenth of a per cent of a
#: temperature — past the honest resolution of a network fitted to a map.
REL_TOL = 0.01

#: How many thermal FEM solves one condition may cost, the reference one
#: included.  Three is the measured number on a Ø50 machine (the second pass
#: moves ``s*`` by ~2 %, the third by ~0.2 %); the cap is there so a condition
#: that oscillates costs a bounded amount of somebody's CPU.
MAX_MAP_PASSES = 4

#: The search window on ``s``.  The top is a machine pulling eight times its
#: reference current, which no real cooling reaches from a duty point; the floor
#: is a machine at a thousandth of it, which is "the iron and the magnets alone".
S_MAX = 8.0
S_MIN = 1e-3


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# Reading a part off a REAL thermal map (the S1 verification passes)
# ---------------------------------------------------------------------------

#: The robotics heat path's TOUCH-limited bodies.  ``housing`` / ``structure``
#: are the names the lumped network and ``coupled_time_to_limit.part_limits``
#: use; ``link`` is the older spelling of the same thing (the mount's link
#: node) and is accepted so a block that still says it is read, not skipped.
TOUCH_PARTS: Tuple[str, ...] = ("housing", "structure", "link")

#: A touch limit is a hard limit (IEC 60335), so a verified point may sit
#: UNDER it by up to ``TOUCH_UNDER_TOL_K`` but never over it; the correction
#: aims ``TOUCH_AIM_K`` below it so a pass that lands a hair high is not lost.
TOUCH_OVER_TOL_K = 0.0
TOUCH_UNDER_TOL_K = 3.0
TOUCH_AIM_K = 0.5

#: Real EM + thermal passes a touch-limited verification may spend (the
#: winding / magnet verification keeps its two).  The housing rises far less
#: than with the square of the current — the iron and rotor losses do not
#: follow it — so one square-law step undershoots the correction; the next
#: steps use the secant through the real readings.
TOUCH_MAX_PASSES = 4


def field_reading(field: Mapping[str, Any], part: str) -> Optional[float]:
    """The temperature of ``part`` read off a REAL thermal map (°C), or
    ``None`` when this map does not carry it.

    ``winding`` / ``magnet`` — the hottest element of that component.
    ``housing`` / ``structure`` / ``link`` — the heat path's body temperature,
    ``cooling.heat_path.t_body_c``, the solve's own node (the map has no
    ``components.housing``).  A named body must be the one the map was solved
    with (``heat_path='shaft'`` has a structure, no housing): a different body
    reads ``None`` rather than silently judging another part.  ``link`` takes
    whichever body the map has, then falls back to the legacy
    ``cooling.mount.link.t_link_c``.
    """
    fld = field or {}
    if part in TOUCH_PARTS:
        cool = fld.get("cooling") or {}
        hp = cool.get("heat_path")
        hp = hp if isinstance(hp, Mapping) else {}
        body = str(hp.get("body") or "")
        t = _num(hp.get("t_body_c"))
        if t is not None and body in ("housing", "structure") and (
                part == "link" or part == body):
            return t
        if part == "link":
            blk = ((cool.get("mount") or {}).get("link")) or {}
            return _num(blk.get("t_link_c"))
        return None
    return _num(((fld.get("components") or {}).get(part) or {}).get("max"))


def touch_next_current(trials: Sequence[Tuple[float, float]], *, limit_c: float,
                       ambient_c: float) -> float:
    """The next trial current for a touch-limited verification, from the REAL
    readings so far (``[(I, T_actual), …]``, in the order they were solved).

    The first correction is the network search's own square law
    ``I·√((aim − amb)/(T − amb))``.  Once two readings exist the secant through
    the last two is used — it learns the machine's real slope, which the
    square law (every watt ∝ I²) over-states.  The secant is refused, and the
    square law used instead, when it is not rising, not finite, moves by more
    than 50 %, or would not go below the lowest current already seen over the
    limit.
    """
    aim = float(limit_c) - TOUCH_AIM_K
    i2, t2 = trials[-1]
    sq = i2 * math.sqrt(max(aim - ambient_c, 1e-6) / max(t2 - ambient_c, 1e-6))
    nxt = sq
    if len(trials) >= 2:
        i1, t1 = trials[-2]
        if abs(i2 - i1) > 1e-9 and (t2 - t1) / (i2 - i1) > 1e-6:
            cand = i2 + (aim - t2) * (i2 - i1) / (t2 - t1)
            over = [i for i, t in trials if t > limit_c]
            if (math.isfinite(cand) and 0.5 * i2 <= cand <= 1.5 * i2
                    and (not over or cand < min(over))):
                nxt = cand
    return max(nxt, 0.01)


# ---------------------------------------------------------------------------
# One cooling condition, as the thermal solve takes it
# ---------------------------------------------------------------------------

#: The keys of a ``routes.thermal.solve_thermal_field`` call that describe HOW
#: THE MACHINE IS COOLED and nothing else — the operating point, the mesh and
#: the electromagnetic identity are not among them.  A condition is a patch over
#: these and only these.
COOLING_KEYS: Tuple[str, ...] = (
    "cooling_mode", "ambient_temp", "h_conv", "air_speed_mps",
    "air_speed_source", "propeller_id", "propeller_position",
    "fluid", "fluid_temp_in_c", "flow_lpm",
    "bore_mode", "bore_air_speed_mps", "bore_fluid", "bore_fluid_temp_in_c",
    "bore_flow_lpm", "shaft_ext_length_mm", "shaft_ext_sides",
    "frame", "open_air_speed_mps", "emissivity", "mount_g_w_per_k",
    "mount_temp_c", "heat_path", "end_faces", "end_face_sides",
)


@dataclass(frozen=True)
class Condition:
    """One cooling condition: a human label and the cooling half of a request."""
    label: str
    params: Dict[str, Any]

    def merged(self, defaults: Mapping[str, Any]) -> Dict[str, Any]:
        """This condition's parameters over the duty's saved setup.

        A condition is a PATCH, so "the saved setup but 20 m/s" is one key.  A
        key the merged mode cannot use is dropped rather than passed on: a
        ``flow_lpm`` beside ``cooling_mode: air`` is a cache-splitting parameter
        the thermal router refuses to see, and the cooling this describes is the
        same either way.
        """
        out = {k: v for k, v in dict(defaults or {}).items()
               if k in COOLING_KEYS and v is not None}
        out.update({k: v for k, v in dict(self.params or {}).items()
                    if k in COOLING_KEYS and v is not None})
        return _prune(out)


#: Which cooling keys belong to which mode — everything else is dropped by
#: :func:`_prune`, exactly as ``thermal_settings.cooling_fields`` only ever
#: SENDS the keys the mode reads.
_OUTER_ONLY = {"manual": ("h_conv",), "air": ("air_speed_mps", "air_speed_source",
                                                "propeller_id", "propeller_position"),
               "liquid": ("fluid", "fluid_temp_in_c", "flow_lpm"),
               "robotics": ("emissivity", "end_faces", "end_face_sides",
                            "heat_path"),
               "none": ()}
_BORE_ONLY = {"air": ("bore_air_speed_mps",),
              "liquid": ("bore_fluid", "bore_fluid_temp_in_c", "bore_flow_lpm"),
              "still": (), "none": ()}


def _prune(kw: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop the parameters this cooling mode does not read."""
    out = dict(kw)
    mode = str(out.get("cooling_mode") or "manual").strip().lower()
    bore = str(out.get("bore_mode") or "none").strip().lower()
    drop = set()
    for m, keys in _OUTER_ONLY.items():
        if m != mode:
            drop.update(keys)
    for m, keys in _BORE_ONLY.items():
        if m != bore:
            drop.update(keys)
    drop -= set(_OUTER_ONLY.get(mode, ())) | set(_BORE_ONLY.get(bore, ()))
    if mode == "air":
        if str(out.get("air_speed_source") or "manual").strip().lower() == "propeller":
            drop.add("air_speed_mps")
        else:
            drop.update(("air_speed_source", "propeller_id", "propeller_position"))
    if str(out.get("frame") or "housed") != "open":
        drop.add("open_air_speed_mps")
    if not _num(out.get("mount_g_w_per_k")):
        drop.update(("mount_g_w_per_k", "mount_temp_c"))
    if str(out.get("end_faces") or "none") == "none":
        drop.add("end_face_sides")
    if str(out.get("heat_path") or "none") == "none":
        # 'none' is the router's default — never sent, so it never splits a
        # cache key (the same rule thermal_settings.cooling_fields follows).
        drop.add("heat_path")
    if not _num(out.get("shaft_ext_length_mm")):
        drop.update(("shaft_ext_length_mm", "shaft_ext_sides"))
    return {k: v for k, v in out.items() if k not in drop}


def cooling_from_duty_thermal(thermal_block: Mapping[str, Any]
                              ) -> Dict[str, Any]:
    """The cooling a duty's STORED thermal map was solved under, as request keys.

    The duty record keeps the answer, not the question: ``thermal.point`` has
    the mode and the ambient, and the rest of what was asked survives only in
    ``thermal.cooling`` — the outer film's own air speed, the bore block, the
    end-winding path an open frame produces, the mount conductance, the end
    faces.  This reads it back, so a condition can be *"the saved setup but 20
    m/s"* and the setup is the one the map beside it came from, not a default.

    Only what the record actually states is returned; a block the map reports as
    ``off`` contributes nothing, which is how the request that made it looked.
    """
    th = dict(thermal_block or {})
    pt = dict(th.get("point") or {})
    cl = dict(th.get("cooling") or {})
    outer = dict(cl.get("outer") or {})
    inner = dict(cl.get("inner") or {})
    ends = dict(cl.get("end_windings") or {})
    shaft = dict(cl.get("shaft_ends") or {})
    mount = dict(cl.get("mount") or {})
    faces = dict(cl.get("end_faces") or {})

    mode = str(pt.get("cooling_mode") or outer.get("mode") or "air").lower()
    amb = _num(pt.get("ambient_temp"))
    if amb is None:
        amb = _num(outer.get("t_sink_c"))
    out: Dict[str, Any] = {"cooling_mode": mode,
                           "ambient_temp": 25.0 if amb is None else amb}
    if mode == "manual":
        out["h_conv"] = _num(pt.get("h_conv")) or _num(outer.get("h_conv"))
    elif mode == "air":
        out["air_speed_mps"] = _num(outer.get("air_speed_mps")) or 0.0
    elif mode == "liquid":
        out["fluid"] = str(outer.get("fluid") or "water")
        out["fluid_temp_in_c"] = _num(outer.get("t_in_c"))
        out["flow_lpm"] = _num(pt.get("flow_lpm")) or _num(outer.get("flow_lpm"))
    if mode == "robotics":
        out["emissivity"] = _num(outer.get("emissivity")) or _num(
            faces.get("emissivity")) or tdc.EMISSIVITY_DEFAULT

    bore = str(inner.get("mode") or "none").lower()
    out["bore_mode"] = "none" if bore in ("off", "") else bore
    if out["bore_mode"] == "air":
        out["bore_air_speed_mps"] = _num(inner.get("air_speed_mps")) or 0.0
    elif out["bore_mode"] == "liquid":
        out["bore_fluid"] = str(inner.get("fluid") or "water")
        out["bore_fluid_temp_in_c"] = _num(inner.get("t_in_c"))
        out["bore_flow_lpm"] = _num(inner.get("flow_lpm"))

    # "housed" is the map's way of saying this machine HAS a lid — the same
    # statement as "off", spelled by the block that would have carried the open
    # frame's two paths.
    if str(ends.get("mode") or "off") not in ("off", "", "housed"):
        out["frame"] = "open"
        v = _num(ends.get("air_speed_mps"))
        if v is not None and abs(v - (_num(out.get("air_speed_mps")) or -1.0)) > 1e-9:
            out["open_air_speed_mps"] = v
    if _num(shaft.get("length_each_side_mm")):
        out["shaft_ext_length_mm"] = _num(shaft.get("length_each_side_mm"))
    if _num(mount.get("G_W_per_K")):
        out["mount_g_w_per_k"] = _num(mount.get("G_W_per_K"))
        if _num(mount.get("t_sink_c")) is not None:
            out["mount_temp_c"] = _num(mount.get("t_sink_c"))
    # THE HEAT PATH (2026-09-26): the choice the map was solved under.
    hp = str((dict(cl.get("heat_path") or {})).get("option") or "none")
    if mode == "robotics" and hp in ("housing", "shaft", "both"):
        out["heat_path"] = hp
    if str(faces.get("mode") or "off") == "still":
        out["end_faces"] = "still"
        out["end_face_sides"] = int(faces.get("sides") or 2)
    return _prune({k: v for k, v in out.items() if v is not None})


# ---------------------------------------------------------------------------
# The reference point — what ONE electromagnetic run is worth here
# ---------------------------------------------------------------------------

def reference_point(em_summary: Mapping[str, Any],
                    thermal_result: Mapping[str, Any]) -> Dict[str, Any]:
    """The block that says WHAT the rating is a multiple of.

    Nothing is computed here that is not on the run: the current the copper was
    solved at, the speed, the load angle, the coil temperature the watts are
    billed at, the 2-D torque and the 3-D factor beside it, and the provenance
    (``computed_at`` / ``geo_fingerprint``) so a reader can tell which run.
    """
    s = dict(em_summary or {})
    th = dict(thermal_result or {})
    i_ref = _num(s.get("I_phase_rms_A")) or _num(s.get("I_phase_rms"))
    if not i_ref or i_ref <= 0.0:
        raise DutyCycleError(
            "no_electromagnetic_run",
            "the reference run's summary carries no phase current, so there is "
            "nothing for the rating to be a multiple of.",
            "Re-run the operating point on the Simulation tab.")
    end3d = dict(s.get("end3d") or {})
    return {
        "I_phase_rms_A": round(float(i_ref), 4),
        "rpm": _num(s.get("rpm")) or _num(th.get("rpm")),
        "gamma_deg": _num(s.get("gamma_deg")),
        "coil_ref_c": _num(s.get("coil_temp_C")) or _num(th.get("coil_temp_c")),
        "T_em_avg_Nm": _num(s.get("T_em_avg_Nm")),
        "P_mech_W": _num(s.get("P_mech_W")),
        "k_flux": _num(end3d.get("k_flux")),
        "P_cu_exact_W": _num(th.get("P_cu_exact_W")) or _num(s.get("P_stranded_W")),
        "P_fe_W": _num(s.get("P_core_W")),
        "P_solid_W": _num(s.get("P_solid_W")),
        "P_mech_extra_W": _num(s.get("P_mech_extra_W")),
        "star_delta": s.get("star_delta"),
        "connection": s.get("connection"),
        "computed_at": th.get("computed_at") or s.get("computed_at"),
        "geo_fingerprint": (th.get("geometry_fingerprint")
                            or s.get("geo_fingerprint")),
        "note": ("the rating is a multiple of THIS run's current; the speed, the "
                 "load angle and the mesh are its own and are never varied here"),
    }


# ---------------------------------------------------------------------------
# The scaling
# ---------------------------------------------------------------------------

#: What the copper does with the current, in words — printed beside every row.
COPPER_LAW = ("copper ∝ s²·ρ_Cu(T_w)/ρ_Cu(coil_ref): the DC and the solved AC "
              "share are scaled by ONE law (the network bills one copper "
              "number), so the proximity share's own 1/ρ dependence is not "
              "separated here")

#: The share of the reference run's MAGNET and SHAFT eddy loss that is left
#: with NO current (the slotting harmonics of the magnets' own field).  The
#: rest is armature reaction and goes as the current squared.  Measured on the
#: CIANO28 L13 passport grid (the same 24-slot / 28-pole family, the same
#: solver): rotor eddy 30.6 W at 25.9 A and 81.4 W at 46.0 A at 4000 rpm fit
#: P = 7.1 W + 0.035·I² — a no-load share of 9 % at 46 A; 1.7 W at 6.5 A
#: against 7.0 W at 38.8 A at 2000 rpm — about 20 %.  A quarter is the
#: CONSERVATIVE end of that and is stated wherever it is used.  Holding the
#: whole eddy loss at every current (the law before 2026-10-19) put 58.6 W of
#: rotor heat into the "s → 0" machine of CIANO28 150_35 new / L35 and called
#: a propeller-cooled motor unrateable; every S1 estimate is confirmed by a
#: real electromagnetic pass anyway, which carries the true eddy loss.
EDDY_NOLOAD_FRACTION = 0.25

#: …and what everything else does.
HELD_LAW = ("iron and mechanical watts are HELD at the reference run's values "
            "(at a fixed speed they move with the field and only weakly with "
            "the current); the magnet and shaft EDDY watts go as "
            "f0 + (1 − f0)·s² with f0 = %.2f — they are driven by the "
            "armature field's harmonics, not by the magnets; Br(T) is not fed "
            "back" % EDDY_NOLOAD_FRACTION)


def eddy_factor(s: float, f0: float = EDDY_NOLOAD_FRACTION) -> float:
    """The magnet / shaft eddy loss at current scale ``s``, per reference watt."""
    f0 = min(max(float(f0), 0.0), 1.0)
    return f0 + (1.0 - f0) * float(s) * float(s)


def eddy_floor_from_grid(rpm_axis: Sequence[float], i_axis: Sequence[float],
                         table: Sequence[Sequence[Optional[float]]],
                         rpm: float, i_ref: float) -> Optional[Dict[str, Any]]:
    """The no-load eddy share a passport grid states at ``rpm``, ``i_ref``.

    Least squares ``P = a + b·I²`` on the grid row nearest ``rpm`` (at least
    two currents with a value), and ``f0 = a / (a + b·i_ref²)`` — the share of
    the eddy loss at the reference current that does NOT go with the current.
    ``None`` when the grid has no usable row.
    """
    best = None
    for k, n in enumerate(rpm_axis or ()):
        row = list((table or [])[k] if k < len(table or []) else [])
        pts = [(float(i), float(v)) for i, v in zip(i_axis or (), row)
               if v is not None and _num(v) is not None]
        if len(pts) < 2:
            continue
        d = abs(float(n) - float(rpm))
        if best is None or d < best[0]:
            best = (d, float(n), pts)
    if best is None:
        return None
    _, n, pts = best
    xs = [i * i for i, _ in pts]
    ys = [v for _, v in pts]
    m = len(pts)
    mx, my = sum(xs) / m, sum(ys) / m
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0.0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = max(my - b * mx, 0.0)
    b = max(b, 0.0)
    at_ref = a + b * float(i_ref) ** 2
    if at_ref <= 0.0:
        return None
    return {"a_W": a, "b_W_per_A2": b, "rpm_row": n,
            "f0": min(a / at_ref, 1.0), "points": len(pts)}


def _held_watts(loss: Mapping[str, Any], s: float) -> Dict[str, float]:
    """The NON-copper node watts at current scale ``s``: the magnets and the
    rotor's solid (shaft) share by :func:`eddy_factor` (its no-load share from
    ``loss["eddy_f0"]`` when the caller stated one), everything else held."""
    watts = tdc.node_watts(loss)
    f0 = _num(loss.get("eddy_f0"))
    k = eddy_factor(s, EDDY_NOLOAD_FRACTION if f0 is None else f0)
    solid_r = float((loss.get("split") or {}).get("rotor_solid_W") or 0.0)
    solid_r = min(max(solid_r, 0.0), watts["rotor"])
    watts["magnet"] = watts["magnet"] * k
    watts["rotor"] = watts["rotor"] - solid_r + solid_r * k
    watts.pop("winding", None)
    return watts


def _segment_at(loss: Mapping[str, Any], p_cu_ref_w: float, s: float,
                duty: str) -> tdc.Segment:
    """The reference point at current scale ``s``, as ONE powered segment."""
    watts = _held_watts(loss, s)
    watts["winding"] = float(p_cu_ref_w) * float(s) * float(s)
    return tdc.Segment(str(duty or "continuous"), t_s=1.0, losses=watts,
                       coil_ref_c=float(loss["coil_ref_c"]),
                       rpm=float(loss.get("rpm") or 0.0),
                       note="; ".join(loss.get("notes") or ()))


# ---------------------------------------------------------------------------
# The machine at another SPEED (owner 2026-10-19: a coupled run must end with
# an answer, so when no current holds at the duty's speed the rating looks for
# the speed that does)
# ---------------------------------------------------------------------------

#: The speed law, in words — printed beside every speed-search answer.
SPEED_LAW = ("at speed ratio r = n/n_ref: iron by its own Bertotti terms "
             "(hysteresis ∝ r, eddy ∝ r², excess ∝ r^1.5; a summary with no "
             "split ∝ r^1.5; a PWM drive's iron HELD, its carrier loss does not "
             "fall with speed), magnet eddy ∝ r² (resistance-limited), shaft "
             "eddy ∝ r^1.5 (skin-limited), mechanical and copper held (the copper's "
             "AC share falls with speed, so holding it is conservative); a "
             "propeller's housing film follows its slipstream speed and the "
             "air gap its Taylor number")


def losses_at_speed(loss: Mapping[str, Any], r: float, *,
                    hold_iron: bool = False) -> Dict[str, Any]:
    """``losses_by_node`` output moved to speed ratio ``r`` at a FIXED current.

    ``hold_iron`` — a PWM drive: the carrier's iron loss is set by the switching
    frequency and the bus, not the speed, and the map cannot tell it from the
    fundamental's, so the whole iron is held (conservative below the duty speed).
    """
    r = max(float(r), 1e-6)
    out = dict(loss)
    split = dict(loss.get("split") or {})

    def _bertotti(blk: Mapping[str, Any]) -> Tuple[float, float]:
        b = dict(blk or {})
        lumped = float(b.get("lumped_W") or 0.0)
        h, e, x = (float(b.get(k) or 0.0)
                   for k in ("hysteresis_W", "eddy_W", "excess_W"))
        now = h + e + x + lumped
        new = h * r + e * r * r + x * r ** 1.5 + lumped * r ** 1.5
        return now, new

    s_now, s_new = _bertotti(split.get("stator_core") or {})
    r_now, r_new = _bertotti(split.get("rotor_core") or {})
    if hold_iron:
        s_new, r_new = s_now, r_now
    solid = float(split.get("rotor_solid_W") or 0.0)
    k_solid = r ** 1.5
    out["stator"] = max(float(loss.get("stator") or 0.0) - s_now + s_new, 0.0)
    out["rotor"] = max(float(loss.get("rotor") or 0.0) - r_now + r_new
                       - solid + solid * k_solid, 0.0)
    out["magnet"] = float(loss.get("magnet") or 0.0) * r * r
    split["rotor_solid_W"] = solid * k_solid
    out["split"] = split
    out["rpm"] = float(loss.get("rpm") or 0.0) * r
    out["total_W"] = round(sum(float(out.get(n) or 0.0) for n in NODES), 4)
    return out


def network_at_speed(net: tdc.Network, *, housing_factor: float = 1.0,
                     gap_factor: float = 1.0) -> tdc.Network:
    """``net`` with its FORCED housing film and its gap conductance scaled."""
    g = dict(net.G)
    if "r_s" in g:
        g["r_s"] = float(g["r_s"]) * float(gap_factor)
    g_fit = dict(net.G_fit)
    for key, kind in (net.film_kind or {}).items():
        if kind == "forced" and key in g_fit:
            g_fit[key] = float(g_fit[key]) * float(housing_factor)
    hg = net.housing_G_fixed
    if hg is not None and (net.film_kind or {}).get("housing") == "forced":
        hg = float(hg) * float(housing_factor)
    return replace(net, G=g, G_fit=g_fit, housing_G_fixed=hg)


def speed_factors(thermal_result: Mapping[str, Any], cooling: Mapping[str, Any],
                  r: float, *, d_housing_m: Optional[float] = None,
                  air_speed_at_ratio: Optional[Callable[[float],
                                                        Optional[float]]] = None
                  ) -> Dict[str, float]:
    """``{housing, gap}`` — how the two speed-dependent films move at ratio r.

    The HOUSING film moves only when the air over it comes from the machine's
    own propeller (a typed air speed, a jacket and still air do not care how
    fast the rotor turns): its slipstream at the new speed, from
    ``air_speed_at_ratio(r)`` when the caller can compute it, else ∝ r (the
    wake of a propeller at a constant thrust coefficient).  The GAP follows
    ``cooling_models.taylor_couette_gap`` at the map's own radii.
    """
    from motor_ai_sim.simulation import cooling_models as cm

    res = dict(thermal_result or {})
    cl = dict(res.get("cooling") or {})
    outer = dict(cl.get("outer") or {})
    amb = _num((cooling or {}).get("ambient_temp")) or 25.0
    house = 1.0
    if str((cooling or {}).get("air_speed_source") or "").lower() == "propeller":
        v_ref = _num(outer.get("air_speed_mps"))
        v_new = air_speed_at_ratio(r) if air_speed_at_ratio else None
        if v_ref and v_ref > 0.0:
            v_new = v_ref * r if v_new is None else float(v_new)
            d_h = float(d_housing_m or 0.0) or 0.1
            h0 = cm.outer_air(air_speed_mps=v_ref, t_ambient_c=amb,
                              d_housing_m=d_h)["h_conv"]
            h1 = cm.outer_air(air_speed_mps=max(v_new, 0.0), t_ambient_c=amb,
                              d_housing_m=d_h)["h_conv"]
            house = float(h1) / float(h0) if h0 else 1.0
    gap = 1.0
    gb = dict(cl.get("gap") or {})
    rpm_ref = _num(res.get("rpm"))
    d_mm, rm_mm = _num(gb.get("delta_mm")), _num(gb.get("r_mean_mm"))
    if rpm_ref and d_mm and rm_mm:
        t_gap = _num(gb.get("T_gap_c")) or 100.0
        ri, ro = (rm_mm - 0.5 * d_mm) * 1e-3, (rm_mm + 0.5 * d_mm) * 1e-3
        k0 = cm.taylor_couette_gap(rpm=rpm_ref, r_rotor_m=ri, r_bore_m=ro,
                                   t_gap_c=t_gap)["k_eff"]
        k1 = cm.taylor_couette_gap(rpm=rpm_ref * r, r_rotor_m=ri, r_bore_m=ro,
                                   t_gap_c=t_gap)["k_eff"]
        gap = float(k1) / float(k0) if k0 else 1.0
    return {"housing": house, "gap": gap}


def _search_speed(loss: Mapping[str, Any], p_cu_ref_w: float,
                  network: tdc.Network, caps: Mapping[str, Any],
                  limits: Sequence[_ttl.PartLimit], duty: str, *,
                  rpm_ref: float,
                  factors_at: Callable[[float], Mapping[str, float]],
                  load_s_at: Optional[Callable[[float], Optional[float]]] = None,
                  r_min: float = 0.1, tol_k: float = TOL_K,
                  max_iter: int = 30, hold_iron: bool = False,
                  n_scan: int = 19) -> Dict[str, Any]:
    """The highest speed at which this machine holds continuously.

    ``s*(r)`` is :func:`_search_scale` on the loss split and the network moved
    to speed ratio r, CAPPED at the duty's own current (``s ≤ 1``): a point
    below the duty speed is never quoted at more current than the duty asked
    for, so nothing here is an extrapolation of the linear torque.

    ``load_s_at(rpm)`` is the duty's LOAD LINE as a current scale — for a
    propeller its own torque curve over the reference torque.  With one, the
    answer is the HIGHEST speed at which the load line fits under ``s*``: the
    grid is scanned from the duty speed DOWN and bisected inside the first
    sign change, so a cooling that improves with speed cannot hide a higher
    crossing.  A load line that returns nothing somewhere on the scan is a
    failed load line, never "no load": the search falls back to the speed of
    the most shaft power, ``max s*(n)·n``, and its ``note`` says so.
    """
    cache: Dict[float, Dict[str, Any]] = {}

    def at(r: float) -> Dict[str, Any]:
        key = round(float(r), 6)
        if key not in cache:
            f = dict(factors_at(key) or {})
            net = network_at_speed(network, housing_factor=f.get("housing", 1.0),
                                   gap_factor=f.get("gap", 1.0))
            ls = losses_at_speed(loss, key, hold_iron=hold_iron)
            res = _search_scale(ls, p_cu_ref_w, net, caps, limits, duty,
                                tol_k=tol_k, s_max=1.0)
            res["r"], res["loss"], res["factors"], res["net"] = key, ls, f, net
            cache[key] = res
        return cache[key]

    def s_star(r: float) -> Optional[float]:
        res = at(r)
        return _num(res.get("s")) if res.get("feasible") else None

    grid = [1.0 - (1.0 - float(r_min)) * i / (n_scan - 1) for i in range(n_scan)]
    note: Optional[str] = None
    if load_s_at is not None:
        ok_line = True
        for r in grid:
            try:
                ok_line = ok_line and _num(load_s_at(rpm_ref * r)) is not None
            except Exception:                                  # noqa: BLE001
                ok_line = False
        if not ok_line:
            note = ("the load line could not be evaluated over the whole speed "
                    "range, so the point is the speed of the most shaft power "
                    "instead of the load line's")
            load_s_at = None

    def need_at(r: float) -> Optional[float]:
        if load_s_at is None:
            return None
        try:
            return _num(load_s_at(rpm_ref * r))
        except Exception:                                      # noqa: BLE001
            return None

    if load_s_at is not None:
        def fits(r: float) -> Optional[bool]:
            s, need = s_star(r), need_at(r)
            if need is None:
                return None
            return s is not None and float(s) >= float(need)

        top = next((i for i, r in enumerate(grid) if fits(r)), None)
        if top is None:
            return {"feasible": False, "n_points": len(cache),
                    "curve": _curve(cache, rpm_ref, need_at),
                    "note": ("no current holds continuously on this load line "
                             "at any speed down to %.0f rpm" % (rpm_ref * r_min))}
        lo = grid[top]
        if top > 0:
            hi = grid[top - 1]
            for _ in range(int(max_iter)):
                mid = 0.5 * (lo + hi)
                ok = fits(mid)
                if ok is None:
                    break
                if ok:
                    lo = mid
                else:
                    hi = mid
                if hi - lo <= 2e-3:
                    break
        best = at(lo)
        basis = "load_line"
    else:
        scored = [(float(s_star(r) or 0.0) * r, r) for r in grid]
        p_best, r_best = max(scored)
        if p_best <= 0.0:
            return {"feasible": False, "n_points": len(cache),
                    "curve": _curve(cache, rpm_ref, need_at), "note": note
                    or ("no current holds continuously at any speed down to "
                        "%.0f rpm" % (rpm_ref * r_min))}
        step = (1.0 - r_min) / (n_scan - 1)
        a, b = max(r_min, r_best - step), min(1.0, r_best + step)
        for _ in range(20):
            m1, m2 = a + (b - a) / 3.0, b - (b - a) / 3.0
            if float(s_star(m1) or 0.0) * m1 < float(s_star(m2) or 0.0) * m2:
                a = m1
            else:
                b = m2
        best = at(0.5 * (a + b))
        basis = "max_power"
    return {"feasible": bool(best.get("feasible")), "r": best["r"],
            "rpm": rpm_ref * best["r"], "s": best.get("s"),
            "s_load": need_at(best["r"]),
            "capped": bool(best.get("capped")),
            "limiting_part": best.get("limiting_part"),
            "state_c": best.get("state_c"), "parts": best.get("parts"),
            "loss": best["loss"], "factors": best["factors"],
            "net": best["net"], "basis": basis, "note": note,
            "curve": _curve(cache, rpm_ref, need_at),
            "n_points": len(cache)}


def _curve(cache: Mapping[float, Mapping[str, Any]], rpm_ref: float,
           need_at: Callable[[float], Optional[float]]) -> List[List[Any]]:
    """``[[rpm, s*, s_load], …]`` over every speed the search evaluated, in
    rising speed — what a caller needs to re-find the crossing once a real
    pass has corrected the network (``s*`` is ``None`` where nothing holds)."""
    out: List[List[Any]] = []
    for r in sorted(cache):
        res = cache[r]
        s = _num(res.get("s")) if res.get("feasible") else None
        q = need_at(r)
        out.append([round(rpm_ref * r, 2), None if s is None else round(s, 6),
                    None if q is None else round(q, 6)])
    return out


def recross(curve: Sequence[Sequence[Any]], k_current: float
            ) -> Optional[Tuple[float, float]]:
    """The highest ``(rpm, s_load)`` at which ``k_current·s*`` still covers the
    load line, interpolated linearly along ``curve`` — the network's S1 line
    corrected by one real pass (``k_current`` = the current the real map
    allows / the network's).  ``None`` when it covers it nowhere."""
    pts = []
    for n, s, q in curve:
        if q is None:
            continue
        pts.append((float(n), None if s is None else float(s) * float(k_current),
                    float(q)))
    pts.sort()

    def margin(p: Tuple[float, Optional[float], float]) -> float:
        return -1.0 if p[1] is None else p[1] - p[2]

    for i in range(len(pts) - 1, -1, -1):
        if margin(pts[i]) >= 0.0:
            if i == len(pts) - 1:
                return pts[i][0], pts[i][2]
            a, b = pts[i], pts[i + 1]
            ma, mb = margin(a), margin(b)
            t = 0.0 if ma == mb else min(max(ma / (ma - mb), 0.0), 1.0)
            return a[0] + t * (b[0] - a[0]), a[2] + t * (b[2] - a[2])
    return None


def _judge(state: Mapping[str, float], limits: Sequence[_ttl.PartLimit],
           network: tdc.Network) -> List[Dict[str, Any]]:
    """Each part's judged quantity at this state, and how far over it is."""
    rows: List[Dict[str, Any]] = []
    for p in limits:
        t_node = float(state[network.rep(p.node)])
        q = t_node + float(p.offset_k)
        rows.append({"part": p.part, "node": p.node,
                     "quantity": _ttl.PART_QUANTITY.get(p.part, p.part),
                     "node_c": t_node, "quantity_c": q,
                     "limit_c": float(p.limit_c), "over_by_K": q - float(p.limit_c),
                     "limit_source": p.source,
                     "offset_K": float(p.offset_k)})
    return rows


def _worst(rows: Sequence[Mapping[str, Any]]) -> Tuple[float, str]:
    """``(the largest over_by_K, the part it belongs to)``."""
    best = max(rows, key=lambda r: float(r["over_by_K"]))
    return float(best["over_by_K"]), str(best["part"])


def _search_scale(loss: Mapping[str, Any], p_cu_ref_w: float,
                  network: tdc.Network, caps: Mapping[str, Any],
                  limits: Sequence[_ttl.PartLimit], duty: str,
                  *, s_max: float = S_MAX, s_min: float = S_MIN,
                  tol_k: float = TOL_K, max_iter: int = 40,
                  ) -> Dict[str, Any]:
    """Bisect ``s`` until the first part sits ON its limit.

    ``over(s)`` — the largest ``quantity − limit`` over the judged parts — is
    monotone in ``s`` (more current is more copper is a hotter machine at every
    node), so a bracket and a bisection are the whole algorithm.  What the two
    ends MEAN is the part worth reading:

      * ``over(s_min) > tol`` — the machine is over a limit with essentially no
        copper in it at all.  The iron and the magnets alone do not fit under
        this cooling, and there IS no continuous rating: ``feasible: false``,
        with the temperature at ``s → 0`` printed so the reader can see by how
        much;
      * ``over(s_max) < −tol`` — the limit is not reached inside the search
        window.  The answer is the window's edge, flagged, rather than an
        extrapolation nobody bracketed.
    """
    n_eval = 0
    cache: Dict[float, Tuple[Dict[str, float], List[Dict[str, Any]]]] = {}

    def evaluate(s: float):
        nonlocal n_eval
        key = round(float(s), 9)
        if key not in cache:
            n_eval += 1
            seg = _segment_at(loss, p_cu_ref_w, key, duty)
            state = tdc.steady_state(seg, network, caps)
            cache[key] = (state, _judge(state, limits, network))
        return cache[key]

    def over(s: float) -> float:
        return _worst(evaluate(s)[1])[0]

    lo_state, lo_rows = evaluate(s_min)
    if _worst(lo_rows)[0] > tol_k:
        part = _worst(lo_rows)[1]
        # THE WATTS AND THE TEMPERATURE travel with the refusal (owner case
        # 2026-10-19): "88 K over at zero current" read as a bug beside a
        # magnet at 112 °C at the 6-minute limit, because the sentence named
        # neither how much heat is left at s → 0 nor what it settles at.
        w = _held_watts(loss, s_min)
        p_held = sum(w.values())
        row = next(r for r in lo_rows if r["part"] == part)
        return {"feasible": False, "s": None, "n_evaluations": n_eval,
                "limiting_part": part, "state_c": lo_state, "parts": lo_rows,
                "held_W": round(p_held, 3),
                "note": ("this cooling cannot hold even the losses that do NOT "
                         "come from the current: at s → 0 the %.1f W left "
                         "(stator %.1f W, rotor %.1f W, magnets %.1f W: iron, "
                         "eddy and mechanical) settle the %s at %.1f °C, "
                         "%.1f K over its %g °C limit, so there is no "
                         "continuous rating at this speed"
                         % (p_held, w["stator"], w["rotor"], w["magnet"],
                            _ttl.part_label(part), float(row["quantity_c"]),
                            _worst(lo_rows)[0], float(row["limit_c"])))}

    hi = 1.0
    while over(hi) < -tol_k and hi < s_max:
        hi = min(hi * 1.5, s_max)
    capped = over(hi) < -tol_k
    if capped:
        state, rows = evaluate(hi)
        return {"feasible": True, "s": hi, "n_evaluations": n_eval,
                "limiting_part": None, "state_c": state, "parts": rows,
                "capped": True,
                "note": ("no part reaches its limit at %g× the reference current, "
                         "which is where this search stops; the rating is at "
                         "least that and the number quoted is the cap, not a "
                         "crossing" % s_max)}
    lo = min(hi, 1.0)
    while over(lo) > tol_k and lo > s_min:
        lo = max(lo / 1.5, s_min)

    for _ in range(int(max_iter)):
        mid = 0.5 * (lo + hi)
        f = over(mid)
        if abs(f) <= tol_k or (hi - lo) <= 1e-6 * max(hi, 1.0):
            break
        if f > 0.0:
            hi = mid
        else:
            lo = mid
    s_star = 0.5 * (lo + hi)
    state, rows = evaluate(s_star)
    resid, part = _worst(rows)
    return {"feasible": True, "s": float(s_star), "n_evaluations": n_eval,
            "limiting_part": part, "state_c": state, "parts": rows,
            "residual_K": float(resid),
            "note": ("the %s sits on its limit within %.2f K at %.4f× the "
                     "reference current" % (part, abs(resid), s_star))}


# ---------------------------------------------------------------------------
# The machine AT the rating
# ---------------------------------------------------------------------------

def _power_block(ref: Mapping[str, Any], loss: Mapping[str, Any],
                 p_cu_ref_w: float, s: float, t_w_c: float,
                 mode: str = "motor") -> Dict[str, Any]:
    """Torque, shaft power and the ONE efficiency at the continuous current.

    The torque is LINEAR in the current and nothing here pretends otherwise:
    this module never re-solves the electromagnetics, so ``T_em(s) = T_em_ref·s``
    is optimistic above the reference point (saturation) and conservative below
    it.  Everything after it is the project's own balance
    (:func:`report.shaft_view`): the rotor power carries the 3-D end-effect
    factor, the mechanical losses come off the shaft, and the efficiency is the
    shaft one.
    """
    from motor_ai_sim.report import shaft_view

    t_ref = _num(ref.get("T_em_avg_Nm"))
    rpm = _num(ref.get("rpm")) or 0.0
    coil_ref = float(loss["coil_ref_c"])
    r = (tdc.cu_rho_ratio(float(t_w_c)) / tdc.cu_rho_ratio(coil_ref))
    p_cu = float(p_cu_ref_w) * float(s) * float(s) * r
    p_other = float(sum(_held_watts(loss, s).values()))
    p_loss_em = p_cu + p_other
    out: Dict[str, Any] = {
        "T_em_Nm": (None if t_ref is None else float(t_ref) * float(s)),
        "P_cu_W": p_cu,
        "P_other_loss_W": p_other,
        "P_loss_em_W": p_loss_em,
        "rho_ratio": r,
        "torque_basis": ("T_em(s) = T_em_ref × s — LINEAR IN CURRENT, a model "
                         "estimate: no electromagnetic run was made at this "
                         "current, so saturation is not in it (optimistic above "
                         "the reference point, conservative below it)"),
    }
    if out["T_em_Nm"] is None or rpm <= 0.0:
        out["note"] = ("the reference run carries no torque or no speed, so no "
                       "power is quoted")
        return out
    p_mech = abs(float(out["T_em_Nm"])) * 2.0 * math.pi * float(rpm) / 60.0
    em = {"P_mech_W": p_mech, "P_loss_total_W": p_loss_em,
          "end3d": ({"k_flux": ref["k_flux"]} if _num(ref.get("k_flux"))
                    else None)}
    brg = ({"has_bearings": True, "P_mech_extra_W": _num(ref["P_mech_extra_W"])}
           if _num(ref.get("P_mech_extra_W")) is not None else None)
    sv = shaft_view(em, brg, mode)
    out.update({
        "P_mech_W": p_mech,
        "P_rotor_W": sv.get("P_rotor_W"),
        "P_elec_W": sv.get("P_elec_W"),
        "P_shaft_W": sv.get("P_shaft_W"),
        "eta_em": sv.get("eta_em"),
        "eta_shaft": sv.get("eta_shaft"),
        "k_flux": sv.get("k"),
        "basis": ("report.shaft_view — the project's one power balance; the "
                  "efficiency quoted is the SHAFT one wherever the run carries "
                  "a mechanical-loss model, and is absent rather than guessed "
                  "where it does not"),
    })
    if brg is None:
        out["shaft_note"] = ("this run names no mechanical losses "
                             "(P_mech_extra_W), so there is no shaft power and "
                             "no shaft efficiency — an unknown friction is not "
                             "a zero")
    return out


# ---------------------------------------------------------------------------
# The answer, for ONE cooling condition
# ---------------------------------------------------------------------------

def rate(*, thermal_result: Mapping[str, Any],
         em_summary: Mapping[str, Any],
         caps: Mapping[str, Any],
         limits: Optional[Sequence[_ttl.PartLimit]] = None,
         geometry: Optional[Mapping[str, Any]] = None,
         cooling: Optional[Mapping[str, Any]] = None,
         side_areas: Optional[Mapping[str, Any]] = None,
         d_housing_m: Optional[float] = None,
         magnet_k_w_per_mk: Optional[float] = None,
         duty: str = "",
         mode: str = "motor",
         resolve: Optional[Callable[[float], Optional[Mapping[str, Any]]]] = None,
         refit: Optional[Callable[[Mapping[str, Any]],
                                  Tuple[Optional[Mapping[str, Any]],
                                        Optional[float]]]] = None,
         max_map_passes: int = MAX_MAP_PASSES,
         rel_tol: float = REL_TOL,
         tol_k: float = TOL_K,
         s_max: float = S_MAX,
         speed_search: Optional[Mapping[str, Any]] = None,
         eddy_noload_fraction: Optional[float] = None,
         **limit_kw: Any) -> Dict[str, Any]:
    """The continuous rating of this machine under the cooling ``thermal_result``
    was solved with.

    ``speed_search`` (2026-10-19, owner: *a coupled run must end with a
    positive result*) — when no current holds at the reference speed, look for
    the highest speed that does (:func:`_search_speed`) and return it as
    ``continuous_point``.  ``{"load_s_at": rpm -> s or None,
    "air_speed_at_ratio": r -> m/s or None, "r_min": 0.1}``; every key is
    optional.  ``None`` (the default) keeps the old answer exactly.

    ``eddy_noload_fraction`` — the no-load share ``f0`` of the magnet / shaft
    eddy loss when the caller knows THIS machine's (a passport grid,
    :func:`eddy_floor_from_grid`); absent, :data:`EDDY_NOLOAD_FRACTION`.

    ``thermal_result`` is the REFERENCE map: the 2-D steady map of the reference
    electromagnetic run's loss field under this condition.  ``resolve(factor)``
    re-solves that map with the reference copper multiplied by ``factor`` and
    returns the new payload (``None`` = it could not, and the answer then stands
    on the maps it has, saying so).  ``refit(map)`` optionally returns
    ``(side_areas, d_housing_m)`` for a re-solved map; without it the reference
    map's are reused, which is right — the end faces are the machine's, not the
    temperature's.

    Everything from ``geometry`` down is exactly the argument
    :func:`coupled_time_to_limit.solve` takes and means the same thing.
    ``**limit_kw`` goes to :func:`coupled_time_to_limit.part_limits`
    (``magnet_grade``, ``bearing_temp_c``, ``winding_limit_c``, …) and is used
    only when ``limits`` is not given.
    """
    ref = reference_point(em_summary, thermal_result)
    loss0 = tdc.losses_by_node(em_summary, thermal_result)
    p_cu_ref_w = float(loss0["winding"])
    if p_cu_ref_w <= 0.0:
        raise DutyCycleError(
            "no_electromagnetic_run",
            "the reference run makes no copper loss, so the current cannot be "
            "scaled against it.")

    passes: List[Dict[str, Any]] = []
    notes: List[str] = [COPPER_LAW, HELD_LAW]
    if eddy_noload_fraction is not None:
        notes.append("the no-load eddy share is THIS machine's, f0 = %.3f "
                     "(from its passport grid), not the default %.2f"
                     % (float(eddy_noload_fraction), EDDY_NOLOAD_FRACTION))
    cur_map: Mapping[str, Any] = thermal_result
    cur_side, cur_dh = side_areas, d_housing_m
    s_prev: Optional[float] = None
    search: Dict[str, Any] = {}
    net: Optional[tdc.Network] = None
    judged: Sequence[_ttl.PartLimit] = ()
    loss: Mapping[str, Any] = loss0
    converged = False
    non_monotone = False

    for k in range(max(int(max_map_passes), 1)):
        net = tdc.network_from_steady(
            cur_map,
            mount_g_w_per_k=float((cooling or {}).get("mount_g_w_per_k") or 0.0),
            mount_temp_c=(cooling or {}).get("mount_temp_c"),
            t_ambient_c=(None if (cooling or {}).get("ambient_temp") is None
                         else float((cooling or {})["ambient_temp"])),
            side_areas=cur_side,
            d_housing_m=(None if cur_dh is None else float(cur_dh)),
            emissivity=(cooling or {}).get("emissivity"),
            geometry=geometry, magnet_k_w_per_mk=magnet_k_w_per_mk,
            calibration_duty=(str(duty) or None),
            # THE SURFACES COME FROM THE MAP (2026-09-20), which is the whole
            # reason this feature can answer for a jacket and an open frame at
            # all: without it the network re-invents a still-air film for a
            # 40 m/s housing and has no key for the 207 W the end turns of this
            # machine take straight to the room.  It is the DEFAULT since
            # 2026-09-21 and is spelled out here anyway, because this feature
            # cannot be correct without it.
            surface_fit=True)
        judged = list(limits if limits is not None
                      else _ttl.part_limits(thermal_result=cur_map,
                                            em_summary=em_summary, **limit_kw))
        if not judged:
            raise DutyCycleError(
                "no_part_limits",
                "nothing on this machine states a temperature limit, so there "
                "is no continuous rating to find.",
                "Assign a magnet with a maximum working temperature, or pass "
                "winding_limit_c.")
        # The non-copper watts follow the CURRENT map (its own solid-loss split
        # and its own mechanical block); the copper is always the REFERENCE
        # run's, because `s` is defined against that run and a later map's
        # copper is already `s²` of it.
        loss = tdc.losses_by_node(em_summary, cur_map)
        if eddy_noload_fraction is not None:
            loss["eddy_f0"] = float(eddy_noload_fraction)
        search = _search_scale(loss, p_cu_ref_w, net, caps, judged, duty,
                               s_max=s_max, tol_k=tol_k)
        s_star = _num(search.get("s"))
        passes.append({
            "pass": k,
            "s": (None if s_star is None else round(s_star, 5)),
            "feasible": bool(search.get("feasible")),
            "limiting_part": search.get("limiting_part"),
            "n_steady_evaluations": search.get("n_evaluations"),
            "map_P_cu_W": round(float(_num(cur_map.get("P_cu_exact_W")) or 0.0), 3),
            "map_winding_mean_c": round(float(
                ((cur_map.get("components") or {}).get("winding") or {})
                .get("avg") or 0.0), 2),
        })
        if not search.get("feasible") and (resolve is None or k > 0):
            # AN INFEASIBLE VERDICT IS CHECKED ON ITS OWN MAP, once.  "Not even
            # the iron and the magnets fit" is a strong claim, and made on a
            # network fitted to a map that was 300 K hotter it is not a claim at
            # all: the conductances came from temperatures the machine would
            # never be at.  One more solve with the copper taken out settles it.
            break
        if search.get("feasible"):
            if s_prev is not None and abs(s_star - s_prev) <= rel_tol * max(
                    s_prev, 1e-9):
                converged = True
                break
            s_prev = s_star
        if resolve is None or k >= int(max_map_passes) - 1:
            break
        t_w = float(search["state_c"][net.rep("winding")])
        factor = ((float(s_star) ** 2 if s_star is not None else 1e-6)
                  * tdc.cu_rho_ratio(t_w)
                  / tdc.cu_rho_ratio(float(loss["coil_ref_c"])))
        try:
            nxt = resolve(factor)
        except DutyCycleError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed re-solve is an answer
            notes.append("the loss map could not be re-solved at %.3f× the "
                         "reference copper (%s), so the answer stands on the "
                         "map(s) already solved" % (factor, exc))
            break
        if not nxt:
            notes.append("the loss map could not be re-solved at %.3f× the "
                         "reference copper, so the answer stands on the map(s) "
                         "already solved" % factor)
            break
        # THE RE-SOLVED MAP MUST MOVE THE RIGHT WAY.  More copper is a hotter
        # machine, always, and a thermal solve that answers otherwise is not a
        # machine this method may iterate on: the fixed point it would walk to
        # is the solver's own inconsistency, not a rating.  Measured 2026-09-20
        # on the robotics (still air + axial end faces) mode of this Ø50 joint:
        # 60 W of copper gave a 320 °C winding and 483 W gave 66 °C, monotone in
        # the WRONG direction over the whole range.  The pass that has already
        # been found is kept and the reason travels with the answer.
        p0_w = _num(cur_map.get("P_cu_exact_W"))
        p1_w = _num(nxt.get("P_cu_exact_W"))
        t0_c = _num(((cur_map.get("components") or {}).get("winding") or {})
                    .get("avg"))
        t1_c = _num(((nxt.get("components") or {}).get("winding") or {})
                    .get("avg"))
        if (None not in (p0_w, p1_w, t0_c, t1_c)
                and abs(p1_w - p0_w) > 1e-6 * max(abs(p0_w), 1.0)
                and (p1_w - p0_w) * (t1_c - t0_c) < 0.0):
            out_note = (
                "THE 2-D THERMAL SOLVE IS NOT MONOTONE under this cooling: "
                "re-solved with %.1f W of copper instead of %.1f W it came back "
                "at %.1f °C instead of %.1f °C — colder with more heat in it. "
                "The map cannot be iterated on, so the answer stops at the "
                "first pass and is NOT a converged rating"
                % (p1_w, p0_w, t1_c, t0_c))
            notes.append(out_note)
            non_monotone = True
            break
        cur_map = nxt
        if refit is not None:
            side, dh = refit(cur_map)
            cur_side = side if side is not None else cur_side
            cur_dh = dh if dh is not None else cur_dh

    assert net is not None
    n_maps = len(passes)
    out: Dict[str, Any] = {
        "reference": ref,
        "feasible": bool(search.get("feasible")),
        "n_thermal_fem_solves": n_maps,
        "converged": bool(converged),
        "trustworthy": not non_monotone,
        "passes": passes,
        "judged": [p.part for p in judged],
        "limits_c": {p.part: round(float(p.limit_c), 2) for p in judged},
        "cooling": _prune(dict(cooling or {})),
        "model": (
            "the four-node lumped network fitted to a 2-D steady thermal map of "
            "THIS machine, driven by the reference run's loss split with the "
            "copper scaled by the square of the current and re-referenced by "
            "ρ_Cu(T); the map is re-solved with the scaled copper and the "
            "network re-fitted until the answer stops moving"),
        "notes": notes,
    }
    if not search.get("feasible"):
        out.update({
            "I_cont_A_rms": None, "s": None,
            "limiting_part": search.get("limiting_part"),
            "temperatures_c": {r["part"]: round(float(r["quantity_c"]), 2)
                               for r in (search.get("parts") or ())},
            "parts": [{k2: (round(v, 3) if isinstance(v, float) else v)
                       for k2, v in r.items()} for r in (search.get("parts") or ())],
            "node_means_c": {n: round(float(search["state_c"][net.rep(n)]), 2)
                             for n in NODES},
            "note": search.get("note"),
        })
        out["network"] = _ttl._fit_residual(
            _ttl._segment(em_summary, cur_map, duty), net, caps, cur_map)
        if speed_search is not None and non_monotone:
            # A network the 2-D solve would not iterate on is not one to move
            # to another speed either.
            out["continuous_point"] = {
                "feasible": False, "trustworthy": False,
                "note": ("no speed search: the 2-D thermal solve was not "
                         "monotone under this cooling, so its network cannot "
                         "be trusted at another speed")}
        elif speed_search is not None:
            out["continuous_point"] = _continuous_point(
                ref=ref, loss=loss, p_cu_ref_w=p_cu_ref_w, net=net, caps=caps,
                limits=judged, duty=duty, mode=mode, cur_map=cur_map,
                cooling=cooling or {}, d_housing_m=cur_dh, tol_k=tol_k,
                opts=speed_search)
        return out

    s_star = float(search["s"])
    state = search["state_c"]
    t_w = float(state[net.rep("winding")])
    i_cont = float(ref["I_phase_rms_A"]) * s_star
    power = _power_block(ref, loss, p_cu_ref_w, s_star, t_w, mode)
    out.update({
        "s": round(s_star, 5),
        "I_cont_A_rms": round(i_cont, 3),
        "I_cont_A_peak": round(i_cont * math.sqrt(2.0), 3),
        "limiting_part": search.get("limiting_part"),
        "limit_residual_K": (None if search.get("residual_K") is None
                             else round(float(search["residual_K"]), 3)),
        "capped": bool(search.get("capped")),
        "temperatures_c": {r["part"]: round(float(r["quantity_c"]), 2)
                           for r in search["parts"]},
        "parts": [{k2: (round(v, 3) if isinstance(v, float) else v)
                   for k2, v in r.items()} for r in search["parts"]],
        "node_means_c": {n: round(float(state[net.rep(n)]), 2) for n in NODES},
        "losses_W": {"winding": round(float(power["P_cu_W"]), 3),
                     **{n: round(float(w), 3)
                        for n, w in _held_watts(loss, s_star).items()}},
        "power": {k2: (round(v, 4) if isinstance(v, float) else v)
                  for k2, v in power.items()},
        # HOW WELL the network reproduces the map it was fitted to, measured on
        # THAT MAP's own watts — not on the rating's.  A residual asks one
        # question (is the fit good?) and mixing the rating's copper into it
        # would answer a different one.
        "network": _ttl._fit_residual(
            _ttl._segment(em_summary, cur_map, duty), net, caps, cur_map),
        "note": search.get("note"),
    })
    if s_star > 1.0:
        out["notes"].append(
            "s* > 1: the rating EXTRAPOLATES above the point that was solved — "
            "the electromagnetics were never run at %.1f A, so the torque is "
            "the linear one and saturation would take some of it back"
            % i_cont)
    if resolve is None:
        out["notes"].append(
            "no loss map was re-solved: the conductances are the reference "
            "map's, fitted at ITS temperatures rather than at the rating's")
    elif not converged and n_maps >= int(max_map_passes):
        out["notes"].append(
            "s* was still moving by more than %.0f %% after %d thermal solves — "
            "the number is the last one, not a converged one"
            % (100.0 * rel_tol, n_maps))
    return out


def _continuous_point(*, ref: Mapping[str, Any], loss: Mapping[str, Any],
                      p_cu_ref_w: float, net: tdc.Network,
                      caps: Mapping[str, Any],
                      limits: Sequence[_ttl.PartLimit], duty: str, mode: str,
                      cur_map: Mapping[str, Any], cooling: Mapping[str, Any],
                      d_housing_m: Optional[float], tol_k: float,
                      opts: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``continuous_point`` block: the highest speed that holds, with its
    current, torque and power — a NETWORK ESTIMATE until the caller confirms
    it with a real electromagnetic pass (``verified``).

    On a LOAD LINE (a propeller) the point is the load's own: ``I_cont_A_rms``
    is the current the propeller draws at that speed, ``I_limit_A_rms`` the
    most the cooling would hold there, and the power is the propeller's torque
    times the speed.  Otherwise it is the speed of the most shaft power at its
    own S1 current.  Never above the duty's current (``s ≤ 1``); ``capped``
    says no part reached its limit even there.
    """
    rpm_ref = _num(ref.get("rpm")) or _num(loss.get("rpm"))
    if not rpm_ref or rpm_ref <= 0.0:
        return {"feasible": False, "note": "the reference run states no speed"}
    air_at = opts.get("air_speed_at_ratio")

    def factors_at(r: float) -> Dict[str, float]:
        return speed_factors(cur_map, cooling, r, d_housing_m=d_housing_m,
                             air_speed_at_ratio=air_at)

    r_min = float(opts.get("r_min") or 0.1)
    res = _search_speed(loss, p_cu_ref_w, net, caps, limits, duty,
                        rpm_ref=float(rpm_ref), factors_at=factors_at,
                        load_s_at=opts.get("load_s_at"), r_min=r_min,
                        tol_k=tol_k, hold_iron=bool(opts.get("hold_iron")))
    notes = [n for n in (opts.get("note"), res.get("note")) if n]
    if not res.get("feasible") or _num(res.get("s")) is None:
        return {"feasible": False, "rpm_min": round(float(rpm_ref) * r_min, 1),
                "note": res.get("note") or (
                    "no speed down to %.0f rpm holds continuously under this "
                    "cooling" % (float(rpm_ref) * r_min)),
                "notes": notes, "curve": res.get("curve"),
                "speed_law": SPEED_LAW}
    s_lim = float(res["s"])
    s_load = _num(res.get("s_load"))
    on_line = res.get("basis") == "load_line" and s_load is not None
    s = min(float(s_load), s_lim) if on_line else s_lim
    rpm = float(res["rpm"])
    rnet = res["net"]
    # THE MACHINE AT THE POINT'S OWN CURRENT — on a load line that is below the
    # limit current, so its temperatures are the load's, not the limit's.
    state = tdc.steady_state(_segment_at(res["loss"], p_cu_ref_w, s, duty),
                             rnet, caps)
    rows = _judge(state, limits, rnet)
    t_w = float(state[rnet.rep("winding")])
    power = _power_block(dict(ref, rpm=rpm), res["loss"], p_cu_ref_w, s, t_w,
                         mode)
    i_ref = float(ref["I_phase_rms_A"])
    i = i_ref * s
    worst = max(rows, key=lambda r: float(r["over_by_K"])) if rows else None
    if res.get("capped"):
        notes.append("no part reaches its limit at this speed even at the "
                     "duty's own current, which is where this search stops — "
                     "the current is the duty's, not an extrapolation")
    return {
        "feasible": True, "verified": None,
        "rpm": round(rpm, 1), "speed_ratio": round(float(res["r"]), 5),
        "s": round(s, 5), "I_cont_A_rms": round(i, 3),
        "I_cont_A_peak": round(i * math.sqrt(2.0), 3),
        "I_limit_A_rms": round(i_ref * s_lim, 3),
        "I_load_A_rms": (None if s_load is None else round(i_ref * s_load, 3)),
        "capped": bool(res.get("capped")),
        # The part that binds the SPEED (its limit is what stops the search
        # going higher), and the one a verification pass judges first — the
        # winding when nothing reached its limit at all.
        "limiting_part": (res.get("limiting_part")
                          or (worst or {}).get("part") or "winding"),
        "temperatures_c": {r["part"]: round(float(r["quantity_c"]), 2)
                           for r in rows},
        "losses_W": {"winding": round(float(power["P_cu_W"]), 3),
                     **{n: round(float(w), 3)
                        for n, w in _held_watts(res["loss"], s).items()}},
        "power": {k: (round(v, 4) if isinstance(v, float) else v)
                  for k, v in power.items()},
        "basis": res.get("basis"),
        "film_factors": {k: round(float(v), 4)
                         for k, v in (res.get("factors") or {}).items()},
        "curve": res.get("curve"),
        "speed_law": SPEED_LAW,
        "load_line": opts.get("load_line") if on_line else None,
        "notes": notes,
        "note": ("network estimate: the highest speed at which this machine "
                 "holds %s continuously under this cooling"
                 % ("its load line" if on_line else "the most shaft power")),
    }


def continuous_point_words(cp: Optional[Mapping[str, Any]]) -> str:
    """``"continuous: 3120 rpm, 54.1 A rms, 7.9 kW (FEM-verified)"`` or ``""``.

    A point the real pass did NOT confirm says by how much it missed, so an
    estimate the real map found hot is never read as a positive result."""
    c = dict(cp or {})
    if not c.get("feasible"):
        return ""
    p = _num((c.get("power") or {}).get("P_shaft_W"))
    if p is None:
        p = _num((c.get("power") or {}).get("P_mech_W"))
    miss = _num(c.get("miss_K"))
    if c.get("verified") is True:
        tag = "FEM-verified"
    elif c.get("verified") is False:
        tag = ("estimate, real pass %+.1f K" % miss if miss is not None
               else "estimate, not verified")
    else:
        tag = "network estimate"
    return "continuous: %.0f rpm, %.1f A rms%s (%s)" % (
        float(c.get("rpm") or 0.0), float(c.get("I_cont_A_rms") or 0.0),
        "" if p is None else ", %.1f kW" % (p / 1000.0), tag)


def headline(block: Optional[Mapping[str, Any]]) -> str:
    """The ONE sentence — ``""`` when there is nothing to say."""
    b = dict(block or {})
    if not b:
        return ""
    if not b.get("feasible"):
        cp = b.get("continuous_point")
        rpm = _num((b.get("reference") or {}).get("rpm"))
        lead = "S1 at %s: none (%s); " % (
            ("%.0f rpm" % rpm) if rpm else "this speed",
            _ttl.part_label(str(b.get("limiting_part") or "a part")))
        cp_words = continuous_point_words(cp)
        if cp_words:
            # NEVER A DEAD END (owner 2026-10-19): the duty's own speed has no
            # rating, and the sentence says what DOES hold.
            return lead + cp_words
        if isinstance(cp, Mapping) and cp:
            # …and when nothing holds at ANY speed the search could try, the
            # sentence says that and what it means, rather than stopping at
            # a bare refusal.
            return lead + "no speed down to %s holds under this cooling — it " \
                "needs more cooling (%s)" % (
                    ("%.0f rpm" % cp["rpm_min"]) if _num(cp.get("rpm_min"))
                    else "the search floor", b.get("note") or cp.get("note")
                    or "see the tooltip")
        return str(b.get("note") or "there is no continuous rating under this "
                                    "cooling")
    if not b.get("trustworthy", True):
        # The SPECIFIC reason, when one was stamped on the block: this
        # module's own non-monotone-map note, a caller's contradiction with
        # this run's own `time_to_limit` verdict, or (falling back to `note`)
        # a re-solve that never converged.  Checked in that order because a
        # block can carry a pass's own ordinary fit note in `note` at the same
        # time as a specific "why untrustworthy" line in `notes`.
        why = next((n for n in (b.get("notes") or ())
                    if n.startswith("THE 2-D") or n.startswith("CONTRADICTS")),
                   None) or b.get("note") or "the map could not be iterated"
        return "NOT A RATING — %s" % why
    # A route may attach the estimate before it runs the real S1 check.  Keep
    # that estimate usable for the estimate UI, but never call it a continuous
    # rating until the physical verification has passed.
    if b.get("verified") is False:
        why = b.get("note") or "the S1 verification did not pass"
        return "NOT VERIFIED — %s" % why
    i = _num(b.get("I_cont_A_rms"))
    part = str(b.get("limiting_part") or "a part")
    lim = _num((b.get("limits_c") or {}).get(part))
    p = _num((b.get("power") or {}).get("P_shaft_W"))
    tail = ("" if p is None else ", %.0f W at the shaft" % p)
    if part in ("housing", "structure"):
        # The robotics heat path's body (2026-09-26): its limit is a TOUCH
        # limit, and the sentence says so rather than a bare temperature.
        return ("%.1f A rms continuously%s — the %s sits on its %s touch "
                "limit" % (i or 0.0, tail, part,
                           "%g °C" % lim if lim is not None else "70 °C"))
    return ("%.1f A rms continuously%s — the %s sits on %s"
            % (i or 0.0, tail, part,
               "%g °C" % lim if lim is not None else "its limit"))
