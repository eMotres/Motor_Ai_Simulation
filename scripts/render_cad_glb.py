#!/usr/bin/env python
"""Render the web-viewer GLB of a generated motor model (``<motor_id>_full.glb``) to an opaque PNG with the palette colours.

  python scripts/render_cad_glb.py MODEL.glb OUT.png [--view iso|front|back|side] [--size 1800 1350] [--background FFFFFF]

The GLB written by ``cad/fc_scripts/export_models.py`` holds one coloured node per part (colours = ``palette.py`` in sRGB, written
linear as glTF wants); this script reads them back, converts them to sRGB again and draws every triangle with them.  Everything is
opaque.  Needs ``trimesh``, ``numpy`` and ``Pillow`` (a developer tool, not part of the API image). A small software rasteriser (numpy z-buffer,
smooth shading, 2x supersampling) draws it, so no GPU / OpenGL context is needed.
Used for the documentation image of a variant (docs/CAD_GENERATOR.md, "Frameless variant"): no FreeCAD GUI is involved.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np

VIEWS = {  # direction the camera looks FROM (towards the motor centre), z = motor axis
    "iso": (1.0, -1.25, 0.9), "front": (0.0, -0.05, 1.0), "back": (0.0, -0.05, -1.0), "side": (1.0, 0.0, 0.0),
}


def _srgb(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)


def parts(path: str):
    """[(node name, vertices (n,3), triangles (m,3), vertex normals (n,3), sRGB colour (3,))] of every mesh of the GLB."""
    import trimesh
    scene = trimesh.load(path, force="scene")
    out = []
    for name, geom in scene.geometry.items():
        node = next((n for n in scene.graph.nodes_geometry if scene.graph[n][1] == name), None)
        pose = scene.graph[node][0] if node else np.eye(4)
        v = trimesh.transformations.transform_points(geom.vertices, pose)
        nrm = geom.vertex_normals @ pose[:3, :3].T
        mat = getattr(geom.visual, "material", None)
        base = getattr(mat, "baseColorFactor", None)
        col = np.array(base[:3], dtype=float) / 255.0 if base is not None else np.array([0.6, 0.6, 0.65])
        out.append((str(node or name), np.asarray(v), np.asarray(geom.faces), np.asarray(nrm), _srgb(col)))
    return out


def _basis(view: str):
    """Orthonormal camera basis (right, up, toward-the-camera) for a named view; z is the motor axis."""
    d = np.array(VIEWS[view], dtype=float)
    d /= np.linalg.norm(d)
    up = np.array([0.0, 0.0, 1.0]) if view in ("iso", "side") else np.array([0.0, 1.0, 0.0])
    right = np.cross(up, d)
    right /= np.linalg.norm(right)
    return right, np.cross(d, right), d


def render(glb: str, png: str, view: str = "iso", size=(1800, 1350), background="FFFFFF", supersample: int = 2) -> dict:
    """Software z-buffer rasteriser (numpy): orthographic camera, smooth (interpolated vertex normals) Lambert + a little specular,
    ``supersample`` x supersampling for anti-aliasing, opaque.  No GPU / OpenGL context needed (VTK / the FreeCAD GUI give black
    images on a machine without one)."""
    from PIL import Image
    ps = parts(glb)
    pts = np.vstack([p[1] for p in ps])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    centre = (lo + hi) / 2.0
    right, up, toward = _basis(view)
    W, H = size[0] * supersample, size[1] * supersample
    allp = (pts - centre) @ np.stack([right, up]).T
    scale = 0.9 * min(W / (np.ptp(allp[:, 0]) or 1.0), H / (np.ptp(allp[:, 1]) or 1.0))
    mid = (allp.min(axis=0) + allp.max(axis=0)) / 2.0
    bg = np.array([int(background[i:i + 2], 16) for i in (0, 2, 4)], dtype=float) / 255.0
    img = np.tile(bg, (H, W, 1))
    zbuf = np.full((H, W), -np.inf)
    key = np.array([-0.45, 0.55, 0.70])                                    # light, in camera space (x right, y up, z toward the viewer)
    key /= np.linalg.norm(key)
    fill = np.array([0.6, -0.2, 0.4])
    fill /= np.linalg.norm(fill)
    ntri = 0
    for _name, v, f, n, col in ps:
        pv = (v - centre) @ np.stack([right, up, toward]).T                # camera space
        sx = (pv[:, 0] - mid[0]) * scale + W / 2.0
        sy = H / 2.0 - (pv[:, 1] - mid[1]) * scale
        sz = pv[:, 2]
        nc = n @ np.stack([right, up, toward]).T
        nl = np.linalg.norm(nc, axis=1, keepdims=True)
        nc = nc / np.where(nl == 0, 1, nl)
        for a, b, c in f:
            x0, x1, x2 = sx[a], sx[b], sx[c]
            y0, y1, y2 = sy[a], sy[b], sy[c]
            den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(den) < 1e-12:
                continue
            xmin, xmax = int(max(0, math.floor(min(x0, x1, x2)))), int(min(W - 1, math.ceil(max(x0, x1, x2))))
            ymin, ymax = int(max(0, math.floor(min(y0, y1, y2)))), int(min(H - 1, math.ceil(max(y0, y1, y2))))
            if xmin > xmax or ymin > ymax:
                continue
            gx, gy = np.meshgrid(np.arange(xmin, xmax + 1) + 0.5, np.arange(ymin, ymax + 1) + 0.5)
            l0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
            l1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
            l2 = 1.0 - l0 - l1
            m = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
            if not m.any():
                continue
            z = l0 * sz[a] + l1 * sz[b] + l2 * sz[c]
            sub = zbuf[ymin:ymax + 1, xmin:xmax + 1]
            win = m & (z > sub)
            if not win.any():
                continue
            nn = l0[..., None] * nc[a] + l1[..., None] * nc[b] + l2[..., None] * nc[c]
            nn = nn / np.maximum(np.linalg.norm(nn, axis=2, keepdims=True), 1e-12)
            nn = nn * np.where(nn[..., 2:3] < 0, -1.0, 1.0)                # two-sided: always shade the side that faces the camera
            diff = np.clip(nn @ key, 0, 1) * 0.62 + np.clip(nn @ fill, 0, 1) * 0.22
            refl = 2.0 * (nn @ key)[..., None] * nn - key
            spec = np.clip(refl[..., 2], 0, 1) ** 24 * 0.18
            shade = 0.30 + diff
            rgb = np.clip(col[None, None, :] * shade[..., None] + spec[..., None], 0, 1)
            sub[win] = z[win]
            img[ymin:ymax + 1, xmin:xmax + 1][win] = rgb[win]
            ntri += 1
    im = Image.fromarray((img * 255 + 0.5).astype(np.uint8), "RGB")
    if supersample > 1:
        im = im.resize(size, Image.LANCZOS)
    im.save(png)
    return dict(png=png, parts=len(ps), triangles=int(sum(len(p[2]) for p in ps)), drawn=ntri,
                bbox_mm=[float(x) for x in np.r_[lo, hi]], colours={p[0]: [round(float(c), 3) for c in p[4]] for p in ps[:12]})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("glb")
    ap.add_argument("png")
    ap.add_argument("--view", choices=sorted(VIEWS), default="iso")
    ap.add_argument("--size", type=int, nargs=2, default=(1800, 1350))
    ap.add_argument("--background", default="FFFFFF", help="hex RGB (opaque; default white)")
    a = ap.parse_args(argv)
    info = render(a.glb, a.png, a.view, tuple(a.size), a.background)
    print("rendered %s: %d parts, %d triangles" % (info["png"], info["parts"], info["triangles"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
