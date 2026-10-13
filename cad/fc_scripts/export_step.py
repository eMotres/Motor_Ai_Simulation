"""freecadcmd script (FreeCAD 1.0.2 / 1.1): health-gated STEP + datum export of the active part.  Started by
motor_ai_sim.cad.build.

  environment: FC_CAD_ROOT (cad/ dir), FC_MODEL (FCStd), FC_OUT (STEP), FC_DATUMS (datums JSON), FC_CHECK (step-check JSON),
               FC_STATUS, FC_SET (what-if overrides, tests)

The model is opened, fully recomputed and must pass motor_templates/segmented_flatwire/health.gate(): any failed /
not-recomputed object, sketch problem, wrong count, pre-check violation or stale geometry -> no STEP and no datums
are written.  The STEP is written under a temporary name, re-imported and checked (solid count, volume = model volume);
only then it replaces the final name.
STEP tree: <motor_id>_active / Stator / Core ; Winding / Coils / Coil_1..N ; Rotor / RotorCore ; Magnets / Magnet_01..M ;
           Mechanics / Shaft, Bearing_618_8_front / (outer_ring, inner_ring, ball_01..), Bearing_618_8_back / (...),
                       Stator_holder, Stator_holder_back, Ring_front, Ring_back, Prop_fix, Screws / Screw_1..
           (Mechanics only for profiles with the stage-3 rows)
Datums: frame, axial and radial datums, the end face of every coil lead (centre, section, normal) and the mechanical
datums (bearing seats, holder faces, A_H, shaft axis / journals / flats, Prop_fix seat, bolt circle).
"""
import datetime, json, math, os, sys, traceback

ROOT = os.environ["FC_CAD_ROOT"]
sys.path.insert(0, ROOT)
import FreeCAD as App  # noqa: E402
import Import  # noqa: E402
from motor_templates.segmented_flatwire import params_sheet as ps, health, mechanics  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402


def r6(v):
    return round(v, 6)


def lead_ends(solid, ytop0, pitch, wh, angle_deg):
    """End faces of the two leads: planar faces with normal -Z at the minimum Z of the coil."""
    zmin = solid.BoundBox.ZMin
    out = []
    for f in solid.Faces:
        try:
            n = f.normalAt(*f.Surface.parameter(f.CenterOfMass))
        except Exception:
            continue
        c = f.CenterOfMass
        if abs(c.z - zmin) < 1e-6 and n.z < -0.999:
            # layer from the radial position in the coil's own frame
            a = math.radians(-angle_deg)
            yl = c.x * math.sin(a) + c.y * math.cos(a)
            layer = int(round((ytop0 - yl - wh / 2) / pitch))
            bb = f.BoundBox
            out.append(dict(centre=[r6(c.x), r6(c.y), r6(c.z)], area_mm2=r6(f.Area), outward_normal=[0, 0, -1],
                            layer=layer, bbox=[r6(bb.XMin), r6(bb.YMin), r6(bb.XMax), r6(bb.YMax)]))
    out.sort(key=lambda d: d["layer"])
    for d, name in zip(out, ("start (layer 0, outer)", "end (last layer, inner)")):
        d["lead"] = name
    return out


def _planar_face_at(shape, z, normal_z):
    """Area and radial extent of the planar faces of shape at z whose outward normal is (0, 0, normal_z)."""
    area, rmax, rmin = 0.0, 0.0, 1e9
    for f in shape.Faces:
        if f.Surface.__class__.__name__ != "Plane" or abs(f.CenterOfMass.z - z) > 1e-6:
            continue
        n = f.normalAt(*f.Surface.parameter(f.CenterOfMass))
        if n.z * normal_z < 0.999:
            continue
        area += f.Area
        for v in f.Vertexes:
            r = math.hypot(v.Point.x, v.Point.y)
            rmax, rmin = max(rmax, r), min(rmin, r)
    return dict(area_mm2=r6(area), r_min=r6(rmin) if area else None, r_max=r6(rmax) if area else None)


