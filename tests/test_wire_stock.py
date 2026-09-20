"""Wire stock table — the loader, its validation, the summed sizes, and the route.

``motor_ai_sim.wire_stock`` reads ``config/wire_stock.yaml``: the enamelled flat
copper wire physically on the shelf, meant to become the source the winding
editors restrict wire-size choice to (owner, 2026-09-20). This file pins the
loader's own contract — mtime-following, validation errors named per entry,
unique (code, spec), sorted by thickness then width — and the route that
serves it, the same shape ``tests/test_bearings.py`` uses for its library.
"""
from __future__ import annotations

import time as _t

import pytest

from motor_ai_sim import wire_stock as ws


def _pin(monkeypatch, path):
    monkeypatch.setattr(ws, "_LIB_PATH", path, raising=True)
    monkeypatch.setattr(ws, "_raw", None, raising=True)
    monkeypatch.setattr(ws, "_raw_mtime", 0.0, raising=True)
    monkeypatch.setattr(ws, "_raw_checked", 0.0, raising=True)
    monkeypatch.setattr(ws, "_wires_cache", None, raising=True)
    monkeypatch.setattr(ws, "_wires_cache_key", ("", 0.0), raising=True)


_GOOD = (
    "version: 1\n"
    "updated: \"2026-09-20\"\n"
    "unit: kg\n"
    "wires:\n"
    "  - code: \"A1\"\n"
    "    spec: \"SFT-AIW 0.5*2.5\"\n"
    "    thickness_mm: 0.5\n"
    "    width_mm: 2.5\n"
    "    insulation: \"AIW\"\n"
    "    self_bonding: false\n"
    "    stock_kg: 6.0\n"
    "    warehouse: \"\"\n"
    "  - code: \"A2\"\n"
    "    spec: \"SFT-AIW/SB 0.3*3.5\"\n"
    "    thickness_mm: 0.3\n"
    "    width_mm: 3.5\n"
    "    insulation: \"AIW\"\n"
    "    self_bonding: true\n"
    "    thermal_class_c: 200\n"
    "    stock_kg: 2.27\n"
    "    warehouse: \"小象仓库\"\n"
    "  - code: \"A3\"\n"
    "    spec: \"SFT-AIW 0.3*3.5 (second lot)\"\n"
    "    thickness_mm: 0.3\n"
    "    width_mm: 3.5\n"
    "    insulation: \"AIW\"\n"
    "    self_bonding: false\n"
    "    stock_kg: 4.52\n"
    "    warehouse: \"富临仓库\"\n"
    "    check: true\n"
)


# ---------------------------------------------------------------------------
# 1.  Loading, sorting, the dataclass
# ---------------------------------------------------------------------------

