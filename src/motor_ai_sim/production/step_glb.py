"""STEP -> GLB for the production module's part viewer (owner 2026-11-12: tooling parts get a rotatable 3-D view).

A production part (a press die, a fixture) carries its STEP file.  The browser viewer takes GLB, so the first request for a
part's view converts the STEP once and keeps the result next to the production's files (``viewcache/``); later requests read
the cache.  The conversion runs in a CHILD process (OpenCASCADE can use a lot of memory or crash on a damaged file, and must
never take the API down), at low priority, one at a time, with a time limit.

The tessellation uses the cadquery / OCP already in the API image (no FreeCAD is involved on the request path).  The GLB has
the layout the viewer reads (cad/fc_scripts/glb_writer.py): millimetres, the CAD frame (Z up), a ``Model`` node whose
children are the parts, each carrying ``extras`` ``{family, explode}``.  A face is tessellated on its own, so normals are smooth
inside a face and hard across faces.

Colours (owner 2026-11-23: "upload the coloured STEP" - a KiCad board export carries a colour per part): a STEP that names colours
(``COLOUR_RGB`` / ``DRAUGHTING_PRE_DEFINED_COLOUR``) is read through the XCAF document (``STEPCAFControl_Reader``): the colour of
each face is its own surface colour, else its solid's, else the one inherited from the assembly (OCCT's own style resolution,
``XCAFPrs``), surface colour before the generic one.  One glTF material per distinct colour; the faces of one solid with different
colours are separate primitives of that solid's ONE mesh, so a part stays one node (picking, exploded view, outlines per part).  A
face without a colour keeps the steel grey.  A STEP without colour entities takes the earlier path unchanged, so its GLB is
byte-identical to before; so does a coloured STEP whose colour reader raises an ordinary exception (logged).  Not covered by that
fallback, on purpose: a ``StepConversionError`` (the file has no geometry - the plain path would say the same), a crash or the
timeout of the child process.  A STEP that names an EXTERNAL document reference never reaches the XCAF reader (it could load other
files from the disk; the bound OCCT has no switch to turn that off): it keeps the plain view, which does not follow references.

Part names (owner 2026-11-24: the component tree and the Frameless button of the project model): the coloured path reads the STEP's
component name of every part and writes it as ``extras.label``; the node name stays the unique ``Part_<n>``.  The plain path (a file
without colours) has no names and is unchanged.

  python -m motor_ai_sim.production.step_glb in.step out.glb      (what the child process runs)
"""
from __future__ import annotations

import json
import logging
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

#: bump when the writer or the tessellation changes: cached views of an older version are not read
VERSION = "v3"                              # v2: STEP colours (a file without colours converts exactly as v1); v3: part names (extras.label)
#: what the GLB of a STEP without colours names as its converter: that path IS the v1 conversion, byte for byte
PLAIN_VERSION = "v1"
#: a STEP over this size is refused for the viewer (the download stays available)
MAX_STEP_BYTES = 30 * 1024 * 1024
#: seconds the child may run
TIMEOUT_S = 120
#: the colour of a tooling part (steel grey), roughness / metallic as the motor GLBs
COLOUR = (0.62, 0.66, 0.72)

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


