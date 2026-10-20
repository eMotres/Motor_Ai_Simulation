"""Bill of materials of a model whose passive parts ARE the Fusion bodies (template A.7.9: CILN28 / G2-L40, owner 2026-10-20).

Same structure and the same file format as ``bom.build_bom`` (lines, totals, comparison, to_be_specified, warnings, header): the CSV / XLSX writers and
``modules.motor_mass`` read it unchanged.  What differs is where the lines come from:

* the ACTIVE parts (stator lamination, rotor lamination, magnets, coils) are the Fusion bodies too (``profile.fusion_parts.files``); they are weighed like
  the generic BOM does (model solid x stacking factor x density of the simulation card).  ``modules.motor_mass`` takes the active masses from the
  APP (``masses.compute_masses``), never from these rows; the comparison block shows the two side by side;
* every other part is an ``extras`` item of ``profile.fusion_parts`` (``fusion_extras.py``): one Part::Feature per Fusion component, carrying its BOM
  row (``BomKey``, ``BomTitle``), its density and where the density comes from.  Items that share a ``BomKey`` (the four screw families, the seven
  copper layers of the phase board) are ONE row, summed;
* the row titles start with the words ``motor_mass`` / ``config/part_materials.yaml`` classify by ("Rotor carrier, front", "Stator holder, back",
  "Housing sleeve", "Coolant fitting", "Fan", "Bearing", "Screws", "Phase board copper", "Phase board insulation", "Ceramic insulation");
* coils are BARE copper (owner rule 2026-10-18: the 0.02 - 0.05 mm strip insulation is not modelled and not counted).

Masses: model volume x density; every density says where it comes from.  The Fusion material labels are NOT used for masses (the Fusion model labels
the magnets "Steel" and the cores "20SW1200"; the owner's decisions are N52UH and B15AHV950M).
"""
from . import bom as B

#: BOM keys in the order of the bill (the generic lines first, then the Fusion parts in this order, then the consumables)
ORDER = ("carrier_front", "carrier_rear", "holder_front", "holder", "holder_back", "housing", "fittings", "fan", "bearings", "screws",
         "board_copper", "board_insulation", "insulation_ceramic")

TBS = {
    "stator_lam": "stack bonding / segment joining (welding, cleat, bonding varnish) not named in the model or the simulation",
    "magnets": "magnet coating / tolerance / magnetisation / grading (the model gives the shape only); adhesive between the 5 mm segments",
    "coils": "lead trimming, lead ends and joints; strip insulation (0.02 - 0.05 mm) is not modelled",
    "bearings": "the Fusion model carries the 61814-2RZ shield, the owner's decision is the 61814-2RS1 (contact seals): same d x D x B, the "
                "mass of a rubber seal differs from a steel shield by a few grams",
    "fan": "alloy / temper of the 1 mm sheet; the flat pattern (laser-cut DXF) is in the production set",
    "board_copper": "copper weight / plating / board house (the production set says 0.4 mm copper sheet layers, the Fusion layers are 0.3 mm)",
    "board_insulation": "laminate grade of the plates",
    "housing": "manufacturing route (tube stock or turned from bar); the production set says FR4 for the housing ring, Fusion says aluminium 6061",
    "fittings": "hose barb / thread type and surface finish",
}


def _dims(e):
    b = e["bbox"]
    return "bbox %.1f x %.1f x %.1f mm" % (b[3] - b[0], b[4] - b[1], b[5] - b[2])


def _items(m):
    """{BomKey: [dump entries]} of the Fusion extras, in the order of ORDER (unknown keys last)."""
    groups = {}
    for e in m.entries:
        mp = e.get("motor") or {}
        if mp.get("BomKey") and e["label"] not in m.claimed:
            groups.setdefault(mp["BomKey"], []).append(e)
    order = [k for k in ORDER if k in groups] + [k for k in groups if k not in ORDER]
    return {k: groups[k] for k in order}