def test_loader_parses_sorts_and_carries_check_flag(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(_GOOD, encoding="utf-8")
    _pin(monkeypatch, lib)

    wires = ws.list_wires()
    assert [w.code for w in wires] == ["A2", "A3", "A1"], (
        "sorted by thickness then width then code: 0.3x3.5 (A2, A3) before 0.5x2.5 (A1)")

    a2 = wires[0]
    assert a2.self_bonding is True
    assert a2.thermal_class_c == 200
    assert a2.check is False

    a3 = wires[1]
    assert a3.check is True, "the ambiguous-label flag must survive the load"

    a1 = wires[2]
    assert a1.thermal_class_c is None
    assert a1.stock_kg == pytest.approx(6.0)


def test_meta_reads_the_table_header(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(_GOOD, encoding="utf-8")
    _pin(monkeypatch, lib)

    m = ws.meta()
    assert m == {"version": 1, "updated": "2026-09-20", "unit": "kg"}


# ---------------------------------------------------------------------------
# 2.  Validation errors, named per entry
# ---------------------------------------------------------------------------

def test_nonpositive_thickness_is_rejected(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(
        "wires:\n"
        "  - code: \"B1\"\n    spec: \"bad\"\n"
        "    thickness_mm: 0\n    width_mm: 2.5\n"
        "    stock_kg: 1.0\n", encoding="utf-8")
    _pin(monkeypatch, lib)
    with pytest.raises(ws.WireStockError) as exc:
        ws.list_wires()
    assert "thickness_mm" in str(exc.value) and "B1" in str(exc.value)


def test_nonpositive_width_is_rejected(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(
        "wires:\n"
        "  - code: \"B2\"\n    spec: \"bad\"\n"
        "    thickness_mm: 0.5\n    width_mm: -1\n"
        "    stock_kg: 1.0\n", encoding="utf-8")
    _pin(monkeypatch, lib)
    with pytest.raises(ws.WireStockError) as exc:
        ws.list_wires()
    assert "width_mm" in str(exc.value) and "B2" in str(exc.value)


def test_negative_stock_is_rejected(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(
        "wires:\n"
        "  - code: \"B3\"\n    spec: \"bad\"\n"
        "    thickness_mm: 0.5\n    width_mm: 2.5\n"
        "    stock_kg: -0.1\n", encoding="utf-8")
    _pin(monkeypatch, lib)
    with pytest.raises(ws.WireStockError) as exc:
        ws.list_wires()
    assert "stock_kg" in str(exc.value)


def test_duplicate_code_and_spec_is_rejected(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(
        "wires:\n"
        "  - code: \"C1\"\n    spec: \"same\"\n"
        "    thickness_mm: 0.5\n    width_mm: 2.5\n    stock_kg: 1.0\n"
        "  - code: \"C1\"\n    spec: \"same\"\n"
        "    thickness_mm: 0.6\n    width_mm: 2.5\n    stock_kg: 2.0\n",
        encoding="utf-8")
    _pin(monkeypatch, lib)
    with pytest.raises(ws.WireStockError) as exc:
        ws.list_wires()
    assert "duplicate" in str(exc.value).lower()


def test_same_code_different_spec_is_allowed(tmp_path, monkeypatch):
    """Two lots of the same item code with distinct spec strings (batches) are a
    real warehouse case, not a data error — only the (code, spec) PAIR must be
    unique."""
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(
        "wires:\n"
        "  - code: \"D1\"\n    spec: \"lot A\"\n"
        "    thickness_mm: 0.5\n    width_mm: 2.5\n    stock_kg: 1.0\n"
        "  - code: \"D1\"\n    spec: \"lot B\"\n"
        "    thickness_mm: 0.5\n    width_mm: 2.5\n    stock_kg: 2.0\n",
        encoding="utf-8")
    _pin(monkeypatch, lib)
    wires = ws.list_wires()
    assert len(wires) == 2


def test_missing_wires_list_is_rejected(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text("version: 1\n", encoding="utf-8")
    _pin(monkeypatch, lib)
    with pytest.raises(ws.WireStockError):
        ws.list_wires()


# ---------------------------------------------------------------------------
# 3.  The mtime-following cache survives a broken edit
# ---------------------------------------------------------------------------

def test_loader_reloads_on_mtime_and_survives_a_broken_edit(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(_GOOD, encoding="utf-8")
    _pin(monkeypatch, lib)

    assert len(ws.list_wires()) == 3

    _t.sleep(1.1)
    lib.write_text(
        "wires:\n"
        "  - code: \"Z1\"\n    spec: \"only one now\"\n"
        "    thickness_mm: 0.4\n    width_mm: 7.0\n    stock_kg: 9.0\n",
        encoding="utf-8")
    wires = ws.list_wires()
    assert len(wires) == 1 and wires[0].code == "Z1", "the cache did not follow the file"

    _t.sleep(1.1)
    lib.write_text("wires:\n  - code: [unclosed\n", encoding="utf-8")
    wires_again = ws.list_wires()
    assert len(wires_again) == 1 and wires_again[0].code == "Z1", (
        "a malformed edit must keep serving the last good, validated copy")


# ---------------------------------------------------------------------------
# 4.  Summed sizes
# ---------------------------------------------------------------------------

def test_available_sizes_sums_stock_per_size_and_lists_codes(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(_GOOD, encoding="utf-8")
    _pin(monkeypatch, lib)

    sizes = ws.available_sizes()
    assert sizes == [
        {"thickness_mm": 0.3, "width_mm": 3.5, "stock_kg": pytest.approx(2.27 + 4.52),
         "codes": ["A2", "A3"]},
        {"thickness_mm": 0.5, "width_mm": 2.5, "stock_kg": pytest.approx(6.0),
         "codes": ["A1"]},
    ]


def test_nearest_sizes_orders_by_distance(tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(_GOOD, encoding="utf-8")
    _pin(monkeypatch, lib)

    nearest = ws.nearest_sizes(0.5, 3.0, n=2)
    assert [(s["thickness_mm"], s["width_mm"]) for s in nearest] == [
        (0.5, 2.5), (0.3, 3.5)]


# ---------------------------------------------------------------------------
# 5.  The route
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    return TestClient(app)


def test_stock_route_serves_the_table(client, tmp_path, monkeypatch):
    lib = tmp_path / "wire_stock.yaml"
    lib.write_text(_GOOD, encoding="utf-8")
    _pin(monkeypatch, lib)

    r = client.get("/api/wires/stock")
    assert r.status_code == 200
    j = r.json()
    assert j["unit"] == "kg"
    assert [w["code"] for w in j["wires"]] == ["A2", "A3", "A1"]
    assert any(w["check"] is True for w in j["wires"])
    sizes = {(s["thickness_mm"], s["width_mm"]): s["stock_kg"] for s in j["available_sizes"]}
    assert sizes[(0.3, 3.5)] == pytest.approx(2.27 + 4.52)
    assert sizes[(0.5, 2.5)] == pytest.approx(6.0)


def test_stock_route_is_not_gated(client):
    """Same tier as GET /api/materials: no entry in auth's tier tables — open
    to an anonymous caller, since it is a reference table, not compute."""
    from motor_ai_sim.auth import required_tier
    assert required_tier("GET", "/api/wires/stock") is None


def test_real_config_wire_stock_yaml_is_valid():
    """The repo's own ``config/wire_stock.yaml`` — the file the owner's data
    landed in — must load and validate cleanly, unpinned."""
    wires = ws.list_wires()
    assert len(wires) > 0
    codes_specs = {(w.code, w.spec) for w in wires}
    assert len(codes_specs) == len(wires)
    for w in wires:
        assert w.thickness_mm > 0 and w.width_mm > 0
        assert w.stock_kg >= 0
