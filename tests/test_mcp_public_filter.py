"""MCP public-catalog filter (2026-10-19): no warehouse stock, no internal notes,
no machines without a catalogue card.

Pure unit tests over ``motor_ai_sim.mcp_tools`` and ``motor_ai_sim.public_text``
with the catalogue stores monkeypatched - no solver, no server, no real config.
The fixture rows are copies of the real strings that leaked on the deployed
catalogue (wire stock lots, bearing design notes, owner attributions).
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from motor_ai_sim import mcp_tools as T
from motor_ai_sim.public_text import public_text, public_wire_spec

P = object()      # the Principal is only handed to acting_as, which is stubbed

_WIRE_BATCHES = ("SFT-AIW 0.300x2.500 (batches W012020...3B260617 9.18 kg, "
                 "W5610102A260624 7.12 kg)")
_WIRE_LOTS = "1*7 (two lots: 302.849 + 18.534 kg)"

_LEAK_WORDS = ("batch", "lot", "kg", "warehouse", "stock table", "W5610102A",
               "holder", "flange", "back tube", "docs/", "23 000", "owner",
               "does not fit")


@pytest.fixture()
def stubbed(monkeypatch, tmp_path):
    @contextmanager
    def _noop(_p):
        yield
    monkeypatch.setattr(T, "acting_as", _noop)
    return tmp_path


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _assert_clean(obj):
    """No leak word in any string VALUE (keys like density_kg_m3 are fine)."""
    blob = " | ".join(_strings(obj)).lower()
    for w in _LEAK_WORDS:
        assert w.lower() not in blob, f"{w!r} leaked: {blob[:300]}"


# ── wires ────────────────────────────────────────────────────────────────────

def _wire_yaml(tmp_path):
    (tmp_path / "wire_stock.yaml").write_text(
        'updated: "2026-09-20"\nwires:\n'
        '  - {code: "010224", spec: "SFT-AIW 0.3*3", thickness_mm: 0.3, width_mm: 3.0,\n'
        '     insulation: AIW, self_bonding: false, stock_kg: 2.35, warehouse: "WH-1"}\n'
        f'  - {{code: "W1", spec: "{_WIRE_BATCHES}", thickness_mm: 0.3, width_mm: 2.5,\n'
        '     insulation: AIW, self_bonding: false, stock_kg: 16.3, check: true}\n'
        f'  - {{code: "L1", spec: "{_WIRE_LOTS}", thickness_mm: 1.0, width_mm: 7.0,\n'
        '     insulation: AIW, self_bonding: false, stock_kg: 321.4}\n',
        encoding="utf-8")


def test_wire_spec_is_technical_only(stubbed, monkeypatch):
    from motor_ai_sim import workspace as ws
    _wire_yaml(stubbed)
    monkeypatch.setattr(ws, "shared_root", lambda: stubbed)
    out = T.list_catalog(P, "wires")
    rows = {r["id"]: r for r in out["entries"]}
    assert rows["W1"]["spec"] == "SFT-AIW 0.300x2.500"
    assert rows["L1"]["spec"] == "1*7"
    assert rows["010224"]["spec"] == "SFT-AIW 0.3*3"
    for r in rows.values():
        assert r["provenance"] == {"source": "eMotres wire catalog"}
        assert "stock_kg" not in r and "warehouse" not in r
    assert rows["W1"]["thickness_mm"] == 0.3 and rows["W1"]["width_mm"] == 2.5
    _assert_clean(out)
    # the lot text cannot be probed through the query filter either
    assert T.list_catalog(P, "wires", query="batches")["count"] == 0


def test_wire_spec_helper_falls_back_to_dimensions():
    assert public_wire_spec("(two lots: 3 + 4 kg)", 1.0, 7.0) == "1*7"
    assert public_wire_spec(None, 0.2, 2.5) == "0.2*2.5"


# ── bearings ─────────────────────────────────────────────────────────────────

_BEARING_LIB = {
    "source": "SKF, 'The SKF model for calculating the frictional moment'",
    "bearings": {
        "61811-2Z": {
            "name": "61811-2Z",
            "description": ("SKF 61811-2Z - deep groove, series 618 (thin section), "
                            "metal shields both sides (the 150 mm motors, user 2026-09-08)"),
            "type": "deep_groove", "d": 55, "D": 72, "B": 9, "contact_angle_deg": 0,
            "balls": None, "seals": "2Z", "default_lubricant": "MT47",
            "n_limit_grease_rpm": 9000, "C_kn": 9.56, "C0_kn": 8.15,
            "note": ("Does NOT fit the CIANO14 50 holder (outer diameter 28 mm: back "
                     "tube r 19 > flange r 17.3). The bearings of the real 150 mm "
                     "24s/28p, see docs/measurements/2026-08-04_noload_decomposition_150mm.md. "
                     "At 23 000 rpm it needs oil-air.")},
        "6010-2RZ": {
            "name": "6010-2RZ",
            "description": ("SKF 6010-2RZ/HC5C3 - hybrid DEEP GROOVE with low-friction "
                            "seals; the 'does not reach the speed' comparison"),
            "type": "deep_groove", "d": 50, "D": 80, "B": 16, "note": "Listed to show the price at 23 000 rpm."},
    },
}


def test_bearing_rows_have_no_internal_notes(stubbed, monkeypatch):
    from motor_ai_sim import bearings as b
    monkeypatch.setattr(b, "library", lambda: _BEARING_LIB)
    out = T.list_catalog(P, "bearings")
    rows = {r["id"]: r for r in out["entries"]}
    assert rows["61811-2Z"]["description"] == (
        "SKF 61811-2Z - deep groove, series 618 (thin section), metal shields both sides")
    assert rows["6010-2RZ"]["description"] == (
        "SKF 6010-2RZ/HC5C3 - hybrid DEEP GROOVE with low-friction seals")
    assert rows["61811-2Z"]["provenance"] == {"source": _BEARING_LIB["source"]}
    # the technical fields the UI-facing tools rely on are intact
    r = rows["61811-2Z"]
    assert (r["bore_mm"], r["outer_diameter_mm"], r["width_mm"]) == (55, 72, 9)
    assert r["dynamic_load_rating_kn"] == 9.56 and r["n_limit_grease_rpm"] == 9000
    _assert_clean(out)
    _assert_clean(T.get_catalog_entry(P, "bearings", "61811-2Z"))


# ── materials ────────────────────────────────────────────────────────────────

def test_magnet_and_steel_text_loses_attributions(stubbed, monkeypatch):
    lib = {"magnet": {"N52UH_150C": {
        "grade": "N52UH", "Br": 1.3, "density": 7600,
        "description": ("Sintered NdFeB N52UH - operating point 150 C "
                        "(owner 2026-10-16: re-measured by the supplier)")}},
        "steel": {"B15": {"description": "Baosteel B15AHV950M - 0.15 mm, EV traction grade, "
                                         "owner 2026-10-15"}}}
    monkeypatch.setattr(T, "_materials", lambda cat: lib[cat])
    m = T.list_catalog(P, "magnets")["entries"][0]
    assert m["description"] == "Sintered NdFeB N52UH - operating point 150 C"
    assert m["br_t"] == 1.3 and m["grade"] == "N52UH"
    s = T.list_catalog(P, "steels")["entries"][0]
    assert s["description"] == "Baosteel B15AHV950M - 0.15 mm, EV traction grade"
    _assert_clean([m, s])


def test_public_text_keeps_datasheet_facts():
    t = "Sintered NdFeB N45EH (EH grade, Tmax 200 C, Hcj >= 30 kOe at 20 C) - operating point 150 C"
    assert public_text(t) == t
    assert public_text(None) is None and public_text(3) == 3


# ── machines: only carded configurations ────────────────────────────────────

def _tree():
    return {"dies": [
        {"name": "D1", "stator_diameter": 50, "slots": 12, "poles": 14, "configs": [
            {"name": "L15", "card_date": "2026-10-18", "stack_mm": 15,
             "duties": [{"name": "rated", "rpm": 3000, "torque_nm": 1.0, "result": {}}]},
            {"name": "L99", "card_date": None, "stack_mm": 99,
             "duties": [{"name": "rated", "rpm": 3000, "torque_nm": 9.0, "result": {}}]}]},
        {"name": "D2", "stator_diameter": 100, "slots": 24, "poles": 28, "configs": [
            {"name": "X", "stack_mm": 20,
             "duties": [{"name": "rated", "rpm": 1000, "torque_nm": 5.0, "result": {}}]}]},
    ]}


@pytest.fixture()
def tree(stubbed, monkeypatch):
    from motor_ai_sim.routes import family as fam
    monkeypatch.setattr(fam, "tree", lambda response=None, authorization=None: _tree())


def test_list_machines_skips_uncarded_configs_and_dies(tree):
    out = T.list_machines(P)
    assert [(m["die"], m["config"]) for m in out["machines"]] == [("D1", "L15")]
    assert out["count"] == 1


def test_dies_catalog_lists_only_carded_configurations(tree):
    rows = {r["id"]: r for r in T.list_catalog(P, "dies")["entries"]}
    assert set(rows) == {"D1"}                       # D2 has no carded config
    assert rows["D1"]["configurations"] == ["L15"]


def test_performance_of_uncarded_config_is_not_found(tree, monkeypatch):
    from motor_ai_sim import duty_results as dr
    monkeypatch.setattr(dr, "index", lambda die: {})
    ok = T.get_machine_performance(P, "D1", "L15", "rated")
    assert ok["config"] == "L15"
    with pytest.raises(T.ToolError, match="not found"):
        T.get_machine_performance(P, "D1", "L99", "rated")
    with pytest.raises(T.ToolError, match="not found"):
        T.get_machine_performance(P, "D2", "X", "rated")
    # indistinguishable from a machine that never existed
    with pytest.raises(T.ToolError) as missing:
        T.get_machine_performance(P, "D1", "NOPE", "rated")
    with pytest.raises(T.ToolError) as uncarded:
        T.get_machine_performance(P, "D1", "L99", "rated")
    assert str(missing.value).replace("NOPE", "L99") == str(uncarded.value)


def test_check_fit_ignores_uncarded_configs(tree, monkeypatch):
    from motor_ai_sim import duty_results as dr
    monkeypatch.setattr(dr, "index", lambda die: {})
    out = T.check_fit(P, torque_nm=0.5, speed_rpm=100)
    names = {(r["die"], r["config"]) for r in out["fits"] + out["rejected"]}
    assert names == {("D1", "L15")}


# ── the equivalent HTTP serialisation for non-admin callers ─────────────────

def test_http_wire_row_for_non_admin_keeps_shape_but_not_lots():
    from motor_ai_sim.routes.wire_stock import _public_wire
    row = {"code": "W1", "spec": _WIRE_BATCHES, "thickness_mm": 0.3, "width_mm": 2.5,
           "insulation": "AIW", "self_bonding": False, "thermal_class_c": None,
           "stock_kg": 16.3, "warehouse": "WH-1", "check": True}
    out = _public_wire(row)
    assert set(out) == set(row)
    assert out["spec"] == "SFT-AIW 0.300x2.500" and out["warehouse"] == ""


def test_http_bearing_library_for_non_admin_drops_design_notes():
    from motor_ai_sim.routes.bearings import _public_library
    lib = dict(_BEARING_LIB, lubricants={})
    out = _public_library(lib)
    c = out["bearings"]["61811-2Z"]
    assert c["description"].endswith("metal shields both sides")
    assert "holder" not in c["note"] and "docs/" not in c["note"] and "23 000" not in c["note"]
    assert c["d"] == 55 and c["C_kn"] == 9.56
    assert lib["bearings"]["61811-2Z"]["note"].startswith("Does NOT fit")   # input untouched
