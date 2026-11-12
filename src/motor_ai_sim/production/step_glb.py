"""STEP -> GLB for the production module's part viewer (owner 2026-11-12: tooling parts get a rotatable 3-D view).

A production part (a press die, a fixture) carries its STEP file.  The browser viewer takes GLB, so the first request for a
part's view converts the STEP once and keeps the result next to the production's files (``viewcache/``); later requests read
the cache.  The conversion runs in a CHILD process (OpenCASCADE can use a lot of memory or crash on a damaged file, and must
never take the API down), at low priority, one at a time, with a time limit.

The tessellation uses the cadquery / OCP already in the API image (no FreeCAD is involved on the request path).  The GLB has
the layout the viewer reads (cad/fc_scripts/glb_writer.py): millimetres, the CAD frame (Z up), a ``Model`` node whose
children are the parts, each carrying ``extras`` ``{family, explode}``.  A face is tessellated on its own, so normals are smooth
inside a face and hard across faces.

  python -m motor_ai_sim.production.step_glb in.step out.glb      (what the child process runs)
"""
from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

#: bump when the writer or the tessellation changes: cached views of an older version are not read
VERSION = "v1"
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
        "asset": {"version": "2.0", "generator": "motor_ai_sim.production.step_glb", "extras": {"units": "mm", "converter": VERSION}},
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


# ── the conversion itself (needs cadquery / OCP; runs in the child process) ──────────────────────────────────────────

def convert_in_process(step_path: str, glb_path: str) -> dict:
    """Read the STEP, tessellate every solid, write the GLB; returns ``{solids, triangles, bbox_mm}``."""
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
