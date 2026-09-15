"""FreeCAD export when OCP cannot be imported (App Control, 2026-09-14).

The user's PC enforces a Windows Defender Application Control policy that
denies the VTK DLL ``import OCP`` pulls in, so ``cadquery`` raises ImportError
and no BREP/STEP can be written server-side.  The export must then degrade to a
bundle FreeCAD can rebuild itself — DXF sections + parameters.csv + a macro —
instead of returning a 500.

Nothing here writes state: only the export path (which reads the live config)
and the pure-Python writers.  The OCP path is NOT exercised — on this machine
it cannot run, which is the whole point.
"""
from __future__ import annotations

import csv
import io
import zipfile

import pytest

from motor_ai_sim import freecad_io as F
from motor_ai_sim.config import get_config
from motor_ai_sim.routes import freecad as R


BLOCKED = ("DLL load failed while importing OCP: An Application Control "
           "policy has blocked this file.")


@pytest.fixture()
def no_ocp(monkeypatch):
    """Force the ImportError path regardless of what this machine can import."""
    monkeypatch.setattr(F, "ocp_available", lambda: (False, BLOCKED))
    return BLOCKED


def _geo() -> dict:
    return dict(get_config().get("geometry") or {})


def _bundle(no_ocp) -> zipfile.ZipFile:
    r = R.export_freecad()
    assert r.headers["X-Bundle-Kind"] == "macro-dxf"
    assert "_freecad_macro.zip" in r.headers["Content-Disposition"]
    return zipfile.ZipFile(io.BytesIO(bytes(r.body)))


# ── DXF entities: layer names and ring counts against the live geometry ──────

def _dxf_rings(text: str):
    """[(layer, n_points)] from POLYLINE/VERTEX/SEQEND — the same twenty-line
    reader the shipped macro uses, so this test pins the format the macro
    parses, not just 'some DXF came out'."""
    lines = text.splitlines()
    pairs = []
    for k in range(0, len(lines) - 1, 2):
        try:
            pairs.append((int(lines[k].strip()), lines[k + 1].strip()))
        except ValueError:
            continue
    out, cur, layer, n = [], None, None, 0
    for code, val in pairs:
        if code == 0:
            if val == "POLYLINE":
                layer, n = None, 0
            elif val == "SEQEND" and layer is not None:
                out.append((layer, n))
                layer = None
            cur = val
        elif cur == "POLYLINE" and code == 8:
            layer = val
        elif cur == "VERTEX" and code == 10:
            n += 1
    return out


def _expected():
    """(layer -> ring count, layer -> total points) from the live builder."""
    rings, pts = {}, {}
    for layer, geom in F.part_polygons(_geo()):
        rs = list(F._rings(geom))
        rings[layer] = rings.get(layer, 0) + len(rs)
        pts[layer] = pts.get(layer, 0) + sum(len(r) for r in rs)
    return rings, pts


def test_layers_are_the_geometry_parts_and_exclude_the_air_bands(no_ocp):
    z = _bundle(no_ocp)
    dxf = next(n for n in z.namelist() if n.endswith(".dxf"))
    layers = {lay for lay, _n in _dxf_rings(z.read(dxf).decode("utf-8", "ignore"))}
    exp_rings, _pts = _expected()
    assert layers == set(exp_rings)
    assert {"stator", "rotor", "shaft"} <= layers
    assert any(l.startswith("magnet_") for l in layers)
    assert any(l.startswith("coil_") for l in layers)
    # Solver scaffolding is not a part.
    assert not (layers & {"air_gap", "in_band", "out_band"})


def test_ring_and_vertex_counts_match_the_live_polygon_builder(no_ocp):
    z = _bundle(no_ocp)
    dxf = next(n for n in z.namelist() if n.endswith(".dxf"))
    got_rings, got_pts = {}, {}
    for lay, n in _dxf_rings(z.read(dxf).decode("utf-8", "ignore")):
        got_rings[lay] = got_rings.get(lay, 0) + 1
        got_pts[lay] = got_pts.get(lay, 0) + n
    exp_rings, exp_pts = _expected()
    assert got_rings == exp_rings
    assert got_pts == exp_pts
    # Holes ride along as their own rings: the stator has slots, so it cannot
    # be a single ring (a regression that dropped interiors would show here).
    assert exp_rings["stator"] > 1


