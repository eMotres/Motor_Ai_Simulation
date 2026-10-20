"""Validity domain (pre-check) and the mandatory health gate of the segmented_flatwire template.

precheck(g)      - pure Python, g(name) -> value (mm / deg / unitless).  Returns a list of violations in words.
                   Run BEFORE building and from the owner macro (macros/CheckMotorParams.FCMacro).
gate(doc)        - run AFTER every recompute and BEFORE any save-as-result / STEP / datum export.  Fails on:
                   invalid / error / not-recomputed objects, sketch solver failure or loss of full constraint,
                   wrong body / instance counts, null / invalid / multi-solid / zero-volume bodies, pre-check
                   violations, and stale geometry (a failed feature keeps its OLD valid shape in FreeCAD, so the
                   shapes are compared with what the current Params demand).
"""
import math

from .common import rotor_numbers, fillet_center_circle_line

TOL = 1e-4   # mm


def _num(g, name):
    return float(g(name))


def _opt(g, name, default):
    """Optional parameter of newer profiles (a Params row that older profiles do not have)."""
    try:
        return float(g(name))
    except Exception:
        return default


def _has(g, name):
    try:
        g(name)
        return True
    except Exception:
        return False


def precheck(g, frameless=False):
    """Necessary conditions for this template; each violation is a sentence naming the parameters.  frameless=True skips the
    validity domain of the mechanical parts (holders, bearings, shaft ... are not built in the frameless kit)."""
    v = []

    def need(cond, text):
        if not cond:
            v.append(text)

    # integer counts and fixed-topology parameters
    for n in ("num_seg", "num_poles_per_segment", "num_wires_per_slot", "num_slots_per_segment"):
        x = _num(g, n)
        need(abs(x - round(x)) < 1e-9 and x >= 1, "%s = %g must be a positive integer" % (n, x))
    need(round(_num(g, "num_wires_per_slot")) >= 3,
         "num_wires_per_slot = %g: the coil builder needs at least 3 layers" % _num(g, "num_wires_per_slot"))
    need(round(_num(g, "num_slots_per_segment")) == 6,
         "num_slots_per_segment = %g is not supported: the template (like the Fusion model, angle_wire = 360/(num_seg*6)) "
         "has 6 slots = 3 wound teeth per segment" % _num(g, "num_slots_per_segment"))
    need(abs(_num(g, "sleeve_thickness")) < 1e-12, "sleeve_thickness = %g is not supported by the template (must be 0)" % _num(g, "sleeve_thickness"))
    # magnet_lamination is the simulation's axial slice length of the LOSS model (die.yaml); the CAD slices are mag_step / magnet_slices
    sl = _opt(g, "magnet_slices", 1)
    need(abs(sl - round(sl)) < 1e-9 and sl >= 1, "magnet_slices = motor_length / mag_step = %g must be an integer >= 1 (the magnet stack is "
                                                  "motor_length long, slices of mag_step)" % sl)
    nkey = _opt(g, "rotor_key_count", 0)
    need(abs(nkey - round(nkey)) < 1e-9 and nkey >= 0, "rotor_key_count = %g must be an integer >= 0" % nkey)
    # G2 (CILN28 200): the key fillet may have its own row rotor_key_fill_r; then rotor_fill_r is the pole-tip rounding (rotor_tip_fill_r)
    own_key = _has(g, "rotor_key_fill_r")
    key_row = "rotor_key_fill_r" if own_key else "rotor_fill_r"
    if round(nkey) == 0:
        if not (own_key or _has(g, "rotor_tip_fill_r")):
            need(abs(_num(g, "rotor_fill_r")) < 1e-12, "rotor_fill_r = %g needs rotor keys (rotor_key_count > 0): it rounds the key "
                                                       "edges and has no other use (must be 0 without keys)" % _num(g, "rotor_fill_r"))
    else:
        kw, kh, ko, kf = _num(g, "rotor_key_w"), _num(g, "rotor_key_h"), _num(g, "rotor_key_offset"), _num(g, key_row)
        Rb_ = _num(g, "rotor_down_r") - _num(g, "rotor_house_height")
        w2_ = kw / 2 - ko
        need(ko >= 0 and kf > 0 and w2_ > 2 * kf and kh - ko > 2 * kf,
             "rotor key: width/2 - offset (%.3f) and depth - offset (%.3f) must both exceed 2 x %s (%.3f), "
             "rotor_key_offset >= 0" % (w2_, kh - ko, key_row, kf))
        need(Rb_ - kh > 0 and (Rb_ - kf) ** 2 > (w2_ + kf) ** 2, "rotor key deeper than the bore radius or too wide for the bore")
        need(round(nkey) * 2 * math.asin(min(1.0, (w2_ + kf) / Rb_)) < 2 * math.pi,
             "rotor keys overlap each other: reduce rotor_key_count or rotor_key_w")
        need(_num(g, "rotor_down_r") > Rb_ + 0.05, "rotor hub ring thinner than 0.05 mm: the key cannot be fused")
    rt = _opt(g, "rotor_tip_fill_r", 0.0)
    if rt > 1e-12:
        # pole-tip rounding: fillet between the pocket lip (vertical line at x = rotor_up_r * sin(lip angle)) and the outer arc must fit
        # above the pocket top fillet and before the half-pole boundary
        n_ = rotor_numbers(lambda k: _num(g, k))
        xl_ = n_["Rup"] * math.sin(math.radians(n_["h"]))
        ok_geo = (n_["Rup"] - rt) ** 2 > (xl_ + rt) ** 2
        need(ok_geo, "rotor_tip_fill_r = %g does not fit between the magnet pocket lip and the outer arc" % rt)
        if ok_geo:
            cy_ = math.sqrt((n_["Rup"] - rt) ** 2 - (xl_ + rt) ** 2)
            phi_ = math.degrees(math.atan2(xl_ + rt, cy_))
            rfp = n_["mfr"] + n_["off"]
            cf_ = fillet_center_circle_line(n_["Rtop"] - n_["mfr"], n_["K"], n_["U"], n_["mfr"])
            lby_ = cf_[1] + math.sqrt(max(rfp * rfp - (xl_ - cf_[0]) ** 2, 0.0))
            need(phi_ < n_["a"] - 0.05, "rotor_tip_fill_r = %g reaches the half-pole boundary (%.2f deg of %.2f deg)" % (rt, phi_, n_["a"]))
            need(cy_ > lby_ + 0.05, "rotor_tip_fill_r = %g overlaps the magnet pocket top fillet (lip line only %.3f mm long)" % (rt, cy_ - lby_))

    # stator core sector
    R_in, R_out = _num(g, "stator_down_r"), _num(g, "stator_up_r")
    tw2, cw, ct = _num(g, "tooth_width") / 2, _num(g, "coil_w"), _num(g, "core_thickness")
    aw, cut, t2 = _num(g, "angle_wire"), _num(g, "cut_width"), _num(g, "tooth2_width")
    fr, f1 = _num(g, "stator_fillet_r"), _num(g, "stator_fillet_r1")
    s = math.sin(math.radians(aw))
    x_l, x_o = tw2 + cw, tw2 + cw + t2
    y_s = R_out - ct
    need(R_in > 0 and R_out > R_in, "stator_down_r (%.4g) must be > 0 and < stator_up_r (%.4g)" % (R_in, R_out))
    rn = ((R_in + cut) * s - x_o) / (1 - s) if s < 1 else -1
    need(rn > 0.01, "core notch radius = ((stator_down_r + cut_width)*sin(angle_wire) - (tooth_width/2 + coil_w + "
                    "tooth2_width)) / (1 - sin(angle_wire)) = %.3f mm must be > 0: the half leg (outer side x = %.3f mm) "
                    "does not fit in the sector (half width %.3f mm at r = stator_down_r + cut_width); reduce "
                    "tooth_width / coil_w / tooth2_width or increase stator_diameter / reduce num_seg"
                    % (rn, x_o, (R_in + cut) * s))
    need(x_l < R_in * s, "slot outer side x = tooth_width/2 + coil_w = %.3f mm must be inside the sector at "
                         "r = stator_down_r (%.3f mm)" % (x_l, R_in * s))
    need(tw2 > f1 > 0, "stator_fillet_r1 (%.3g) must be > 0 and < tooth_width/2 (%.3g)" % (f1, tw2))
    y_tip = math.sqrt(max(R_in ** 2 - tw2 ** 2, 0))
    need(y_s > y_tip + f1, "slot top (stator_up_r - core_thickness = %.3f) must be above the tooth tip (%.3f)" % (y_s, y_tip + f1))
    need(x_o - fr > 0 and x_o < R_out * s, "outer leg x = %.3f mm must be inside the sector at stator_up_r and > stator_fillet_r" % x_o)
    if rn > 0:
        Ny = (R_in + cut + rn) * math.cos(math.radians(aw))
        need(Ny + rn < y_s, "core notch (top y = %.3f) must stay below the slot top y = %.3f" % (Ny + rn, y_s))

    # winding
    ins, sx, sy = _num(g, "insulation_thickness"), _num(g, "wire_spacing_x"), _num(g, "wire_spacing_y")
    ww, wh, n = _num(g, "wire_width"), _num(g, "wire_height"), round(_num(g, "num_wires_per_slot"))
    x1 = tw2 + ins + sx / 2
    need(x1 + ww <= x_l + 1e-9, "wire outer side x = %.3f mm exceeds the slot (tooth_width/2 + coil_w = %.3f mm)" % (x1 + ww, x_l))
    stack = ins + sy / 2 + n * (wh + sy) - sy
    need(y_s - stack > y_tip, "winding stack (%d layers, %.3f mm) reaches below the tooth tip: inner layer y = %.3f, tooth "
                              "tip y = %.3f" % (n, stack, y_s - stack, y_tip))
    need(wh > 0 and ww > 0, "wire_width and wire_height must be > 0")
    ax = _num(g, "coil_bend_axis_dx")
    need(0 < ax < x1 - 1e-3, "end-turn bend axis x = coil_bend_axis_dx = tooth_width/2 + insulation_thickness + "
                             "wire_spacing_x/2 - wire_beng_r = %.3f mm must be > 0 (bridge 2x wide) and < the leg inner "
                             "side x = %.3f mm (wire_beng_r > 0)" % (ax, x1))

    # rotor / magnets
    Rup, Rdn = _num(g, "rotor_up_r"), _num(g, "rotor_down_r")
    Rhub = Rdn - _num(g, "rotor_house_height")
    a = _num(g, "angle_mag") / 2
    fd = _num(g, "magnet_fill_down") * a
    fu = _num(g, "magnet_fill_up") * a
    h = fu * _num(g, "rotor_hole")
    need(Rup < R_in, "rotor_up_r (%.4g) must be < stator_down_r (%.4g) (air gap)" % (Rup, R_in))
    need(0 < Rhub < Rdn < Rup, "need 0 < rotor_down_r - rotor_house_height (%.4g) < rotor_down_r (%.4g) < rotor_up_r (%.4g)"
                               % (Rhub, Rdn, Rup))
    need(0 < h < fu < fd < a, "magnet angles must satisfy 0 < lip (%.3f) < magnet top (%.3f) < magnet bottom (%.3f) < half "
                              "pole pitch (%.3f) deg (magnet_fill_up, rotor_hole, magnet_fill_down)" % (h, fu, fd, a))
    off, mfr, mdh = _num(g, "offset_rotor"), _num(g, "magnet_fill_radius"), _num(g, "magnet_down_height")
    need(off >= 0 and mfr > 0 and mdh > 0, "offset_rotor >= 0, magnet_fill_radius > 0, magnet_down_height > 0 required")
    need(Rdn + mdh < Rup - _num(g, "magnet_up_gap") - 2 * mfr,
         "magnet_down_height (%.3g) too large for the magnet height (rotor_up_r - rotor_down_r = %.3g)" % (mdh, Rup - Rdn))
    need(_num(g, "mag_step") > 0 and _num(g, "rotor_w") > 0 and _num(g, "motor_length") > 0,
         "motor_length, rotor_w and mag_step must be > 0")
    if frameless:
        return v
    try:
        _num(g, "holder_face_z")
    except Exception:
        return v                                   # profile without the mechanical parts
    v += precheck_mechanics(g)
    return v