def build_bom_fusion(dump, inputs):
    m = B.Model(dump)
    cards = inputs.get("cards", {})
    cfg = inputs["config"]
    warns, tbs, lines = [], [], []

    def add(key, name, typ, qty, unit, material, dims, unit_g=None, source="", notes="", sim_g=None, in_total=True, tbs_text=None):
        lines.append(dict(key=key, part=name, type=typ, qty=qty, unit=unit, material=material, dimensions=dims,
                          unit_mass_g=B._r(unit_g, 4), total_mass_g=B._r(unit_g * qty, 3) if unit_g is not None else None,
                          sim_mass_g=B._r(sim_g, 2), source=source, notes=notes, in_total=in_total, to_be_specified=bool(tbs_text)))
        if tbs_text:
            tbs.append("%s: %s" % (name, tbs_text))

    for e in dump["entries"]:
        if e.get("state"):
            warns.append("model object %s is %s - shapes may be stale" % (e["label"], "/".join(e["state"])))
    L = m.p("motor_length")
    doc_name = (dump.get("meta") or {}).get("motor_fusion_document") or "Fusion document"
    steel = inputs["sim_materials"].get("stator_core") or "B15AHV950M"
    sc = cards.get(steel, {})
    kf = float(sc.get("stacking_factor") or 0.95)
    rho_fe = float(sc.get("density") or 7650)
    t_sheet = B._sheet_thickness(sc)
    n_sheets = int(round(L * kf / t_sheet))
    lam_note = ("%d sheets = round(L %.0f mm x k_f %.2f / %.2f mm); k_f %.2f = stacking_factor of card %s in materials_library.yaml (the factor the "
                "simulation masses use); mass = Fusion solid x k_f x %.0f kg/m3 (%s)" % (n_sheets, L, kf, t_sheet, kf, steel, rho_fe, steel))
    sim = {c["name"].split(" (")[0]: c for c in inputs.get("sim_components", [])}
    simg = lambda k: (sim[k]["mass_kg"] * 1000.0) if k in sim else None

    core = m.get("Core")
    if core:
        add("stator_lam", "Stator lamination (laser-cut / stamped, stacked) - the Fusion core", "manufactured", n_sheets, "pcs", steel,
            "OD %s mm, bore %s mm, %d slots, sheet %.2f mm, stack %.0f mm" % (
                B._num(2 * m.p("stator_up_r"), 2), B._num(2 * m.p("stator_down_r"), 2), round(m.p("num_seg") * 6), t_sheet, L),
            core["volume"] * kf * rho_fe * 1e-6 / n_sheets, "Fusion component Core (%s), material per owner decision 2026-10-18: Baosteel %s "
            "(the Fusion label 20SW1200 is wrong)" % (doc_name, steel), lam_note, sim_g=simg("Stator core"), tbs_text=TBS["stator_lam"])
    rot = m.get("RotorCore")
    if rot:
        steel_r = inputs["sim_materials"].get("rotor_core") or steel
        add("rotor_lam", "Rotor lamination (magnet pockets, keys), stacked - the Fusion rotor", "manufactured", n_sheets, "pcs", steel_r,
            "OD %s mm, bore %s mm, %d magnet pockets, sheet %.2f mm, stack %.0f mm" % (
                B._num(2 * m.p("rotor_up_r"), 2), B._num(2 * (m.p("rotor_down_r") - m.p("rotor_house_height")), 2), round(m.p("rotor_poles")), t_sheet, L),
            rot["volume"] * kf * rho_fe * 1e-6 / n_sheets, "Fusion component Rotor (%s)" % doc_name, lam_note, sim_g=simg("Rotor back-iron"))
    mag = m.get("Magnet")
    mags = m.instances("Magnet")
    if mag and mags:
        mcard = inputs["sim_materials"].get("magnet", "?")
        rho_m, rho_src = B._card_density(cards, mcard)
        rho_m = rho_m or 7500.0
        w, h, ln = B._bb(mag)
        slices = round(m.params.get("magnet_slices", 1) or 1)
        for lbl in ("MagnetStack", "MagnetArray"):
            m.get(lbl)
        n_mag = round(m.p("rotor_poles")) * slices
        add("magnets", "Permanent magnet (sintered NdFeB, ground, coated) - Fusion slices", "manufactured (custom shape)", n_mag, "pcs", mcard,
            "%.2f x %.2f x %.0f mm bounding box (width x radial x length), %d poles x %d axial slices" % (w, h, ln, round(m.p("rotor_poles")), slices),
            mag["volume"] * rho_m * 1e-6, "Fusion component Magnet (%s, %d slices of %.0f mm per pole); simulation magnet card %s" % (doc_name, slices, ln, mcard),
            "every pole is a stack of %d axial slices of %.0f mm (Fusion mag_step; the simulation loss model uses magnet_lamination = 5 mm); density %.0f kg/m3 (%s); "
            "the Fusion label of the material is 'Steel' (placeholder)" % (slices, ln, rho_m, rho_src), sim_g=simg("Magnets"), tbs_text=TBS["magnets"])
    coil = m.get("Coil")
    coils = m.instances("Coil")
    if coil and coils:
        ww, wh = m.p("wire_width"), m.p("wire_height")
        rho_cu, cu_src = B.density_of("Copper", inputs)
        length = coil["volume"] / (ww * wh)
        n_coils = coils["n_solids"]
        cu_g = coil["volume"] * rho_cu * 1e-6
        add("coils", "Coil (edge-wound flat copper strip, bare) - the Fusion coil", "manufactured", n_coils, "pcs", "Copper (bare)",
            "%d turns of %.1f x %.2f mm strip, conductor length %.1f mm per coil incl. leads" % (round(m.p("num_wires_per_slot")), ww, wh, length),
            cu_g, "Fusion component Coil (%s, 65 conductor bodies fused into one solid)" % doc_name,
            "BARE copper as drawn (owner rule 2026-10-18); total conductor length %.2f m for %d coils; density %.0f kg/m3 (%s); the volume is the UNION of the "
            "65 Fusion bodies (Fusion's body sum counts the overlaps at the bends twice)" % (length * n_coils / 1000.0, n_coils, rho_cu, cu_src),
            sim_g=simg("Copper windings"), tbs_text=TBS["coils"])
        lines[-1]["_cu_g_unit"] = cu_g
        lines[-1]["_length_mm"] = length

    for key, ents in _items(m).items():
        for e in ents:
            m.claimed.add(e["label"])
        mps = [e["motor"] for e in ents]
        title = mps[0].get("BomTitle") or ents[0]["label"]
        typ = mps[0].get("BomType") or "manufactured"
        mass_g = vol = 0.0
        qty = 0
        mats, dsrc, fusion_mats, parts = [], [], [], []
        for e, mp in zip(ents, mps):
            rho = float(mp.get("DensityKgM3") or 0.0)
            if rho <= 0:
                raise KeyError("Fusion part %s has no density (profile.fusion_parts.extras.density_kg_m3)" % e["label"])
            mass_g += e["volume"] * rho * 1e-6
            vol += e["volume"]
            n_occ = max(1, int(mp.get("FusionOccurrences") or 1))
            qty += e["n_solids"] if mp.get("CountSolids") else n_occ
            if mp.get("MaterialName") not in mats:
                mats.append(mp.get("MaterialName"))
            if mp.get("DensitySource") not in dsrc:
                dsrc.append(mp.get("DensitySource"))
            fusion_mats += [x for x in (mp.get("FusionMaterials") or []) if x not in fusion_mats]
            parts.append("%s x%d" % (mp.get("FusionComponent") or e["label"], n_occ))
        unit_g = mass_g / qty
        dens = mass_g / vol * 1e6 if vol else 0.0         # g / mm3 -> kg / m3
        bb = [min(e["bbox"][i] for e in ents) for i in range(3)] + [max(e["bbox"][i] for e in ents) for i in range(3, 6)]
        dims = _dims(dict(bbox=bb)) + ("; %s" % ", ".join(parts) if len(ents) > 1 else "")
        add(key, title, typ, qty, "pcs", " + ".join(mats),
            dims, unit_g, "Fusion component%s %s (%s, read-only STEP export)" % ("s" if len(ents) > 1 else "", ", ".join(sorted({(mp.get("FusionComponent") or "?") for mp in mps})), doc_name),
            "the Fusion bodies at their Fusion placement; density %.0f kg/m3 (%s); Fusion material label%s: %s" % (
                dens, "; ".join(x for x in dsrc if x), "s" if len(fusion_mats) > 1 else "", ", ".join(fusion_mats) or "-"),
            tbs_text=TBS.get(key))

    add("adhesive", "Adhesive (magnet / coil / lamination bonding)", "material", 1, "set", "to be specified", "not named in the model", None, "-",
        "no adhesive type named in the simulation or the CAD", tbs_text="adhesive type and amount")
    add("varnish_stator", "Stator impregnation varnish (rated 200 C)", "material", 1, "set", "to be specified", "-", None, "owner decision 2026-10-18",
        "mandatory: the stator is impregnated; the varnish must be rated up to 200 C; brand and datasheet to follow from the owner", tbs_text="varnish brand and amount")
    add("varnish_rotor", "Rotor impregnation varnish (cure at 80 C at most)", "material", 1, "set", "to be specified", "-", None, "owner decision 2026-10-18",
        "mandatory: the rotor is impregnated; the cure must not exceed 80 C so the magnets are not demagnetised", tbs_text="varnish brand and amount")
    add("leads", "Power leads / soldering", "material", 1, "set", "to be specified", "-", None, "-", "not in the CAD", tbs_text="power leads (wire, length, termination) and solder")

    unclassified = []
    skip_templates = {e.get("base") for e in m.entries if e.get("base")}
    for e in m.unclaimed():
        if e["label"] in skip_templates:
            continue
        unclassified.append(e)
        warns.append("model object %s (%d solid(s), %.1f mm3) has no BOM rule - listed as unclassified" % (e["label"], e["n_solids"], e["volume"]))
        add("x_" + e["label"], "UNCLASSIFIED model object: " + e["label"], "unknown", e["n_solids"], "solids", "?",
            "volume %.1f mm3, bbox %s" % (e["volume"], [round(x, 2) for x in e["bbox"]]), None, "model", "no rule in bom_fusion.build_bom_fusion",
            tbs_text="classify model object " + e["label"])
    for i, ln_ in enumerate(lines, 1):
        ln_["item"] = i
    totals = B._totals(lines)
    comparison = B._compare(lines, inputs, totals)
    comparison["explain"] = _explain(lines, comparison)
    return dict(header=B._header(dump, inputs), lines=lines, totals=totals, comparison=comparison,
                to_be_specified=tbs, warnings=warns, unclassified=[e["label"] for e in unclassified])


