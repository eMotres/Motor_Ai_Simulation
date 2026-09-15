"""THE HEAT PATHS, as a thing you can draw.

User, 2026-09-15: *"лучше нарисовать 3D модель с катушками (end windings) и на
ней прямо показывать, куда и сколько тепла может отводиться, чтобы пользователю
было всё ясно и понятно"*.

Every watt in this module already existed — ``cooling.outer``,
``cooling.mount``, ``cooling.end_faces``, ``cooling.inner``,
``cooling.shaft_ends`` and ``cooling.heat_budget`` have carried them since the
robotics mode landed (2026-09-14).  What did not exist was a way to see WHERE
each of them leaves the machine.  On the Ø85 robot joint the honest answer is
89 % through four bolts and 9 % off the end turns, and no table makes that as
obvious as a picture of the joint with the flange glowing.

So this module answers exactly one question, and computes no physics of its own:

    given a thermal result (live payload or the compact per-duty record) and the
    machine's geometry, list the SINKS — watts, share, film or conductance,
    surface and sink temperature — and say where each one sits on a machine
    drawn out of cylinders and annuli.

MACHINE-AGNOSTIC BY CONSTRUCTION.  Nothing here knows about L13 or L155: the
radii come from the same five subtractions ``simulation.geometry_2d
.params_from_config`` makes, the end-winding overhang from the run's own
``k_end`` the way ``routes.thermal`` derives it (ℓ_end = (k_end − 1)·L/2), and a
path that is switched off reports ``active: false`` rather than disappearing —
"this machine is not bolted to anything" is an answer, and a legend that simply
omitted the row could not tell it from a machine nobody asked about.

THE DERIVED GEOMETRY FIELDS IN A STORED DIE ARE NOT READ.  ``die.yaml`` carries
``stator_inner_radius`` beside the primitives it is derived from, and on the
Ø85 die the two disagree (33.1 stored against 42.5 − 2.4 − 7.4 = 32.7): the
stored value is whatever the last writer left there.  Every radius below is
recomputed from the primitives, which is what the solver meshes.

UNITS: millimetres for every length, watts for every flow, °C for every
temperature.  The axis is +z, the stack is CENTRED on z = 0 (so it spans
±L/2) and the cross-section is in xy — the same convention as the 3-D viewer.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional

#: Bumped when the shape below changes in a way a client has to notice.  The
#: web view reads it and refuses to draw a model it does not understand rather
#: than silently mis-placing a surface.
HEAT_PATH_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# small readers — a missing key is None, never 0
# ---------------------------------------------------------------------------

def _f(v: Any) -> Optional[float]:
    """A finite float, or ``None``.  ``None`` is not 0: a path with no measured
    area and a path with zero area are different statements."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _f0(v: Any, default: float = 0.0) -> float:
    x = _f(v)
    return default if x is None else x


def _d(block: Any) -> Dict[str, Any]:
    return dict(block) if isinstance(block, Mapping) else {}


def _r(v: Optional[float], nd: int = 3) -> Optional[float]:
    return None if v is None else round(v, nd)


# ---------------------------------------------------------------------------
# 1. THE MACHINE, as primitives
# ---------------------------------------------------------------------------

