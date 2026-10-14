"""THE colour palette of every motor model (owner 2026-10-14: «поконтрастнее; все цвета по умолчанию для всех моделей
одинаково»).  One source of truth, used by

  view.py            body / array colours of the FreeCAD GUI finish (GuiDocument.xml), thread faces, phase board faces
  export_models.py   materials of the web viewer GLB (and any render made from it)
  palette.json       JSON copy for the web viewer (written by `python palette.py`, checked by the build tests)

Aligned with the approved logo (navy / red / blue).  All colours are opaque.  A body that has no entry falls back to
`DEFAULT`.  Change a colour HERE only; run `python cad/motor_templates/segmented_flatwire/palette.py` to refresh palette.json.
"""
import colorsys
import json
import os

# role -> hex
PALETTE = {
    "core": "#4A5560",             # stator laminations: dark steel
    "rotor_core": "#2E343B",       # rotor laminations: darker steel
    "coil": "#C8642A",             # vivid copper
    "magnet_n": "#D7263D",         # magnets alternate by index: N red ...
    "magnet_s": "#1B6EF3",         # ... S blue
    "shaft": "#A7B0B8",            # polished steel
    "aluminium": "#7F8C99",        # anodised aluminium: holders, Prop_fix, carriers (clearly different from the core)
    "ring": "#E0B341",             # GFRP ring: amber
    "bearing_ring": "#C0C6CC",
    "bearing_ball": "#E3E6E9",
    "screw": "#5E646B",            # dark zinc
    "pcb_mask": "#1E7A3B",         # solder mask green
    "pcb_gold": "#D4AF37",         # copper pads / plated copper
    "pcb_fr4": "#C9B27C",          # FR4 edge
    "pcb_pins": "#B5A642",         # brass pins
}
DEFAULT = "#9AA3AB"

# body / array name -> palette role (the names of the FreeCAD document)
BODY_ROLE = {
    "Core": "core", "Coil": "coil", "RotorCore": "rotor_core", "Magnet": "magnet_n",
    "Shaft": "shaft", "Shaft_rear": "aluminium",
    "Bearing_outer_ring": "bearing_ring", "Bearing_inner_ring": "bearing_ring", "Bearing_ball": "bearing_ball",
    "Stator_holder": "aluminium", "Stator_holder_back": "aluminium", "Ring": "ring", "Prop_fix": "aluminium", "Screw": "screw",
}
THREAD_DARKEN = 0.72               # cosmetic thread faces: the host colour, a little darker


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(round(int(h[i:i + 2], 16) / 255.0, 4) for i in (0, 2, 4))


def rgb(role):
    return hex_to_rgb(PALETTE.get(role, DEFAULT))


def body_colour(name):
    return rgb(BODY_ROLE.get(name, ""))


def thread_colour(host_rgb):
    h, l, s = colorsys.rgb_to_hls(*host_rgb[:3])
    return tuple(round(c, 4) for c in colorsys.hls_to_rgb(h, max(0.0, l * THREAD_DARKEN), s))


def magnet_colour(index):
    """Alternating poles: even index N (red), odd index S (blue)."""
    return rgb("magnet_n" if index % 2 == 0 else "magnet_s")


def pcb_role(rgb3):
    """Phase board face colour (from the STEP: mask green, yellow copper, tan FR4, silver / grey tin and pins) -> palette role."""
    r, g, b = rgb3[:3]
    if g > r and g > b and g - r > 0.05:
        return "pcb_mask"
    if r > 0.7 and g > 0.6 and b < 0.3:
        return "pcb_gold"
    if r - b > 0.06 and abs(r - g) < 0.08:
        return "pcb_fr4"
    return "pcb_pins"


def as_dict():
    return dict(version=1, note="single colour palette of all motor models (opaque); see palette.py",
                hex=dict(PALETTE), default=DEFAULT, body_role=dict(BODY_ROLE), thread_darken=THREAD_DARKEN,
                magnet_alternate=["magnet_n", "magnet_s"])


def write_json(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "palette.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(as_dict(), fh, indent=1)
    return path


if __name__ == "__main__":
    print(write_json())
