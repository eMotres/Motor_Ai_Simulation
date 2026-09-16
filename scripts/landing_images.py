"""The three pictures on the signed-out landing page, redrawn from our own fields.

`web/public/landing/*.png` are committed binaries, and a committed binary whose
generator lives in somebody's scratchpad is a picture nobody can check and
nobody can reproduce.  This is that generator.

WHAT IT DRAWS.  One machine at one operating point — CIANO10 200 opt /
"L155 motor" / rated 1x9 mm — so the three cards are the same machine three
ways and not three unrelated pretty pictures.  Each picture comes out of
`report.py`'s OWN map builder, off the arrays that duty's solves filed in
`motor_ai_sim.duty_fields`, so what the landing shows is what the report
prints:

    em-field.png            report._em_maps(...)["b"]              |B|  [T]
    thermal-map.png         report._thermal_map(...)               temperature  [degC]
    rotor-displacement.png  report._mech_extra_maps(...)["disp"]   displacement |u|  [um]

The third is the rotor WITH ITS SLEEVE, drawn deformed at the report's own
exaggeration (~22x) and coloured by the true |u| — the mechanical section's
displacement figure, which is what the user asked that card to show
(2026-09-16, "механическое моделирование").

ONE CUT, THE WHOLE MACHINE.  The three must show the same section of the
machine, and they do not out of the box.  The mechanical solve is a full 360
deg ring; the electromagnetic and thermal solves are stored as HALF models
(both span exactly 180 deg, and the thermal store says n_sectors = 2 — this
machine is 12 slots / 10 poles, so its smallest periodic sector is a half).
Left alone, that is two 2.2:1 wedges beside a disc.

So those two are completed to the full ring HERE, for the picture only, by
ROTATING the solved half 180 deg.  Rotating, not mirroring: the rotation is the
periodic continuation the solve itself assumed, so it is what the other half of
the machine actually is, while a reflection would be a machine that does not
exist.  |B| is a magnitude and the temperature is a scalar, so both are
invariant under the sign flip an ANTI-periodic boundary carries, and the
completed picture is the machine rather than an artist's impression.
Coincident nodes on the seam are merged, or the per-class nodal average
`report._map_png` draws would see half the neighbours there and paint a
hairline down the joint.

The completed mesh then goes through the same builders as before, so the colour
scales are the half pictures' own: |B| still caps at 2.393 T (p99.5; the raw
maximum is 3.626 T at a tooth-tip singularity) and the temperature bar still
runs 68.93 to 135.2 degC.  That agreement is the check that the replication is
faithful — if a rotation were wrong, the scale would move.  **Nothing in
report.py changes**: the report still prints the half it solved.

THE MESH, AT CARD SIZE (user 2026-09-16: "add the mesh to these pictures").
`report._map_png` already draws it — `mesh=True` is its default, and every
builder here takes that default, so the landing has always been showing the
solved mesh.  At the size a REPORT places a figure, its hairline (0.12 pt,
alpha 0.35, #2b2b2b) reads as texture over the field; at ~442 px on a card it
disappears, which is why the first live cards looked like flat colour.

So the weight of that ONE call is lifted for these three pictures, and only for
them: :func:`mesh_weight` wraps `Axes.triplot` for the duration of a build and
substitutes :data:`MESH_LW` / :data:`MESH_ALPHA` into it.  Nothing in report.py
is touched and nothing outside this process changes — the report still prints
its own hairline.  The thermal map gets the lighter alpha of the two
(:data:`MESH_ALPHA_THERMAL`): most of it is dark red, and the weight that reads
as texture over a pale field reads as ink over that one.  `--no-mesh` draws the
fields bare, which is how the two styles get compared.

Edges do not double on the seam: the replication merges coincident nodes, and
`triplot` draws a triangulation's UNIQUE edge list, so the joint carries one
line like every other interior edge.

SIZE.  Each card places a picture at ~442 CSS px and the disc inside the
1.25:1 box is height-limited to ~354 px, so 1000 device px covers a 2x screen
with room to spare.  The palette steps down until the file clears
:data:`TARGET_BYTES`; the hard ceiling is :data:`MAX_BYTES` (300 KB, the
landing's budget, enforced by the test beside the component) and the target is
lower on purpose, so a later tweak cannot quietly blow it.  All three are
placed at one width (:data:`PLACE_CM`), which is what gives the three colour
bars the same size of tick label.

Run it STANDALONE — never against the live API on 8001.  It imports the solver
package in-process and reads the npz stores directly; it starts no server and
calls none::

    python scripts/landing_images.py            # rewrite web/public/landing/*.png
    python scripts/landing_images.py --check     # redraw elsewhere and compare
    python scripts/landing_images.py --no-mesh   # the fields with no mesh over them

``--check`` writes nothing into the repo: it redraws into a temporary directory
and reports, per file, whether the bytes still match what is committed.  A
mismatch is not automatically a bug — a new matplotlib or Pillow can shift a
byte — but it does mean the committed picture is no longer exactly what this
script produces, and somebody should look.
"""
from __future__ import annotations

