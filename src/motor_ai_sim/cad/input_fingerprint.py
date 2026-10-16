"""Fingerprint the complete, duty-independent input set of one CAD build.

The hash is intentionally based on build inputs rather than the complete YAML
documents: duty records, simulation operating points and UI metadata do not
change the parametric CAD model and must not enqueue a rebuild.
"""
from __future__ import annotations

import hashlib
import functools
import os
from pathlib import Path
from typing import Any, Dict, Iterable

from . import store


@functools.lru_cache(maxsize=4096)
def _file_hash(path: str, size: int, mtime_ns: int, ctime_ns: int) -> str:
    """Cache byte hashes by filesystem identity so API save checks stay cheap."""
    p = Path(path)
    data = p.read_bytes()
    if p.suffix.lower() in (".py", ".json", ".yaml", ".yml", ".txt", ".csv"):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _tree_hash(path: Path) -> str:
    """Hash file names and bytes under a file or directory, including non-Python assets."""
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.name.encode("utf-8"))
        st = path.stat()
        h.update(_file_hash(str(path.resolve()), st.st_size, st.st_mtime_ns, st.st_ctime_ns).encode())
        return h.hexdigest()
    if not path.is_dir():
        return "missing"
    ignored = {"__pycache__", ".git", ".pytest_cache"}
    for f in sorted(p for p in path.rglob("*") if p.is_file()
                    and not ignored.intersection(p.relative_to(path).parts)
                    and p.suffix.lower() not in (".pyc", ".pyo", ".tmp", ".bak", ".swp")
                    and not p.name.endswith("~")):
        h.update(f.relative_to(path).as_posix().encode("utf-8"))
        h.update(b"\0")
        st = f.stat()
        h.update(_file_hash(str(f.resolve()), st.st_size, st.st_mtime_ns, st.st_ctime_ns).encode())
    return h.hexdigest()


def _stat_signature(path: Path) -> tuple:
    """Fast change detector for cached current fingerprints; values are still content-hashed on misses."""
    ignored = {"__pycache__", ".git", ".pytest_cache"}
    if path.is_file():
        st = path.stat()
        return ((path.name, st.st_size, st.st_mtime_ns, st.st_ctime_ns),)
    if not path.is_dir():
        return (("missing",),)
    rows = []
    for f in sorted(p for p in path.rglob("*") if p.is_file()
                    and not ignored.intersection(p.relative_to(path).parts)
                    and p.suffix.lower() not in (".pyc", ".pyo", ".tmp", ".bak", ".swp")
                    and not p.name.endswith("~")):
        st = f.stat()
        rows.append((f.relative_to(path).as_posix(), st.st_size, st.st_mtime_ns, st.st_ctime_ns))
    return tuple(rows)


def _find_cards(doc: Any, names: Iterable[str]) -> Dict[str, Any]:
    """Return the complete card values for named cards, wherever the library nests them."""
    wanted = set(map(str, names))
    found: Dict[str, Any] = {}

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if str(key) in wanted:
                found[str(key)] = value
            visit(value)

    visit(doc)
    return {name: found.get(name, {"missing": True}) for name in sorted(wanted)}


def _load_yaml(path: str) -> Any:
    import yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _source_paths(eff: Dict[str, Any], root: str, cad_dir: str) -> Dict[str, str]:
    """Hashes of reference, mapping, template, and CAD exporter dependencies."""
    root_path, cad_path = Path(root), Path(cad_dir)
    base_profile, fusion_values = store.reference_for(eff["die"])
    mapping = store.load_mapping(root)
    sources = {"reference/profile": _tree_hash(Path(base_profile)),
               "reference/fusion_values": _tree_hash(Path(fusion_values))}
    for rel in (mapping.get("sources") or {}):
        sources["mapping/" + rel] = _tree_hash(root_path / rel)
    # Only geometry_schema from motor_config affects CAD mapping. Hashing the
    # whole live config would make duty points and other simulation edits stale.
    sources["mapping/geometry_schema"] = store.canonical_hash(mapping.get("schema") or {})
    template_dir = cad_path / "motor_templates" / "segmented_flatwire"
    sources["template/segmented_flatwire"] = _tree_hash(template_dir)
    board_step = os.environ.get("FC_PHASE_BOARD_STEP")
    if board_step:
        sources["reference/phase_board_step"] = _tree_hash(Path(board_step))
    else:
        sources["reference/phase_board"] = _tree_hash(cad_path / "reference" / "phase_board")
    # These Python modules and FreeCAD scripts can change geometry or exported
    # artifacts even when the parameter template itself stays byte-identical.
    for rel in ("src/motor_ai_sim/cad/build.py", "src/motor_ai_sim/cad/store.py",
                "src/motor_ai_sim/cad/manifest.py", "src/motor_ai_sim/cad/bom.py",
                "src/motor_ai_sim/cad/bom_store.py"):
        sources["exporter/" + rel] = _tree_hash(root_path / rel)
    sources["exporter/fc_scripts"] = _tree_hash(cad_path / "fc_scripts")
    return sources