def test_minimal_writer_matches_the_ezdxf_writer_ring_for_ring():
    """ezdxf is optional — the hand-written fallback must be readable by the
    same parser and describe the same rings."""
    parts = F.part_polygons(_geo())
    a = _dxf_rings(F.write_dxf(parts).decode("utf-8", "ignore"))
    b = _dxf_rings(F._write_dxf_minimal(parts).decode("utf-8", "ignore"))
    assert a == b
    assert b


# ── parameters.csv: the Fusion set, our names ───────────────────────────────

def test_csv_header_and_rows_are_the_fusion_primary_set(no_ocp):
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    from motor_ai_sim.routes.fusion import FUSION_EXCLUDED_NAMES, _rows

    z = _bundle(no_ocp)
    rows = list(csv.reader(io.StringIO(z.read("parameters.csv").decode("utf-8"))))
    assert rows[0] == list(F.CSV_HEADER) == ["parameter", "value", "unit",
                                             "description"]
    names = [r[0] for r in rows[1:]]
    assert len(names) == len(set(names)) == len(_rows())
    # Same set as the Fusion channel (one source, no drift) but under OUR keys,
    # which is what read_fcstd_params() looks up on the way back.
    assert names == [our_key for _f, our_key, _v, _u, _d in _rows()]
    assert not (set(names) & set(DERIVED_GEOMETRY_NAMES))
    assert not (set(names) & set(FUSION_EXCLUDED_NAMES))
    geo = _geo()
    for name, value, _unit, _desc in rows[1:]:
        assert abs(float(value) - float(geo[name])) < 1e-5, name


# ── bundle shape + the macro itself ─────────────────────────────────────────

def test_bundle_members_and_readme_say_why(no_ocp):
    z = _bundle(no_ocp)
    names = set(z.namelist())
    assert "parameters.csv" in names
    assert "build_motor.FCMacro" in names
    assert "README.txt" in names
    assert sum(1 for n in names if n.endswith(".dxf")) == 1
    readme = z.read("README.txt").decode("utf-8")
    assert "Application Control" in readme
    assert BLOCKED in readme
    assert "build_motor.FCMacro" in readme
    # No solids in this bundle — that is the point.
    assert not [n for n in names if n.endswith((".FCStd", ".step", ".brp"))]


def test_macro_is_valid_python_and_carries_the_parameters(no_ocp):
    z = _bundle(no_ocp)
    src = z.read("build_motor.FCMacro").decode("utf-8")
    compile(src, "build_motor.FCMacro", "exec")      # syntax, not execution
    dxf = next(n for n in z.namelist() if n.endswith(".dxf"))
    assert ("DXF = %r" % dxf) in src
    assert ("LENGTH = %r" % float(_geo()["motor_length"])) in src
    assert "setAlias" in src and "extrude" in src
    # It must not reach the network or the legacy DXF importer.
    assert "importDXF" not in src
    # Hundreds of coil/magnet profiles become two compounds, as the .FCStd
    # export does — one tree item per conductor is unusable (verified in
    # FreeCAD 1.1.3: 463 profiles -> 5 objects).
    assert '("magnet_", "Magnets")' in src and '("coil_", "Coils")' in src
    assert "makeCompound" in src
    for name, _v, _u, _d in F.macro_rows(_geo()):
        assert ("(%r," % name) in src


def test_export_is_the_ocp_bundle_when_ocp_works(monkeypatch):
    """The fallback must not steal the good path on a machine without the
    policy: only an ImportError switches bundles."""
    monkeypatch.setattr(F, "ocp_available", lambda: (True, ""))
    called = {}

    def _boom(*a, **k):
        called["build_solids"] = True
        raise RuntimeError("stop here — the OCP path was taken")

    monkeypatch.setattr(F, "build_solids", _boom)
    monkeypatch.setattr(F, "build_macro_bundle",
                        lambda *a, **k: pytest.fail("macro bundle on a working OCP"))
    with pytest.raises(Exception):
        R.export_freecad()
    assert called.get("build_solids")