def precheck_mechanics(g):
    """Validity domain of the mechanical parts (stage 3): the topology the sketches assume, in words."""
    v = []

    def need(cond, text):
        if not cond:
            v.append(text)

    if _opt(g, "occupancy_variant", None) is not None:                # occupancy variant (CIANO28 150)
        return precheck_occupancy(g)
    carrier = _opt(g, "carrier_bore_r", None) is not None            # ring-carrier variant (CIANO28 85)
    if carrier:
        v += precheck_carrier(g)
    else:
        rb, rj, rs, rsh = _num(g, "shaft_bore_r"), _num(g, "shaft_journal_r"), _num(g, "shaft_seat_r"), _num(g, "shaft_shoulder_r")
        ch, rt, rtip = _num(g, "shaft_chamfer"), _num(g, "shaft_thread_r"), _num(g, "shaft_tip_r")
        rfl = rs + _num(g, "shaft_flange_step")
        need(0 < rb < rj - ch, "shaft bore radius (shaft_seat_r - shaft_height = %.3f) must be > 0 and < journal radius - chamfer "
                               "(%.3f): the shaft wall vanishes" % (rb, rj - ch))
        need(rj < rsh < rfl, "shaft shoulder radius %.3f must lie between the journal (%.3f) and the flange (%.3f)" % (rsh, rj, rfl))
        need(rj + ch < rs, "bearing bore / 2 (%.3f) + chamfer must be < the rotor seat radius (rotor_down_r - rotor_house_height = "
                           "%.3f)" % (rj, rs))
        need(rtip <= rt < rj - ch, "thread radius %.3f must be >= tip radius %.3f and < journal radius - chamfer" % (rt, rtip))
        need(_num(g, "shaft_flat_w") / 2 < rj, "drive flats (shaft_flat_w / 2 = %.3f) must cut the journal (r %.3f)"
             % (_num(g, "shaft_flat_w") / 2, rj))
        zj = _num(g, "shaft_journal_end_z")
        zbore = zj - rb / math.tan(math.radians(_num(g, "shaft_bore_tip_angle")))
        need(zbore > _num(g, "holder_face_z"), "the bore tip cone (bore ends at z %.3f) must end in front of the front bearing (z %.3f)"
             % (zbore, _num(g, "holder_face_z")))
        need(_num(g, "shaft_flat_len") < zj - _num(g, "front_holder_end_z"),
             "drive flats (%.3f long) must stay in front of the front holder (journal end %.3f, holder end %.3f)"
             % (_num(g, "shaft_flat_len"), zj, _num(g, "front_holder_end_z")))
    d, D, B = _num(g, "bearing_d"), _num(g, "bearing_D"), _num(g, "bearing_B")
    li, lo, bd = _num(g, "bearing_inner_land_d") / 2, _num(g, "bearing_outer_land_d") / 2, _num(g, "bearing_ball_d") / 2
    pr, e = (d + D) / 4, _num(g, "bearing_edge_r")
    need(d / 2 + e < li < pr < lo < D / 2 - e, "bearing radii out of order: bore %.3f < inner land %.3f < pitch %.3f < outer "
                                               "land %.3f < outer %.3f" % (d / 2, li, pr, lo, D / 2))
    need(pr - li < bd and lo - pr < bd and bd < B / 2, "ball (d %.3f) must cut both lands and fit the width B %.3f" % (2 * bd, B))
    n = _num(g, "bearing_ball_count")
    need(abs(n - round(n)) < 1e-9 and n >= 3 and 2 * pr * math.sin(math.pi / max(n, 1)) > 2 * bd,
         "bearing_ball_count = %g: must be an integer >= 3 and the balls must not overlap on the pitch circle" % n)
    Ri, Rh, R8 = _num(g, "holder_inner_r"), _num(g, "holder_hub_r"), _num(g, "bearing_od_r")
    aw = _num(g, "angle_wire")
    sa = _opt(g, "holder_spoke_angle", aw)                          # window axis -> spoke axis (= angle_wire on the CIANO14 40)
    w, bolt_r, hb = _num(g, "bolt_head_d") / 2, _num(g, "holder_bolt_r"), _num(g, "bolt_d") / 2
    rf = _num(g, "holder_window_r")
    need(R8 < Rh < Ri, "holder radii: bearing seat %.3f < hub %.3f < flange %.3f" % (R8, Rh, Ri))
    need(_num(g, "holder_lip_r") < R8, "holder lip radius must be < the bearing seat radius")
    need((Rh + rf) * math.sin(math.radians(sa)) > w + rf,
         "the window fillets do not fit between the spokes: spokes too wide (bolt_head_d) or angle_wire too small")
    need(math.sqrt(max(Ri * Ri - w * w, 0.0)) < bolt_r and bolt_r - 3 * w < Ri,
         "bolt circle %.3f: the boss (head circle radius %.3f) must reach the flange radius %.3f (tangent spoke lines from the flange "
         "arc to the head circle)" % (bolt_r, w, Ri))
    leg = bolt_r * math.sin(math.radians(aw)) - _num(g, "core_leg_outer_x")
    need(max(hb, _num(g, "holder_tap_d") / 2) < leg < w,
         "seat: the bolt axis is %.3f mm from the core leg face; it must be more than the hole radius and less than "
         "bolt_head_d / 2 (%.3f) - change holder_bolt_r or the core leg (tooth_width / coil_w / tooth2_width)" % (leg, w))
    need(_num(g, "holder_seat_depth") > _num(g, "holder_chamfer") > 0, "holder_seat_depth must exceed holder_chamfer > 0")
    Ro, ff = _num(g, "back_holder_outer_r"), _num(g, "back_holder_fillet_r")
    need(R8 < Ro < Ri and 0 < ff < w, "back holder: seat %.3f < outer %.3f < flange %.3f and 0 < fillet %.3f < bolt_head_d/2"
         % (R8, Ro, Ri, ff))
    rm, dm = _num(g, "back_screw_r"), _num(g, "back_screw_tap_d") / 2
    need(R8 < rm - dm and rm + dm < Ro and dm < w, "M3 holes (r %.3f, d %.3f) must lie in the back holder spokes (between the "
                                                   "bearing seat r %.3f and the outer r %.3f)" % (rm, 2 * dm, R8, Ro))
    need(_num(g, "ring_inner_r") > _num(g, "stator_down_r"), "GFRP ring inner radius %.3f must be > the stator bore %.3f"
         % (_num(g, "ring_inner_r"), _num(g, "stator_down_r")))
    need(_num(g, "holder_screw_l") > _num(g, "holder_flange_z") + _num(g, "motor_length") / 2,
         "holder screw (%.3f) must reach the back bosses" % _num(g, "holder_screw_l"))
    l_req, l_max = _opt(g, "holder_screw_req", None), _opt(g, "holder_screw_max", None)   # the screw follows the stack (2026-10-15)
    if l_req is not None and l_max is not None:                                         # older profiles have no such rows: no check
        l_sel = _num(g, "holder_screw_l")
        need(l_req - 1e-6 <= l_sel <= l_max + 1e-6,
             "no stock screw length fits the holder stack: it needs at least %.3f mm under the head (front flange face to the back holder "
             "face), rounded up to an even mm = %.3f mm, but the tip may not go beyond %.3f mm (back flange outer face less the tip "
             "margin) - change the stack length (motor_length) or the holder flange / rib thickness" % (l_req, l_sel, l_max))
    if not carrier:
        need(_num(g, "prop_fix_d") / 2 > rj + _num(g, "prop_fix_relief_r"), "Prop_fix diameter too small for the hole")
    return v