def _explain(lines, comp):
    by = {l["key"]: l for l in lines}
    rows = {r["component"]: r for r in comp["rows"]}
    out = []
    five = rows.get("Sum of the five simulation components")
    d_tot = comp["bom_total_g"] - comp["sim_total_g"]
    if five:
        out.append("Total: BOM %.1f g vs simulation %.1f g = %+.1f g, made of %+.1f g in the components the simulation counts and %+.1f g of parts it "
                   "does not model." % (comp["bom_total_g"], comp["sim_total_g"], d_tot, five["diff_g"], comp["extra_parts_g"]))
    for nm in ("Stator core", "Rotor back-iron", "Magnets", "Copper windings"):
        r = rows.get(nm)
        if r:
            out.append("%s %+.1f g (%+.1f %%): the BOM weighs the Fusion body (with its CAD-only assembly clearances and, for the coil, the real leads and "
                       "bends); the simulation weighs its nominal 2-D section x stack length (copper with its end-winding factor)." % (
                           nm, r["diff_g"], r["diff_pct"] if r["diff_pct"] is not None else 0.0))
    out.append("Parts without a simulation mass: " + ", ".join("%s %.1f g" % (by[k]["part"].split(" (")[0], by[k]["total_mass_g"])
               for k in ORDER if k in by and by[k]["total_mass_g"]) + ". The simulation shaft tube is replaced by the two rotor carriers (aluminium hubs, no steel shaft).")
    return out