def machine_envelope(geometry: Optional[Mapping[str, Any]],
                     *, k_end: Optional[float] = None,
                     shaft_ext_mm: float = 0.0,
                     bore_r_mm: Optional[float] = None) -> Dict[str, Any]:
    """Every radius and length a primitive-built 3-D of this machine needs, in mm.

    The five subtractions are ``geometry_2d.params_from_config``'s, in its own
    order, so the drawing and the mesh describe one machine:

        r_stator_out = stator_diameter / 2
        r_stator_in  = r_stator_out − core_thickness − slot_height
        r_rotor_out  = r_stator_in − air_gap
        r_rotor_in   = r_rotor_out − magnet_height − rotor_house_height
        r_shaft_in   = r_rotor_in − shaft_height

    ``bore_r_mm`` overrides the last of them when the caller has a better number
    — the solver reports the bore radius it actually found in the mesh
    (``cooling.inner.r_bore_mm``), and a compact record's ``inner.area_m2``
    divided by 2πL is that same radius to the last digit.  The geometry's
    ``r_shaft_in`` is the fallback, and on the Ø85 joint the three agree at
    22.6 mm.

    ``k_end`` is the run's own end-winding factor; the overhang per side is
    ℓ_end = (k_end − 1)·L/2, the identity ``routes.thermal`` states when it
    builds the end-winding path.  With no k_end (and no geometry to derive one
    from) the overhang is 0 — a machine with k_end = 1 has no end turns, and
    inventing a length for it would put watts on a surface that is not there.
    """
    g = _d(geometry)
    if not g:
        return {"units": "mm", "known": False}

    r_so = _f0(g.get("stator_diameter")) / 2.0
    core_t = _f0(g.get("core_thickness"))
    slot_h = _f0(g.get("slot_height"))
    gap = _f0(g.get("air_gap"))
    mag_h = _f0(g.get("magnet_height"))
    house_h = _f0(g.get("rotor_house_height"))
    shaft_h = _f0(g.get("shaft_height"))
    sleeve_t = max(_f0(g.get("sleeve_thickness")), 0.0)
    L = _f0(g.get("motor_length"))

    r_si = r_so - core_t - slot_h
    r_ro = r_si - gap
    r_ri = r_ro - mag_h - house_h
    r_sh = r_ri - shaft_h

    r_bore = bore_r_mm if (bore_r_mm is not None and bore_r_mm > 0) else r_sh
    r_bore = max(min(r_bore, r_ri), 0.0)

    # The magnets stop where the retaining sleeve starts; the sleeve is the
    # outermost skin of the rotor and is what faces the gap.
    r_mag_out = max(r_ro - sleeve_t, r_ri)
    r_mag_in = max(r_ri + house_h, r_ri)

    k = _f(k_end)
    if k is None:
        k = _k_end_from_geometry(g)
    l_end = (max(k, 1.0) - 1.0) * L / 2.0 if (k and L > 0) else 0.0
    ext = max(_f0(shaft_ext_mm), 0.0)

    half = L / 2.0
    return {
        "units": "mm",
        "known": r_so > 0 and L > 0,
        "stack_length_mm": _r(L, 3),
        "z_stack_mm": [_r(-half, 3), _r(half, 3)],
        # the whole drawn extent, end turns and shaft stubs included
        "z_extent_mm": [_r(-(half + max(l_end, ext)), 3),
                        _r(half + max(l_end, ext), 3)],

        "stator_od_mm": _r(2.0 * r_so, 3),
        "stator_id_mm": _r(2.0 * r_si, 3),
        "housing_r_mm": _r(r_so, 3),          # the cooled outer surface
        "yoke_r_in_mm": _r(r_si + slot_h, 3),  # slot top = yoke inner radius
        "slot_r_in_mm": _r(r_si, 3),
        "slot_r_out_mm": _r(r_si + slot_h, 3),
        "slot_height_mm": _r(slot_h, 3),
        "core_thickness_mm": _r(core_t, 3),

        "air_gap_mm": _r(gap, 3),
        "gap_r_in_mm": _r(r_ro, 3),
        "gap_r_out_mm": _r(r_si, 3),

        "rotor_od_mm": _r(2.0 * r_ro, 3),
        "rotor_id_mm": _r(2.0 * r_ri, 3),
        "rotor_iron_r_in_mm": _r(r_ri, 3),
        "rotor_iron_r_out_mm": _r(r_mag_in, 3),
        "magnet_r_in_mm": _r(r_mag_in, 3),
        "magnet_r_out_mm": _r(r_mag_out, 3),
        "sleeve_thickness_mm": _r(sleeve_t, 3),
        "sleeve_r_in_mm": _r(r_mag_out, 3) if sleeve_t > 0 else None,
        "sleeve_r_out_mm": _r(r_ro, 3) if sleeve_t > 0 else None,

        # The hub between the bore and the rotor iron.  A machine solved with a
        # bore has a HOLE inside `bore_r_mm`, so the shaft is drawn as a tube.
        "shaft_r_out_mm": _r(r_ri, 3),
        "shaft_r_in_mm": _r(r_bore, 3),
        "bore_r_mm": _r(r_bore, 3),
        "shaft_extension_mm": _r(ext, 3),

        "end_winding_overhang_mm": _r(l_end, 3),
        "end_winding_r_in_mm": _r(r_si, 3),
        "end_winding_r_out_mm": _r(r_si + slot_h, 3),
        "k_end": _r(k, 4) if k else None,

        "num_slots": int(_f0(g.get("num_slots"))) or None,
        "num_poles": int(_f0(g.get("num_poles"))) or None,
    }