def mech_datums(g, mech):
    """Mechanical datums (stage 3), named as in the Fusion model; the A_H face is measured on the exported solid."""
    zf, zfl, zb = g("holder_face_z"), g("holder_flange_z"), g("bearing_out_z")
    zah = -g("back_holder_end_z")
    aw, n = g("angle_wire"), int(round(g("holder_bolt_count")))
    return dict(
        A_H=dict(z=r6(zah), outward_normal=[0, 0, -1],
                 what="hub-boss plane: back end face of Stator_holder_back (heatsink boss seat, datum A_H of the full stack)",
                 measured=_planar_face_at(mech["Stator_holder_back"], zah, -1)),
        shaft=dict(axis="Z", back_end_z=r6(-zb), front_end_z=r6(g("shaft_front_end_z")),
                   journal_r=r6(g("shaft_journal_r")), rotor_seat_r=r6(g("shaft_seat_r")), bore_r=r6(g("shaft_bore_r")),
                   flange=dict(r=r6(g("shaft_seat_r") + g("shaft_flange_step")), z=[r6(g("rotor_w") / 2), r6(zf - g("shaft_shoulder_len"))]),
                   shoulder=dict(r=r6(g("shaft_shoulder_r")), z=[r6(zf - g("shaft_shoulder_len")), r6(zf)]),
                   journal_end_z=r6(g("shaft_journal_end_z")),
                   flats=dict(width=r6(g("shaft_flat_w")), z=[r6(g("shaft_journal_end_z") - g("shaft_flat_len")), r6(g("shaft_journal_end_z"))],
                              normals="+-X"),
                   thread=dict(r=r6(g("shaft_thread_r")), z=[r6(g("shaft_journal_end_z")), r6(g("shaft_thread_end_z"))], note="M5x0.8 in Fusion"),
                   tip=dict(r=r6(g("shaft_tip_r")), z=[r6(g("shaft_thread_end_z")), r6(g("shaft_front_end_z"))])),
        bearings=dict(card="618/8 (d %g, D %g, B %g)" % (g("bearing_d"), g("bearing_D"), g("bearing_B")),
                      front=dict(z=[r6(zf), r6(zb)]), back=dict(z=[r6(-zb), r6(-zf)]),
                      seat_r=r6(g("bearing_od_r")), journal_r=r6(g("shaft_journal_r"))),
        holders=dict(front_face_z=r6(zf), back_face_z=r6(-zf), front_flange_outer_z=r6(zfl), back_flange_outer_z=r6(-zfl),
                     front_end_z=r6(g("front_holder_end_z")), back_end_z=r6(zah), flange_r=r6(g("holder_inner_r")),
                     hub_r=r6(g("holder_hub_r")), back_outer_r=r6(g("back_holder_outer_r")), lip_r=r6(g("holder_lip_r")),
                     seats=dict(z_front=[r6(g("holder_seat_z")), r6(g("motor_length") / 2)],
                                z_back=[r6(-g("motor_length") / 2), r6(-g("holder_seat_z"))],
                                core_leg_outer_x=r6(g("core_leg_outer_x"))),
                     bolt_circle=dict(r=r6(g("holder_bolt_r")), count=n,
                                      angles_from_y_deg=[r6(aw + 360.0 * k / n) for k in range(n)],
                                      front_hole_d=r6(g("bolt_d")), back_tap_d=r6(g("holder_tap_d"))),
                     back_m3=dict(r=r6(g("back_screw_r")), d=r6(g("back_screw_tap_d")),
                                  angles_from_y_deg=[r6(g("back_screw_angle") + 360.0 * k / n) for k in range(n)])),
        rings=dict(front_z=[r6(g("motor_length") / 2), r6(g("motor_length") / 2 + g("ring_h"))],
                   back_z=[r6(-g("motor_length") / 2 - g("ring_h")), r6(-g("motor_length") / 2)],
                   r=[r6(g("ring_inner_r")), r6(g("coil_corner_r"))]),
        prop_fix=dict(seat_z=r6(g("shaft_journal_end_z")), z=[r6(g("shaft_journal_end_z") - g("prop_fix_hub_t")),
                                                             r6(g("shaft_journal_end_z") + g("prop_fix_plate_t"))],
                      d=r6(g("prop_fix_d"))),
        threads=dict(
            note="Fusion threads are COSMETIC (ThreadFeature modeled = False, full length, read 2026-10-12 from the Fusion model): "
                 "geometry is the core / tap diameter, the thread faces are coloured in the model",
            shaft=dict(designation="M5x0.8 6g (Thread9)", modeled=False, r=r6(g("shaft_thread_r")),
                       z=[r6(g("shaft_journal_end_z")), r6(g("shaft_thread_end_z"))]),
            back_holder_M3=dict(designation="M3x0.5 6H (Thread12)", modeled=False, tap_r=r6(g("back_screw_tap_d") / 2), count=n),
            back_holder_M2=dict(designation="M2x0.4 6H (Thread14)", modeled=False, tap_r=r6(g("holder_tap_d") / 2), count=n),
            screws="no thread: the Fusion component is '91292A032_NO THREADS' (shank r 1.0)"),
        screws=dict(count=n, length=r6(g("holder_screw_l")), head_seat_z=r6(zfl), tip_z=r6(zfl - g("holder_screw_l")),
                    thread_engagement=r6(max(0.0, -g("holder_seat_z") - max(zfl - g("holder_screw_l"), -zfl)))),
        note="z along the motor axis (+z propeller side); every value from the Params of this model; A_H measured on the "
             "exported Stator_holder_back solid")


