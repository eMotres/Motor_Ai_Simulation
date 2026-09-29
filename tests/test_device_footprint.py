"""Device-card ``footprint`` block and the catalogue's "fits the board" groups.

Owner, 2026-09-26 (Task 9): every card carries its PCB side — outline ID,
land-pattern reference, body height, top cooling tab, compatibility group —
and the Controller catalogue filters by group, warning in one line when parts
of one group differ in height or top tab.  The block is optional on a card,
but once present it is complete; a null always carries a note.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from motor_ai_sim.inverter import devices as dv

REPO_CARDS = Path(__file__).resolve().parents[1] / "config" / "devices"
QDPAK = "IMCQ120R004M2H"


def _doc(part: str) -> dict:
    return yaml.safe_load((REPO_CARDS / f"{part}.yaml").read_text(encoding="utf-8"))


# ── the shipped cards ───────────────────────────────────────────────────────

@pytest.mark.parametrize("path", sorted(REPO_CARDS.glob("*.yaml")),
                         ids=lambda p: p.stem)
def test_every_shipped_card_has_a_valid_footprint(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "footprint" in doc
    assert dv.validate_footprint(doc) == []
    fp = doc["footprint"]
    # the height is the SAME transcription as package_size_mm (Figure 1, A max)
    assert fp["body_height_mm"] == doc["package_size_mm"]["height_mm"]
    assert str(fp["package_outline_id"]).startswith(doc["package"])


def test_shipped_groups_are_the_owners():
    groups = {p.stem: _doc(p.stem)["footprint"]["compatibility_group"]
              for p in REPO_CARDS.glob("*.yaml")}
    for part, g in groups.items():
        pkg = _doc(part)["package"]
        if pkg.startswith("PG-HDSOP-22-U0"):          # U01 750 V and U03 1200 V
            assert g == "qdpak_750_1200", part
        else:
            assert (pkg, g) == ("PG-WHSON-8", "whson8_tson8"), part


# ── validator: loud on every malformed block ────────────────────────────────

def _with(mut) -> list:
    doc = copy.deepcopy(_doc(QDPAK))
    mut(doc["footprint"]) if callable(mut) else None
    return dv.validate_footprint(doc)


def test_validator_accepts_a_published_top_tab():
    def m(fp):
        fp["top_tab_mm"] = {"length_mm": 12.0, "width_mm": 10.0,
                            "source": "Figure 1, page 14"}
        fp["top_tab_note"] = None
    assert _with(m) == []


@pytest.mark.parametrize("mut, needle", [
    (lambda fp: fp.pop("compatibility_group"), "missing compatibility_group"),
    (lambda fp: fp.update(extra_key=1), "unknown key(s) extra_key"),
    (lambda fp: fp.update(compatibility_group="Q-DPAK!"), "compatibility_group must be"),
    (lambda fp: fp.update(compatibility_basis=""), "compatibility_basis is required"),
    (lambda fp: fp.update(package_outline_id="PG-TO247-3"), "not a variant"),
    (lambda fp: fp.update(package_outline_id=None), "package_outline_id is required"),
    (lambda fp: fp.update(land_pattern_note=None), "land_pattern_note must say why"),
    (lambda fp: fp.update(top_tab_note=""), "top_tab_note must say"),
    (lambda fp: fp.update(top_tab_mm={"length_mm": 12.0}), "top_tab_mm.width_mm"),
    (lambda fp: fp.update(top_tab_mm={"length_mm": 12.0, "width_mm": 10.0}),
     "top_tab_mm.source is required"),
    (lambda fp: fp.update(top_tab_mm="big"), "must be a mapping"),
    (lambda fp: fp.update(body_height_mm=-1), "positive number"),
    (lambda fp: fp.update(body_height_mm=2.25), "disagrees with package_size_mm"),
    (lambda fp: fp.update(body_height_mm=None, body_height_source=None),
     "body_height_source must say why"),
])
def test_validator_names_what_is_wrong(mut, needle):
    bad = _with(mut)
    assert any(needle in b for b in bad), bad


def test_footprint_not_a_mapping_is_refused_by_the_card_validator():
    doc = copy.deepcopy(_doc(QDPAK))
    doc["footprint"] = "Q-DPAK"
    assert any("footprint must be a mapping" in b for b in dv.validate_card(doc))


def test_card_without_footprint_still_loads():
    doc = copy.deepcopy(_doc(QDPAK))
    doc.pop("footprint")
    assert dv.validate_card(doc) == []
    assert dv.DeviceCard(doc).row()["footprint"] is None


# ── groups and the one-line warning ─────────────────────────────────────────

def _row(part, group, h, tab=None):
    return {"part": part, "footprint": {
        "compatibility_group": group, "body_height_mm": h,
        "top_tab_mm": None if tab is None else
        {"length_mm": tab[0], "width_mm": tab[1]}}}


def test_group_with_identical_published_parts_has_no_warning():
    g = dv.footprint_groups([_row("A", "g1", 2.35, (12, 10)),
                             _row("B", "g1", 2.35, (12, 10))])
    assert g["g1"]["parts"] == ["A", "B"] and g["g1"]["warning"] is None


def test_height_and_tab_differences_are_one_line():
    g = dv.footprint_groups([_row("A", "g", 0.75, (2.0, 2.0)),
                             _row("B", "g", 1.10, (2.4, 2.0)),
                             _row("C", "other", 9.0)])
    w = g["g"]["warning"]
    assert "\n" not in w
    assert "body height differs (0.75 / 1.1 mm)" in w
    assert "top tab differs (2×2 / 2.4×2 mm)" in w
    assert g["other"]["warning"] is None          # a one-part group has nothing to differ from


def test_unknown_is_never_read_as_equal():
    g = dv.footprint_groups([_row("A", "g", 2.35), _row("B", "g", None)])
    w = g["g"]["warning"]
    assert "height not published on 1 of 2" in w
    assert "top tab not published on 2 of 2" in w


def test_catalogue_rows_carry_group_and_warning():
    rows = {r["part"]: r for r in dv.list_devices() if "error" not in r}
    fp = rows[QDPAK]["footprint"]
    assert fp["compatibility_group"] == "qdpak_750_1200"
    assert fp["package_outline_id"] == "PG-HDSOP-22-U03"
    assert fp["body_height_mm"] == pytest.approx(2.35)
    qd = [p for p, r in rows.items()
          if (r.get("footprint") or {}).get("compatibility_group") == "qdpak_750_1200"]
    assert sorted(fp["group_parts"]) == sorted(qd) and len(qd) == 10
    # all ten heights are 2.35 (Figure 1, A max) — the top tab is what is unknown
    assert "height" not in fp["group_warning"]
    assert "top tab not published on 10 of 10" in fp["group_warning"]
    solo = rows["IQE050N08NM5SC"]["footprint"]
    assert solo["group_parts"] == ["IQE050N08NM5SC"] and solo["group_warning"] is None


def test_route_rows_carry_the_footprint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import controller as rc
    app = FastAPI(); app.include_router(rc.router)
    r = TestClient(app).get("/api/controller/devices",
                            params={"i_switch_rms_A": 100.0})
    assert r.status_code == 200
    row = next(d for d in r.json()["devices"] if d["part"] == QDPAK)
    assert row["footprint"]["compatibility_group"] == "qdpak_750_1200"