OCC_PREFIXES = ("rotor_holder1", "rotor_holder2", "stator_holder", "stator_holder_back")
SCREW_PREFIXES = ("screwA", "screwB", "screwC")


def precheck_occupancy(g):
    """Occupancy variant (CIANO28 150): rings of the four aluminium parts, the bearing and the three screw families."""
    v = []

    def need(cond, text):
        if not cond:
            v.append(text)

    for pre in OCC_PREFIXES:
        n = _num(g, pre + "_pattern_n")
        need(abs(n - round(n)) < 1e-9 and n >= 1, "%s: pattern_n must be a positive integer" % pre)
        k = 0
        while _opt(g, "%s_r0_%d" % (pre, k + 1), None) is not None:
            k += 1
            r0, r1, z0, z1 = (_num(g, "%s_%s_%d" % (pre, x, k)) for x in ("r0", "r1", "z0", "z1"))
            f = _opt(g, "%s_fill_%d" % (pre, k), 1.0)
            need(0 <= r0 < r1 and z1 > z0 and 0 < f <= 1.0 + 1e-9,
                 "%s ring %d: needs 0 <= r0 (%.4g) < r1 (%.4g), z1 (%.4g) > z0 (%.4g) and 0 < fill (%.4g) <= 1" % (pre, k, r0, r1, z1, z0, f))
        need(k >= 1, "%s: no occupancy ring rows (%s_r0_1 ...)" % (pre, pre))
    d, D, B = _num(g, "bearing_d"), _num(g, "bearing_D"), _num(g, "bearing_B")
    li, lo, bd = _num(g, "bearing_inner_land_d") / 2, _num(g, "bearing_outer_land_d") / 2, _num(g, "bearing_ball_d") / 2
    pr, e = (d + D) / 4, _num(g, "bearing_edge_r")
    need(d / 2 + e < li < pr < lo < D / 2 - e, "bearing radii out of order: bore %.3f < inner land %.3f < pitch %.3f < outer "
                                               "land %.3f < outer %.3f" % (d / 2, li, pr, lo, D / 2))
    need(pr - li < bd and lo - pr < bd and bd < B / 2, "ball (d %.3f) must cut both lands and fit the width B %.3f" % (2 * bd, B))
    n = _num(g, "bearing_ball_count")
    need(abs(n - round(n)) < 1e-9 and n >= 3 and 2 * pr * math.sin(math.pi / max(n, 1)) > 2 * bd,
         "bearing_ball_count = %g: must be an integer >= 3 and the balls must not overlap on the pitch circle" % n)
    for pre in SCREW_PREFIXES:
        if _opt(g, pre + "_bolt_r", None) is None:
            continue
        rs, rh, af2, dep = (_num(g, pre + "_shank_d") / 2, _num(g, pre + "_head_d") / 2, _num(g, pre + "_socket_af") / 2,
                            _num(g, pre + "_socket_depth"))
        cnt = _num(g, pre + "_count")
        need(0 < rs < rh and 0 < af2 / math.cos(math.radians(30)) < rh and 0 < dep < _num(g, pre + "_head_h") + 0.5 * _num(g, pre + "_length"),
             "%s: shank r %.3f < head r %.3f, the hex socket circle must fit the head and the depth be positive" % (pre, rs, rh))
        need(_num(g, pre + "_length") > 0 and abs(cnt - round(cnt)) < 1e-9 and cnt >= 1, "%s: length > 0 and an integer count >= 1" % pre)
        need(_num(g, pre + "_tip_chamfer") < rs and _num(g, pre + "_head_fillet") < _num(g, pre + "_head_h"),
             "%s: tip chamfer / head fillet too large" % pre)
    return v


