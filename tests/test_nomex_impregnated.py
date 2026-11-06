"""The Nomex liner counts as vacuum impregnated: k 0.25 W/(m K), fitted on the bench (owner 2026-11-06)."""
import pathlib

import pytest
import yaml


def _card():
    root = pathlib.Path(__file__).resolve().parents[1]
    lib = yaml.safe_load((root / "config" / "materials_library.yaml").read_text(encoding="utf-8"))

    def find(x):
        if isinstance(x, dict):
            if "Nomex" in x:
                return x["Nomex"]
            for v in x.values():
                r = find(v)
                if r:
                    return r
        return None

    return find(lib)


def test_nomex_is_the_impregnated_value_and_says_where_it_came_from():
    c = _card()
    assert c["thermal_conductivity"] == pytest.approx(0.25)
    assert c["prov"]["thermal_conductivity"]["type"] == "derived"
    assert "0.14" in c["prov"]["thermal_conductivity"]["note"] or "0.139" in c["prov"]["thermal_conductivity"]["note"]