def main():
    model = os.environ["FC_MODEL"]
    src = App.openDocument(model)
    for o in src.Objects:
        o.touch()
    src.recompute()
    sh = src.getObject("Params")
    overrides = ps.parse_overrides(os.environ.get("FC_SET", ""))
    for k, v in overrides.items():
        ps.set_param(sh, k, v)
    if overrides:
        src.recompute()
    gt = health.gate(src)
    motor_id = (src.Meta or {}).get("motor_motor_id") or os.path.splitext(os.path.basename(model))[0]
    out = os.environ["FC_OUT"]
    dat = os.environ["FC_DATUMS"]
    if not gt["ok"]:
        for e in gt["errors"]:
            print("  GATE:", e)
        App.closeDocument(src.Name)
        return finish(False, "health gate failed - NO STEP / datums written (%s): %s" % (
            ", ".join("%s=%s" % kv for kv in overrides.items()) or "model as saved", " | ".join(gt["errors"][:8])),
            gate=gt["errors"])
    g = lambda n: ps.pval(sh, n)
    core = src.getObject("Core").Shape.copy()
    rotor = src.getObject("RotorCore").Shape.copy()
    coils = [s.copy() for s in [o for o in src.Objects if o.Label == "Coils"][0].Shape.Solids]
    mags = [s.copy() for s in [o for o in src.Objects if o.Label == "MagnetArray"][0].Shape.Solids]
    mech = mechanics.solids(src)
    meta = dict(src.Meta or {})
    L = g("motor_length"); Lc = L + g("coil_straight_extra")
    pitch = g("wire_height") + g("wire_spacing_y")
    ytop0 = g("stator_up_r") - g("core_thickness") - g("insulation_thickness") - g("wire_spacing_y") / 2
    n_layers = int(round(g("num_wires_per_slot")))
    ncoil = len(coils)
    leads = []
    for i, s in enumerate(coils):
        ang = 360.0 * i / ncoil
        for d in lead_ends(s, ytop0, pitch, g("wire_height"), ang):
            d["coil"] = i + 1
            d["coil_angle_deg"] = r6(ang)
            leads.append(d)
    datums = dict(
        motor_id=motor_id, variant=meta.get("motor_variant"), revision=meta.get("motor_revision"),
        model=os.path.basename(model), generated=datetime.datetime.now().isoformat(timespec="seconds"),
        frame=dict(origin=[0, 0, 0], axis="+Z = motor axis (+z = propeller / front side)",
                   x_axis="+X", first_coil_and_magnet="centred on +Y", units="mm"),
        axial=dict(core_faces_z=[r6(-L / 2), r6(L / 2)], rotor_faces_z=[r6(-g("rotor_w") / 2), r6(g("rotor_w") / 2)],
                   magnet_faces_z=[r6(-L / 2), r6(-L / 2 + g("mag_step"))],
                   coil_straight_ends_z=[r6(-Lc / 2), r6(Lc / 2)],
                   coil_end_turn_max_z=r6(max(s.BoundBox.ZMax for s in coils)),
                   coil_lead_end_z=r6(min(s.BoundBox.ZMin for s in coils))),
        radial=dict(stator_outer_r=r6(g("stator_up_r")), stator_bore_r=r6(g("stator_down_r")), rotor_outer_r=r6(g("rotor_up_r")),
                    rotor_bore_r=r6(g("rotor_down_r") - g("rotor_house_height")), air_gap=r6(g("air_gap")),
                    slot_top_r=r6(g("stator_up_r") - g("core_thickness"))),
        counts=dict(core_sectors=gt["counts"]["core_sectors"], coils=ncoil, magnets=len(mags), layers=n_layers),
        coil_leads=leads,
        lead_section=dict(width=r6(g("wire_width")), height=r6(g("wire_height"))),
        note="Lead end faces measured on the exported solids (same frame as the STEP); lead start = layer 0 (outer, "
             "under the yoke), lead end = last layer (inner).")
    if mech:
        datums["mechanics"] = mech_datums(g, mech)
    if src.getObject("Electronics") is not None:
        from motor_templates.segmented_flatwire import electronics
        datums["phase_board"] = electronics.datums(src, g)
    App.closeDocument(src.Name)

    ex = App.newDocument("export")
    root = ex.addObject("App::Part", motor_id + "_active")

    def part(parent, name):
        p = ex.addObject("App::Part", name); p.Label = name; parent.addObject(p); return p

    def solid(parent, name, shape):
        f = ex.addObject("Part::Feature", name); f.Label = name; f.Shape = shape; parent.addObject(f); return f

    solid(part(root, "Stator"), "Core", core)
    cg = part(part(root, "Winding"), "Coils")
    for i, s in enumerate(coils):
        solid(cg, "Coil_%d" % (i + 1), s)
    solid(part(root, "Rotor"), "RotorCore", rotor)
    mg = part(root, "Magnets")
    for i, s in enumerate(mags):
        solid(mg, "Magnet_%02d" % (i + 1), s)
    if mech:
        mp = part(root, "Mechanics")
        sub_parts = {}
        for name, grp in mechanics.groups(list(mech)).items():
            parent = mp
            if grp:
                if grp not in sub_parts:
                    sub_parts[grp] = part(mp, grp)
                parent = sub_parts[grp]
            solid(parent, name, mech[name])
    ex.recompute()
    tmp = os.path.splitext(out)[0] + ".partial.step"      # written under a temporary name, moved after the check
    Import.export([root], tmp)
    App.closeDocument(ex.Name)

    chk = App.newDocument("check")
    Import.insert(tmp, chk.Name)
    rows = [dict(label=o.Label, volume=round(o.Shape.Volume, 4)) for o in chk.Objects
            if o.TypeId == "Part::Feature" and not o.Shape.isNull() and o.Shape.Solids]
    parts = sorted({o.Label for o in chk.Objects if o.TypeId == "App::Part"})
    App.closeDocument(chk.Name)
    expv = core.Volume + rotor.Volume + sum(s.Volume for s in coils) + sum(s.Volume for s in mags) + sum(s.Volume for s in mech.values())
    res = dict(step=os.path.basename(out), datums=os.path.basename(dat), freecad_version=".".join(str(x) for x in App.Version()[:3]), size_bytes=os.path.getsize(tmp), parts=parts,
               n_solid_objects=len(rows), total_volume=round(sum(r["volume"] for r in rows), 3), expected_total_volume=round(expv, 3),
               leads=len(leads), objects=rows)
    chk_path = os.environ["FC_CHECK"]
    with open(chk_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "objects"}, indent=1))
    # re-import volume: relative 1e-6 (the coil transition lofts are B-splines; the STEP round trip moves ~1e-3 mm3)
    ok = abs(res["total_volume"] - res["expected_total_volume"]) < 1e-6 * res["expected_total_volume"] + 1e-3 \
        and len(rows) == 2 + ncoil + len(mags) + len(mech) and len(leads) == 2 * ncoil
    if ok:
        os.replace(tmp, out)
        with open(dat, "w", encoding="utf-8") as fh:
            json.dump(datums, fh, indent=1)
    else:
        os.remove(tmp)                                     # nothing is left behind after a failed check
    return finish(ok, "STEP + datums written (%d solids, %d lead ends)" % (len(rows), len(leads)) if ok
                  else "STEP re-import check failed: %s" % {k: v for k, v in res.items() if k != "objects"})


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