def precheck_carrier(g):
    """Ring carrier (Shaft + Shaft_rear, CIANO28 85): the rectilinear half section must keep its order, the rim tab / slot and the
    hub holes must fit."""
    v = []

    def need(cond, text):
        if not cond:
            v.append(text)

    bore, floor, jr, shr, fl = (_num(g, n) for n in ("carrier_bore_r", "carrier_floor_r", "shaft_journal_r", "shaft_shoulder_r",
                                                      "carrier_flange_r"))
    rin, seat = _num(g, "carrier_rim_in_r"), _num(g, "shaft_seat_r")
    need(0 < bore < floor < jr < shr < rin < seat < fl,
         "carrier radii out of order: bore %.3f < hub floor %.3f < journal %.3f < shoulder %.3f < rim inner %.3f < rotor seat %.3f < "
         "flange %.3f" % (bore, floor, jr, shr, rin, seat, fl))
    zr, zfl, zweb, zrim = (_num(g, n) for n in ("rotor_w", "carrier_flange_front_z", "carrier_web_z", "carrier_rim_end_z"))
    need(zrim < zweb < zfl, "carrier axial order: rim end %.3f < web %.3f < flange face %.3f (rotor face %.3f)" % (zrim, zweb, zfl, zr / 2))
    need(_num(g, "holder_face_z") > zfl, "bearing inner face z %.3f must be beyond the carrier flange face %.3f" % (
        _num(g, "holder_face_z"), zfl))
    nk = _opt(g, "rotor_key_count", 0)
    kw = _num(g, "rotor_key_w") / 2
    rc, rf = _num(g, "carrier_tab_cap_r"), _num(g, "carrier_tab_fillet_r")
    need(nk >= 1, "the carrier key tabs need rotor_key_count >= 1")
    need(rc > kw > 0 and rf > 0 and rin - rf > 0,
         "carrier tab: cap radius %.3f must exceed the slot half width %.3f; flank radius %.3f must be > 0" % (rc, kw, rf))
    pcd, hd = _num(g, "carrier_hole_pcd_r"), _num(g, "carrier_hole_d") / 2
    need(bore + hd < pcd < jr - hd, "carrier hub holes (pitch radius %.3f, r %.3f) must lie between the bore %.3f and the journal %.3f"
         % (pcd, hd, bore, jr))
    need(_num(g, "shaft_journal_end_z") > zfl, "hub end z %.3f must be beyond the flange face %.3f" % (_num(g, "shaft_journal_end_z"), zfl))
    return v


def expected_counts(g):
    ns = int(round(_num(g, "num_seg")))
    out = dict(core_sectors=ns * 3, coils=ns * 3,
               magnets=ns * int(round(_num(g, "num_poles_per_segment"))) * int(round(_opt(g, "magnet_slices", 1))),
               layers=int(round(_num(g, "num_wires_per_slot"))))
    try:
        out.update(screws=int(round(_opt(g, "holder_bolt_count", ns * 3))), balls=int(round(_num(g, "bearing_ball_count"))))
    except Exception:
        pass
    return out


def _bb(shape):
    try:
        return shape.optimalBoundingBox(False, False)   # exact: never the display mesh (GUI)
    except Exception:
        return shape.BoundBox


def gate(doc, sheet_name="Params"):
    """Mandatory health gate.  Returns dict(ok, errors, counts, volumes)."""
    import FreeCAD as App
    from .params_sheet import pval
    sh = doc.getObject(sheet_name)
    g = lambda n: pval(sh, n)
    errors = []
    from . import frameless
    kit = frameless.is_frameless(doc)
    pre = precheck(g, frameless=kit)
    errors += ["precheck: " + t for t in pre]
    for o in doc.Objects:
        st = list(getattr(o, "State", []))
        if "Invalid" in st or "Error" in st or (hasattr(o, "isValid") and not o.isValid()):
            errors.append("object %s (%s) failed: state %s" % (o.Label, o.TypeId, ",".join(st)))
        elif "Touched" in st:
            errors.append("object %s not recomputed (Touched)" % o.Label)
        if o.TypeId == "Sketcher::SketchObject":
            rc = o.solve()
            if rc != 0:
                errors.append("sketch %s: solver failed (rc=%d) - check its constraints / the Params they use" % (o.Label, rc))
            if not o.FullyConstrained:
                errors.append("sketch %s lost full constraint" % o.Label)
            for k in ("ConflictingConstraints", "RedundantConstraints", "PartiallyRedundantConstraints", "MalformedConstraints"):
                if list(getattr(o, k, None) or []):
                    errors.append("sketch %s: %s %s" % (o.Label, k, list(getattr(o, k))))
    vols = {}
    shapes = {}
    warnings = []
    # A.7.8: an active part that is the Fusion body (profile.fusion_parts) is judged by its Fusion volume; where it disagrees with the Params
    # rows the gate WARNS (Fusion is the design truth, the rows feed the EM solver) instead of refusing
    fusion_active = {bn for bn in ("Core", "Coil", "RotorCore", "Magnet") if doc.getObject(bn + "_Fusion") is not None}
    for bn in ("Core", "Coil", "RotorCore", "Magnet"):
        b = doc.getObject(bn)
        if b is None:
            errors.append("body %s missing" % bn)
            continue
        s = b.Shape
        if s.isNull() or not _fusion_valid(doc, bn, s) or len(s.Solids) != 1 or s.Volume <= 0:
            errors.append("body %s: null/invalid/%d solids/volume<=0" % (bn, 0 if s.isNull() else len(s.Solids)))
            continue
        if bn in fusion_active:
            errors += _fusion_volume_errors(doc, bn)
        vols[bn] = s.Volume
        shapes[bn] = s
    exp = expected_counts(g)
    counts = {}
    for label, key in (("Coils", "coils"), ("MagnetArray", "magnets")):
        arr = [o for o in doc.Objects if o.Label == label]
        n = len(arr[0].Shape.Solids) if arr else 0
        counts[key] = n
        if n != exp[key]:
            errors.append("array %s has %d solids, Params require %d" % (label, n, exp[key]))
        if arr:
            vols[label] = arr[0].Shape.Volume
    if kit:
        errors += frameless.gate(doc, g)
    elif doc.getObject("Mechanics") is not None:
        errors += _gate_mechanics(doc, g, exp, counts, vols, warnings)
    if doc.getObject("Electronics") is not None:
        from . import electronics
        errors += electronics.gate(doc, g)
    from . import fusion_extras
    errors += fusion_extras.gate(doc)
    # stale-geometry invariants (a failed feature keeps its previous shape) - always evaluated
    try:
        L = g("motor_length")
        Lc = L + g("coil_straight_extra")
        inv = []
        if "Core" in shapes:
            b = _bb(shapes["Core"])
            inv += [("Core z length = motor_length", b.ZLength, L), ("Core outer radius = stator_up_r", b.YMax, g("stator_up_r"))]
            sk = doc.getObject("CoreSectorSketch")
            try:
                if "Core" in fusion_active:                     # the Fusion core has no template sketch: z length and radius judge it
                    raise _Skip()
                import Part
                area = Part.Face(Part.Wire(Part.__sortEdges__([e for e in sk.Shape.Edges]))).Area
                inv.append(("Core volume = sector area x motor_length x num_seg*3", shapes["Core"].Volume,
                            area * L * exp["core_sectors"]))
            except _Skip:
                pass
            except Exception as e:
                errors.append("Core sector sketch area not computable: %s" % e)
        if "RotorCore" in shapes:
            s = shapes["RotorCore"]
            inv.append(("Rotor z length = rotor_w", _bb(s).ZLength, g("rotor_w")))
            a = math.radians(90 - g("angle_mag") / 2)
            Rup, Rhub = g("rotor_up_r"), g("rotor_down_r") - g("rotor_house_height")
            p_in = App.Vector((Rup - 0.01) * math.cos(a), (Rup - 0.01) * math.sin(a), 0)
            p_out = App.Vector((Rup + 0.01) * math.cos(a), (Rup + 0.01) * math.sin(a), 0)
            ab = math.radians(90 - g("angle_mag") / 2)          # at the pole boundary: a key (pole axis) is not in the way
            h_in = App.Vector((Rhub + 0.01) * math.cos(ab), (Rhub + 0.01) * math.sin(ab), 0)
            h_out = App.Vector((Rhub - 0.01) * math.cos(ab), (Rhub - 0.01) * math.sin(ab), 0)
            rot_err = warnings if "RotorCore" in fusion_active else errors
            if not (s.isInside(p_in, 1e-6, True) and not s.isInside(p_out, 1e-6, True)):
                rot_err.append("stale/incorrect Rotor: outer radius is not rotor_up_r = %.4f" % Rup)
            if not (s.isInside(h_in, 1e-6, True) and not s.isInside(h_out, 1e-6, True)):
                rot_err.append("stale/incorrect Rotor: bore radius is not rotor_down_r - rotor_house_height = %.4f" % Rhub)
            if _opt(g, "rotor_key_count", 0) > 0:                    # keys: tip at bore radius - rotor_key_h + rotor_key_offset
                yt = Rhub - g("rotor_key_h") + g("rotor_key_offset")
                if not (s.isInside(App.Vector(0, yt + 0.01, 0), 1e-6, True) and not s.isInside(App.Vector(0, yt - 0.01, 0), 1e-6, True)):
                    rot_err.append("stale/incorrect Rotor key: tip is not at bore radius - rotor_key_h + rotor_key_offset = %.4f" % yt)
        if "Magnet" in shapes:
            b = _bb(shapes["Magnet"])
            inv += [("Magnet z length = mag_step", b.ZLength, g("mag_step")),
                    ("Magnet top = rotor_up_r - magnet_up_gap", b.YMax, g("rotor_up_r") - g("magnet_up_gap")),
                    ("Magnet bottom = rotor_down_r*cos(fill_down)", b.YMin,
                     g("rotor_down_r") * math.cos(math.radians(g("magnet_fill_down") * g("angle_mag") / 2)))]
        if "Coil" in shapes:
            b = _bb(shapes["Coil"])
            pitch = g("wire_height") + g("wire_spacing_y")
            ytop = g("stator_up_r") - g("core_thickness") - g("insulation_thickness") - g("wire_spacing_y") / 2
            inv += [("Coil top y", b.YMax, ytop),
                    ("Coil bottom y (num_wires_per_slot layers)", b.YMin, ytop - g("wire_height") - (exp["layers"] - 1) * pitch),
                    ("Coil lead end z", b.ZMin, -(Lc / 2 + g("coil_lead_ext") + g("coil_lead_len"))),
                    ("Coil end-turn top z = (L+extra)/2 + wire_beng_r + wire_width", b.ZMax,
                     Lc / 2 + g("wire_beng_r") + g("wire_width")),
                    ("Coil half width x", b.XMax, g("tooth_width") / 2 + g("insulation_thickness") + g("wire_spacing_x") / 2 + g("wire_width"))]
        for name, got, want in inv:
            tol = max(TOL, 1e-7 * abs(want)) if "volume" not in name else 1e-6 * abs(want)
            if name.startswith("Coil") and (" y" in name or " x" in name):
                tol = 1e-3   # the -z transition loft (B-spline through 7 sections) overshoots its layer by < 1 um
            if abs(got - want) > tol:
                part = "RotorCore" if name.startswith("Rotor") else name.split(" ")[0]
                if part in fusion_active:                       # A.7.8: the Fusion body disagrees with the Params rows: warned, not refused
                    warnings.append("Fusion body vs Params: %s: Fusion %.6f, Params %.6f" % (name, got, want))
                else:
                    errors.append("stale/incorrect geometry: %s: model %.6f, Params require %.6f" % (name, got, want))
    except Exception as e:
        errors.append("geometry invariants not evaluable: %s" % e)
    return dict(ok=not errors, errors=errors, counts=dict(counts, core_sectors=exp["core_sectors"]), volumes=vols,
                precheck_violations=pre, fusion_active=sorted(fusion_active), fusion_warnings=warnings)


