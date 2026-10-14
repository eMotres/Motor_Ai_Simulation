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
    "core": "#5D6974",             # stator laminations: iron (owner 2026-10-14: a bit lighter)
    "rotor_core": "#424A53",       # rotor laminations: iron, darker than the stator
    "coil": "#C8642A",             # vivid copper
    "magnet_n": "#D7263D",         # magnets alternate by index: N red ...
    "magnet_s": "#1B6EF3",         # ... S blue
    "shaft": "#9AA3AB",            # stainless steel (shaft): light, matte (owner 2026-10-14)
    "aluminium": "#7F8C99",        # anodised aluminium: holders, Prop_fix, carriers (clearly different from the core)
    "ring": "#E0B341",             # GFRP ring: amber
    "bearing_ring": "#C0C6CC",
    "bearing_ball": "#E3E6E9",
    "bearing_seal": "#26292D",     # rubber seal
    "screw": "#D9DCDF",            # nickel-plated: bright, slightly warm silver (owner 2026-10-14)
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
    "Bearing_outer_ring": "bearing_ring", "Bearing_inner_ring": "bearing_ring", "Bearing_ball": "bearing_ball", "Bearing_seal": "bearing_seal",
    "PhaseBoard": "pcb_mask", "PhaseBoardPins": "pcb_pins",
    "Stator_holder": "aluminium", "Stator_holder_back": "aluminium", "Ring": "ring", "Prop_fix": "aluminium", "Screw": "screw",
}
# role -> (metallic, roughness) of the GLB material and, via appearance(), of the FreeCAD ShapeAppearance; default (0.1, 0.55)
MATERIAL = {"shaft": (0.4, 0.6), "screw": (0.9, 0.25)}      # stainless steel: matte (owner); screws: nickel, shiny
DEFAULT_MATERIAL = (0.1, 0.55)
# GLB group name -> palette role (export_models.py)
GROUP_ROLE = {"Core": "core", "Coils": "coil", "Rotor": "rotor_core", "Magnets": "magnet_n", "Magnets_S": "magnet_s",
              "Shaft": "shaft", "Shaft_rear": "aluminium", "Bearings_front": "bearing_ring", "Bearings_back": "bearing_ring",
              "Bearings_front_balls": "bearing_ball", "Bearings_back_balls": "bearing_ball", "Holder_front": "aluminium",
              "Holder_back": "aluminium", "Bearings_front_seals": "bearing_seal", "Bearings_back_seals": "bearing_seal", "Ring_front": "ring", "Ring_back": "ring", "Screws": "screw", "Prop_fix": "aluminium",
              "PhaseBoard_FR4": "pcb_mask", "PhaseBoard_copper": "pcb_gold", "PhaseBoard_pins": "pcb_pins", "PhaseBoard_other": "pcb_fr4"}
THREAD_DARKEN = 0.72               # cosmetic thread faces: the host colour, a little darker


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(round(int(h[i:i + 2], 16) / 255.0, 4) for i in (0, 2, 4))


def rgb(role):
    return hex_to_rgb(PALETTE.get(role, DEFAULT))


def body_colour(name):
    return rgb(BODY_ROLE.get(name, ""))


def material(role):
    return MATERIAL.get(role, DEFAULT_MATERIAL)


def group_material(gname):
    return material(GROUP_ROLE.get(gname, ""))


def specular_shininess(role):
    """FreeCAD ShapeAppearance values that read like the GLB material: specular grows with metallic and smoothness (low for matte
    steel, high for the nickel screws), shininess = 1 - roughness."""
    m, r = material(role)
    sp = round(0.05 + 0.7 * m * (1.0 - r), 3)
    return (sp, sp, sp), round(1.0 - r, 3)


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
                material={k: dict(metallic=v[0], roughness=v[1]) for k, v in MATERIAL.items()}, default_material=dict(metallic=0.1, roughness=0.55),
                group_role=dict(GROUP_ROLE),
                magnet_alternate=["magnet_n", "magnet_s"])


def write_json(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "palette.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(as_dict(), fh, indent=1)
    return path


if __name__ == "__main__":
    print(write_json())