import hashlib
import io
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np                                          # noqa: E402
from PIL import Image                                       # noqa: E402

from motor_ai_sim import duty_fields as df                  # noqa: E402
from motor_ai_sim import report                             # noqa: E402

#: Where the page asks for them.
OUT_DIR = ROOT / "web" / "public" / "landing"

#: The landing's budget, checked again by web/src/components/landing/__tests__.
MAX_BYTES = 300 * 1024
#: What the palette search actually aims at — see SIZE above.
TARGET_BYTES = 255 * 1024
#: Device pixels across, for a picture placed at ~442 CSS px on a 2x screen.
CARD_PX = 1000

#: The mesh over the field, in points and alpha — see THE MESH above.  The
#: report's own values are 0.12 / 0.35, which is right for a figure placed on a
#: page and invisible on a 442 px card.
MESH_LW = 0.22
MESH_ALPHA = 0.55
#: The temperature map gets its own value.  Its stator is pale and its rotor is
#: one flat dark red, so the two halves of the picture want opposite things: at
#: the other maps' 0.55 the stator reads busy, and at 0.38 the mesh vanishes
#: inside the rotor altogether.  0.50 is where it is legible in both.
MESH_ALPHA_THERMAL = 0.50

DIE, CFG, DUTY = "CIANO10 200 opt", "L155 motor", "rated 1x9 mm"
#: The width the figures are PLACED at, in cm.  `report.map_font_pt` sizes the
#: colour-bar type from it, so one value here is one tick-label size on all
#: three cards.
PLACE_CM = 12.0


# ── the mesh ────────────────────────────────────────────────────────────────

@contextmanager
def mesh_weight(lw: float, alpha: float, on: bool = True) -> Iterator[None]:
    """Draw `report._map_png`'s mesh at THIS weight, for as long as the block.

    That one call is ``ax.triplot(tri, color="#2b2b2b", linewidth=0.12,
    alpha=0.35)``, and it is the only `triplot` in the builders this script
    uses — the mode gallery draws its outlines with a `LineCollection` and the
    flux lines come from `tricontour`, so wrapping `triplot` reaches the mesh
    and nothing else.  `on=False` suppresses the mesh entirely (``--no-mesh``).

    A wrapper rather than an argument because `_map_png` does not take one, and
    teaching it to would put a landing-page concern in the report.
    """
    import matplotlib.axes
    original = matplotlib.axes.Axes.triplot

    def patched(self: Any, *a: Any, **kw: Any) -> Any:
        # Only the mesh call passes both — leave anything else exactly alone.
        if "linewidth" in kw and "alpha" in kw:
            if not on:
                return []
            kw["linewidth"], kw["alpha"] = lw, alpha
        return original(self, *a, **kw)

    matplotlib.axes.Axes.triplot = patched          # type: ignore[assignment]
    try:
        yield
    finally:
        matplotlib.axes.Axes.triplot = original     # type: ignore[assignment]