class _Skip(Exception):
    pass


MECH_BODIES = ("Shaft", "Stator_holder", "Stator_holder_back", "Ring", "Prop_fix", "Bearing_outer_ring", "Bearing_inner_ring",
               "Bearing_ball", "Screw")
MECH_BODIES_CARRIER = ("Shaft", "Shaft_rear", "Stator_holder", "Stator_holder_back", "Ring", "Bearing_outer_ring",
                       "Bearing_inner_ring", "Bearing_seal", "Bearing_ball", "Screw")


def _fusion_valid(doc, bn, s):
    """A part is a valid shape; a Fusion body (A.7.6 / A.7.7) may be exactly as invalid as its STEP (tolerance-level wire errors of Fusion's B-rep)."""
    if s.isNull():
        return False
    fus = doc.getObject(bn + "_Fusion")
    return s.isValid() or (fus is not None and not fus.SourceValid and all(x.isClosed() for x in s.Shells))


def _fusion_volume_errors(doc, bn):
    """The imported Fusion solid must have the Fusion body volume (at this motor_length; wrong or stale STEP otherwise)."""
    fus = doc.getObject(bn + "_Fusion")
    if fus is None or not fus.FusionBodyVolume > 0:
        return []
    if abs(fus.SourceVolume - fus.FusionBodyVolume) <= 1e-3 * fus.FusionBodyVolume:
        return []
    return ["%s: imported Fusion solid %.3f mm3 differs from the Fusion body volume %.3f mm3 by more than 0.1 %% (wrong or stale STEP)" % (
        bn, fus.SourceVolume, fus.FusionBodyVolume)]


