"""Fusion parts the template has no geometry for (template A.7.8, CIANO28 150_35; owner 2026-10-20: the 3-D model of the 150_35 comes from Fusion).

`profile.fusion_parts.extras` lists Fusion components that exist in the owner's design but not in the parametric template (CIANO28 150: the two
fan plates "Fan 200 24s" and the 72 alumina insulation pieces "Insulation_ceramic").  `profile.fusion_parts.board` lists the components of the
Fusion phase board (8 insulation layers + 7 copper components); electronics.build_board_fusion builds the board from it.

    "extras": [{"name": "Fan_plates", "component": "Fan 200 24s", "file": "Fan_200_24s.step", "material": "Aluminium_6061",
                "occurrences": [[3 x 4], [3 x 4]], "volume_mm3": 3560.979685,   (one occurrence: the sum of the component's bodies)
                "group": "Fans", "role": "aluminium", "bom": {"key": "fan_plates", "title": "Fan plate (...)", "type": "manufactured"}}, ...]

Every solid of the component STEP is placed at every occurrence (component -> motor frame, mm) and the copies go into ONE Part::Feature
(a compound) per extra, inside the App::Part `Fusion_extras`.  Nothing is parametric: the shape is the Fusion export.  Gate: the solid count
is solids x occurrences and every occurrence carries the Fusion volume (1e-4 relative).
"""
import json

PART = "Fusion_extras"


def _cfg_dir(cfg):
    return cfg.get("dir", "")


def placed_solids(cfg, item):
    """[(occurrence index, solid in the motor frame)] of one extras / board item."""
    from . import fusion_parts as FP
    path, cands = FP.find_fusion_part(item["file"], _cfg_dir(cfg))
    if path is None:
        raise RuntimeError("Fusion STEP %s of %s not found; looked in: %s.  The part is the Fusion body (no fall-back): put the file there "
                           "or set $FC_FUSION_PARTS" % (item["file"], item.get("name") or item.get("component"), " | ".join(cands)))
    shape = FP.read_step(path)
    sols = list(shape.Solids)
    if FP.is_canonical(cfg, item.get("name") or item.get("component")):       # STEP round trip once (the build's STEP re-import check)
        sols = [FP._canonical(s) for s in sols]
    out = []
    for k, m12 in enumerate(item["occurrences"]):
        for s in sols:
            out.append((k, FP.transformed(s, FP.matrix(m12))))
    return out, path


def _props(f, item, path, n_solids):
    from . import fusion_parts as FP
    for prop, typ, val, tip in (
            ("SourceFile", "App::PropertyString", item["file"], "Fusion STEP the shape was read from (imported once, not parametric)"),
            ("SourceSHA256", "App::PropertyString", FP._sha256(path), "SHA-256 of that STEP"),
            ("FusionComponent", "App::PropertyString", item.get("component", ""), "Fusion component name"),
            ("FusionOccurrences", "App::PropertyInteger", len(item["occurrences"]), "occurrences of the component in the Fusion design"),
            ("FusionVolume", "App::PropertyFloat", float(item.get("volume_mm3", 0.0)), "Fusion volume of ONE occurrence (mm3), sum of its bodies"),
            ("SolidCount", "App::PropertyInteger", n_solids, "solids of the placed compound (gate)"),
            ("MaterialName", "App::PropertyString", item.get("material", ""), "material (library card) of the part"),
            ("BomKey", "App::PropertyString", (item.get("bom") or {}).get("key", ""), "bill-of-materials row key"),
            ("BomTitle", "App::PropertyString", (item.get("bom") or {}).get("title", ""), "bill-of-materials row name"),
            ("BomType", "App::PropertyString", (item.get("bom") or {}).get("type", "manufactured"), "bill-of-materials row type"),
            ("ViewGroup", "App::PropertyString", item.get("group", item.get("name", "")), "GLB group of the 3-D viewer"),
            ("ColourRole", "App::PropertyString", item.get("role", ""), "palette role (palette.py)"),
            ("SplitSides", "App::PropertyBool", bool(item.get("split_sides", False)), "GLB: one group per side (<group>_front / _back by the solid's z)")):
        f.addProperty(typ, prop, "Motor", tip)
        setattr(f, prop, val)


def build_extras(doc, cfg):
    """App::Part Fusion_extras with one Part::Feature (compound) per profile.fusion_parts.extras item.  Returns the report."""
    import Part
    items = cfg.get("extras") or []
    if not items:
        return None
    part = doc.addObject("App::Part", PART)
    part.Label = PART
    rep = {}
    for item in items:
        placed, path = placed_solids(cfg, item)
        f = doc.addObject("Part::Feature", item["name"])
        f.Label = item["name"]
        f.Shape = Part.makeCompound([s for _, s in placed])
        _props(f, item, path, len(placed))
        part.addObject(f)
        rep[item["name"]] = dict(step=item["file"], sha256=f.SourceSHA256, occurrences=len(item["occurrences"]), solids=len(placed),
                                 volume=round(f.Shape.Volume, 3))
    doc.recompute()
    return rep


def objects(doc):
    """The extras features (Part::Feature inside Fusion_extras), in build order."""
    part = doc.getObject(PART)
    if part is None:
        return []
    return [o for o in part.Group if o.TypeId == "Part::Feature"]


def solids(doc):
    """{name: [solids in the motor frame]} of the extras (the Part has the identity placement)."""
    return {o.Label: [s.copy() for s in o.Shape.Solids] for o in objects(doc)}


def gate(doc):
    errors = []
    for o in objects(doc):
        s = o.Shape
        if s.isNull() or len(s.Solids) != int(o.SolidCount):
            errors.append("Fusion extra %s: %d solids, the import recorded %d" % (o.Label, 0 if s.isNull() else len(s.Solids), o.SolidCount))
            continue
        if not all(sh.isClosed() for x in s.Solids for sh in x.Shells):
            errors.append("Fusion extra %s: an open shell" % o.Label)
        want = float(o.FusionVolume) * int(o.FusionOccurrences)
        if want > 0 and abs(s.Volume - want) > 1e-4 * want:
            errors.append("Fusion extra %s: volume %.3f mm3, the Fusion component gives %.3f mm3 (wrong or stale STEP)" % (o.Label, s.Volume, want))
    return errors


def bom_props(o):
    """The properties bom_dump copies into the dump (BOM rows of the extras and the Fusion board)."""
    out = {}
    for k in ("MaterialName", "BomKey", "BomTitle", "BomType", "FusionComponent", "FusionOccurrences", "FusionVolume", "SolidMaterials"):
        if hasattr(o, k):
            v = getattr(o, k)
            out[k] = json.loads(v) if k == "SolidMaterials" and v else v
    return out