def _xy(v: Any) -> np.ndarray:
    """Vertices as (N, 2), whichever way round the store wrote them."""
    a = np.asarray(v, dtype=float)
    return a.T if (a.ndim == 2 and a.shape[0] == 2 and a.shape[1] != 2) else a


def _tris(t: Any) -> np.ndarray:
    """Triangles as (M, 3), whichever way round the store wrote them."""
    a = np.asarray(t, dtype=np.int64)
    return a.T if (a.ndim == 2 and a.shape[0] == 3 and a.shape[1] != 3) else a


def sectors_of(verts: Any) -> int:
    """How many copies of this mesh make the whole machine.

    Read off the mesh's own angular span rather than taken from a stored
    ``n_sectors``: the electromagnetic store does not carry one, and a picture
    that silently assumed the wrong count would be wrong in a way nobody could
    see.  A span that is not a whole fraction of 360 deg stops the script.
    """
    p = _xy(verts)
    ang = np.degrees(np.arctan2(p[:, 1], p[:, 0]))
    span = float(ang.max() - ang.min())
    n = 360.0 / span
    if abs(n - round(n)) > 1e-3:
        raise SystemExit("span %.4f deg is not a whole fraction of 360" % span)
    return int(round(n))


def to_full(verts: Any, tris: Any,
            per_node: Dict[str, Any],
            per_tri: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray,
                                              Dict[str, Any], Dict[str, Any]]:
    """The mesh turned around to a full 360 deg ring, seam nodes merged.

    A no-op on a mesh that is already whole, so the mechanical field goes
    through it untouched and all three pictures are built the same way.
    """
    p, t = _xy(verts), _tris(tris)
    n = sectors_of(p)
    if n == 1:
        return p, t, dict(per_node), dict(per_tri)

    pieces_p: List[np.ndarray] = []
    pieces_t: List[np.ndarray] = []
    for k in range(n):
        a = 2.0 * np.pi * k / n
        c, s = np.cos(a), np.sin(a)
        pieces_p.append(np.column_stack([p[:, 0] * c - p[:, 1] * s,
                                         p[:, 0] * s + p[:, 1] * c]))
        pieces_t.append(t + k * p.shape[0])
    P = np.concatenate(pieces_p)
    T = np.concatenate(pieces_t)

    # MERGE THE SEAM — see the module docstring for what an unmerged joint does
    # to the per-class nodal average.
    tol = 1e-6 * float(np.abs(P).max() or 1.0)
    key = np.round(P / tol).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True,
                                  return_inverse=True)
    P = P[first]
    T = inverse[T]

    node_out = {k: (None if v is None
                    else np.concatenate([np.asarray(v, float).ravel()] * n)[first])
                for k, v in per_node.items()}
    tri_out = {k: (None if v is None
                   else np.concatenate([np.asarray(v).ravel()] * n))
               for k, v in per_tri.items()}
    return P, T, node_out, tri_out


# ── the file ────────────────────────────────────────────────────────────────