def _gate_mechanics(doc, g, exp, counts, vols, warnings=None):
    """Mechanical parts: valid single solids, array counts, links, and stale-geometry invariants (extents that the
    current Params demand)."""
    from . import mechanics
    errors = []
    carrier = doc.getObject("Shaft_rear") is not None
    if _opt(g, "occupancy_variant", None) is not None:
        return _gate_occupancy(doc, g, exp, counts, vols, warnings)
    for bn in (MECH_BODIES_CARRIER if carrier else MECH_BODIES):
        b = doc.getObject(bn)
        if b is None:
            errors.append("body %s missing" % bn)
            continue
        s = b.Shape
        if s.isNull() or not _fusion_valid(doc, bn, s) or len(s.Solids) != 1 or s.Volume <= 0:
            errors.append("body %s: null/invalid/%d solids/volume<=0" % (bn, 0 if s.isNull() else len(s.Solids)))
            continue
        errors += _fusion_volume_errors(doc, bn)
        vols[bn] = s.Volume
    for label, key in (("Screws", "screws"), ("Balls", "balls")):
        arr = [o for o in doc.Objects if o.Label == label]
        n = len(arr[0].Shape.Solids) if arr else 0
        counts[key] = n
        if n != exp.get(key):
            errors.append("array %s has %d solids, Params require %s" % (label, n, exp.get(key)))
    for ln in ("Bearing_618_8_back", "Ring_back") + (("Bearing_seal_2",) if carrier else ()):
        lk = doc.getObject(ln)
        if lk is None or lk.LinkedObject is None:
            errors.append("link %s missing or empty" % ln)
    if errors:
        return errors
    so = mechanics.solids(doc)
    zf, zfl, zb = g("holder_face_z"), g("holder_flange_z"), g("bearing_out_z")
    if carrier:
        inv = [("Shaft rim end z = carrier_rim_end_z", _bb(so["Shaft"]).ZMin, g("carrier_rim_end_z")),
               ("Shaft hub end z = shaft_journal_end_z", _bb(so["Shaft"]).ZMax, g("shaft_journal_end_z")),
               ("Shaft flange radius", _bb(so["Shaft"]).XMax, g("carrier_flange_r")),
               ("Shaft_rear hub end z = -bearing_out_z", _bb(so["Shaft_rear"]).ZMin, -zb),
               ("Shaft_rear rim end z = -carrier_rim_end_z", _bb(so["Shaft_rear"]).ZMax, -g("carrier_rim_end_z")),
               ("Shaft_rear flange radius", _bb(so["Shaft_rear"]).XMax, g("carrier_flange_r"))]
    else:
        inv = [("Shaft back end z = -bearing_out_z", _bb(so["Shaft"]).ZMin, -zb),
               ("Shaft front end z = shaft_front_end_z", _bb(so["Shaft"]).ZMax, g("shaft_front_end_z")),
               ("Prop_fix z min", _bb(so["Prop_fix"]).ZMin, g("shaft_journal_end_z") - g("prop_fix_hub_t")),
               ("Prop_fix z max", _bb(so["Prop_fix"]).ZMax, g("shaft_journal_end_z") + g("prop_fix_plate_t"))]
        if doc.getObject("Shaft_Fusion") is None:               # the flange of a Fusion shaft is Fusion's (dia 13.9), not the template row; the z ends above still hold
            inv.insert(2, ("Shaft flange radius", _bb(so["Shaft"]).XMax, g("shaft_seat_r") + g("shaft_flange_step")))
    inv += [("front bearing inner face z", _bb(so["Bearing_618_8_front_outer_ring"]).ZMin, zf),
            ("front bearing outer face z", _bb(so["Bearing_618_8_front_outer_ring"]).ZMax, zb),
            ("back bearing inner face z", _bb(so["Bearing_618_8_back_outer_ring"]).ZMax, -zf),
            ("bearing outer radius", _bb(so["Bearing_618_8_front_outer_ring"]).XMax, g("bearing_D") / 2),
            ("Stator_holder z min = seat bottom", _bb(so["Stator_holder"]).ZMin, g("holder_seat_z")),
            ("Stator_holder z max = front_holder_end_z", _bb(so["Stator_holder"]).ZMax, g("front_holder_end_z")),
            ("Stator_holder_back z min = A_H = -back_holder_end_z", _bb(so["Stator_holder_back"]).ZMin, -g("back_holder_end_z")),
            ("Stator_holder_back z max = -seat bottom", _bb(so["Stator_holder_back"]).ZMax, -g("holder_seat_z")),
            ("Ring_front z", _bb(so["Ring_front"]).ZMin, g("motor_length") / 2),
            ("Ring_back z", _bb(so["Ring_back"]).ZMax, -g("motor_length") / 2),
            ("Screw head top z", _bb(so["Screw_1"]).ZMax, g("screw_head_top_z")),
            ("Screw tip z", _bb(so["Screw_1"]).ZMin, zfl - g("holder_screw_l"))]
    for name, got, want in inv:
        if abs(got - want) > max(TOL, 1e-7 * abs(want)):
            errors.append("stale/incorrect geometry: %s: model %.6f, Params require %.6f" % (name, got, want))
    # every patterned feature present (a tool that stops cutting after an edit keeps the body valid but wrong)
    n = int(round(g("holder_bolt_count")))
    if carrier:        # CIANO28 85 counts: window fillets + rib fillets (4 per spoke), one chamfer per spoke
        want = {("Stator_holder", "chamfer45"): n, ("Stator_holder", g("holder_window_r")): 4 * n,
                ("Stator_holder_back", "chamfer45"): n, ("Stator_holder_back", g("holder_window_r")): 4 * n,
                ("Stator_holder_back", g("back_holder_fillet_r")): 2 * n, ("Stator_holder_back", g("back_screw_tap_d") / 2): n,
                ("Stator_holder", g("bolt_d") / 2): n, ("Stator_holder_back", g("holder_tap_d") / 2): n}
    else:
        want = {("Stator_holder", "chamfer45"): 2 * n, ("Stator_holder", g("holder_window_r")): 2 * n,
                ("Stator_holder_back", "chamfer45"): 2 * n, ("Stator_holder_back", g("holder_window_r")): 2 * n,
                ("Stator_holder_back", g("back_holder_fillet_r")): 2 * n, ("Stator_holder_back", g("back_screw_tap_d") / 2): n,
                ("Stator_holder", g("bolt_d") / 2): n, ("Stator_holder_back", g("holder_tap_d") / 2): n}
    for (body, kind), cnt in want.items():
        if doc.getObject(body + "_Fusion") is not None:         # a Fusion holder has Fusion's features, not the template's counts
            continue
        got = _feature_count(so[body], kind)
        if got != cnt:
            errors.append("incomplete %s: %d %s faces, Params require %d" % (
                body, got, "45-deg chamfer" if kind == "chamfer45" else "r = %.4g cylinder" % kind, cnt))
    return errors


MECH_BODIES_OCC = ("Shaft", "Shaft_rear", "Stator_holder", "Stator_holder_back", "Bearing_outer_ring", "Bearing_inner_ring",
                   "Bearing_seal", "Bearing_ball", "Screw", "Screw_M3x30", "Screw_M3x6")
OCC_BODY = {"rotor_holder1": "Shaft", "rotor_holder2": "Shaft_rear", "stator_holder": "Stator_holder", "stator_holder_back": "Stator_holder_back"}
SCREW_BODY = {"screwA": ("Screw", "Screws", "Screw_1"), "screwB": ("Screw_M3x30", "Screws_M3x30", "Screw_M3x30_1"),
              "screwC": ("Screw_M3x6", "Screws_M3x6", "Screw_M3x6_1")}


def _gate_occupancy(doc, g, exp, counts, vols, warnings=None):
    """Mechanics of the occupancy variant (CIANO28 150): single valid solids, array counts, links, stale-geometry invariants.
    Each aluminium part has the volume of its ring table (sum of fill * pi * (r1^2 - r0^2) * dz) and its z extent."""
    from . import mechanics
    from .mechanics_occupancy import ring_table
    errors = []
    sh = doc.getObject("Params")
    for bn in MECH_BODIES_OCC:
        b = doc.getObject(bn)
        if b is None:
            errors.append("body %s missing" % bn)
            continue
        s = b.Shape
        if s.isNull() or not _fusion_valid(doc, bn, s) or len(s.Solids) != 1 or s.Volume <= 0:
            errors.append("body %s: null/invalid/%d solids/volume<=0" % (bn, 0 if s.isNull() else len(s.Solids)))
            continue
        if doc.getObject(bn + "_Fusion") is not None and bn not in OCC_BODY.values():    # A.7.8: bearings and screws from Fusion
            errors += _fusion_volume_errors(doc, bn)
        vols[bn] = s.Volume
    want_arr = [("Balls", "balls", int(round(g("bearing_ball_count"))))]
    for pre, (_, lbl, _k) in SCREW_BODY.items():
        if _opt(g, pre + "_count", None) is not None:
            want_arr.append((lbl, lbl.lower(), int(round(g(pre + "_count")))))
    for label, key, want in want_arr:
        arr = [o for o in doc.Objects if o.Label == label]
        n = len(arr[0].Shape.Solids) if arr else 0
        counts[key] = n
        if n != want:
            errors.append("array %s has %d solids, Params require %s" % (label, n, want))
    for ln in ("Bearing_618_8_back", "Bearing_seal_2"):
        lk = doc.getObject(ln)
        if lk is None or lk.LinkedObject is None:
            errors.append("link %s missing or empty" % ln)
    if errors:
        return errors
    so = mechanics.solids(doc)
    zf, zb, zbk = g("holder_face_z"), g("holder_face_z") + g("bearing_B"), g("bearing_back_z0")
    inv = []
    for pre, bn in OCC_BODY.items():
        rings = ring_table(sh, pre)
        vol = sum(r["fill"] * math.pi * (r["r1"] ** 2 - r["r0"] ** 2) * (r["z1"] - r["z0"]) for r in rings)
        inv += [("%s z min = lowest ring z0" % bn, _bb(so[bn]).ZMin, min(r["z0"] for r in rings)),
                ("%s z max = highest ring z1" % bn, _bb(so[bn]).ZMax, max(r["z1"] for r in rings))]
        fus = doc.getObject(bn + "_Fusion")
        if fus is None:
            inv.append(("%s volume = sum of its occupancy rings" % bn, so[bn].Volume, vol))
        else:                                                         # the real Fusion body (A.7.6): the volume of the Fusion body, one solid
            inv.append(("%s volume = volume of the imported solid" % bn, so[bn].Volume, fus.SourceVolume))
            if fus.FusionBodyVolume > 0:
                errors += [] if abs(fus.SourceVolume - fus.FusionBodyVolume) <= 1e-3 * fus.FusionBodyVolume else [
                    "%s: imported Fusion solid %.3f mm3 differs from the Fusion body volume %.3f mm3 by more than 0.1 %% (wrong or stale STEP)" % (
                        bn, fus.SourceVolume, fus.FusionBodyVolume)]
    inv += [("front bearing inner face z", _bb(so["Bearing_618_8_front_outer_ring"]).ZMin, zf),
            ("front bearing outer face z", _bb(so["Bearing_618_8_front_outer_ring"]).ZMax, zb),
            ("back bearing inner face z", _bb(so["Bearing_618_8_back_outer_ring"]).ZMax, zbk + g("bearing_B")),
            ("back bearing outer face z", _bb(so["Bearing_618_8_back_outer_ring"]).ZMin, zbk),
            ("bearing outer radius", _bb(so["Bearing_618_8_front_outer_ring"]).XMax, g("bearing_D") / 2)]
    for pre, (bn, lbl, key) in SCREW_BODY.items():
        if _opt(g, pre + "_count", None) is None:
            continue
        top, tip = g(pre + "_head_top_z"), g(pre + "_flange_z") - g(pre + "_length")
        if g(pre + "_flip") > 0.5:
            top, tip = -tip, -top                                     # head down: local +z = global -z
        inv += [("%s head top z" % bn, _bb(so[key]).ZMax, top), ("%s tip z" % bn, _bb(so[key]).ZMin, tip)]
    fus_body = {"bearing": "Bearing_outer_ring"}
    for name, got, want in inv:
        tol = 1e-6 * abs(want) if "volume" in name else max(TOL, 1e-7 * abs(want))
        if abs(got - want) > tol:
            owner = fus_body["bearing"] if "bearing" in name else name.split(" ")[0]
            if warnings is not None and doc.getObject(owner + "_Fusion") is not None and owner not in OCC_BODY.values():
                warnings.append("Fusion body vs Params: %s: Fusion %.6f, Params %.6f" % (name, got, want))     # A.7.8: warned, not refused
            else:
                errors.append("stale/incorrect geometry: %s: model %.6f, Params require %.6f" % (name, got, want))
    return errors


