"""Bill of materials of a generated motor model (CIANO14 40 family, template segmented_flatwire).

ONE module, two homes with identical content: the owner repo (tools/bom_core.py, driven by tools/make_bom.py) and the
server generator (motor_ai_sim src/motor_ai_sim/cad/bom.py, called after the STEP export).  Stdlib only; openpyxl is
needed only for the .xlsx writer, PyYAML only for `sim_components_from_config`.  NO commercial data here: extra columns / sheets
are added by the caller (hooks `extra_columns` / `extra_sheets`).

Inputs (all plain data, assembled by the caller):
  dump      JSON of fc_scripts/bom_dump.py: BOM-relevant objects of the FCStd (volumes, bboxes, arrays, links) + Params
  inputs    dict(die, config, motor_id, materials {body: name}, cards {sim card name: card}, bearing_card, wire_stock [..],
            sim_materials {slot: card}, sim_components [..], sim_total_kg, sim_source, provenance {..})
Output of `build_bom`: dict(lines, totals, comparison, to_be_specified, warnings, unclassified, header).
Masses: model volume x density (laminations x stacking factor); unit g.  Every density says where it comes from.
"""
import csv
import datetime
import json
import math
import os
import re

try:                                        # the CAD scripts can run where the package is not importable
    from motor_ai_sim.sleeve import has_sleeve
except ImportError:                         # pragma: no cover - the same rule as motor_ai_sim.sleeve
    def has_sleeve(thickness):
        try:
            return float(thickness or 0) > 0
        except (TypeError, ValueError):
            return False

TEMPLATE = "bom/1"
# purchased / standard parts the model does not carry as solids (nominal data from the PCB project and the stock tables)
M2_STOCK_LENGTHS = [3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 26, 30, 35, 40, 45, 50, 55, 60]     # ISO 4762 / DIN 912, M2 (14, 18 and 26: catalogue sizes, 18 chosen by the owner 2026-10-13 for L12, 26 on 2026-10-19 for L20)
ASSUMED_DENSITY = {            # kg/m3, used only when the simulation library has no card (stated in the line notes)
    "GFRP": (1900, "assumed: glass-fibre / epoxy laminate (G10-type) 1.8-2.0 g/cm3"),
    "Steel (bearing)": (7830, "assumed: bearing steel 100Cr6"),
    "18-8 Stainless Steel": (7930, "assumed: A2 / 18-8 stainless"),
    "Copper": (8933, "simulation library card 'copper'"),
    "Aluminum 6061": (2700, "simulation library card 'Aluminium_6061'"),
    "Steel": (7850, "simulation library card 'Steel_42CrMo4_QT'"),
    "3Cr13": (7720, "assumed: martensitic stainless 3Cr13 / AISI 420"),
    "3Cr13 (stainless)": (7720, "assumed: martensitic stainless 3Cr13 / AISI 420"),
}
PIN_DENSITY = (8900, "assumed: CuCrZr C18150")
BOARD_NOMINAL = dict(area_mm2=764.0, thickness_mm=1.6, mass_g=4.0,
                     basis="estimate: 4 layers x 0.21 mm Cu (about 50 % coverage) + 3 x 0.25 mm FR-4, ENIG, mask; "
                           "+-40 %, replace with a weighed sample")
PIN_NOMINAL = dict(d=2.0, tail=2.05, mating=2.70, collar_d=3.0, collar_h=1.15)             # phase board README rev C / pin rev 7


FRAMELESS_SCOPE = "frameless kit"
FRAMELESS_EXPLAIN = ("FRAMELESS KIT (cad_variant = frameless): this bill of materials lists the kit only - stator lamination stack, coils, "
                     "phase board, rotor lamination stack and magnets (and the sleeve when the rotor has one). Housing, stator holders, "
                     "rotor carriers / hub, bearings, shaft, screws, housing rings and the propeller plate are NOT included. The "
                     "simulation passport mass below still includes its shaft stand-in tube and is not the kit mass.")


def is_frameless(dump, inputs):
    """True when this BOM is the frameless kit: the caller's ``cad_variant`` input, else the variant stored in the model's Meta."""
    v = inputs.get("cad_variant") or (dump.get("meta") or {}).get("motor_cad_variant") or "full"
    return v == "frameless"