def _k_end_from_geometry(g: Mapping[str, Any]) -> float:
    """``masses.end_winding_factor`` on a bare geometry dict.

    Same estimator, same span (tooth + the whole wire COLUMN), so the overhang
    drawn here is the overhang the loss, the mass and the end-face area were all
    computed with.  Falls back to the bare ``wire_width`` when the winding
    module cannot make sense of ``wire_split`` — a slightly narrow loop is a far
    better answer than no end turns at all.
    """
    L = _f0(g.get("motor_length"))
    if L <= 0:
        return 1.0
    tooth_w = _f0(g.get("tooth_width"))
    try:
        from motor_ai_sim.winding import winding_footprint_mm as _fp
        wire_w = _f0(_fp(dict(g)))
    except Exception:                              # noqa: BLE001
        wire_w = _f0(g.get("wire_width"))
    span = max(tooth_w, 0.0) + max(wire_w, 0.0)
    if span <= 0:
        return 1.0
    return (math.pi * span / 2.0 + L) / L


# ---------------------------------------------------------------------------
# 2. THE SINKS
# ---------------------------------------------------------------------------

#: Every path this model knows how to place, in the order a legend reads best:
#: the big one first on a robot joint, then the axial faces, then the rotor's.
_SINK_ORDER = ("mount", "housing", "end_face_winding", "end_face_stator",
               "end_windings", "slot_channels", "bore", "shaft_ends",
               "end_face_rotor", "end_face_magnet")


def _bore_radius_mm(cooling: Mapping[str, Any], stack_mm: float) -> Optional[float]:
    """The bore radius the SOLVE used, from the payload alone.

    ``cooling.inner.r_bore_mm`` on a live payload; on a compact per-duty record
    that key is not kept, but ``area_m2 = 2πrL`` is — and inverting it gives the
    same radius back (22.59 mm on the Ø85's stored 0.001846 m² over 13 mm).
    """
    inner = _d(cooling.get("inner"))
    r = _f(inner.get("r_bore_mm"))
    if r is not None and r > 0:
        return r
    a = _f(inner.get("area_m2"))
    if a and a > 0 and stack_mm > 0:
        return a / (2.0 * math.pi * (stack_mm * 1e-3)) * 1e3
    return None


def _sink(sid: str, *, group: str, label: str, short: str, w: float,
          placement: Dict[str, Any], active: bool,
          detail: Optional[List[Dict[str, Any]]] = None,
          h: Optional[float] = None, g_wk: Optional[float] = None,
          area: Optional[float] = None, t_surface: Optional[float] = None,
          t_sink: Optional[float] = None, mode: Optional[str] = None,
          note: str = "") -> Dict[str, Any]:
    return {
        "id": sid,
        "group": group,                 # 'stator' | 'rotor' — which side pays
        "label": label,                 # the legend's row
        "short": short,                 # the billboard's noun
        "mode": mode,
        "active": bool(active),
        "W": _r(w, 3),
        "pct": None,                    # filled once the total is known
        "h_W_per_m2K": _r(h, 3),
        "G_W_per_K": _r(g_wk, 5),
        "area_m2": _r(area, 6),
        "t_surface_c": _r(t_surface, 2),
        "t_sink_c": _r(t_sink, 2),
        "detail": list(detail or ()),
        "placement": placement,
        "note": note,
    }


