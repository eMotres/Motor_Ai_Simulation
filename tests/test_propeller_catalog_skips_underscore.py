"""The catalogue loader skips files AND folders whose name starts with an underscore (_polars, _airfoils, _designs hold other data),
so a design file (schema propeller-design-1) is never tried as a propeller and nothing is logged on a cache rebuild."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import yaml

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_design as pd

REPO_DIR = Path(pp.__file__).resolve().parents[2] / "config" / "propellers"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _load_fresh():
    cap = _Capture()
    lg = logging.getLogger("motor_ai_sim.propeller")
    lg.addHandler(cap)
    pp._cache.clear()
    try:
        cat = pp.load_catalog()
    finally:
        lg.removeHandler(cap)
    return cat, cap.records


def test_the_design_file_is_not_a_catalogue_entry_and_nothing_is_logged():
    assert pd.DESIGN_PATH.is_file() and pd.DESIGN_PATH.parent.name == "_designs"
    cat, warnings = _load_fresh()
    assert warnings == [], [w.getMessage() for w in warnings]
    assert "own_family" not in cat
    # the catalogue list is exactly the yaml files outside underscore folders / files (nothing lost, nothing added)
    expected = set()
    for p in REPO_DIR.rglob("*.yaml"):
        if any(part.startswith("_") for part in p.relative_to(REPO_DIR).parts):
            continue
        expected.add(yaml.safe_load(p.read_text(encoding="utf-8"))["id"])
    assert set(cat) == expected and len(cat) >= 18


def test_an_underscore_folder_in_any_catalogue_directory_is_skipped():
    root = Path(tempfile.mkdtemp())
    (root / "vendor").mkdir()
    (root / "_hidden").mkdir()
    ok = {"schema": "propeller-1", "id": "p_ok", "vendor": "T", "model": "m", "geometry": {"diameter_in": 10.0, "diameter_mm": 254.0},
          "performance": {"data_quality": "geometry_only"}}
    (root / "vendor" / "ok.yaml").write_text(yaml.safe_dump(ok))
    (root / "_hidden" / "bad.yaml").write_text("schema: propeller-design-1\nid: nope\n")
    (root / "vendor" / "_skipped.yaml").write_text("schema: propeller-design-1\nid: nope2\n")
    old = pp._DIR
    pp._DIR = root
    cap = _Capture()
    lg = logging.getLogger("motor_ai_sim.propeller")
    lg.addHandler(cap)
    try:
        pp._cache.clear()
        assert sorted(pp.load_catalog()) == ["p_ok"]
        assert cap.records == []
    finally:
        lg.removeHandler(cap)
        pp._DIR = old
        pp._cache.clear()