class StepConversionError(Exception):
    """The STEP could not be turned into a view (detail is safe to show to the caller)."""

    def __init__(self, detail: str, status: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def looks_like_step(head: bytes) -> bool:
    """An ISO 10303-21 file starts with ``ISO-10303-21;`` (blank space before it is tolerated)."""
    return head.lstrip()[:12].upper() == b"ISO-10303-21"


# ── GLB writer (numpy only) ──────────────────────────────────────────────────────────────────────────────────────

def build_glb(solids: list[tuple[str, "object", "object", "object"]]) -> bytes:
    """``solids`` = ``[(name, positions (N,3) float32, normals (N,3) float32, indices (M,) uint32), ...]`` -> GLB bytes."""
    import numpy as np

    bin_parts: list[bytes] = []
    views: list[dict] = []
    accessors: list[dict] = []
    nodes: list[dict] = [{"name": "Model", "children": list(range(1, len(solids) + 1))}]
    meshes: list[dict] = []
    offset = 0

    def add_view(data: bytes, target: int) -> int:
        nonlocal offset
        pad = (-offset) % 4
        if pad:
            bin_parts.append(b"\0" * pad)
            offset += pad
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        bin_parts.append(data)
        offset += len(data)
        return len(views) - 1

    for i, (name, pos, nrm, idx) in enumerate(solids):
        pos = np.ascontiguousarray(pos, dtype="<f4")
        nrm = np.ascontiguousarray(nrm, dtype="<f4")
        idx = np.ascontiguousarray(idx, dtype="<u4")
        a_pos = len(accessors)
        accessors.append({"bufferView": add_view(pos.tobytes(), 34962), "componentType": 5126, "count": int(len(pos)), "type": "VEC3",
                          "min": [float(x) for x in pos.min(axis=0)], "max": [float(x) for x in pos.max(axis=0)]})
        a_nrm = len(accessors)
        accessors.append({"bufferView": add_view(nrm.tobytes(), 34962), "componentType": 5126, "count": int(len(nrm)), "type": "VEC3"})
        a_idx = len(accessors)
        accessors.append({"bufferView": add_view(idx.tobytes(), 34963), "componentType": 5125, "count": int(len(idx)), "type": "SCALAR"})
        meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": a_pos, "NORMAL": a_nrm}, "indices": a_idx, "material": 0, "mode": 4}]})
        nodes.append({"name": name, "mesh": i, "extras": {"family": name, "explode": 0}})

    blob = b"".join(bin_parts)
    blob += b"\0" * ((-len(blob)) % 4)
    doc = {
        "asset": {"version": "2.0", "generator": "motor_ai_sim.production.step_glb", "extras": {"units": "mm", "converter": PLAIN_VERSION}},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes, "meshes": meshes,
        "materials": [{"name": "steel", "pbrMetallicRoughness": {"baseColorFactor": [*COLOUR, 1.0], "metallicFactor": 0.1, "roughnessFactor": 0.55},
                       "doubleSided": True}],
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (struct.pack("<III", GLB_MAGIC, 2, total) + struct.pack("<II", len(js), CHUNK_JSON) + js
            + struct.pack("<II", len(blob), CHUNK_BIN) + blob)


def smooth_normals(pos, tri):
    """Area-weighted vertex normals of ONE face patch (hard edges stay hard because faces are meshed separately)."""
    import numpy as np
    p = pos[tri]
    fn = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])               # length = 2 * triangle area
    n = np.zeros_like(pos)
    for k in range(3):
        np.add.at(n, tri[:, k], fn)
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    return (n / ln[:, None]).astype("float32")


def merge_faces(patches: list[tuple["object", "object"]]):
    """Join per-face ``(positions, triangles)`` patches into one ``(positions, normals, indices)``."""
    import numpy as np
    pos_l, nrm_l, idx_l, base = [], [], [], 0
    for pos, tri in patches:
        pos = np.asarray(pos, dtype="float32")
        tri = np.asarray(tri, dtype="int64").reshape(-1, 3)
        if len(pos) == 0 or len(tri) == 0:
            continue
        pos_l.append(pos)
        nrm_l.append(smooth_normals(pos, tri))
        idx_l.append((tri + base).reshape(-1).astype("uint32"))
        base += len(pos)
    if not pos_l:
        raise StepConversionError("The STEP file has no solid geometry.")
    return np.concatenate(pos_l), np.concatenate(nrm_l), np.concatenate(idx_l)


def _material(name: str, rgb) -> dict:
    return {"name": name, "pbrMetallicRoughness": {"baseColorFactor": [float(c) for c in rgb] + [1.0], "metallicFactor": 0.1,
                                                   "roughnessFactor": 0.55}, "doubleSided": True}


