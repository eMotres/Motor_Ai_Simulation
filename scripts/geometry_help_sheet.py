#!/usr/bin/env python
"""Regenerate the static Geometry-tab Help picture.

ONE picture, for every model — not a per-machine render.  The Geometry tab's
Help button opens ``web/public/help/geometry_parameters.svg`` (a static
asset, no backend call), so labels never carry a value: a number from
whichever machine this script happened to load would be wrong on every other
model that shares the page (owner's correction, 2026-09-20 — the original
brief asked for a live per-machine render; keep that as the still-available
dev route ``GET /api/geometry/dimension_sheet``, but the button no longer
opens it).

Run this whenever the geometry schema grows (a new parameter needs a legend
row) or the drawing needs a tweak:

    python scripts/geometry_help_sheet.py

Draws CIANO10 200 opt / "L155 motor" (config/dies/CIANO10 200 opt/die.yaml +
that configuration's geometry_overrides) — the one machine in the catalog
that carries every drawable feature at once (a retaining sleeve, on top of
the usual slots/teeth/yoke/magnets/shaft/air gap), so the picture has a real
leader for as much of the schema as possible. The polygons are the real
``CadQueryMotor.get_2d_polygons()`` section for that machine, enlarged to one
sector (owner's addendum, 2026-09-20: the full ring was too small to letter);
only the printed labels are name-only.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

OUT_DIR = REPO_ROOT / "web" / "public" / "help"
DIE_PATH = REPO_ROOT / "config" / "dies" / "CIANO10 200 opt" / "die.yaml"
DUTY_PATH = REPO_ROOT / "config" / "dies" / "CIANO10 200 opt" / "L155 motor.yaml"
CONFIG_PATH = REPO_ROOT / "config" / "motor_config.yaml"


def main() -> None:
    import yaml

    from motor_ai_sim.geometry.motor_geometry import MotorGeometryParams
    from motor_ai_sim.routes._validation import SCHEMA_FALLBACK
    from motor_ai_sim.services.dimension_sheet import build_dimension_sheet

    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    schema = {k: dict(v) for k, v in (cfg.get("geometry_schema") or {}).items()}
    for k, v in SCHEMA_FALLBACK.items():
        schema.setdefault(k, dict(v))
    groups_cfg = cfg.get("parameter_groups", {}) or {}
    groups = sorted(
        [{"id": gid, "label": meta.get("label", gid.title()), "order": meta.get("order", 99)}
         for gid, meta in groups_cfg.items()],
        key=lambda g: g["order"])

    die = yaml.safe_load(DIE_PATH.read_text(encoding="utf-8"))
    duty = yaml.safe_load(DUTY_PATH.read_text(encoding="utf-8"))
    full_geo = {**die["geometry"], **(duty.get("geometry_overrides") or {})}
    geo = MotorGeometryParams(full_geo, {}).to_dict()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    title = "Geometry parameters — one sector, enlarged (bore to sleeve)"

    svg = build_dimension_sheet(geo, schema, groups, fmt="svg", show_values=False,
                                 layout="sector", title=title)
    (OUT_DIR / "geometry_parameters.svg").write_bytes(svg)
    print(f"wrote {OUT_DIR / 'geometry_parameters.svg'} ({len(svg):,} bytes)")

    png = build_dimension_sheet(geo, schema, groups, fmt="png", show_values=False,
                                 layout="sector", title=title)
    (OUT_DIR / "geometry_parameters.png").write_bytes(png)
    print(f"wrote {OUT_DIR / 'geometry_parameters.png'} ({len(png):,} bytes)")

    missing = [k for k in schema if k.encode() not in svg]
    if missing:
        print(f"WARNING: {len(missing)} schema key(s) not found verbatim in the SVG text: "
              f"{missing}", file=sys.stderr)
    else:
        print(f"all {len(schema)} schema keys are present in the SVG text.")


if __name__ == "__main__":
    main()