def fingerprint_effective(eff: Dict[str, Any], *, root: str | None = None,
                          cad_dir: str | None = None, materials_library: str | None = None,
                          bearings_library: str | None = None,
                          die_document: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Return the full input snapshot and SHA-256 for a ``store.load_effective`` result.

    ``die_document`` supplies non-geometry die-level build inputs such as part
    material assignments. It is optional to keep this usable by the FreeCAD
    build image, which already has the effective geometry/config snapshot.
    """
    root = root or store.ROOT
    cad_dir = cad_dir or store.CAD_DIR
    die_dir = Path(eff["dies_dir"]) / eff["die"]
    loaded = eff.get("_build_input_documents") or {}
    if die_document is None:
        die_document = loaded.get("die") or _load_yaml(str(die_dir / "die.yaml"))

    mats = eff.get("materials") or {}
    part_materials = die_document.get("part_materials") or {}
    from . import bom_store
    material_names = [str(v) for v in mats.values() if v]
    material_names.extend(str(v) for v in part_materials.values() if v)
    material_names.extend(bom_store.EXTRA_CARDS)
    material_path = materials_library or store.default_materials_library()
    material_doc = loaded.get("materials")
    if material_doc is None:
        material_doc = _load_yaml(material_path) if os.path.isfile(material_path) else {}
    material_cards = _find_cards(material_doc, material_names)

    bearings = eff.get("bearings") or {}
    bearing_names = {str(v.get("card")) for v in bearings.values()
                     if isinstance(v, dict) and v.get("card")}
    bearing_names.update(str(v) for k, v in (eff.get("cad_overrides") or {}).items()
                         if k == store.BEARING_CARD_KEY and v)
    bearing_path = bearings_library or store.default_bearings_library()
    bearing_doc = loaded.get("bearings")
    if bearing_doc is None:
        bearing_doc = _load_yaml(bearing_path) if os.path.isfile(bearing_path) else {}
    bearing_cards = _find_cards(bearing_doc, bearing_names)
    wire_stock_path = bom_store.DEFAULT_WIRE_STOCK

    inputs = {
        "schema": "motres-cad-build-inputs/1",
        "die": eff["die"],
        "config": eff["variant"],
        "geometry": eff.get("geometry") or {},
        "geometry_overrides": eff.get("geometry_overrides") or {},
        "winding": eff.get("winding") or {},
        "materials": mats,
        "material_cards": material_cards,
        "part_materials": part_materials,
        "bearings": bearings,
        "bearing_cards": bearing_cards,
        "wire_stock_sha256": (eff.get("_build_input_hashes") or {}).get(
            "wire_stock", _tree_hash(Path(wire_stock_path))),
        "cad_overrides": store.clean_cad_overrides(eff.get("cad_overrides")),
        "cad_variant": eff.get("cad_variant") or store.DEFAULT_CAD_VARIANT,
        "sources": _source_paths(eff, root, cad_dir),
    }
    return {"build_inputs_hash": store.canonical_hash(inputs), "build_inputs": inputs}


def fingerprint_profile(profile: Dict[str, Any], *, root: str | None = None,
                        cad_dir: str | None = None) -> Dict[str, Any]:
    """Fingerprint a resolved standalone profile build that has no die/config store."""
    root = root or store.ROOT
    cad_dir = cad_dir or store.CAD_DIR
    from . import bom_store
    template_name = (profile.get("profile") or {}).get("template") or "segmented_flatwire"
    root_path, cad_path = Path(root), Path(cad_dir)
    board_step = os.environ.get("FC_PHASE_BOARD_STEP")
    board = Path(board_step) if board_step else cad_path / "reference" / "phase_board"
    base_profile, fusion_values = store.reference_for(str(profile.get("die") or "")) if profile.get("die") else (None, None)
    sources = {
        "template/" + template_name: _tree_hash(cad_path / "motor_templates" / template_name),
        "exporter/fc_scripts": _tree_hash(cad_path / "fc_scripts"),
        "exporter/wire_stock": _tree_hash(Path(bom_store.DEFAULT_WIRE_STOCK)),
        "reference/phase_board": _tree_hash(board),
    }
    if base_profile:
        sources["reference/profile"] = _tree_hash(Path(base_profile))
        sources["reference/fusion_values"] = _tree_hash(Path(fusion_values))
    for rel in ("src/motor_ai_sim/cad/build.py", "src/motor_ai_sim/cad/store.py",
                "src/motor_ai_sim/cad/manifest.py", "src/motor_ai_sim/cad/bom.py",
                "src/motor_ai_sim/cad/bom_store.py"):
        sources["exporter/" + rel] = _tree_hash(root_path / rel)
    inputs = {"schema": "motres-cad-build-inputs/1", "profile_sha256": store.canonical_hash(profile),
              "sources": sources}
    return {"build_inputs_hash": store.canonical_hash(inputs), "build_inputs": inputs}


def current_build_inputs(dies_dir: str, die: str, config: str, *,
                         materials_library: str | None = None,
                         bearings_library: str | None = None,
                         root: str | None = None,
                         cad_dir: str | None = None) -> Dict[str, Any]:
    """Load saved inputs and return their build fingerprint; errors propagate to callers."""
    root = str(root or store.ROOT)
    cad_dir = str(cad_dir or store.CAD_DIR)
    materials_library = str(materials_library or store.default_materials_library())
    bearings_library = str(bearings_library or store.default_bearings_library())
    # Always load mutable catalog/library inputs once per call, then hash that
    # exact snapshot. Source file bytes retain their stat-keyed hash cache.
    eff = store.load_effective(dies_dir, die, config, materials_library=materials_library,
                               bearings_library=bearings_library)
    return fingerprint_effective(eff, root=root, cad_dir=cad_dir,
                                 materials_library=materials_library,
                                 bearings_library=bearings_library)