# ------------------------------------------------------------------------------------------------------ helpers
def thread_name(kind, radius_mm):
    """ISO 261 coarse designation of a smooth cylinder that carries a thread ("M2x0.4"), '' when it matches no size or the table
    (cad/fc_scripts/threads.py, the one the 3D-viewer export uses) is not in this image."""
    try:
        import importlib.util
        from . import store
        spec = importlib.util.spec_from_file_location("_motor_threads", os.path.join(store.CAD_DIR, "fc_scripts", "threads.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        t = mod.thread_for(kind, radius_mm)
        return mod.designation(*t) if t else ""
    except Exception:
        return ""


def _r(x, n=3):
    return None if x is None else round(float(x), n)


def _num(x, n=3):
    s = ("%." + str(n) + "f") % x
    return s.rstrip("0").rstrip(".") if "." in s else s


def _bb(e):
    b = e["bbox"]
    return b[3] - b[0], b[4] - b[1], b[5] - b[2]


def nearest_m2_stock(length):
    return min(M2_STOCK_LENGTHS, key=lambda s: (abs(s - length), s))


def _card_density(cards, *names):
    for n in names:
        c = cards.get(n)
        if c and c.get("density"):
            return float(c["density"]), "simulation library card '%s'" % n
    return None, None


def density_of(material, inputs):
    """kg/m3 and the source text for a profile material name."""
    cards = inputs.get("cards", {})
    pick = {
        "Copper": ("copper",),
        "Aluminum 6061": ("Aluminium_6061",),
        "Steel": ("Steel_42CrMo4_QT",),
        "3Cr13": ("Stainless_420",),
        "3Cr13 (stainless)": ("Stainless_420",),
    }
    d, src = _card_density(cards, *pick.get(material, (material,)))
    if d:
        return d, src
    if material in ASSUMED_DENSITY:
        return ASSUMED_DENSITY[material]
    raise KeyError("no density for material %r" % material)


# ------------------------------------------------------------------------------------------------------ model reading
class Model:
    """Dump entries by role: bodies, array instances, placed copies; anything unclaimed stays in `unclaimed`."""

    def __init__(self, dump):
        self.entries = list(dump["entries"])
        self.params = dict(dump.get("params") or {})
        self.by_label = {e["label"]: e for e in self.entries}
        self.claimed = set()

    def get(self, label, claim=True):
        e = self.by_label.get(label)
        if e is not None and claim:
            self.claimed.add(label)
        return e

    def instances(self, label):
        """The entry whose solids are the instances of body `label` (a Draft array on it) or the body itself."""
        for e in self.entries:
            if e.get("base") == label:
                self.claimed.add(e["label"])
                self.claimed.add(label)
                return e
        return self.get(label)

    def copies(self, label):
        """Number of placed copies (App::Link) of a body or of its parent part."""
        n = 0
        for e in self.entries:
            if e.get("linked") == label:
                self.claimed.add(e["label"])
                n += 1
        return n

    def p(self, name, default=None):
        v = self.params.get(name, default)
        if v is None:
            raise KeyError("Params row %r missing in the model" % name)
        return float(v)

    def unclaimed(self):
        return [e for e in self.entries if e["label"] not in self.claimed]


def _extra(model, pattern):
    """Entries (not yet claimed) whose label matches `pattern` - parts the template does not know yet (board, pins)."""
    rx = re.compile(pattern, re.I)
    return [e for e in model.entries if e["label"] not in model.claimed and rx.search(e["label"])]


# ------------------------------------------------------------------------------------------------------ the BOM
def build_bom(dump, inputs):
    if (dump.get("meta") or {}).get("motor_bom_mode") == "fusion_assembly":    # A.7.9 (G2): the passive parts are Fusion bodies (profile.fusion_parts.extras)
        from .bom_fusion import build_bom_fusion
        return build_bom_fusion(dump, inputs)
    m = Model(dump)
    kit = is_frameless(dump, inputs)
    cfg = inputs["config"]
    mats = inputs["materials"]
    cards = inputs.get("cards", {})
    warns, tbs, lines = [], [], []
    src_model = "model volume (%s)" % dump.get("model", "FCStd")

    def add(key, name, typ, qty, unit, material, dims, unit_g=None, source="", notes="", sim_g=None, in_total=True,
            tbs_text=None, formula=None):
        lines.append(dict(key=key, part=name, type=typ, qty=qty, unit=unit, material=material, dimensions=dims,
                          unit_mass_g=_r(unit_g, 4), total_mass_g=_r(unit_g * qty, 3) if unit_g is not None else None,
                          sim_mass_g=_r(sim_g, 2), source=source, notes=notes, in_total=in_total,
                          to_be_specified=bool(tbs_text)))
        if tbs_text:
            tbs.append("%s: %s" % (name, tbs_text))

    for e in dump["entries"]:
        if e.get("state"):
            warns.append("model object %s is %s - shapes may be stale" % (e["label"], "/".join(e["state"])))

    # ---- laminations -------------------------------------------------------------------------------------------
    L = m.p("motor_length")
    occ = "occupancy_variant" in m.params                  # CIANO28 150: volume-equivalent aluminium holders, three screw families
    fus = set(dump.get("fusion_bodies") or [])            # bodies that ARE the Fusion solids (template A.7.6 - A.7.8)
    seat_r = m.p("shaft_seat_r") if "shaft_seat_r" in m.params else m.p("rotor_down_r") - m.p("rotor_house_height")
    steel = inputs["sim_materials"].get("stator_core") or "20SW1200"
    sc = cards.get(steel, {})
    kf = float(sc.get("stacking_factor") or 0.95)
    rho_fe = float(sc.get("density") or 7650)
    t_sheet = _sheet_thickness(sc)
    n_sheets = int(round(L * kf / t_sheet))
    if abs(L * kf / t_sheet - n_sheets) > 0.05:
        warns.append("stack %.2f mm x k_f %.3f / %.2f mm = %.2f sheets, rounded to %d" % (L, kf, t_sheet, L * kf / t_sheet, n_sheets))
    lam_note = ("%d sheets = round(L %.0f mm x k_f %.2f / %.2f mm); k_f %.2f = stacking_factor of card %s in "
                "materials_library.yaml (the factor the simulation masses use), real coating thickness gives %.3f mm per "
                "sheet incl. coating; mass = model solid x k_f x %.0f kg/m3 (%s)" % (
                    n_sheets, L, kf, t_sheet, kf, steel, L / n_sheets, rho_fe, steel))
    sim = {c["name"].split(" (")[0]: c for c in inputs.get("sim_components", [])}
    simg = lambda k: (sim[k]["mass_kg"] * 1000.0) if k in sim else None

    core = m.get("Core")
    if core:
        mass = core["volume"] * kf * rho_fe * 1e-6
        so = _num(2 * m.p("stator_up_r"), 2)
        bore = _num(2 * m.p("stator_down_r"), 2)
        add("stator_lam", "Stator lamination (laser-cut / stamped, stacked)", "manufactured", n_sheets, "pcs", steel,
            "OD %s mm, bore %s mm, %d slots, sheet %.2f mm, stack %.0f mm" % (so, bore, round(m.p("num_seg") * 6), t_sheet, L),
            mass / n_sheets, "JFE 20SW1200 non-oriented, 0.20 mm", lam_note + "; model 'Core' = one solid, %d electrical segments "
            "(num_seg) - segmenting / bonding of the stack: to be specified" % round(m.p("num_seg")),
            sim_g=simg("Stator core"), tbs_text="stack bonding / segment joining (welding, cleat, bonding varnish) not named in the model or the simulation")
    rot = m.get("RotorCore")
    if rot:
        mass = rot["volume"] * kf * rho_fe * 1e-6
        steel_r = inputs["sim_materials"].get("rotor_core") or steel
        add("rotor_lam", "Rotor lamination (magnet pockets, key), stacked", "manufactured", n_sheets, "pcs", steel_r,
            "OD %s mm, bore %s mm, %d magnet pockets, sheet %.2f mm, stack %.0f mm" % (
                _num(2 * m.p("rotor_up_r"), 2), _num(2 * seat_r, 2), round(m.p("rotor_poles")), t_sheet, L),
            mass / n_sheets, "JFE 20SW1200 non-oriented, 0.20 mm", lam_note, sim_g=simg("Rotor back-iron"))

    # ---- magnets -----------------------------------------------------------------------------------------------
    mag_label = "Magnet"
    mag = m.get(mag_label)
    mags = m.instances(mag_label)
    if mag and mags:
        mcard = inputs["sim_materials"].get("magnet", "?")
        rho_m, rho_src = _card_density(cards, mcard)
        rho_m = rho_m or 7500.0
        w, h, ln = _bb(mag)
        sec = mag["volume"] / ln
        seg = m.params.get("magnet_lamination", 0)
        slices = round(m.params.get("magnet_slices", 1) or 1)
        n_mag = mags["n_solids"]
        if slices > 1:                                      # CIANO28 150: every pole is a stack of axial slices (Fusion R-Pattern1)
            for lbl in ("MagnetStack", "MagnetArray"):
                m.get(lbl)
            n_mag = round(m.p("rotor_poles")) * slices
        add("magnets", "Permanent magnet (sintered NdFeB, ground, coated)", "manufactured (custom shape)", n_mag, "pcs", mcard,
            "%.2f x %.2f x %.0f mm bounding box (width x radial x length), wedge section %.2f mm2, edge fillet %.1f mm, %d poles" % (
                w, h, ln, sec, m.params.get("magnet_edge_fillet", 0.2), round(m.p("rotor_poles"))),
            mag["volume"] * rho_m * 1e-6, "simulation magnet card %s (Br at 120 C)" % mcard,
            ("one piece per pole over the full stack length %.0f mm; segmentation: %s; density %.0f kg/m3 (%s); coating, "
             "magnetisation direction and tolerances: to be specified" % (
                 ln, "none (magnet_lamination = 0, solid magnet in the simulation)" if not seg else "%.0f" % seg, rho_m, rho_src))
            if slices <= 1 else
            ("%d axial slices of %.0f mm per pole (stack %.0f mm, the Fusion model; the simulation loss model uses magnet_lamination = %.0f mm "
             "slices); density %.0f kg/m3 (%s); coating, magnetisation direction and tolerances: to be specified" % (
                 slices, ln, L, seg or 0, rho_m, rho_src)),
            sim_g=simg("Magnets"), tbs_text="magnet coating / tolerance / magnetisation / grading (model gives the shape only)")

    # ---- coils / wire ------------------------------------------------------------------------------------------
    coil = m.get("Coil")
    coils = m.instances("Coil")
    if coil and coils:
        ww, wh = m.p("wire_width"), m.p("wire_height")
        ins = m.p("insulation_thickness")
        turns = round(m.p("num_wires_per_slot"))
        rho_cu, cu_src = density_of(mats.get("Coil", "Copper"), inputs)
        cu_vol = coil["volume"]
        length = cu_vol / (ww * wh)
        n_coils = coils["n_solids"]
        enam_sec = (ww + 2 * ins) * (wh + 2 * ins) - ww * wh
        rho_en, _ = _card_density(cards, "polyimide")
        rho_en = rho_en or 1420.0
        cu_g = cu_vol * rho_cu * 1e-6
        en_g = length * enam_sec * rho_en * 1e-6
        stock = _wire_stock_item(inputs.get("wire_stock") or [], ww, wh)
        w = inputs.get("winding", {})
        add("coils", "Coil (edge-wound flat wire, formed, with two leads)", "manufactured", n_coils, "pcs", "Copper, enamel AIW class 200",
            "%d turns of %.1f x %.1f mm bare wire (enamel %.2f mm per side), conductor length %.1f mm per coil incl. leads" % (
                turns, ww, wh, ins, length),
            cu_g + en_g, "CAD coil solid; winding %s" % (w.get("connection", "?")),
            "total conductor length %.2f m for %d coils; copper %.2f g (rho %.0f, %s) + enamel %.2f g (polyimide %.0f kg/m3); "
            "simulation lists %d coils/phase (coil sides), CAD has %d coils of %d turns (single-layer, 2 per phase); "
            "length = coil volume / (%.1f x %.1f); lead trimming, insulation of joints, lead ends: to be specified" % (
                length * n_coils / 1000.0, n_coils, cu_g * n_coils, rho_cu, cu_src, en_g * n_coils, rho_en,
                w.get("n_coils_per_phase", 0), n_coils, turns, ww, wh),
            sim_g=simg("Copper windings"), tbs_text=None)
        lines[-1]["_cu_g_unit"] = cu_g
        lines[-1]["_length_mm"] = length
        add("wire", "Enamelled flat copper wire (for the coils above)", "material", _r(length * n_coils / 1000.0, 3), "m",
            "Copper, polyamide-imide (AIW) enamel, class N 200 C",
            "%.1f x %.1f mm bare (nominal size of the stock item), enamelled build %.2f per side" % (ww, wh, ins),
            None, ("stock item %s (%s), %.1f kg in stock on 2026-09-20" % (stock["code"], stock["spec"], stock["stock_kg"]))
            if stock else "no matching item in wire_stock.yaml",
            "copper %.2f g + enamel %.2f g for the %d coils = %.2f g; mass already counted in the coil line (not added again); "
            "scrap allowance: to be specified; the stock spec carries no '/200' mark - class 200 follows from the AIW enamel type" % (
                cu_g * n_coils, en_g * n_coils, n_coils, (cu_g + en_g) * n_coils), in_total=False)

    # ---- shaft -------------------------------------------------------------------------------------------------
    sh = m.get("Shaft")
    sh_rear = m.get("Shaft_rear")
    if sh and sh_rear:                                    # ring carrier (CIANO28 85): front steel hub + rear aluminium hub
        for key, label, title, dflt in (("shaft", "Shaft", "Rotor carrier, front (hub with 4 M3 end-face holes, rotor seat, bearing journal)", "Steel"),
                                        ("shaft_rear", "Shaft_rear", "Rotor carrier, rear (hub tube, rotor seat, bearing journal)", "Aluminum 6061")):
            if occ:
                title = {"Shaft": "Rotor holder, front (Fusion Rotor_holder1; volume-equivalent model)",
                         "Shaft_rear": "Rotor holder, rear (Fusion Rotor_holder2; volume-equivalent model)"}[label]
                if label in fus:                          # A.7.8: the Fusion body; named as the rotor carrier it is (motor_mass then counts
                    title = {"Shaft": "Rotor carrier, front (Fusion Rotor_holder1, the Fusion body)",      # it instead of the simulation's
                             "Shaft_rear": "Rotor carrier, rear (Fusion Rotor_holder2, the Fusion body)"}[label]   # shaft stand-in tube)
            e = m.get(label)
            w, h, ln = _bb(e)
            mat = mats.get(label, dflt)
            rho, rs = density_of(mat, inputs)
            sim_mat = inputs["sim_materials"].get("shaft") if label == "Shaft" else None
            add(key, title, "manufactured", 1, "pcs", sim_mat or mat,
                ("length %.1f mm, max Ø%.2f mm, rotor seat Ø%.1f, bearing seat Ø%.1f, %d key pockets %.1f wide; %s" % (
                     ln, w, 2 * seat_r, m.p("bearing_d"), round(m.p("rotor_key_count")), m.p("rotor_key_w"),
                     "the Fusion body as designed (read-only STEP export)" if label in fus else
                     "shape modelled as rings of equal volume (pockets, ribs and holes of the Fusion body are not modelled)")) if occ else
                ("length %.1f mm, max Ø%.2f mm, rotor seat Ø%.1f, journal Ø%.1f, %d key slots %.1f wide in the rim" % (
                    ln, w, 2 * seat_r, m.p("bearing_d"), round(m.p("rotor_key_count")), m.p("rotor_key_w"))),
                e["volume"] * rho * 1e-6, "model" + ("; simulation shaft slot = %s" % sim_mat if sim_mat else ""),
                "density %.0f (%s); the rotor keys engage the rim slots; machining, finish, M3 thread depth: to be specified" % (rho, rs),
                sim_g=simg("Shaft") if label == "Shaft" else None, tbs_text="material / heat treatment / finish of the rotor carrier")
        sh = None
    if sh:
        w, h, ln = _bb(sh)
        mat = mats.get("Shaft", "Steel")
        if mat in ("3Cr13", "3Cr13 (stainless)", "Stainless_420"):     # the motor profile states the shaft steel (Ø40: 3Cr13, owner 2026-10-16)
            shaft_mat_label = "3Cr13 (stainless)"
        else:
            sim_mat = inputs["sim_materials"].get("shaft")
            shaft_mat_label = sim_mat or "42CrMo4 QT (assumed, profile says '%s')" % mat

        rho, rs = density_of(mat, inputs)
        sim_mat = inputs["sim_materials"].get("shaft")
        add("shaft", "Shaft (stepped, drive flats, M-thread tip)", "manufactured", 1, "pcs", shaft_mat_label,
            "length %.1f mm, max Ø%.2f mm, journal Ø%.1f, bore Ø%.1f, drive flats %.1f mm across x %.1f long, chamfers %.1f mm" % (
                ln, w, m.p("bearing_d"), 2 * (m.p("shaft_bore_r") if "shaft_bore_r" in m.params else m.p("shaft_seat_r") - m.p("shaft_height")),
                m.p("shaft_flat_w"), m.p("shaft_flat_len"), m.p("shaft_chamfer")),
            sh["volume"] * rho * 1e-6, "model; simulation shaft slot " + ("= %s" % sim_mat if sim_mat else "not set in %s.yaml" % cfg),
            "density %.0f (%s); simulation shaft is a hollow tube 50 mm2 x stack and sits outside the active mass; thread pitch / "
            "heat treatment / surface: to be specified" % (rho, rs), sim_g=simg("Shaft"),
            tbs_text="thread type and pitch on the tip, heat treatment, surface finish")

    # ---- holders, rings, Prop_fix -----------------------------------------------------------------------------------
    for key, label, title in (("holder_front", "Stator_holder", "Stator holder, front (propeller side)"),
                              ("holder_back", "Stator_holder_back", "Stator holder, back (A_H hub face, six M3 tapped holes)")):
        e = m.get(label)
        if e and occ and label in fus:                    # CIANO28 150: the Fusion body itself (A.7.6)
            w, h, ln = _bb(e)
            mat = mats.get(label, "Aluminum 6061")
            rho, rs = density_of(mat, inputs)
            add(key, title.split(" (")[0] + " (the Fusion body)", "manufactured", 1, "pcs", mat,
                "envelope %.1f x %.1f x %.1f mm, volume %.1f mm3 (the Fusion body as designed: pockets, ribs and holes included)" % (
                    w, h, ln, e["volume"]),
                e["volume"] * rho * 1e-6, "model (Fusion STEP)", "density %.0f (%s); machining, anodising / finish: to be specified" % (rho, rs),
                tbs_text="finish (anodising) and tolerances of the holder")
        elif e and occ:                                   # CIANO28 150: occupancy rings (volume of the Fusion body)
            w, h, ln = _bb(e)
            mat = mats.get(label, "Aluminum 6061")
            rho, rs = density_of(mat, inputs)
            add(key, title.split(" (")[0] + " (volume-equivalent model)", "manufactured", 1, "pcs", mat,
                "envelope %.1f x %.1f x %.1f mm, volume %.1f mm3 (equals the Fusion body; pockets, ribs and holes not modelled)" % (
                    w, h, ln, e["volume"]),
                e["volume"] * rho * 1e-6, "model", "density %.0f (%s); machining, anodising / finish: to be specified" % (rho, rs),
                tbs_text="finish (anodising) and tolerances of the holder")
        elif e:
            w, h, ln = _bb(e)
            mat = mats.get(label, "Aluminum 6061")
            rho, rs = density_of(mat, inputs)
            add(key, title, "manufactured", 1, "pcs", mat, "envelope %.1f x %.1f x %.1f mm, volume %.1f mm3, %d spokes, M%g %s" % (
                w, h, ln, e["volume"], round(m.p("holder_bolt_count")), m.p("bolt_d"),
                "clearance holes" if key == "holder_front" else "tapped holes %.3f mm (minor)%s" % (
                    m.p("holder_tap_d"), (", thread " + tn + " (ISO 261)") if (tn := thread_name("internal", m.p("holder_tap_d") / 2)) else "")),
                e["volume"] * rho * 1e-6, "model", "density %.0f (%s); machining, anodising / finish: to be specified" % (rho, rs),
                tbs_text="finish (anodising) and tolerances of the holder")
    ring = m.get("Ring")
    if ring:
        n_ring = 1 + m.copies("Ring")
        w, h, ln = _bb(ring)
        mat = mats.get("Ring", "GFRP")
        rho, rs = density_of(mat, inputs)
        add("rings", "Ring (glass-fibre)", "manufactured", n_ring, "pcs", mat,
            "OD %.2f mm, ID %.2f mm, thickness %.1f mm" % (w, 2 * m.p("ring_inner_r"), ln), ring["volume"] * rho * 1e-6, "model",
            "front and back copy; density %.0f (%s); laminate type / fibre orientation: to be specified" % (rho, rs),
            tbs_text="glass-fibre ring laminate (resin, thickness tolerance)")
    pf = m.get("Prop_fix")
    if pf:
        w, h, ln = _bb(pf)
        mat = mats.get("Prop_fix", "Aluminum 6061")
        rho, rs = density_of(mat, inputs)
        add("prop_fix", "Prop_fix (propeller fixing, double-D plate)", "manufactured", 1, "pcs", mat,
            "Ø%.1f x %.1f mm, double-D hole on the shaft flats (clearance %.2f mm)" % (w, ln, m.p("prop_fix_clearance")),
            pf["volume"] * rho * 1e-6, "model", "density %.0f (%s)" % (rho, rs))

    # ---- bearings ----------------------------------------------------------------------------------------------
    bo, bi = m.get("Bearing_outer_ring"), m.get("Bearing_inner_ring")
    balls = m.instances("Bearing_ball")
    if bo and bi and balls:
        nb = 1 + m.copies("Bearing_618_8")
        brg = mats.get("Bearing_outer_ring", "Steel (bearing)")
        rho, rs = density_of(brg, inputs)
        v = bo["volume"] + bi["volume"] + balls["volume"]
        card = inputs.get("bearing_card") or {}
        seal = m.get("Bearing_seal")
        m.claimed.add("Bearing_seal_2")                   # the turned-over copy of the seal inside each bearing
        if seal and occ:                                  # 61811-2RZ (CIANO28 150): shielded
            add("bearings", "Ball bearing 61811-2RZ (55 x 72 x 9, shielded)", "purchased", nb, "pcs", "bearing steel, steel shields both sides",
                "d %.0f x D %.0f x B %.0f mm, %d balls Ø%.3f mm" % (m.p("bearing_d"), m.p("bearing_D"), m.p("bearing_B"),
                                                                    round(m.p("bearing_ball_count")), m.p("bearing_ball_d")),
                (v + 2 * seal["volume"]) * rho * 1e-6, "ISO 15 series 618; Fusion component 61811-2rz",
                "front + back copy; mass = rings + balls + two shield models (density %.0f, %s)%s; cage not modelled; grease / clearance "
                "class: to be specified" % (rho, rs, "; rings and shields are the Fusion bodies, the balls the template spheres (Fusion draws "
                                                   "the 22 balls as one body)" if "Bearing_outer_ring" in fus else ""),
                tbs_text="bearing grease and internal clearance class")
            m.claimed.add("Bearing_seal_2")
        elif seal:                                        # 61707-2RS1 (CIANO28 85): sealed, not in the bearings library
            rho_seal = 1200.0                              # NBR seal (assumed)
            add("bearings", "Ball bearing 61707-2RS1 (35 x 44 x 5, rubber sealed)", "purchased", nb, "pcs", "bearing steel, NBR seals both sides",
                "d %.0f x D %.0f x B %.0f mm, %d balls Ø%.0f mm" % (m.p("bearing_d"), m.p("bearing_D"), m.p("bearing_B"),
                                                                    round(m.p("bearing_ball_count")), m.p("bearing_ball_d")),
                (v * rho + 2 * seal["volume"] * rho_seal) * 1e-6, "ISO 15 series 617; Fusion component w_61707-2rs1 (not in bearings_library.yaml)",
                "front + back copy; mass = rings + balls (density %.0f, %s) + two seals (volume-equivalent model, NBR %.0f assumed); cage not "
                "modelled; grease / clearance class: to be specified" % (rho, rs, rho_seal), tbs_text="bearing grease, clearance class, seal material")
        else:
          add("bearings", "Ball bearing 618/8-2Z (SKF 618/8-2Z, shielded)", "purchased", nb, "pcs", "bearing steel, shields both sides",
            "d %.0f x D %.0f x B %.0f mm, %d balls Ø%.0f mm" % (m.p("bearing_d"), m.p("bearing_D"), m.p("bearing_B"),
                                                                round(m.p("bearing_ball_count")), m.p("bearing_ball_d")),
            v * rho * 1e-6, "ISO 15 series 618; card %s in bearings_library.yaml (C %.2f kN, C0 %.3f kN, grease limit %s rpm)" % (
                card.get("catalog", {}).get("part_number", "618/8-2Z"), card.get("C_kn", 0), card.get("C0_kn", 0),
                card.get("n_limit_grease_rpm", "?")),
            "front + back copy; mass from the modelled rings and balls only (shields and cage not modelled, so the real part is "
            "about the same or slightly heavier); density %.0f (%s); grease / clearance class: to be specified" % (rho, rs),
            tbs_text="bearing grease and internal clearance class (C3?)")

    # ---- screws -------------------------------------------------------------------------------------------------
    scr_i = m.instances("Screw")
    m.get("Screw")
    if occ:
        for pre, body, key, nm, ref in (
                ("screwA", "Screw", "screws", "Screw M3 x 50 socket head (holder bolts)", "McMaster 91290A137 (Fusion component)"),
                ("screwB", "Screw_M3x30", "screws_m3_front", "Screw DIN 912 M3 socket head cap (front)", "Fusion component 'M3 x 0.5 x 30'; the model shank is 34 mm long"),
                ("screwC", "Screw_M3x6", "screws_m3_back", "Screw DIN 912 M3 x 6 socket head cap (back)", "Fusion component 'M3 x 0.5 x 6'")):
            si = m.instances(body)
            if not si:
                continue
            m.get(body)
            mat = mats.get(body, "Steel")
            rho, rs = density_of(mat, inputs)
            one = si["volume"] / si["n_solids"]
            ln = m.p(pre + "_length")
            add(key, nm, "purchased", si["n_solids"], "pcs", mat,
                "length under the head %s mm, head Ø%.1f x %.1f, hex socket %.1f, bolt circle R%.1f" % (
                    _num(ln, 1), m.p(pre + "_head_d"), m.p(pre + "_head_h"), m.p(pre + "_socket_af"), m.p(pre + "_bolt_r")),
                one * rho * 1e-6, ref,
                ("model is the Fusion body (thread not modelled in Fusion)" if body in fus else
                 "model is a plain shank (thread and head knurling not modelled; shank/head volume-equivalent to the Fusion body)")
                + "; density %.0f (%s)" % (rho, rs),
                tbs_text=("stock length: the Fusion component is named M3 x 30 but its model shank is %s mm long - confirm the bought length" % _num(ln, 1))
                if pre == "screwB" else None)
        scr_i = None
    if scr_i:
        ln = m.p("holder_screw_l")
        mat = mats.get("Screw", "18-8 Stainless Steel")
        rho, rs = density_of(mat, inputs)
        stock = nearest_m2_stock(ln)
        flag = abs(stock - ln) > 0.01
        one = scr_i["volume"] / scr_i["n_solids"]
        sz = "M%g" % m.p("bolt_d")
        ref = "McMaster 91292A032 (M2 x 0.4)" if sz == "M2" else "DIN 912 %s x 0.45 (Fusion component)" % sz
        note = "model is a plain shank (thread not modelled); density %.0f (%s); CAD reference part %s; head Ø%.1f x %.1f, hex socket %.1f" % (
            rho, rs, ref, m.p("screw_head_d"), m.p("screw_head_h"), m.p("screw_socket_af"))
        if flag:
            lo = max([s for s in M2_STOCK_LENGTHS if s <= ln] or [stock])
            hi = min([s for s in M2_STOCK_LENGTHS if s >= ln] or [stock])
            warns.append("%s screw length %.1f mm is not a stock length; nearest stock %s x %d" % (sz, ln, sz, stock))
            note = ("NON-STOCK LENGTH %.1f mm (model length under the head: front flange face to the back holder face). "
                    "Nearest stock: %s x %d (%+.1f mm; shorter by %.1f mm = less thread engagement in the back boss - check "
                    "the tap depth); next longer %s x %d protrudes %.1f mm past the back holder face - check clearance. "
                    "Proposed: %s x %d. " % (ln, sz, stock, stock - ln, ln - stock if stock < ln else 0.0, sz, hi, hi - ln, sz, stock)) + note
        add("screws", "Screw %s x %s socket head cap%s" % (sz, _num(ln, 1), " (NON-STOCK length)" if flag else ""), "purchased",
            scr_i["n_solids"], "pcs", mat, "%s x %s mm%s, head Ø%.1f x %.1f, one per holder spoke on the bolt circle R%.2f" % (
                sz, _num(ln, 1), (", thread " + tn + " (ISO 261)") if (tn := thread_name("external", m.p("screw_shank_d" if "screw_shank_d" in m.params else "bolt_d") / 2)) else "",
                m.p("screw_head_d"), m.p("screw_head_h"), m.p("holder_bolt_r")),
            one * rho * 1e-6, "ISO 4762 / DIN 912, A2-70", note,
            tbs_text=("screw length %s mm is not a stock length - choose %s x %d (or x %d)" % (_num(ln, 1), sz, stock, nearest_m2_stock(ln + 3)))
            if flag else None)

    # ---- phase board and pins (model objects when present, else nominal data) -------------------------------------
    carrier_board = bool(m.get("Shaft_rear")) or "board_outer_r" in m.params          # the CIANO28 85 plate board (also without a carrier: frameless kit)
    fboard = next((e for e in m.entries if (e.get("motor") or {}).get("SolidMaterials")), None)     # A.7.8: the Fusion board in the model
    if carrier_board and occ and fboard is not None:      # CIANO28 150: the model board IS the Fusion PCB 150 (insulation + copper bodies)
        for e in _extra(m, r"board|pcb|pin"):
            m.claimed.add(e["label"])
        bm = fboard["motor"]["SolidMaterials"]
        sv = fboard["solid_volumes"]
        cu_mm3 = sum(v for v, x in zip(sv, bm) if str(x).lower().startswith("copper"))
        ins_mm3 = sum(v for v, x in zip(sv, bm) if not str(x).lower().startswith("copper"))
        n_ins = sum(1 for x in bm if not str(x).lower().startswith("copper"))
        add("board", "Phase board 150 (coil connection, copper layers + %d insulation layers, the Fusion board)" % n_ins, "purchased (custom)", 1, "pcs",
            "insulation (GFRP, 1.85 g/cm3 assumed), copper",
            "outer Ø%.1f, stack z %.2f .. %.2f mm, %d solids (the Fusion bodies)" % (
                fboard["bbox"][3] - fboard["bbox"][0], fboard["bbox"][2], fboard["bbox"][5], fboard["n_solids"]),
            ins_mm3 * 1.85e-3 + cu_mm3 * 8.933e-3, "Fusion component PCB 150 2 parallel 0.6x14 (read-only STEP export of its bodies)",
            "the model board is the Fusion phase board (every body at its Fusion placement); mass: insulation %.0f mm3 x 1.85 + copper %.0f mm3 x "
            "8.933 g/cm3; the controller is NOT part of this BOM" % (ins_mm3, cu_mm3),
            tbs_text="board stack-up, copper weight, lead terminations and fabrication data of the 150 phase board")
    elif carrier_board and occ:                           # CIANO28 150: Fusion PCB 150 (8 insulation layers + 6 copper layers, 2026-10-16 dump)
        for e in _extra(m, r"board|pcb|pin"):
            m.claimed.add(e["label"])
        ins_mm3, cu_mm3 = 8 * 3751.64, 1833.4 + 1121.4 + 1777.8 + 1218.5 + 1777.8 + 1248.6 + 914.4
        mass150 = ins_mm3 * 1.85e-3 + cu_mm3 * 8.933e-3
        add("board", "Phase board 150 (coil connection, 6 copper layers + 8 insulation layers)", "purchased (custom)", 1, "pcs",
            "insulation (GFRP, 1.85 g/cm3 assumed), copper",
            "outer Ø%.1f, inner Ø%.2f (envelope), stack %.1f mm, %d coil-lead slots %.1f x %.1f mm" % (
                2 * m.p("board_outer_r"), 2 * m.p("board_inner_r"), m.p("board_t"), round(2 * m.p("coil_count")), m.p("board_pin_w"), m.p("board_pin_t")),
            mass150, "Fusion component PCB 150 2 parallel 0.6x14 (read-only dump 2026-10-16)",
            "the model has a simplified envelope (ring plate + %d lead sleeves, positions exact; its volume is larger than the real layers); mass from "
            "the Fusion board bodies: insulation %.0f mm3 x 1.85 + copper %.0f mm3 x 8.933 g/cm3; the controller is NOT part of this BOM" % (
                round(2 * m.p("coil_count")), ins_mm3, cu_mm3),
            tbs_text="board stack-up, copper weight, lead terminations and fabrication data of the 150 phase board")
    elif carrier_board:                                     # CIANO28 85: simplified plate + lead sleeves in the model, Fusion bodies for the mass
        for e in _extra(m, r"board|pcb|pin"):
            m.claimed.add(e["label"])
        ins_mm3, cu_mm3 = 5 * 432.313, 1560.7              # Fusion PCB 85 serial: 5 insulation discs, copper layers + pins + pads (2026-10-14 dump)
        mass85 = ins_mm3 * 1.85e-3 + cu_mm3 * 8.933e-3
        add("board", "Phase board 85 (coil connection, 4 copper layers + 5 insulation layers)", "purchased (custom)", 1, "pcs",
            "insulation (FR-4 / GFRP, 1.85 g/cm3 assumed), copper",
            "outer Ø%.1f, inner Ø%.2f, stack %.1f mm, %d coil-lead slots %.1f x %.1f mm" % (
                2 * m.p("board_outer_r"), 2 * m.p("board_inner_r"), m.p("board_t"), round(2 * m.p("coil_count")), m.p("board_pin_w"), m.p("board_pin_t")),
            mass85, "Fusion component PCB 85 serial (read-only dump 2026-10-14)",
            "the model has a simplified envelope (ring plate + %d lead sleeves, positions exact); mass from the Fusion board bodies: insulation "
            "%.0f mm3 x 1.85 + copper %.0f mm3 x 8.933 g/cm3 (pins, pads and layers); the controller is NOT part of this BOM" % (
                round(2 * m.p("coil_count")), ins_mm3, cu_mm3),
            tbs_text="board stack-up, copper weight, lead terminations and fabrication data of the 85 phase board")
    pn = PIN_NOMINAL
    pin_vol = math.pi * (pn["d"] / 2) ** 2 * (pn["tail"] + pn["mating"]) + math.pi * (pn["collar_d"] / 2) ** 2 * pn["collar_h"]
    if not carrier_board:
        boards = _extra(m, r"board|pcb")
        model_pins = []
        for e in boards:
            m.claimed.add(e["label"])
            model_pins += [v for v in e["solid_volumes"] if abs(v - pin_vol) < 0.08 * pin_vol]
        b_note = ("model object(s) %s: %d solids, %.1f mm3 in total, of which %d pin solids (%.2f mm3 each) and the notch-wall models; "
                  "mass kept at the nominal estimate" % (
                      ", ".join(e["label"] for e in boards), sum(e["n_solids"] for e in boards), sum(e["volume"] for e in boards),
                      len(model_pins), (sum(model_pins) / len(model_pins)) if model_pins else 0.0)
                  if boards else "not in the model yet - nominal data from the PCB project")
        add("board", "Phase board rev C (coil connection, 4 layers x 6 oz)", "purchased", 1, "pcs", "FR-4 Tg>=170, Cu 4 x 0.21 mm, ENIG",
            "Ø40.05 mm, window Ø24.5, thickness 1.6 mm (1.44-1.76), 6 plated U-notches, 3 pin holes Ø2.20",
            BOARD_NOMINAL["mass_g"], "PCB_CIANO14_40 RELEASE_2026-10-11_FAB_4L_6OZ, ciano40_4L_revC.kicad_pcb sha256 b80d7b91...",
            "%s; %s; the controller is NOT part of this BOM" % (b_note, BOARD_NOMINAL["basis"]),
            tbs_text="fab confirmation of 0.35 mm copper clearance and 35 um notch plating (DFM answer pending)")
        pins = _extra(m, r"pin|_pin|^pin")
        for e in pins:
            m.claimed.add(e["label"])
        pin_one = (sum(model_pins) / len(model_pins)) if model_pins else pin_vol
        add("pins", "Phase pin CIANO40-PIN-C rev 7", "purchased (custom turned)", 3, "pcs", "CuCrZr C18150 (rev 5 requirement), Ni + Ag plated",
            "Ø%.1f body, collar Ø%.1f x %.2f, tail %.2f, mating %.2f mm" % (pn["d"], pn["collar_d"], pn["collar_h"], pn["tail"], pn["mating"]),
            pin_one * PIN_DENSITY[0] * 1e-6, "PIN_CIANO40-PIN-C requirements (PCB project); dimensions of rev 7 from the phase board README",
            "mass from the %s (%.2f mm3), %s%s; mating socket belongs to the controller (not in this BOM)" % (
                "model pin solids" if model_pins else "nominal turned shape", pin_one, PIN_DENSITY[1],
                "; model objects: " + ", ".join(e["label"] for e in pins) if pins else ""),
            tbs_text="pin/socket pair not selected yet, pins not ordered (PCB release notes); material of rev 7 to be confirmed")

    # ---- Fusion-only parts (template A.7.8: fan plates, alumina insulation pieces of the CIANO28 150) ------------------------
    for e in m.entries:
        mp = e.get("motor") or {}
        if not mp.get("BomKey") or e["label"] in m.claimed:
            continue
        m.claimed.add(e["label"])
        mat = mp.get("MaterialName") or "?"
        rho, rs = density_of(mat, inputs)
        n_occ = max(1, int(mp.get("FusionOccurrences") or 1))
        per = e["n_solids"] // n_occ
        add(mp["BomKey"], mp.get("BomTitle") or e["label"], mp.get("BomType") or "manufactured", n_occ, "pcs" if per == 1 else "sets", mat,
            "%d solid%s per %s, %.1f mm3 each %s, bbox of all %s" % (per, "s" if per != 1 else "", "piece" if per == 1 else "set", e["volume"] / n_occ,
                                                                  "piece" if per == 1 else "set", [round(x, 1) for x in e["bbox"]]),
            e["volume"] / n_occ * rho * 1e-6, "Fusion component '%s' (read-only STEP export)" % mp.get("FusionComponent", ""),
            "the Fusion bodies at their Fusion placement (not in the simulation); density %.0f (%s)" % (rho, rs),
            tbs_text="material grade, finish and supplier of the %s" % (mp.get("FusionComponent") or e["label"]))

    # ---- consumables the model or the simulation names ----------------------------------------------------------------
    glue = m.params.get("glue")
    add("adhesive", "Adhesive (magnet / coil / lamination bonding)", "material", 1, "set", "to be specified",
        ("model parameter 'glue' = %.2f mm (info row)" % glue) if glue is not None else "not named", None, "model Params",
        "no adhesive type named in the simulation or the CAD", tbs_text="adhesive type and amount (model carries only a glue-layer parameter)")
    add("varnish", "Impregnation varnish", "material", 1, "set", "to be specified", "-", None, "-",
        "not named in the model or the simulation", tbs_text="impregnation / varnish")
    slot_ins = inputs["sim_materials"].get("slot_insulation")
    add("slot_liner", "Slot insulation sheet", "material", 1, "set", slot_ins or "to be specified",
        "thickness not given; no liner in the CAD", None, "simulation config " + cfg,
        ("simulation names %s as slot liner" % slot_ins) if slot_ins else "%s.yaml names no slot liner" % cfg,
        tbs_text="slot liner material and thickness" + (" (%s.yaml names %s, no thickness)" % (cfg, slot_ins) if slot_ins else ""))
    sleeve = inputs["sim_materials"].get("sleeve")
    # OWNER 2026-10-26: a rotor with no sleeve (thickness 0) has no sleeve line at all, however its material assignment
    # reads - not a zero-quantity row, not a "confirm that no sleeve is wanted" question.
    if sleeve and has_sleeve(m.params.get("sleeve_thickness", 0)):
        add("sleeve", "Rotor sleeve", "manufactured", 0, "pcs", sleeve, "sleeve_thickness = %s mm in the model" % _num(m.params.get("sleeve_thickness", 0)),
            None, "simulation config " + cfg, "%s.yaml names sleeve material %s but the thickness is 0: no sleeve in the CAD or here" % (cfg, sleeve),
            tbs_text="%s.yaml names rotor sleeve %s with zero thickness - confirm that no sleeve is wanted" % (cfg, sleeve))
    add("leads", "Power leads / soldering", "material", 1, "set", "to be specified", "-", None, "PCB project ASSEMBLY_NOTES_MOTOR",
        "PCB notes: a 12 AWG wire soldered onto each pin in the MOTOR build; not in the CAD", tbs_text="power leads (wire, length, termination) and solder")

    # ---- anything the rules did not claim -------------------------------------------------------------------------------
    unclassified = []
    skip_templates = {e.get("base") for e in m.entries if e.get("base")}
    for e in m.unclaimed():
        if e["label"] in skip_templates:
            continue
        unclassified.append(e)
        warns.append("model object %s (%d solid(s), %.1f mm3) has no BOM rule - listed as unclassified" % (e["label"], e["n_solids"], e["volume"]))
        add("x_" + e["label"], "UNCLASSIFIED model object: " + e["label"], "unknown", e["n_solids"], "solids", "?",
            "volume %.1f mm3, bbox %s" % (e["volume"], [round(x, 2) for x in e["bbox"]]), None, "model",
            "no rule in bom_core.build_bom: add one (material, source)", tbs_text="classify model object " + e["label"])

    for i, ln_ in enumerate(lines, 1):
        ln_["item"] = i

    totals = _totals(lines)
    comparison = _compare(lines, inputs, totals)
    comparison["explain"] = _explain(lines, comparison, inputs, m)
    if kit:
        totals["scope"] = FRAMELESS_SCOPE
        comparison["explain"].insert(0, FRAMELESS_EXPLAIN)
    return dict(header=_header(dump, inputs), lines=lines, totals=totals, comparison=comparison,
                to_be_specified=tbs, warnings=warns, unclassified=[e["label"] for e in unclassified])


def _sheet_thickness(card):
    if card.get("sheet_thickness_mm"):
        return float(card["sheet_thickness_mm"])
    mt = re.search(r"(\d\.\d+)\s*mm", card.get("description") or "")
    return float(mt.group(1)) if mt else 0.2


def _wire_stock_item(stock, w, h):
    for s in stock:
        if abs(s.get("width_mm", 0) - w) < 1e-6 and abs(s.get("thickness_mm", 0) - h) < 1e-6 and s.get("insulation") == "AIW":
            return s
    return None


def _totals(lines):
    tm = sum(l["total_mass_g"] or 0.0 for l in lines if l["in_total"] and l["total_mass_g"] is not None)
    return dict(n_lines=len(lines), motor_mass_g=_r(tm, 2), mass_lines=sum(1 for l in lines if l["in_total"] and l["total_mass_g"] is not None))


def _compare(lines, inputs, totals):
    """BOM lines vs the simulation mass components of the saved duty (stator, copper, magnets, rotor, shaft)."""
    by = {l["key"]: l for l in lines}
    rows = []
    pairs = (("Stator core", "stator_lam"), ("Copper windings", "coils"), ("Magnets", "magnets"), ("Rotor back-iron", "rotor_lam"),
             ("Shaft", "shaft"))
    sim = {c["name"].split(" (")[0]: c for c in inputs.get("sim_components", [])}
    s_sum = b_sum = 0.0
    for sname, key in pairs:
        if sname not in sim or key not in by:
            continue
        s = sim[sname]["mass_kg"] * 1000.0
        if key == "coils":
            b = by[key]["_cu_g_unit"] * by[key]["qty"]          # the simulation copper mass is copper only (no enamel)
            basis = "copper only"
        else:
            b = by[key]["total_mass_g"]
            basis = ""
        rows.append(dict(component=sname, sim_g=_r(s, 2), bom_g=_r(b, 2), diff_g=_r(b - s, 2), diff_pct=_r(100 * (b - s) / s, 1) if s else None, basis=basis))
        s_sum += s
        b_sum += b
    rows.append(dict(component="Sum of the five simulation components", sim_g=_r(s_sum, 2), bom_g=_r(b_sum, 2), diff_g=_r(b_sum - s_sum, 2),
                     diff_pct=_r(100 * (b_sum - s_sum) / s_sum, 1) if s_sum else None, basis=""))
    sim_total = (inputs.get("sim_total_kg") or 0) * 1000.0
    return dict(rows=rows, sim_total_g=_r(sim_total, 1), bom_total_g=totals["motor_mass_g"],
                extra_parts_g=_r(totals["motor_mass_g"] - b_sum, 2), sim_source=inputs.get("sim_source", ""))


def _explain(lines, comp, inputs, m):
    by = {l["key"]: l for l in lines}
    rows = {r["component"]: r for r in comp["rows"]}
    out = []
    d_tot = comp["bom_total_g"] - comp["sim_total_g"]
    five = rows.get("Sum of the five simulation components")
    if five:
        out.append("Total: BOM %.1f g vs simulation %.1f g = %+.1f g, made of %+.1f g in the five components the simulation counts "
                   "and %+.1f g of parts it does not model." % (comp["bom_total_g"], comp["sim_total_g"], d_tot, five["diff_g"], comp["extra_parts_g"]))
    sh = rows.get("Shaft")
    if sh and "shaft" in by:
        out.append("Shaft %+.1f g: the simulation shaft is a hollow tube of 50 mm2 section x stack length (%.1f g); the CAD shaft is the real stepped "
                   "part (%s), so the CAD value is the one to buy against." % (sh["diff_g"], sh["sim_g"], by["shaft"]["dimensions"].split(", journal")[0]))
    cu = rows.get("Copper windings")
    if cu and "coils" in by:
        ln = by["coils"]["_length_mm"]
        out.append("Copper %+.1f g (%+.1f %%): the simulation takes the straight slot copper (126 mm2 x stack) times an end-winding factor; the CAD coil is the "
                   "modelled conductor, %.1f mm per turn incl. the two leads." % (cu["diff_g"], cu["diff_pct"], ln / max(1, round(m.p("num_wires_per_slot")))))
    for nm in ("Stator core", "Rotor back-iron", "Magnets"):
        r = rows.get(nm)
        if r:
            out.append("%s %+.1f g (%+.1f %%): simulation masses come from CAD polygon sections at the geometry saved with the duty and are rounded to "
                       "1 g; the BOM uses the current model solid (air gap, fillets, key)." % (nm, r["diff_g"], r["diff_pct"]))
    out.append("Parts without a simulation mass: " + ", ".join("%s %.1f g" % (by[k]["part"].split(" (")[0], by[k]["total_mass_g"])
               for k in ("holder_front", "holder_back", "rings", "prop_fix", "bearings", "screws", "board", "pins", "fan_plates", "insulation_ceramic") if k in by and by[k]["total_mass_g"]) + ".")
    return out


def _header(dump, inputs):
    h = dict(die=inputs.get("die"), config=inputs.get("config"), motor_id=inputs.get("motor_id"), model=dump.get("model"),
             cad_variant="frameless" if is_frameless(dump, inputs) else "full",
             freecad_version=dump.get("freecad_version"), template_meta=dump.get("meta"), generated=datetime.datetime.now().isoformat(timespec="seconds"),
             bom_format=TEMPLATE)
    h.update(inputs.get("provenance") or {})
    return h


# ------------------------------------------------------------------------------------------------------ simulation side
def sim_components_from_config(path):
    """(components, total_kg, source text) of the LAST saved duty summary with mass_components in a config yaml."""
    import yaml
    d = yaml.safe_load(open(path, encoding="utf-8"))
    found = []

    def walk(o, p):
        if isinstance(o, dict):
            if "mass_components" in o:
                found.append((p, o))
            for k, v in o.items():
                walk(v, "%s/%s" % (p, k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (p, i))
    walk(d, "")
    if not found:
        return [], None, "no mass_components in " + str(path)
    p, o = found[-1]
    return o["mass_components"], o.get("mass_total_kg"), "%s%s (mass_total_kg %s)" % (str(path).replace("\\", "/").split("/")[-1], p, o.get("mass_total_kg"))


# ------------------------------------------------------------------------------------------------------ writers
CSV_COLUMNS = [("item", "item"), ("part", "part"), ("type", "type"), ("qty", "qty"), ("unit", "unit"), ("material", "material / grade"),
               ("dimensions", "key dimensions"), ("unit_mass_g", "unit mass g"), ("total_mass_g", "total mass g"),
               ("sim_mass_g", "simulation mass g"), ("source", "source / standard"), ("notes", "notes"), ("tbs", "to be specified")]


def _csv_value(l, k):
    if k == "tbs":
        return "yes" if l["to_be_specified"] else ""
    v = l.get(k)
    if k == "total_mass_g" and not l["in_total"] and v is not None:
        return ""
    return "" if v is None else v


def write_csv(bom, path, extra_columns=None):
    """extra_columns: {'col title': {line key: value}} appended after the standard columns (a caller may add its own columns)."""
    extra_columns = extra_columns or {}
    keys = [k for k, _ in CSV_COLUMNS]

    def row(d, extra=None):
        return [d.get(k, "") for k in keys] + [(extra or {}).get(c, "") for c in extra_columns]
    t, c = bom["totals"], bom["comparison"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([title for _, title in CSV_COLUMNS] + list(extra_columns))
        for l in bom["lines"]:
            w.writerow([_csv_value(l, k) for k in keys] + [extra_columns[col].get(l["key"], "") for col in extra_columns])
        w.writerow(row(dict(item="T1", part=("TOTAL mass of the frameless kit (BOM lines, model)" if t.get("scope") == FRAMELESS_SCOPE
                                              else "TOTAL mass of the BOM lines (model)"), type="total", unit="g", total_mass_g=t["motor_mass_g"])))
        w.writerow(row(dict(item="T2", part="Simulation passport mass (%s)" % c["sim_source"], type="total", unit="g", sim_mass_g=c["sim_total_g"])))
        for r in c["rows"]:
            w.writerow(row(dict(item="C", part="compare %s: BOM vs simulation %s %% %s" % (r["component"], r["diff_pct"], r["basis"]),
                                type="comparison", unit="g", total_mass_g=r["bom_g"], sim_mass_g=r["sim_g"])))
        w.writerow(row(dict(item="C", part="parts the simulation mass does not contain", type="comparison", unit="g", total_mass_g=c["extra_parts_g"])))
        for ex in c.get("explain", []):
            w.writerow(row(dict(item="E", part=ex, type="difference explained")))


def write_xlsx(bom, path, banner="", extra_sheets=None, sheet_name=None):
    """One sheet per config.  banner: text in row 1 of every sheet (e.g. INTERNAL - not for distribution).
    extra_sheets: callables f(workbook, bom, banner) that add caller-specific sheets."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name or (bom["header"].get("config") or "BOM")
    thin = Side(style="thin", color="BBBBBB")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    warn_fill = PatternFill("solid", fgColor="FFF2CC")
    flag_fill = PatternFill("solid", fgColor="FCE4D6")
    r = 1
    if banner:
        ws.cell(r, 1, banner).font = Font(bold=True, color="C00000", size=12)
        r += 1
    h = bom["header"]
    ws.cell(r, 1, "Bill of materials - %s (%s / %s)" % (h.get("motor_id"), h.get("die"), h.get("config"))).font = Font(bold=True, size=14)
    r += 1
    ws.cell(r, 1, "Generated %s from %s (FreeCAD %s); masses = model volume x density, laminations x stacking factor." % (
        h.get("generated"), h.get("model"), h.get("freecad_version"))).font = Font(italic=True, color="555555")
    r += 2
    ws.cell(r, 1, "TO BE SPECIFIED (%d)" % len(bom["to_be_specified"])).font = Font(bold=True, size=12, color="9C5700")
    r += 1
    for t in bom["to_be_specified"]:
        c = ws.cell(r, 1, "- " + t)
        c.fill = warn_fill
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(CSV_COLUMNS))
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 15 if len(t) < 150 else 30
        r += 1
    if bom["warnings"]:
        r += 1
        ws.cell(r, 1, "FLAGS").font = Font(bold=True, size=12, color="C00000")
        r += 1
        for t in bom["warnings"]:
            c = ws.cell(r, 1, "! " + t)
            c.fill = flag_fill
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(CSV_COLUMNS))
            r += 1
    r += 1
    hdr = r
    for j, (_, title) in enumerate(CSV_COLUMNS, 1):
        c = ws.cell(r, j, title)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = box
    r += 1
    first = r
    for l in bom["lines"]:
        for j, (k, _) in enumerate(CSV_COLUMNS, 1):
            c = ws.cell(r, j, _csv_value(l, k))
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = box
            if k == "tbs" and l["to_be_specified"]:
                c.fill = warn_fill
            if k in ("unit_mass_g", "total_mass_g", "sim_mass_g") and isinstance(c.value, (int, float)):
                c.number_format = "0.00"
        if not l["in_total"]:
            ws.cell(r, 9, "(in coils)").font = Font(italic=True, color="777777")
        if "NON-STOCK" in l["part"]:
            for j in range(1, len(CSV_COLUMNS) + 1):
                ws.cell(r, j).fill = flag_fill
        r += 1
    last = r - 1
    t, c = bom["totals"], bom["comparison"]
    r += 1
    ws.cell(r, 2, "Total mass of the BOM lines (model)").font = Font(bold=True)
    ws.cell(r, 9, "=SUMPRODUCT(--ISNUMBER(I%d:I%d),I%d:I%d)" % (first, last, first, last)).number_format = "0.0"
    ws.cell(r, 10, "g")
    r += 1
    ws.cell(r, 2, "Simulation passport mass (%s)" % c["sim_source"]).font = Font(bold=True)
    ws.cell(r, 9, c["sim_total_g"]).number_format = "0.0"
    ws.cell(r, 10, "g")
    r += 2
    ws.cell(r, 2, "Comparison with the simulation mass components").font = Font(bold=True)
    r += 1
    for j, tt in enumerate(["component", "simulation g", "BOM g", "difference g", "difference %", "basis"], 2):
        cc = ws.cell(r, j, tt)
        cc.font = Font(bold=True, color="FFFFFF")
        cc.fill = head_fill
    r += 1
    for row in c["rows"]:
        for j, k in enumerate(["component", "sim_g", "bom_g", "diff_g", "diff_pct", "basis"], 2):
            ws.cell(r, j, row[k])
        r += 1
    ws.cell(r, 2, "Parts the simulation mass does not contain (holders, rings, bearings, Prop_fix, screws, board, pins; shaft/enamel basis)")
    ws.cell(r, 4, c["extra_parts_g"])
    ws.cell(r, 5, "g")
    r += 2
    ws.cell(r, 2, "Difference explained").font = Font(bold=True)
    for ex in c.get("explain", []):
        r += 1
        cc = ws.cell(r, 2, ex)
        cc.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=12)
        ws.row_dimensions[r].height = 30
    widths = [6, 38, 16, 7, 7, 26, 52, 11, 11, 11, 40, 80, 9]
    for j, wd in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = wd
    ws.freeze_panes = ws.cell(hdr + 1, 3)
    ws.sheet_view.zoomScale = 90
    for fn in extra_sheets or []:
        fn(wb, bom, banner)
    wb.save(path)


def write_dump(dump, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dump, fh, indent=1)
