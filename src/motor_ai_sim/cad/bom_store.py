"""Store side of the BOM: simulation die / config + FreeCAD model dump -> <motor_id>_BOM.csv / .xlsx next to the STEP.

`bom.py` (pure structure, no commercial data) does the work; this module only assembles its inputs from the same store
files the generator already reads (effective machine, materials library, bearings library, wire stock) and writes the files.
Called by `build.py` after the STEP export; a BOM failure never refuses a build (the manifest records it).
"""
import json
import os

from . import bom, store

DEFAULT_WIRE_STOCK = os.path.join(os.path.dirname(store.DEFAULT_SERVER_MATERIALS), "wire_stock.yaml")
EXTRA_CARDS = ("copper", "polyimide", "Nomex", "Aluminium_6061", "Steel_42CrMo4_QT", "Stainless_420")
SKIP_KEYS = ("bh_curve", "core_loss_curves")


def _yaml(path):
    import yaml
    return yaml.safe_load(open(path, encoding="utf-8")) or {}


def assemble(eff, profile, materials_library=None, bearings_library=None, wire_stock=None):
    """Plain-data inputs of bom.build_bom for one effective machine (store.load_effective) and its profile."""
    cards = dict(eff.get("material_cards") or {})
    lib_path = materials_library or store.default_materials_library()
    if os.path.exists(lib_path):
        for cat, d in _yaml(lib_path).items():
            if isinstance(d, dict):
                for name in EXTRA_CARDS:
                    if name in d and name not in cards:
                        cards[name] = dict(category=cat, **{k: v for k, v in d[name].items() if k not in SKIP_KEYS})
    bcard = None
    used = [v.get("card") for v in (eff.get("bearings") or {}).values() if isinstance(v, dict) and v.get("card")]
    blib = bearings_library or store.default_bearings_library()
    if used and os.path.exists(blib):
        for v in _yaml(blib).values():
            if isinstance(v, dict) and used[0] in v:
                bcard = v[used[0]]
        bcard = bcard or _yaml(blib).get(used[0])
    ws_path = wire_stock or DEFAULT_WIRE_STOCK
    stock = (_yaml(ws_path).get("wires") or []) if os.path.exists(ws_path) else []
    comps, total, src = [], None, "no config yaml"
    cfg_file = os.path.join(eff["dies_dir"], eff["die"], eff["config"])
    if os.path.exists(cfg_file):
        comps, total, src = bom.sim_components_from_config(cfg_file)
    return dict(die=eff["die"], config=eff["variant"], motor_id=profile["profile"]["motor_id"], materials=profile["profile"]["materials"],
                cad_variant=(profile["profile"].get("cad_variant") or eff.get("cad_variant") or "full"),
                cards=cards, bearing_card=bcard, wire_stock=stock, winding=eff.get("winding"), sim_materials=eff.get("materials") or {},
                sim_components=comps, sim_total_kg=total, sim_source=src,
                provenance=dict(store_files={k: v["sha256"][:12] for k, v in (eff.get("files") or {}).items()}))


def write(dump_path, eff, profile, out_prefix, source=None, **kw):
    """Build the BOM from a bom_dump.py JSON and write <out_prefix>_BOM.csv (+ .xlsx when openpyxl is installed).
    Returns {files, lines, mass_g, sim_mass_g, to_be_specified, warnings}."""
    dump = json.load(open(dump_path, encoding="utf-8"))
    b = bom.build_bom(dump, assemble(eff, profile, **kw))
    files = [out_prefix + "_BOM.csv"]
    bom.write_csv(b, files[0], source=source)
    try:
        bom.write_xlsx(b, out_prefix + "_BOM.xlsx", **({"banner": "Source: " + source} if source else {}))
        files.append(out_prefix + "_BOM.xlsx")
    except ImportError:
        pass
    return dict(files=[os.path.basename(f) for f in files], lines=len(b["lines"]), mass_g=b["totals"]["motor_mass_g"],
                scope=b["totals"].get("scope", "full motor"), cad_variant=b["header"].get("cad_variant", "full"),
                sim_mass_g=b["comparison"]["sim_total_g"], to_be_specified=len(b["to_be_specified"]), warnings=b["warnings"],
                unclassified=b["unclassified"])