def _feature_count(shape, kind):
    """Faces of a feature kind: 'chamfer45' = planar faces at 45 deg to the axis; a number = cylinders of that radius."""
    k = 0
    for f in shape.Faces:
        sf = f.Surface
        if kind == "chamfer45":
            if sf.__class__.__name__ == "Plane" and abs(abs(sf.Axis.z) - math.sqrt(0.5)) < 1e-6:
                k += 1
        elif sf.__class__.__name__ == "Cylinder" and abs(sf.Radius - kind) < 1e-6:
            k += 1
    return k


# pairs checked by fit(): (a, b, kind) - kind "contact" (must touch, must not overlap), "clear" (must not overlap),
# "thread" (screw in its tapped hole: the overlap is the engaged thread, reported, not refused)
FIT_PAIRS = [
    ("Shaft", "RotorCore", "contact"), ("Shaft", "Bearing_618_8_front_inner_ring", "contact"),
    ("Shaft", "Bearing_618_8_back_inner_ring", "contact"), ("Bearing_618_8_front_outer_ring", "Stator_holder", "contact"),
    ("Bearing_618_8_back_outer_ring", "Stator_holder_back", "contact"), ("Stator_holder", "Core", "contact"),
    ("Stator_holder_back", "Core", "contact"), ("Ring_front", "Core", "contact"), ("Ring_back", "Core", "contact"),
    ("Prop_fix", "Shaft", "contact"), ("Stator_holder", "Coils", "clear"), ("Stator_holder_back", "Coils", "clear"),
    ("Ring_front", "Coils", "clear"), ("Ring_back", "Coils", "clear"), ("Stator_holder", "Ring_front", "clear"),
    ("Stator_holder_back", "Ring_back", "clear"), ("RotorCore", "Stator_holder", "clear"),
    ("RotorCore", "Stator_holder_back", "clear"), ("MagnetArray", "Stator_holder", "clear"),
    ("MagnetArray", "Stator_holder_back", "clear"), ("RotorCore", "Ring_front", "clear"), ("RotorCore", "Ring_back", "clear"),
    ("Shaft", "Stator_holder", "clear"), ("Shaft", "Stator_holder_back", "clear"), ("Screws", "Core", "clear"),
    ("Screws", "Coils", "clear"), ("Screws", "Stator_holder", "contact"), ("Screws", "Stator_holder_back", "thread"),
    ("Screws", "Shaft", "clear"), ("Screws", "RotorCore", "clear"), ("Screws", "MagnetArray", "clear"),
    ("Screws", "Ring_front", "clear"), ("Screws", "Ring_back", "clear"),
]
FIT_PAIRS_OCCUPANCY = [   # occupancy variant (CIANO28 150): the holders are volume-equivalent rings, not the real pockets and holes,
    # so only the pairs whose faces the rings keep exactly are checked (the screws are not tested against the holders)
    # kind "approx": the ring stands in for a pocketed part (rotor keys, bearing seat, coil end turns sit in real pockets), so a small overlap
    # is expected and reported; more than APPROX_FRACTION of the smaller part refuses the build
    ("Shaft", "RotorCore", "approx"), ("Shaft_rear", "RotorCore", "approx"),
    ("Shaft", "Bearing_618_8_front_inner_ring", "contact"), ("Shaft_rear", "Bearing_618_8_back_inner_ring", "contact"),
    ("Bearing_618_8_front_outer_ring", "Stator_holder", "approx"), ("Bearing_618_8_back_outer_ring", "Stator_holder_back", "approx"),
    ("Stator_holder", "Coils", "approx"), ("Stator_holder_back", "Coils", "approx"),
    ("RotorCore", "Stator_holder", "clear"), ("RotorCore", "Stator_holder_back", "clear"),
    ("MagnetArray", "Stator_holder", "clear"), ("MagnetArray", "Stator_holder_back", "clear"),
    ("Shaft", "Stator_holder", "clear"), ("Shaft_rear", "Stator_holder_back", "clear"),
    ("Shaft", "Stator_holder_back", "clear"), ("Shaft_rear", "Stator_holder", "clear"),
    ("Screws", "Core", "clear"), ("Screws", "Coils", "clear"), ("Screws", "RotorCore", "clear"), ("Screws", "MagnetArray", "clear")]
FIT_PAIRS_CARRIER = [(a, b, k) for a, b, k in FIT_PAIRS if "Prop_fix" not in (a, b) and a != "Shaft"
                     and not (b == "Shaft" and a == "Screws")] + [
    ("Shaft", "RotorCore", "contact"), ("Shaft_rear", "RotorCore", "contact"),
    ("Shaft", "Bearing_618_8_front_inner_ring", "contact"), ("Shaft_rear", "Bearing_618_8_back_inner_ring", "contact"),
    ("Shaft", "Stator_holder", "clear"), ("Shaft_rear", "Stator_holder_back", "clear"),
    ("Shaft", "Stator_holder_back", "clear"), ("Shaft_rear", "Stator_holder", "clear"),
    ("Shaft", "Ring_front", "clear"), ("Shaft_rear", "Ring_back", "clear"), ("Screws", "Shaft", "clear"), ("Screws", "Shaft_rear", "clear")]
