"""The Fusion CSV import must refuse an impossible machine LOUDLY, on the
PREVIEW (``dry_run=1``, what the confirm dialog is built from) as well as
the real write (``dry_run=0``) -- and the same values typed straight into
the Geometry tab (``PUT /api/geometry``) must be refused with the identical
message, since both paths write through ``update_geometry`` /
``check_geometry_submission``.

Regression for the 2026-09-25 owner report: importing
``CIAN_12_40_canonical_params.csv`` (stator_diameter 30 -> 6 mm, alongside a
slot_height/core_thickness/air_gap/magnet_height/rotor_house_height stack
that does not fit inside it) showed a clean "apply 30 parameters?" confirm
dialog -- the dry run only diffed values, it never validated the RESULT of
applying them together -- and only the second click (the real write) was
refused, by which point the user had already told the browser to go ahead.
The rotor bore comes out negative: rotor_outer_radius = stator_diameter/2 -
core_thickness - slot_height - air_gap = 3 - 0.7 - 1.842 - 0.1 = 0.358 mm,
and rotor_inner_radius = 0.358 - magnet_height(2) - rotor_house_height(0.3)
= -1.942 mm -- there is no rotor left for the magnets to sit in.
"""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.config import get_config
from motor_ai_sim.routes.fusion import import_params

client = TestClient(app)

# The owner's actual CSV (config/motor_config.yaml keys only; `slot_hs` is a
# FUSION_EXCLUDED_NAMES row and rides along unchanged).
OWNER_CSV = b"""Name,Unit,Expression,Value,Comment,Favorite
air_gap,mm,0.1,0.1,,False
core_thickness,mm,0.7,0.7,,False
cut_width,mm,1,1,,False
insulation_thickness,mm,0.05,0.05,,False
magnet_down_height,mm,2,2,,False
magnet_fill_down,,0.85,0.85,,False
magnet_fill_radius,mm,0.2,0.2,,False
magnet_fill_up,,0.6,0.6,,False
magnet_height,mm,2,2,,False
magnet_lamination,mm,40,40,,False
magnet_up_gap,mm,0.1,0.1,,False
motor_length,mm,40,40,,False
num_poles_per_segment,,5,5,,False
num_seg,,2,2,,False
num_slots_per_segment,,6,6,,False
num_wires_per_slot,,3,3,,False
rotor_fill_r,mm,0.2,0.2,,False
rotor_hole,,0.7,0.7,,False
rotor_house_height,mm,0.3,0.3,,False
shaft_height,mm,2,2,,False
sleeve_thickness,mm,0,0,,False
slot_height,mm,1.842,1.842,,False
slot_hs,,0.11,0.11,,False
stator_diameter,mm,6,6,,False
stator_fillet_r,mm,0.5,0.5,,False
stator_fillet_r1,mm,0.05,0.05,,False
tooth2_width,mm,0.5,0.5,,False
tooth_width,mm,1,1,,False
wire_height,mm,0.2,0.2,,False
wire_spacing_x,mm,0.05,0.05,,False
wire_spacing_y,mm,0.05,0.05,,False
wire_width,mm,0.8,0.8,,False
"""


def _upload(body: bytes = OWNER_CSV) -> UploadFile:
    return UploadFile(filename="owner.csv", file=io.BytesIO(body))


def test_dry_run_refuses_the_impossible_machine_before_any_confirm():
    """The PREVIEW must already say no -- this is what the confirm dialog's
    contents come from, and it must disable OK rather than list 30 clean
    changes for an unbuildable machine."""
    out = asyncio.run(import_params(file=_upload(), dry_run=1,
                                    _admin={"role": "admin"}))
    assert out["ok"] is False
    assert out.get("invalid_parameters"), (
        "dry run reported no errors for a machine whose rotor bore is "
        "negative -- the confirm dialog would show a clean list")
    fields = {p["field"] for p in out["invalid_parameters"]}
    assert fields & {"magnet_height", "air_gap", "slot_height"}, out["invalid_parameters"]
    # nothing may have been written by a dry run, valid or not
    assert dict(get_config(reload=True).get("geometry") or {}).get(
        "stator_diameter") != 6


def test_apply_refuses_with_the_same_message_as_the_dry_run(monkeypatch):
    dry = asyncio.run(import_params(file=_upload(), dry_run=1,
                                    _admin={"role": "admin"}))
    with pytest.raises(Exception) as ei:
        asyncio.run(import_params(file=_upload(), dry_run=0,
                                  _admin={"role": "admin"}))
    detail = getattr(ei.value, "detail", None)
    assert isinstance(detail, dict) and detail.get("invalid_parameters"), ei.value
    assert detail["invalid_parameters"] == dry["invalid_parameters"], (
        "the real write disagreed with the preview it showed the user")
    # nothing was written: the live geometry still is not the refused machine
    assert dict(get_config(reload=True).get("geometry") or {}).get(
        "stator_diameter") != 6


def test_geometry_patch_refuses_the_same_values_the_import_does():
    """Typed straight into the Geometry tab, the identical combination must
    be refused with the identical message -- both paths go through
    ``update_geometry`` / ``check_geometry_submission``."""
    changed = {
        "air_gap": 0.1, "core_thickness": 0.7, "magnet_height": 2,
        "rotor_house_height": 0.3, "slot_height": 1.842, "stator_diameter": 6,
    }
    r = client.put("/api/geometry", json=changed)
    assert r.status_code == 422, r.text
    body = r.json()["detail"]
    assert body["error"] == "invalid geometry parameter value"
    fields = {p["field"] for p in body["invalid_parameters"]}
    assert fields & {"magnet_height", "air_gap", "slot_height"}, body


def test_a_valid_csv_still_applies():
    """A round-tripped export of the CURRENT (valid) machine, with one small
    still-valid tweak, must still import cleanly through the same dry-run +
    apply path -- the new guard must not refuse a design that fits."""
    from motor_ai_sim.routes.fusion import export_params_csv
    import csv as _csv

    csv_text = bytes(export_params_csv().body).decode("utf-8-sig")
    rows = list(_csv.reader(io.StringIO(csv_text)))
    header, body_rows = rows[0], rows[1:]
    out_rows = []
    bumped = False
    for row in body_rows:
        name, unit, expr, value, comment, fav = row
        if name == "wire_spacing_x" and not bumped:
            v = float(expr) + 0.01           # tiny, well inside every bound
            expr = value = ("%g" % v)
            bumped = True
        out_rows.append([name, unit, expr, value, comment, fav])
    assert bumped, "fixture drifted: wire_spacing_x no longer exported"
    buf = io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(out_rows)
    body_bytes = buf.getvalue().encode("utf-8-sig")

    dry = asyncio.run(import_params(file=_upload(body_bytes), dry_run=1,
                                    _admin={"role": "admin"}))
    assert dry["ok"] is True, dry
    assert not dry.get("invalid_parameters")
    assert "wire_spacing_x" in dry["applied"]

    applied = asyncio.run(import_params(file=_upload(body_bytes), dry_run=0,
                                        _admin={"role": "admin"}))
    assert applied["ok"] is True, applied
    assert applied["applied"].get("wire_spacing_x") is not None
    assert (dict(get_config(reload=True).get("geometry") or {})
            .get("wire_spacing_x")) == pytest.approx(
                float(applied["applied"]["wire_spacing_x"]))
