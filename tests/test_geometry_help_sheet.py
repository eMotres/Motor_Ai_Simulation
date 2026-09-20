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
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

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
