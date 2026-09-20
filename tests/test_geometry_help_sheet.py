"""The Geometry tab's Help picture.

Two things are covered:

1. The STATIC asset (``web/public/help/geometry_parameters.svg`` /
   ``.png``) — what the Help button actually opens (one picture, for every
   model, built once by ``scripts/geometry_help_sheet.py``; see that
   script's docstring for why it is not a per-machine render). Every
   geometry-schema key must appear in the SVG text (as a dimension label or
   a legend row), the SVG must be valid XML, and the PNG must have real PNG
   magic bytes — these are exactly the invariants the owner asked to be
   guarded so schema growth cannot silently drop a parameter off the page.

2. The (now hidden/dev-only) per-machine route builder,
   ``services.dimension_sheet.build_dimension_sheet``, still works for the
   three dies actually present in ``config/`` — kept per the owner's
   permission to leave it as a dev endpoint even though the Help button no
   longer opens it.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from motor_ai_sim.geometry.motor_geometry import MotorGeometryParams
from motor_ai_sim.routes._validation import SCHEMA_FALLBACK, geometry_schema_meta
from motor_ai_sim.services.dimension_sheet import build_dimension_sheet

_ROOT = Path(__file__).resolve().parents[1]
_HELP_SVG = _ROOT / "web" / "public" / "help" / "geometry_parameters.svg"
_HELP_PNG = _ROOT / "web" / "public" / "help" / "geometry_parameters.png"
_RADII_SVG = _ROOT / "web" / "public" / "help" / "geometry_parameters_radii.svg"
_RADII_PNG = _ROOT / "web" / "public" / "help" / "geometry_parameters_radii.png"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: The 5 diameters/radii plus the 3 gap-zone dimensions the Radii tab
#: (second Help-window tab) must carry — not every schema key, only the ones
#: that page's own two pictures (full ring + gap-zone callout) draw.
_RADII_PAGE_KEYS = ("stator_diameter", "stator_inner_radius", "rotor_outer_radius",
                    "rotor_inner_radius", "shaft_height", "air_gap",
                    "magnet_up_gap", "sleeve_thickness")

_CONFIG_PATH = _ROOT / "config" / "motor_config.yaml"


def _schema_keys() -> set:
    """Every key the STATIC generator script draws from — the config's own
    ``geometry_schema`` block plus the server-owned fallback knobs
    (``SCHEMA_FALLBACK``), exactly what ``scripts/geometry_help_sheet.py``
    unions before rendering."""
    cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    keys = set((cfg.get("geometry_schema") or {}).keys())
    keys |= set(SCHEMA_FALLBACK.keys())
    return keys


# ── the static Help picture ─────────────────────────────────────────────────

def test_static_svg_exists_and_is_committed():
    assert _HELP_SVG.is_file(), (
        "web/public/help/geometry_parameters.svg is missing — run "
        "`python scripts/geometry_help_sheet.py` and commit the result.")
    assert _HELP_PNG.is_file()


def test_radii_picture_exists_and_is_committed():
    assert _RADII_SVG.is_file(), (
        "web/public/help/geometry_parameters_radii.svg is missing — run "
        "`python scripts/geometry_help_sheet.py` and commit the result.")
    assert _RADII_PNG.is_file()
    ET.parse(_RADII_SVG)
    assert _RADII_PNG.read_bytes()[:8] == _PNG_MAGIC


def test_radii_page_keys_are_on_the_radii_picture():
    svg_text = _RADII_SVG.read_text(encoding="utf-8")
    tree = ET.fromstring(svg_text)
    rendered = " ".join(
        (node.text or "") for node in tree.iter()
        if node.tag.endswith("}text") or node.tag.endswith("}tspan"))
    missing = sorted(k for k in _RADII_PAGE_KEYS if k not in rendered)
    assert not missing, f"radii-page key(s) missing from the picture: {missing}"


def test_static_svg_is_valid_xml():
    ET.parse(_HELP_SVG)   # raises ParseError on malformed XML


def test_static_png_has_real_png_magic_bytes():
    head = _HELP_PNG.read_bytes()[:8]
    assert head == _PNG_MAGIC


def test_every_schema_key_is_on_the_static_picture():
    """Parses the SVG's own <text> content (not just a raw byte search) so
    the assertion is really "the key renders as text on the page", matching
    what the owner asked to be checked."""
    svg_text = _HELP_SVG.read_text(encoding="utf-8")
    tree = ET.fromstring(svg_text)
    rendered = " ".join(
        (node.text or "") for node in tree.iter()
        if node.tag.endswith("}text") or node.tag.endswith("}tspan"))
    missing = sorted(k for k in _schema_keys() if k not in rendered)
    assert not missing, f"schema key(s) missing from the Help picture: {missing}"


def test_static_picture_carries_no_values():
    """The static picture is ONE for every model — a value from whichever
    machine the generator happened to load would be wrong on every other
    model that opens the same page (owner's correction, 2026-09-20).

    ``key = value unit`` is the per-machine dev route's label format; the
    static picture must never contain that pattern for any schema key."""
    svg_text = _HELP_SVG.read_text(encoding="utf-8")
    offenders = [k for k in _schema_keys() if f"{k} = " in svg_text]
    assert not offenders, (
        f"static Help picture carries a live value for: {offenders} — it "
        f"should print names only "
        f"(build_dimension_sheet(..., show_values=False)).")


# ── the per-machine dev route builder (kept, not wired to the Help button) ──

_DIES = ["CIANO14 40 new", "CIANO28 85 20SW1200", "CIANO10 200 opt"]


@pytest.mark.parametrize("die_name", _DIES)
@pytest.mark.parametrize("fmt", ["svg", "png"])
def test_dev_route_builder_works_for_every_catalog_die(die_name, fmt):
    cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    schema = geometry_schema_meta()
    groups_cfg = cfg.get("parameter_groups", {}) or {}
    groups = sorted(
        [{"id": gid, "label": meta.get("label", gid), "order": meta.get("order", 99)}
         for gid, meta in groups_cfg.items()],
        key=lambda g: g["order"])

    die_path = _ROOT / "config" / "dies" / die_name / "die.yaml"
    die = yaml.safe_load(die_path.read_text(encoding="utf-8"))
    geo = MotorGeometryParams(dict(die["geometry"]), {}).to_dict()

    payload = build_dimension_sheet(geo, schema, groups, fmt=fmt)
    assert len(payload) > 1000
    if fmt == "png":
        assert payload[:8] == _PNG_MAGIC
    else:
        ET.fromstring(payload)   # valid XML
        rendered = " ".join(
            (node.text or "") for node in ET.fromstring(payload).iter()
            if node.tag.endswith("}text") or node.tag.endswith("}tspan"))
        missing = sorted(k for k in schema if k not in rendered)
        assert not missing, f"{die_name}: schema key(s) missing: {missing}"


# ── magnet dimensions drawn on the sector must be the REAL edges ───────────
# Regression for a bug the owner caught by eye: magnet_down_height and
# rotor_house_height were being drawn as the literal same span (rotor_inner_
# radius to the magnet's own bottom) — dimension_sheet.py read both off the
# picked magnet polygon's bounding box instead of the actual edges
# cadquery_geometry.py's magnet-pocket builder (get_2d_polygons, the "Magnet
# local polygon" block, mp1..mp6) moves. This test builds the SAME real
# polygons the picture is drawn from and checks that the four magnet
# dimensions' endpoints coincide, to within 0.1 mm, with an actual vertex
# radius of the magnet (or rotor) polygon — not merely with a schema number.

def _all_coords(geom):
    """Every (x, y) over a Polygon's or MultiPolygon's exterior AND
    interiors — the rotor is built as an annulus with the bore as an
    INTERIOR ring, so restricting to `.exterior` alone would miss it."""
    polys = list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]
    for poly in polys:
        yield from poly.exterior.coords
        for interior in poly.interiors:
            yield from interior.coords


def _has_vertex_at_radius(geom, r: float, tol: float = 0.1) -> bool:
    import math
    return any(abs(math.hypot(x, y) - r) < tol for x, y in _all_coords(geom))


def test_magnet_dimensions_match_real_geometry_vertices():
    import math

    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.services.dimension_sheet import _nearest_to_angle

    die_path = _ROOT / "config" / "dies" / "CIANO10 200 opt" / "die.yaml"
    duty_path = _ROOT / "config" / "dies" / "CIANO10 200 opt" / "L155 motor.yaml"
    die = yaml.safe_load(die_path.read_text(encoding="utf-8"))
    duty = yaml.safe_load(duty_path.read_text(encoding="utf-8"))
    full_geo = {**die["geometry"], **(duty.get("geometry_overrides") or {})}
    geo = MotorGeometryParams(full_geo, {}).to_dict()

    motor = CadQueryMotor()
    motor.set_parameters(geo)
    regions = motor.get_2d_polygons()

    rotor_radii = [math.hypot(x, y) for x, y in _all_coords(regions["rotor"])]
    rotor_ir_real, rotor_or_real = min(rotor_radii), max(rotor_radii)

    magnets = regions.get("magnets") or []
    assert magnets, "no magnets in this geometry — nothing to check"
    magnet = _nearest_to_angle(magnets, 90.0, key=lambda mp: mp[0])
    mag_poly = magnet[0]

    rotor_hh = float(geo["rotor_house_height"])
    mag_down_h = float(geo["magnet_down_height"])
    mag_up_gap = float(geo["magnet_up_gap"])

    magnet_r = rotor_ir_real + rotor_hh          # rotor_house_height's top edge
    magnet_r2 = magnet_r + mag_down_h            # magnet_down_height's top edge
    magnet_top_r = rotor_or_real - mag_up_gap    # magnet_up_gap's bottom edge

    # rotor_house_height: rotor_inner_radius -> magnet_r. magnet_r itself
    # must be a real vertex of the magnet (its bottom corner, mp1/mp6).
    assert _has_vertex_at_radius(mag_poly, magnet_r), (
        f"no magnet vertex at magnet_r={magnet_r:.3f} mm (rotor_inner_radius="
        f"{rotor_ir_real:.3f} + rotor_house_height={rotor_hh:.3f}) — "
        f"rotor_house_height's own top edge")

    # magnet_down_height: magnet_r -> magnet_r + magnet_down_height. THIS is
    # the specific regression check — the old code drew this dimension
    # identically to rotor_house_height (rotor_ir -> magnet_r) instead.
    assert _has_vertex_at_radius(mag_poly, magnet_r2), (
        f"no magnet vertex at magnet_r + magnet_down_height={magnet_r2:.3f} mm "
        f"— magnet_down_height must span magnet_r to THIS radius, not "
        f"rotor_inner_radius to magnet_r (that is rotor_house_height's span)")
    assert abs(magnet_r2 - magnet_r) > 0.1, (
        "magnet_down_height collapsed to ~0 in this fixture — pick a die/duty "
        "where it is nonzero so this test can tell the two dimensions apart")

    # magnet_up_gap: magnet's real top (the arc, mp3/mp4) -> rotor_outer_radius.
    assert abs(max(math.hypot(x, y) for x, y in _all_coords(mag_poly)) - magnet_top_r) < 0.1, (
        f"magnet's own max radius does not match rotor_outer_radius - "
        f"magnet_up_gap={magnet_top_r:.3f} mm")

    # magnet_height: magnet_r -> rotor_outer_radius (the NOMINAL envelope —
    # rotor_inner_radius is DERIVED as rotor_or - magnet_height -
    # rotor_house_height, see geometry/motor_geometry.py, so magnet_height
    # algebraically equals rotor_or - magnet_r; it is NOT the magnet's own
    # physical top when magnet_up_gap > 0).
    magnet_height_expected = rotor_or_real - magnet_r
    assert abs(float(geo["magnet_height"]) - magnet_height_expected) < 0.1, (
        f"geo['magnet_height']={geo['magnet_height']:.3f} != "
        f"rotor_outer_radius - magnet_r = {magnet_height_expected:.3f} mm")
