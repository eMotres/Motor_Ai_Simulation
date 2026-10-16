"""Package outlines — a small line drawing of what a part physically IS.

Owner, 2026-09-22: the catalogue should show *"a small picture of the package
so one can see what the part is"*.  Nothing is downloaded: every thumbnail is
GENERATED from a package-outline library keyed by the package family, scaled by
the card's own ``package_size_mm`` when it has one, so the picture is a drawing
of the dimensions in the table rather than a stock photo of something similar.

A card may instead point at a local file the owner dropped in
``config/devices/img/`` (``image: <file name>``); that wins, and the route
serves it.  No vendor artwork is fetched, ever.

The drawing is a TOP VIEW with a side bar for the height: body, lead rows and —
where the family has one — the cooled tab, shaded.  Labels are not drawn: the
numbers are in the table next to it (the tab's "one short line" rule).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

__all__ = ["PACKAGE_FAMILIES", "family_for", "outline_svg"]

#: family -> how the outline is built.
#:   ``leads``      pins per row (0 = no gull-wing rows, e.g. a module)
#:   ``rows``       1 = one lead row (TO-247), 2 = two opposite rows (Q-DPAK)
#:   ``tab``        which face is the cooled tab: top / bottom / none
#:   ``aspect``     fallback body L:W when the card gives no dimensions
PACKAGE_FAMILIES: Dict[str, Dict[str, Any]] = {
    "q-dpak":      {"leads": 11, "rows": 2, "tab": "top",    "aspect": (15.0, 15.4)},
    "to-247":      {"leads": 3,  "rows": 1, "tab": "top",    "aspect": (15.9, 20.0)},
    "to-263":      {"leads": 5,  "rows": 1, "tab": "top",    "aspect": (10.2, 15.0)},
    "to-220":      {"leads": 3,  "rows": 1, "tab": "top",    "aspect": (10.0, 15.0)},
    "sot-227":     {"leads": 0,  "rows": 0, "tab": "none",   "aspect": (38.0, 25.0)},
    "62mm-module": {"leads": 0,  "rows": 0, "tab": "none",   "aspect": (106.0, 62.0)},
    "xm3":         {"leads": 0,  "rows": 0, "tab": "none",   "aspect": (53.0, 80.0)},
    "easypack":    {"leads": 0,  "rows": 0, "tab": "none",   "aspect": (60.0, 32.0)},
    # A small source-down/drain-down QFN — leads on two opposite sides and a
    # large exposed pad (the cooled face) covering most of the body, e.g.
    # Infineon's PG-TSON-8 / PG-WHSON-8 family (owner 2026-09-22: IQE050N08NM5SC,
    # PQFN 3.3x3.3 mm, datasheet package PG-TSON-8-4).
    "pqfn":        {"leads": 4,  "rows": 2, "tab": "bottom", "aspect": (3.3, 3.3)},
    "generic":     {"leads": 4,  "rows": 2, "tab": "top",    "aspect": (14.0, 14.0)},
}

#: What a package NAME may be called.  Matched case-insensitively, longest
#: pattern first, against the card's ``package`` and ``package_common_name``.
_ALIASES = (
    ("q-dpak", (r"q[\s_-]*dpak", r"hdsop[\s_-]*22")),
    ("to-247", (r"to[\s_-]*247", r"to247", r"pg[\s_-]*to247")),
    ("to-263", (r"to[\s_-]*263", r"d2pak")),
    ("to-220", (r"to[\s_-]*220",)),
    ("sot-227", (r"sot[\s_-]*227", r"isotop")),
    ("62mm-module", (r"62\s*mm", r"econodual")),
    ("xm3", (r"\bxm3\b",)),
    ("easypack", (r"easy\s*1b", r"easy\s*2b", r"easypack")),
    ("pqfn", (r"tson[\s_-]*8", r"whson[\s_-]*8", r"pqfn")),
)


def family_for(package: Optional[str],
               common_name: Optional[str] = None) -> str:
    """Which outline to draw for this package name — ``"generic"`` if unknown."""
    hay = " ".join(str(x or "") for x in (common_name, package)).lower()
    for fam, pats in _ALIASES:
        for p in pats:
            if re.search(p, hay):
                return fam
    return "generic"


def _dims(card: Dict[str, Any], fam: str) -> tuple:
    """(length, width) in mm — the card's own when it has them."""
    size = card.get("package_size_mm")
    if isinstance(size, dict):
        try:
            l = float(size.get("length_mm"))
            w = float(size.get("width_mm"))
            if l > 0 and w > 0:
                return l, w
        except (TypeError, ValueError):
            pass
    return PACKAGE_FAMILIES.get(fam, PACKAGE_FAMILIES["generic"])["aspect"]


def outline_svg(card: Dict[str, Any], *, box: int = 64) -> str:
    """An inline ``<svg>`` thumbnail of this card's package, ``box`` px square.

    ``currentColor`` throughout, so it inherits the tab's theme; no text.
    """
    fam = family_for(card.get("package"), card.get("package_common_name"))
    spec = PACKAGE_FAMILIES.get(fam, PACKAGE_FAMILIES["generic"])
    l_mm, w_mm = _dims(card, fam)

    pad = 5.0
    avail = box - 2 * pad - 10          # 10 px reserved for the side bar
    scale = avail / max(l_mm, w_mm)
    bw = l_mm * scale
    bh = w_mm * scale
    x0 = pad + (avail - bw) / 2.0
    y0 = pad + (avail - bh) / 2.0

    p = [f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
         f'rx="1.5" fill="none" stroke="currentColor" stroke-width="1.1"/>']

    if spec["tab"] != "none":
        # the cooled tab, shaded, on the upper part of the body
        th = bh * 0.52
        p.append(f'<rect x="{x0 + bw * 0.08:.1f}" y="{y0 + bh * 0.1:.1f}" '
                 f'width="{bw * 0.84:.1f}" height="{th:.1f}" rx="1" '
                 f'fill="currentColor" opacity="0.18"/>')
    else:
        # a module: two terminal pads and a screw hole at each end
        for fx in (0.18, 0.82):
            p.append(f'<circle cx="{x0 + bw * fx:.1f}" cy="{y0 + bh * 0.5:.1f}" '
                     f'r="{min(bw, bh) * 0.1:.1f}" fill="none" '
                     f'stroke="currentColor" stroke-width="1"/>')

    n = int(spec["leads"])
    if n > 0:
        step = bw / (n + 1)
        lead = max(2.5, bh * 0.14)
        rows = (1,) if spec["rows"] == 1 else (1, -1)
        for r in rows:
            y = (y0 + bh) if r == 1 else y0
            for k in range(1, n + 1):
                x = x0 + k * step
                p.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" '
                         f'y2="{y + r * lead:.1f}" stroke="currentColor" '
                         f'stroke-width="1"/>')

    # side bar: the height, to the same scale, so a 2.35 mm Q-DPAK reads flat
    h_mm = 0.0
    size = card.get("package_size_mm")
    if isinstance(size, dict):
        try:
            h_mm = float(size.get("height_mm") or 0.0)
        except (TypeError, ValueError):
            h_mm = 0.0
    if h_mm > 0:
        hb = max(1.5, min(h_mm * scale, avail))
        p.append(f'<rect x="{box - pad - 6:.1f}" y="{pad + avail - hb:.1f}" '
                 f'width="6" height="{hb:.1f}" fill="currentColor" '
                 f'opacity="0.35"/>')

    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {box} {box}" '
            f'width="{box}" height="{box}" role="img" '
            f'aria-label="{fam} package outline">' + "".join(p) + "</svg>")