FIT_PAIRS_FUSION_ASSEMBLY = [   # A.7.8 (CIANO28 150_35): EVERY part is the Fusion body, so the pairs are judged exactly.  Kind "design": an
    # overlap that the Fusion design itself has (bearing press fits in the holder seats, the holder bolts in their holes / tapped holes, the
    # fan plates on the rotor holders), reported, never refused: the model reproduces Fusion and Fusion is the design truth
    ("Shaft", "RotorCore", "clear"), ("Shaft_rear", "RotorCore", "clear"),
    ("Shaft", "Bearing_618_8_front_inner_ring", "clear"), ("Shaft_rear", "Bearing_618_8_back_inner_ring", "clear"),
    ("Bearing_618_8_front_outer_ring", "Stator_holder", "design"), ("Bearing_618_8_back_outer_ring", "Stator_holder_back", "design"),
    ("Stator_holder", "Core", "clear"), ("Stator_holder_back", "Core", "clear"),
    ("Stator_holder", "Coils", "clear"), ("Stator_holder_back", "Coils", "clear"),
    ("RotorCore", "Stator_holder", "clear"), ("RotorCore", "Stator_holder_back", "clear"),
    ("MagnetArray", "Stator_holder", "clear"), ("MagnetArray", "Stator_holder_back", "clear"),
    ("Shaft", "Stator_holder", "clear"), ("Shaft_rear", "Stator_holder_back", "clear"),
    ("Shaft", "Stator_holder_back", "clear"), ("Shaft_rear", "Stator_holder", "clear"),
    ("Screws", "Core", "clear"), ("Screws", "Coils", "clear"), ("Screws", "RotorCore", "clear"), ("Screws", "MagnetArray", "clear"),
    ("Screws", "Stator_holder", "design"), ("Screws", "Stator_holder_back", "design"),
    ("Fan_plates", "Shaft", "design"), ("Fan_plates", "Shaft_rear", "design"), ("Fan_plates", "Stator_holder", "clear"),
    ("Fan_plates", "Stator_holder_back", "clear"), ("Fan_plates", "Coils", "clear"), ("Fan_plates", "RotorCore", "clear"),
    ("Fan_plates", "MagnetArray", "clear"), ("Insulation_ceramic", "Stator_holder", "clear"), ("Insulation_ceramic", "Stator_holder_back", "clear")]
# (the active parts against each other - magnets in their pockets, coils and alumina pieces in the slots - are Fusion's own assembly: the
# instance check proves every copy sits on its Fusion occurrence; their booleans over hundreds of coincident faces would take tens of minutes)
SCREW_ENGAGE_MIN_D = 1.5     # thread engagement in the back boss: at least 1.5 x screw diameter (owner 2026-10-13, reported)
APPROX_FRACTION = 0.025     # occupancy variant: overlap allowed between a volume-equivalent ring part and its neighbour (share of the smaller part)
FIT_REFUSE_MM3 = 0.5        # an overlap above this (outside "thread") refuses the build: a part runs into another one


def fit(doc):
    """Interference / contact table of the assembled motor (stage 3 fit check).  Returns dict(pairs, errors)."""
    import Part
    from . import mechanics
    so = mechanics.solids(doc)
    act = {"Core": doc.getObject("Core").Shape, "RotorCore": doc.getObject("RotorCore").Shape}
    for lbl in ("Coils", "MagnetArray"):
        act[lbl] = [o for o in doc.Objects if o.Label == lbl][0].Shape
    act["Screws"] = Part.makeCompound([v for k, v in so.items() if k.startswith("Screw_")])
    from . import fusion_extras
    for o in fusion_extras.objects(doc):                       # A.7.8: Fusion parts the template has no geometry for
        act[o.Label] = o.Shape
    rows, errors = [], []
    pairs = FIT_PAIRS_CARRIER if doc.getObject("Shaft_rear") is not None else FIT_PAIRS
    from .params_sheet import has_param
    if doc.getObject("Params") is not None and has_param(doc.getObject("Params"), "occupancy_variant"):
        pairs = FIT_PAIRS_OCCUPANCY
        if all(doc.getObject(n + "_Fusion") is not None for n in ("Core", "RotorCore", "Shaft", "Stator_holder")):
            pairs = FIT_PAIRS_FUSION_ASSEMBLY
    import time
    for a, b, kind in pairs:
        sa, sb = so.get(a, act.get(a)), so.get(b, act.get(b))
        if sa is None or sb is None:
            continue
        t0 = time.time()
        ov = _common_volume(sa, sb)
        imported = (doc.getObject(a + "_Fusion") is not None or doc.getObject(b + "_Fusion") is not None
                    or pairs is FIT_PAIRS_FUSION_ASSEMBLY)     # A.7.8: every part is a Fusion body (fan plates vs the 196 magnets: 300 s of distance)
        if ov < 1e-9 and (kind == "contact" or not imported):
            dist = sa.distToShape(sb)[0]
        else:                              # a Fusion body (700 faces) against the coil lofts: the exact distance takes > 10 min and no rule uses it
            dist = None if ov < 1e-9 else 0.0
        row = dict(a=a, b=b, kind=kind, overlap_mm3=round(ov, 6), min_distance_mm=None if dist is None else round(dist, 6),
                   seconds=round(time.time() - t0, 1))
        if kind == "contact":
            row["ok"] = ov < 1e-3 and dist < 1e-6
        elif kind == "clear":
            row["ok"] = ov < 1e-3
        elif kind == "approx":
            lim = APPROX_FRACTION * min(sa.Volume, sb.Volume)
            row["ok"] = ov <= lim
            row["overlap_limit_mm3"] = round(lim, 3)
            row["note"] = "volume-equivalent occupancy part: the real pockets are not modelled, overlap reported (limit %.1f %% of the smaller part)" % (
                100 * APPROX_FRACTION)
            if not row["ok"]:
                errors.append("fit: %s runs into %s (overlap %.3f mm3, limit %.3f)" % (a, b, ov, lim))
        elif kind == "design":
            row["ok"] = True
            row["note"] = "overlap of the Fusion design itself (press fit / bolt in its hole), reported: the model reproduces Fusion"
        else:
            row["ok"] = True
            row["note"] = "screw thread in the tapped hole (overlap = engaged thread)"
        rows.append(row)
        if kind not in ("thread", "approx", "design") and ov > FIT_REFUSE_MM3:
            errors.append("fit: %s runs into %s (overlap %.3f mm3)" % (a, b, ov))
    return dict(pairs=rows, errors=errors, screw=_screw_check(doc, so, errors))


def _common_volume(sa, sb):
    """Overlap volume of two shapes, solid by solid with a bounding-box pre-filter: the same number as sa.common(sb) (the solids of one
    group do not overlap each other) without the boolean of a whole compound against a whole compound (fan plates against the 196 magnets
    took 350 s, A.7.8)."""
    ov = 0.0
    for x in sa.Solids:
        bx = x.BoundBox
        for y in sb.Solids:
            if not bx.intersect(y.BoundBox):
                continue
            c = x.common(y)
            ov += c.Volume if not c.isNull() else 0.0
    return ov


def _screw_check(doc, so, errors):
    """Screw tip position and thread engagement in the back boss (measured on the solids).  The tip must stay inside the back
    holder flange (not break through its outer face); the engagement (z extent of the screw inside the back holder) should be
    >= SCREW_ENGAGE_MIN_D x bolt_d - reported as `engagement_ok`, not refused."""
    from .params_sheet import pval
    sh = doc.getObject("Params")
    from .params_sheet import has_param
    s1, back = so.get("Screw_1"), so.get("Stator_holder_back")
    if sh is None or s1 is None or back is None or has_param(sh, "occupancy_variant"):
        return None
    g = lambda n: pval(sh, n)
    com = s1.common(back)
    eng = com.BoundBox.ZLength if not com.isNull() and com.Volume > 1e-9 else 0.0
    tip = s1.BoundBox.ZMin
    outer = -g("holder_flange_z")                  # outer face of the back flange
    try:
        outer -= g("holder_rib_h")                 # CIANO28 85: the spoke ribs continue the flange outwards (Fusion screw tip z -10.4)
    except Exception:
        pass
    need_eng = SCREW_ENGAGE_MIN_D * g("bolt_d")
    r = dict(length=round(g("holder_screw_l"), 6), length_required=round(g("holder_screw_req"), 6), tip_z=round(tip, 6),
             back_flange_outer_z=round(outer, 6), tip_margin_to_outer_face=round(tip - outer, 6),
             engagement=round(eng, 6), engagement_min=round(need_eng, 6), engagement_ok=eng >= need_eng - 1e-6,
             tip_inside_holder=tip > outer + 1e-6)
    if not r["tip_inside_holder"]:
        errors.append("fit: screw tip z %.3f breaks through the back flange outer face z %.3f" % (tip, outer))
    return r