def build_coloured_glb(parts: list[tuple], palette: list) -> bytes:
    """``parts`` = ``[(name, [(positions, normals, indices, material), ...], component name), ...]``: one node and ONE mesh per part,
    one primitive per colour of its faces; ``palette[material]`` = linear RGB, ``palette[0]`` the steel grey of a face without a colour."""
    import numpy as np

    bin_parts: list[bytes] = []
    views: list[dict] = []
    accessors: list[dict] = []
    nodes: list[dict] = [{"name": "Model", "children": list(range(1, len(parts) + 1))}]
    meshes: list[dict] = []
    offset = 0

    def add_view(data: bytes, target: int) -> int:
        nonlocal offset
        pad = (-offset) % 4
        if pad:
            bin_parts.append(b"\0" * pad)
            offset += pad
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        bin_parts.append(data)
        offset += len(data)
        return len(views) - 1

    for i, (name, prims, label) in enumerate(parts):
        primitives = []
        for pos, nrm, idx, mat in prims:
            pos = np.ascontiguousarray(pos, dtype="<f4")
            nrm = np.ascontiguousarray(nrm, dtype="<f4")
            idx = np.ascontiguousarray(idx, dtype="<u4")
            a_pos = len(accessors)
            accessors.append({"bufferView": add_view(pos.tobytes(), 34962), "componentType": 5126, "count": int(len(pos)), "type": "VEC3",
                              "min": [float(x) for x in pos.min(axis=0)], "max": [float(x) for x in pos.max(axis=0)]})
            a_nrm = len(accessors)
            accessors.append({"bufferView": add_view(nrm.tobytes(), 34962), "componentType": 5126, "count": int(len(nrm)), "type": "VEC3"})
            a_idx = len(accessors)
            accessors.append({"bufferView": add_view(idx.tobytes(), 34963), "componentType": 5125, "count": int(len(idx)), "type": "SCALAR"})
            primitives.append({"attributes": {"POSITION": a_pos, "NORMAL": a_nrm}, "indices": a_idx, "material": int(mat), "mode": 4})
        meshes.append({"name": name, "primitives": primitives})
        extras = {"family": name, "explode": 0}
        if label:
            extras["label"] = label
        nodes.append({"name": name, "mesh": i, "extras": extras})

    blob = b"".join(bin_parts)
    blob += b"\0" * ((-len(blob)) % 4)
    materials = [_material("steel", COLOUR)] + [_material("colour_%02x%02x%02x" % tuple(int(round(c * 255)) for c in rgb), rgb)
                                                for rgb in palette[1:]]
    doc = {
        "asset": {"version": "2.0", "generator": "motor_ai_sim.production.step_glb", "extras": {"units": "mm", "converter": VERSION}},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes, "meshes": meshes, "materials": materials,
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (struct.pack("<III", GLB_MAGIC, 2, total) + struct.pack("<II", len(js), CHUNK_JSON) + js
            + struct.pack("<II", len(blob), CHUNK_BIN) + blob)


# ── the conversion itself (needs cadquery / OCP; runs in the child process) ──────────────────────────────────────────

COLOUR_ENTITIES = frozenset({b"COLOUR_RGB", b"DRAUGHTING_PRE_DEFINED_COLOUR"})
#: the entities through which a STEP points at ANOTHER file (what OCCT's STEPConstruct_ExternRefs follows)
EXTERNAL_ENTITIES = frozenset({b"DOCUMENT_FILE", b"PRODUCT_DEFINITION_WITH_ASSOCIATED_DOCUMENTS", b"APPLIED_DOCUMENT_REFERENCE",
                               b"DOCUMENT_PRODUCT_EQUIVALENCE", b"DOCUMENT_PRODUCT_ASSOCIATION", b"EXTERNALLY_DEFINED_ITEM"})
# a comment, a quoted string ('' is a quote inside it), or an entity keyword (a name directly followed by its parenthesis).  Linear
# time on any input: a keyword starts only where no name character precedes it, and the possessive quantifiers never re-scan a long
# name or a long run of spaces (a 1 MB name must not cost the converter's time budget)
_LEXEME = re.compile(rb"/\*.*?\*/|'(?:[^']++|'')*+'|(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*+)\s*+\(", re.S)


def step_entities(step_path: str, wanted: frozenset) -> set:
    """The entity types among ``wanted`` that the STEP really instantiates - never a word inside a comment or a quoted name.  The
    whole file is read at once (the viewer refuses a STEP over ``MAX_STEP_BYTES``), so no token is split at a read boundary."""
    data = Path(step_path).read_bytes()
    found = set()
    for m in _LEXEME.finditer(data):
        word = m.group(1)
        if word is not None and word.upper() in wanted:
            found.add(word.upper())
            if found == wanted:
                break
    return found


def names_colours(step_path: str) -> bool:
    """Does the STEP instantiate any colour entity?  (A file without one never reaches the colour reader.)"""
    return bool(step_entities(step_path, COLOUR_ENTITIES))


def _rgb(style):
    """The RGB of an XCAF style: the surface colour first, then the generic one (``XCAFPrs_Style`` keeps both); else None.
    LINEAR RGB: OCCT's ``Quantity_Color.Red()`` / ``Green()`` / ``Blue()`` are linear (the STEP's sRGB values are converted on read),
    and glTF's ``baseColorFactor`` is linear too - so it is written as it is; never gamma-convert it again."""
    if style.IsSetColorSurf():
        c = style.GetColorSurf()
        return (round(c.Red(), 6), round(c.Green(), 6), round(c.Blue(), 6))
    if hasattr(style, "IsSetColorCurv") and style.IsSetColorCurv():
        c = style.GetColorCurv()
        return (round(c.Red(), 6), round(c.Green(), 6), round(c.Blue(), 6))
    return None


#: names OCCT makes up itself for an unnamed instance: not a component name
_GENERATED_NAME = re.compile(r"^(=>\[.*\]|NAUO\d+|\d+(:\d+)+|SOLID|COMPOUND|SHELL|Open CASCADE STEP translator.*)$", re.IGNORECASE)
_LABEL_MAX = 120


def clean_label(raw) -> str:
    """A component name as shown in the tree: one line, at most ``_LABEL_MAX`` characters; "" for a generated or empty name."""
    text = " ".join("".join(c if c.isprintable() else " " for c in str(raw or "")).split())[:_LABEL_MAX]
    return "" if _GENERATED_NAME.match(text) else text


def _component_name(node) -> str:
    """The name of a leaf of the assembly: the instance's own name (Fusion "Core:1"), else the part's."""
    from OCP.TDataStd import TDataStd_Name
    for label in (node.Label, node.RefLabel):
        attr = TDataStd_Name()
        if not label.IsNull() and label.FindAttribute(TDataStd_Name.GetID_s(), attr):
            text = clean_label(attr.Get().ToExtString())
            if text:
                return text
    return ""


def coloured_solids(step_path: str):
    """``[(solid, [(face, rgb), ...], component name), ...]`` in the document's order, solids and faces already placed, ``rgb`` =
    linear RGB or None per face; None when the document holds no colour at all.  Raises on a reader failure (the caller falls back)."""
    import cadquery as cq
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.XCAFPrs import XCAFPrs, XCAFPrs_DocumentExplorer, XCAFPrs_DocumentExplorerFlags_OnlyLeafNodes, XCAFPrs_IndexedDataMapOfShapeStyle

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    if reader.ReadFile(str(step_path)) != IFSelect_RetDone or not reader.Transfer(doc):
        raise RuntimeError("STEPCAFControl_Reader could not read the file")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    out, any_colour = [], False
    explorer = XCAFPrs_DocumentExplorer(doc, XCAFPrs_DocumentExplorerFlags_OnlyLeafNodes)
    while explorer.More():
        node = explorer.Current()
        shape = shape_tool.GetShape_s(node.RefLabel)              # in the part's own frame; node.Location places it
        own = _rgb(node.Style)                                    # the leaf's colour, the assembly's inherited when it has none
        component = _component_name(node)
        styles = XCAFPrs_IndexedDataMapOfShapeStyle()
        XCAFPrs.CollectStyleSettings_s(node.RefLabel, TopLoc_Location(), styles)

        def colour_of(sub, fallback):
            # the part's own shape: its colour is already resolved in the node's style (a placement's own colour before the
            # part's, the assembly's when neither has one); the style map decides only true sub-shapes (faces, solids of a compound)
            if sub.IsSame(shape):
                return fallback
            if styles.Contains(sub):
                c = _rgb(styles.FindFromKey(sub))
                if c is not None:
                    return c
            return fallback

        solids = TopExp_Explorer(shape, TopAbs_SOLID)
        found = False
        while solids.More():
            found = True
            solid = solids.Current()
            base = colour_of(solid, own)
            faces, coloured = TopExp_Explorer(solid, TopAbs_FACE), []
            while faces.More():                                   # the colour looked up on the face itself, then placed
                coloured.append((cq.Face(faces.Current().Moved(node.Location)), colour_of(faces.Current(), base)))
                faces.Next()
            any_colour = any_colour or any(c is not None for _f, c in coloured)
            out.append((cq.Solid(solid.Moved(node.Location)), coloured, component))
            solids.Next()
        if not found and not shape.IsNull():                      # a surface-only part: its faces as one body, as the plain path does
            faces, coloured = TopExp_Explorer(shape, TopAbs_FACE), []
            while faces.More():
                coloured.append((cq.Face(faces.Current().Moved(node.Location)), colour_of(faces.Current(), own)))
                faces.Next()
            if coloured:
                any_colour = any_colour or any(c is not None for _f, c in coloured)
                out.append((cq.Shape.cast(shape.Moved(node.Location)), coloured, component))
        explorer.Next()
    return out if any_colour else None


def _convert_coloured(step_path: str):
    """``(glb bytes, info)`` for a STEP with colours, or None when it holds none (the plain path then runs)."""
    import cadquery as cq
    import numpy as np

    found = coloured_solids(step_path)
    if not found:
        return None
    bb = cq.Compound.makeCompound([s for s, _f, _n in found]).BoundingBox()
    diag = max(bb.DiagonalLength, 1e-3)
    tol = min(0.2, max(0.002, diag * 0.0005))                    # the same tessellation as the plain path
    palette: list = [None]
    index: dict = {}
    parts, triangles = [], 0
    for i, (_solid, faces, component) in enumerate(found, 1):
        groups: dict = {}
        for face, rgb in faces:
            verts, tris = face.tessellate(tol, 0.1)
            if not verts or not tris:
                continue
            if rgb is None:
                mat = 0
            else:
                mat = index.get(rgb)
                if mat is None:
                    mat = index[rgb] = len(palette)
                    palette.append(rgb)
            groups.setdefault(mat, []).append((np.array([(v.x, v.y, v.z) for v in verts], dtype="float32"), np.array(tris, dtype="int64")))
        prims = []
        for mat in sorted(groups):
            pos, nrm, idx = merge_faces(groups[mat])
            triangles += len(idx) // 3
            prims.append((pos, nrm, idx, mat))
        if prims:
            parts.append(("Part_%d" % i if len(found) > 1 else "Part", prims, component))
    if not parts:
        raise StepConversionError("The STEP file has no solid geometry.")
    info = {"solids": len(parts), "triangles": triangles, "colours": len(palette) - 1,
            "bbox_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)]}
    return build_coloured_glb(parts, palette), info


def convert_in_process(step_path: str, glb_path: str) -> dict:
    """Read the STEP, tessellate every solid, write the GLB; returns ``{solids, triangles, bbox_mm}`` (+ ``colours``)."""
    kinds = step_entities(step_path, COLOUR_ENTITIES | EXTERNAL_ENTITIES)
    if kinds & EXTERNAL_ENTITIES:
        log.warning("production: STEP names external document references (%s); colours not read, converting without them",
                    ", ".join(sorted(k.decode() for k in kinds & EXTERNAL_ENTITIES)))
    elif kinds & COLOUR_ENTITIES:
        try:
            coloured = _convert_coloured(step_path)
        except StepConversionError:
            raise
        except Exception as ex:                                  # noqa: BLE001 - the colour reader never costs the view
            log.warning("production: STEP colours not read (%s); converting without colours", ex)
            coloured = None
        if coloured is not None:
            Path(glb_path).write_bytes(coloured[0])
            return coloured[1]
    return _convert_plain(step_path, glb_path)


def _convert_plain(step_path: str, glb_path: str) -> dict:
    """The conversion without colours (v1, unchanged): every solid in the steel grey."""
    import cadquery as cq
    import numpy as np

    wp = cq.importers.importStep(step_path)
    shapes = [v for v in wp.vals() if hasattr(v, "Solids")]
    solids = [s for sh in shapes for s in sh.Solids()]
    if not solids:                                                 # a surface-only STEP: mesh its faces as one body
        solids = shapes
    if not solids:
        raise StepConversionError("The STEP file has no solid geometry.")
    bb = cq.Compound.makeCompound(solids).BoundingBox()
    diag = max(bb.DiagonalLength, 1e-3)
    tol = min(0.2, max(0.002, diag * 0.0005))
    out, triangles = [], 0
    for i, solid in enumerate(solids, 1):
        patches = []
        for face in solid.Faces():
            verts, tris = face.tessellate(tol, 0.1)
            if not verts or not tris:
                continue
            patches.append((np.array([(v.x, v.y, v.z) for v in verts], dtype="float32"), np.array(tris, dtype="int64")))
        if not patches:
            continue
        pos, nrm, idx = merge_faces(patches)
        triangles += len(idx) // 3
        out.append(("Part_%d" % i if len(solids) > 1 else "Part", pos, nrm, idx))
    if not out:
        raise StepConversionError("The STEP file has no solid geometry.")
    Path(glb_path).write_bytes(build_glb(out))
    return {"solids": len(out), "triangles": triangles,
            "bbox_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)]}


# ── the parent side: run the child, cache the result ─────────────────────────────────────────────────────────────

_slot = threading.Semaphore(1)                  # one conversion at a time on the API host (CPU discipline)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def cache_path(cache_dir: Path, file_id: str) -> Path:
    return cache_dir / ("%s.%s.glb" % (file_id, VERSION))


def _run_child(step_path: Path, glb_path: Path, timeout: int) -> None:
    cmd = [sys.executable, "-m", "motor_ai_sim.production.step_glb", str(step_path), str(glb_path)]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00004000                       # BELOW_NORMAL_PRIORITY_CLASS
    else:
        kwargs["preexec_fn"] = lambda: os.nice(10)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        raise StepConversionError("The 3-D view of this file took too long to build; download the STEP instead.", status=504)
    except OSError as ex:
        log.error("production: STEP converter did not start: %s", ex)
        raise StepConversionError("The 3-D view is not available right now.", status=503)
    if p.returncode != 0 or not glb_path.is_file() or glb_path.stat().st_size == 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
        log.warning("production: STEP conversion failed (rc=%s): %s", p.returncode, " | ".join(tail))
        if "StepConversionError" in (p.stderr or ""):
            raise StepConversionError("The STEP file has no solid geometry.")
        raise StepConversionError("This STEP file could not be turned into a 3-D view.")


def glb_for_step(step_path: Path, file_id: str, cache_dir: Path, timeout: int = TIMEOUT_S) -> bytes:
    """The GLB of one STEP file: from the cache when it is there, else built (once) and cached.

    A cache that cannot be written (read-only volume) is not an error: the view is served from memory and rebuilt next time.
    """
    cached = cache_path(cache_dir, file_id)
    try:
        if cached.is_file() and cached.stat().st_size > 0 and cached.stat().st_mtime >= step_path.stat().st_mtime:
            return cached.read_bytes()
    except OSError:
        pass
    with _lock_for(file_id):
        try:                                                       # another request may have built it while we waited
            if cached.is_file() and cached.stat().st_size > 0:
                return cached.read_bytes()
        except OSError:
            pass
        with _slot:
            with tempfile.TemporaryDirectory(prefix="stepglb_") as tmp:
                out = Path(tmp) / "view.glb"
                _run_child(step_path, out, timeout)
                data = out.read_bytes()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            part = cache_dir / (".%s.%s.tmp" % (file_id, os.getpid()))
            part.write_bytes(data)
            os.replace(part, cached)
        except OSError as ex:
            log.warning("production: view cache not writable (%s); served from memory", ex)
        return data


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: python -m motor_ai_sim.production.step_glb in.step out.glb", file=sys.stderr)
        return 2
    try:
        info = convert_in_process(argv[0], argv[1])
    except StepConversionError as ex:
        print("StepConversionError: %s" % ex.detail, file=sys.stderr)
        return 3
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