def heat_path_model(result: Optional[Mapping[str, Any]],
                    geometry: Optional[Mapping[str, Any]] = None,
                    point: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The heat-path model of ONE thermal result.

    ``result`` is either the live ``/api/thermal/field`` payload or the compact
    per-duty record ``duty_results.compact_thermal`` writes — both carry the
    same ``cooling`` block, which is the only thing read.  ``point`` is the
    operating point (the record keeps it under ``point``); it supplies the
    ambient and the shaft-stub length when the cooling block does not.

    Returns ``{"ok": False, "reason": ...}`` when there is no cooling block to
    read — a view that cannot say where the heat went must say so, not draw an
    empty machine that looks like a solved one.
    """
    res = _d(result)
    cooling = _d(res.get("cooling"))
    if not cooling:
        return {"ok": False, "schema_version": HEAT_PATH_SCHEMA_VERSION,
                "reason": "no cooling block in this result"}

    pt = _d(point) if point is not None else _d(res.get("point"))
    budget = _d(cooling.get("heat_budget"))
    outer = _d(cooling.get("outer"))
    inner = _d(cooling.get("inner"))
    mount = _d(cooling.get("mount"))
    ends = _d(cooling.get("end_faces"))
    stubs = _d(cooling.get("shaft_ends"))
    ew = _d(cooling.get("end_windings"))
    ch = _d(cooling.get("slot_channels"))

    geo = _d(geometry)
    stack_mm = _f0(geo.get("motor_length"))
    env = machine_envelope(
        geo,
        k_end=_f(ends.get("k_end")),
        shaft_ext_mm=_f0(stubs.get("length_each_side_mm"),
                         _f0(pt.get("shaft_ext_length_mm"))),
        bore_r_mm=_bore_radius_mm(cooling, stack_mm),
    )

    half = _f0(env.get("z_stack_mm", [0, 0])[1]) if env.get("known") else 0.0
    r_house = _f0(env.get("housing_r_mm"))
    l_end = _f0(env.get("end_winding_overhang_mm"))
    ext = _f0(env.get("shaft_extension_mm"))
    ef_sides = int(_f0(ends.get("sides")))
    # WHICH ends are open.  1 side is the flange side bolted shut, so the open
    # face is drawn on +z alone; 2 is both.  0 = none, and the band is grey.
    ef_z = [1] if ef_sides == 1 else ([-1, 1] if ef_sides >= 2 else [])
    stub_sides = int(_f0(stubs.get("sides"), 2.0)) if stubs.get("mode") not in (None, "off") else 0
    stub_z = [1] if stub_sides == 1 else ([-1, 1] if stub_sides >= 2 else [])

    ambient = (_f(pt.get("ambient_temp")) if _f(pt.get("ambient_temp")) is not None
               else _f(outer.get("t_sink_c")))

    sinks: List[Dict[str, Any]] = []

    # ── the housing cylinder ────────────────────────────────────────────────
    o_mode = str(outer.get("mode") or "none")
    conv_w = _f(budget.get("housing_convection_W"))
    rad_w = _f(budget.get("housing_radiation_W"))
    if conv_w is None:
        conv_w = _f(outer.get("convection_W"))
    if rad_w is None:
        rad_w = _f(outer.get("radiation_W"))
    o_detail: List[Dict[str, Any]] = []
    if conv_w is not None and rad_w is not None and (conv_w or rad_w):
        o_detail = [{"label": "convection", "W": _r(conv_w, 3)},
                    {"label": "radiation", "W": _r(rad_w, 3)}]
    o_w = _f0(outer.get("heat_removed_W"), _f0(budget.get("housing_W")))
    sinks.append(_sink(
        "housing", group="stator", mode=o_mode,
        label=("Housing — liquid jacket" if o_mode == "liquid"
               else "Housing — still air + radiation" if o_mode == "robotics"
               else "Housing — forced air" if o_mode == "air"
               else "Housing"),
        short="housing", w=o_w, active=(o_mode != "none" and abs(o_w) > 1e-9),
        detail=o_detail, h=_f(outer.get("h_total")) or _f(outer.get("h_conv")),
        area=_f(outer.get("area_m2")), t_surface=_f(outer.get("t_wall_c")),
        t_sink=_f(outer.get("t_sink_c")),
        placement={"kind": "cylinder", "facing": "out", "r_mm": _r(r_house, 3),
                   "z0_mm": _r(-half, 3), "z1_mm": _r(half, 3)},
        note=str(outer.get("regime") or ""),
    ))

    # ── the bolted mount: ONE end annulus of the housing ────────────────────
    m_mode = str(mount.get("mode") or "off")
    m_w = _f0(mount.get("heat_removed_W"), _f0(budget.get("mount_W")))
    sinks.append(_sink(
        "mount", group="stator", mode=m_mode,
        label="Mount — bolted flange (conduction)", short="mount",
        w=m_w, active=(m_mode == "conduction" and abs(m_w) > 1e-9),
        g_wk=_f(mount.get("G_W_per_K")),
        t_surface=_f(mount.get("t_housing_mean_c")), t_sink=_f(mount.get("t_sink_c")),
        # The flange is the stator's own end annulus — yoke inner radius to the
        # housing — on the side the arm is on.  The model draws ONE side: the
        # conductance is lumped and the solve never says which, so the picture
        # must not claim two.
        placement={"kind": "annulus", "r_in_mm": _f(env.get("yoke_r_in_mm")),
                   "r_out_mm": _r(r_house, 3), "sides": [-1],
                   "z_mm": _r(half, 3)},
        note=str(mount.get("t_sink_source") or ""),
    ))

    # ── the four axial end faces ────────────────────────────────────────────
    ef_mode = str(ends.get("mode") or "off")
    for node, sid, group, label, short, r_in, r_out in (
        ("winding", "end_face_winding", "stator", "End windings — axial faces",
         "end turns", env.get("end_winding_r_in_mm"), env.get("end_winding_r_out_mm")),
        ("stator", "end_face_stator", "stator", "Stator core — end annulus",
         "stator ends", env.get("slot_r_out_mm"), env.get("housing_r_mm")),
        ("rotor", "end_face_rotor", "rotor", "Rotor core — end annulus",
         "rotor ends", env.get("rotor_iron_r_in_mm"), env.get("rotor_iron_r_out_mm")),
        ("magnet", "end_face_magnet", "rotor", "Magnets — end annulus",
         "magnet ends", env.get("magnet_r_in_mm"), env.get("magnet_r_out_mm")),
    ):
        blk = _d(ends.get(node))
        w = _f0(blk.get("heat_removed_W"))
        on = str(blk.get("mode") or ef_mode) == "still" and abs(w) > 1e-12
        if node == "winding":
            # The end turns are a BAND, not a flat annulus: they stand ℓ_end
            # proud of the core on each side, and that is the surface the film
            # acts on (shielded perimeter 2t + w, per the solver).
            place = {"kind": "band", "r_in_mm": r_in, "r_out_mm": r_out,
                     "length_mm": _r(l_end, 3), "sides": ef_z or [-1, 1],
                     "z_mm": _r(half, 3)}
        else:
            place = {"kind": "annulus", "r_in_mm": r_in, "r_out_mm": r_out,
                     "sides": ef_z, "z_mm": _r(half, 3)}
        sinks.append(_sink(
            sid, group=group, mode=str(blk.get("mode") or ef_mode),
            label=label, short=short, w=w, active=on,
            h=_f(blk.get("h_total")), area=_f(blk.get("area_m2")),
            g_wk=_f(blk.get("G_W_per_K")), t_surface=_f(blk.get("t_mean_c")),
            t_sink=_f(blk.get("t_sink_c")), placement=place,
            note=(f"{int(_f0(blk.get('n_faces')))} face(s)"
                  if _f0(blk.get("n_faces")) else ""),
        ))

    # ── the open frame's two paths ──────────────────────────────────────────
    ew_mode = str(ew.get("mode") or "housed")
    ew_w = _f0(ew.get("heat_removed_W"), _f0(budget.get("end_windings_W")))
    sinks.append(_sink(
        "end_windings", group="stator", mode=ew_mode,
        label="End windings — in the wash (open frame)", short="end turns",
        w=ew_w, active=(ew_mode not in ("housed", "off") and abs(ew_w) > 1e-9),
        h=_f(ew.get("h_conv")), t_sink=_f(ew.get("t_sink_c")),
        placement={"kind": "band", "r_in_mm": env.get("end_winding_r_in_mm"),
                   "r_out_mm": env.get("end_winding_r_out_mm"),
                   "length_mm": _r(l_end, 3), "sides": [-1, 1],
                   "z_mm": _r(half, 3)},
    ))
    ch_mode = str(ch.get("mode") or "housed")
    ch_w = _f0(ch.get("heat_removed_W"), _f0(budget.get("slot_channels_W")))
    sinks.append(_sink(
        "slot_channels", group="stator", mode=ch_mode,
        label="Slot channels — axial ducts (open frame)", short="slot ducts",
        w=ch_w, active=(ch_mode not in ("housed", "off") and abs(ch_w) > 1e-9),
        h=_f(ch.get("h_conv")), t_sink=_f(ch.get("t_sink_c")),
        placement={"kind": "cylinder", "facing": "in",
                   "r_mm": env.get("slot_r_out_mm"),
                   "z0_mm": _r(-half, 3), "z1_mm": _r(half, 3)},
    ))

    # ── the bore ────────────────────────────────────────────────────────────
    i_mode = str(inner.get("mode") or "none")
    i_w = _f0(inner.get("heat_removed_W"), _f0(budget.get("bore_W")))
    i_detail: List[Dict[str, Any]] = []
    if _f(inner.get("convection_W")) is not None and _f(inner.get("radiation_W")) is not None:
        i_detail = [{"label": "convection", "W": _r(_f(inner.get("convection_W")), 3)},
                    {"label": "radiation", "W": _r(_f(inner.get("radiation_W")), 3)}]
    sinks.append(_sink(
        "bore", group="rotor", mode=i_mode,
        label=("Bore — open, still air" if i_mode == "still" else
               "Bore — forced air" if i_mode == "air" else
               "Bore — liquid" if i_mode == "liquid" else "Bore"),
        short="bore", w=i_w, active=(i_mode != "none" and abs(i_w) > 1e-9),
        detail=i_detail, h=_f(inner.get("h_total")) or _f(inner.get("h_conv")),
        area=_f(inner.get("area_m2")), t_surface=_f(inner.get("t_wall_c")),
        t_sink=_f(inner.get("t_sink_c")),
        placement={"kind": "cylinder", "facing": "in",
                   "r_mm": env.get("bore_r_mm"),
                   "z0_mm": _r(-half, 3), "z1_mm": _r(half, 3)},
        note=str(inner.get("regime") or ""),
    ))

    # ── the shaft stubs ─────────────────────────────────────────────────────
    s_mode = str(stubs.get("mode") or "off")
    s_w = _f0(stubs.get("heat_removed_W"), _f0(budget.get("shaft_ends_W")))
    sinks.append(_sink(
        "shaft_ends", group="rotor", mode=s_mode,
        label="Shaft ends — fin in ambient air", short="shaft ends",
        w=s_w, active=(s_mode != "off" and abs(s_w) > 1e-9),
        h=_f(stubs.get("h_conv")), g_wk=_f(stubs.get("G_W_per_K")),
        t_surface=_f(stubs.get("t_shaft_mean_c")), t_sink=_f(stubs.get("t_sink_c")),
        placement={"kind": "stub", "r_mm": env.get("shaft_r_out_mm"),
                   "r_in_mm": env.get("bore_r_mm"),
                   "length_mm": _r(ext, 3), "sides": stub_z or [-1, 1],
                   "z_mm": _r(half, 3)},
        note=(f"fin efficiency {round(_f0(stubs.get('fin_efficiency')) * 100)} %"
              if _f(stubs.get("fin_efficiency")) is not None else ""),
    ))

    # ── shares, and the balance ─────────────────────────────────────────────
    # The share is of what LEFT, not of what was made: a share of the generation
    # moves with the closure error and would read as physics.  The residual is
    # reported once, on its own line, which is where an unclosed budget belongs.
    order = {sid: i for i, sid in enumerate(_SINK_ORDER)}
    sinks.sort(key=lambda s: order.get(str(s["id"]), 99))
    removed = sum(_f0(s["W"]) for s in sinks)
    for s in sinks:
        s["pct"] = (round(100.0 * _f0(s["W"]) / removed, 1)
                    if abs(removed) > 1e-9 else None)

    generated = _f(budget.get("losses_W"))
    if generated is None:
        generated = _f(res.get("P_loss_total_W"))
    resid = _f(budget.get("residual_W"))
    if resid is None and generated is not None:
        resid = generated - removed

    peak = max((abs(_f0(s["W"])) for s in sinks if s["active"]), default=0.0)
    for s in sinks:
        # 0…1 against the BIGGEST path, which is what a colour scale and an
        # arrow length can both be read off.  Not the share: on this joint the
        # mount is 89 % and everything else would be invisible on a linear
        # share scale, while against the peak the end turns still show.
        s["intensity"] = (round(abs(_f0(s["W"])) / peak, 4)
                          if peak > 1e-12 and s["active"] else 0.0)

    stator_w = sum(_f0(s["W"]) for s in sinks if s["group"] == "stator")
    rotor_w = sum(_f0(s["W"]) for s in sinks if s["group"] == "rotor")

    return {
        "ok": True,
        "schema_version": HEAT_PATH_SCHEMA_VERSION,
        "cooling_mode": str(pt.get("cooling_mode") or outer.get("mode") or "none"),
        "ambient_c": _r(ambient, 2),
        "totals": {
            "generated_W": _r(generated, 3),
            "removed_W": _r(removed, 3),
            "residual_W": _r(resid, 3),
            "residual_pct": (_r(100.0 * resid / generated, 2)
                             if (resid is not None and generated
                                 and abs(generated) > 1e-9) else None),
            "stator_side_W": _r(stator_w, 3),
            "rotor_side_W": _r(rotor_w, 3),
            "stator_side_pct": (_r(100.0 * stator_w / removed, 1)
                                if abs(removed) > 1e-9 else None),
            "rotor_side_pct": (_r(100.0 * rotor_w / removed, 1)
                               if abs(removed) > 1e-9 else None),
        },
        "sinks": sinks,
        "geometry": env,
    }