def encode(png: bytes, max_w: int = CARD_PX) -> bytes:
    """One builder's PNG, downscaled and palette-quantised to the budget."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)),
                       Image.LANCZOS)
    data = b""
    for colors in (256, 192, 128, 96, 64):
        buf = io.BytesIO()
        im.convert("P", palette=Image.ADAPTIVE, colors=colors).save(
            buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= TARGET_BYTES:
            break
    if len(data) > MAX_BYTES:
        raise SystemExit("picture is %d B, over the %d B budget"
                         % (len(data), MAX_BYTES))
    return data


# ── the three pictures ──────────────────────────────────────────────────────

def build(mesh: bool = True) -> Dict[str, bytes]:
    """``{filename: png bytes}`` for the whole set."""
    out: Dict[str, bytes] = {}

    em = df.load(DIE, CFG, DUTY, "em")
    if em is None or em.get("b_mag_per_tri") is None:
        raise SystemExit("no em field for %s / %s / %s" % (DIE, CFG, DUTY))
    print("em           %d sector(s)" % sectors_of(em["vertices"]))
    P, T, _n, tri = to_full(em["vertices"], em["triangles"], {},
                            {"b": em["b_mag_per_tri"], "tags": em["tags"]})
    with mesh_weight(MESH_LW, MESH_ALPHA, on=mesh):
        maps = report._em_maps({"P_mm": P, "T": T, "tags": tri["tags"],
                                "b_mag": tri["b"]}, width_cm=PLACE_CM)
    if not maps.get("b"):
        raise SystemExit("the |B| map did not render")
    print("             |B| max %.3f T, bar capped at %.3f T"
          % (maps.get("b_max_T") or 0.0, maps.get("b_cap_T") or 0.0))
    out["em-field.png"] = encode(maps["b"])

    th = df.load(DIE, CFG, DUTY, "thermal")
    if th is None or th.get("temperature_per_node") is None:
        raise SystemExit("no thermal field for this duty")
    print("thermal      %d sector(s)" % sectors_of(th["vertices"]))
    P, T, node, tri = to_full(th["vertices"], th["triangles"],
                              {"t": th["temperature_per_node"]},
                              {"dom": th["domain_per_tri"]})
    with mesh_weight(MESH_LW, MESH_ALPHA_THERMAL, on=mesh):
        png = report._thermal_map({"field": {
            "vertices": P, "triangles": T,
            "temperature_per_node": node["t"], "domain_per_tri": tri["dom"],
        }}, width_cm=PLACE_CM)
    if not png:
        raise SystemExit("the temperature map did not render")
    out["thermal-map.png"] = encode(png)

    src = report._duty_map_sources(DIE, CFG, DUTY)
    rs: Optional[Dict[str, Any]] = src.get("rotor_stress")
    if rs is None:
        raise SystemExit("no rotor_stress field for this duty")
    print("rotor        %d sector(s)" % sectors_of((rs.get("field") or {})["vertices"]))
    with mesh_weight(MESH_LW, MESH_ALPHA, on=mesh):
        mech = report._mech_extra_maps(rs, width_cm=PLACE_CM)
    if not mech.get("disp"):
        raise SystemExit("the displacement map did not render "
                         "(this duty stored no u_mag_per_node)")
    print("             shape exaggerated x%s" % mech.get("disp_exagg"))
    out["rotor-displacement.png"] = encode(mech["disp"])
    return out


def main(argv: List[str]) -> int:
    check = "--check" in argv[1:]
    mesh = "--no-mesh" not in argv[1:]
    unknown = [a for a in argv[1:] if a not in ("--check", "--no-mesh")]
    if unknown:
        raise SystemExit("unknown argument(s): %s" % " ".join(unknown))

    pics = build(mesh=mesh)
    # A bare-field run is a comparison, not the shipped set: it never writes
    # into the repo, whether or not --check was asked for.
    dest = (Path(tempfile.mkdtemp(prefix="landing-check-"))
            if (check or not mesh) else OUT_DIR)
    dest.mkdir(parents=True, exist_ok=True)

    same = True
    for name, data in pics.items():
        # READ THE COMMITTED BYTES FIRST.  Writing before comparing made the
        # write mode report "unchanged" for every file, always — it was
        # comparing the picture with the copy of itself it had just saved.
        live = OUT_DIR / name
        old = live.read_bytes() if live.exists() else b""
        (dest / name).write_bytes(data)
        match = (hashlib.sha256(old).digest() == hashlib.sha256(data).digest())
        same = same and match
        print("%-24s %6.0f KB  %s" % (
            name, len(data) / 1024,
            "unchanged" if match else ("DIFFERS from the committed file"
                                       if old else "new")))
    if dest is not OUT_DIR:
        print("\nredrawn into %s (nothing in the repo was touched)" % dest)
        return 0 if (same or not mesh) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
