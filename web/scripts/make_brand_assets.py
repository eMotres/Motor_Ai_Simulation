"""Build the web logo assets from the approved AeroStator master PNG.

Usage:  python -I web/scripts/make_brand_assets.py <master.png> [out_dir]

The artwork is only resized.  The white background OUTSIDE the navy stator
outline (the part connected to the image border) is made transparent; the white
inside the outline stays opaque, so the stator keeps its white body on dark
backgrounds.  Edge pixels are un-mixed from white so there is no white halo.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

src = Path(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "public" / "brand"
out.mkdir(parents=True, exist_ok=True)

rgb = np.asarray(Image.open(src).convert("RGB")).astype(np.float64)
mn = rgb.min(axis=2)

# 1. exterior = near-white pixels connected to the border.
near_white = mn >= 215
lab, _ = ndi.label(near_white)
border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
exterior = np.isin(lab, list(border))

# 2. edge band: pixels within 3 px of the exterior that are not clearly white.
band = ndi.binary_dilation(exterior, iterations=3) & ~(mn >= 245) | exterior
# alpha for the band: un-mix against white using the outline navy as the ink.
navy = np.array([2.0, 38.0, 78.0])
a_ch = (255.0 - rgb) / (255.0 - navy)            # per-channel coverage estimate
alpha_edge = np.clip(a_ch[..., 1], 0.0, 1.0)      # green separates navy/white best
alpha = np.ones(mn.shape)
alpha[exterior & (mn >= 245)] = 0.0
edge = band & ~(exterior & (mn >= 245))
# only pixels that touch the exterior are un-mixed (inner antialiasing is kept)
touch = ndi.binary_dilation(exterior & (mn >= 245), iterations=3) & edge
alpha[touch] = alpha_edge[touch]
alpha[exterior & ~touch & (mn < 245)] = 1.0
out_rgb = rgb.copy()
t = touch & (alpha > 0.02)
out_rgb[t] = (rgb[t] - (1 - alpha[t, None]) * 255.0) / alpha[t, None]
out_rgb = np.clip(out_rgb, 0, 255)
rgba = np.dstack([out_rgb, alpha * 255.0]).round().astype(np.uint8)
im = Image.fromarray(rgba, "RGBA")

# 3. trim transparent margins, pad to a square with a small margin.
x0, y0, x1, y1 = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
im = im.crop((x0, y0, x1, y1))
side = max(im.size)
pad = round(side * 0.03)
sq = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
sq.paste(im, ((sq.width - im.width) // 2, (sq.height - im.height) // 2))
im = sq


def sized(n):
    return im.resize((n, n), Image.LANCZOS)


for n in (512, 192, 64, 32):
    sized(n).save(out / f"aerostator-logo-{n}.png", optimize=True)
sized(512).save(out / "aerostator-logo-512.webp", quality=90, method=6)
sized(192).save(out / "aerostator-logo-192.webp", quality=90, method=6)
# iOS fills transparency with black, so the touch icon sits on white.
_t = Image.new("RGBA", (180, 180), (255, 255, 255, 255))
_t.alpha_composite(sized(156), (12, 12))
_t.convert("RGB").save(out / "apple-touch-icon.png", optimize=True)
sized(256).save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
print("done", im.size)
