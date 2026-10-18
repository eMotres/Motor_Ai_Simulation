"""CAD parameter expressions come from uploaded Fusion / FreeCAD files: they are parsed, never eval-ed (audit 2026-10-18)."""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.cad import store as S


def test_ordinary_cad_expressions_still_evaluate():
    vals = S.evaluate({"a": "10 mm", "b": "=a * 2 + 1.5mm", "c": "sqrt(b^2 - 4)", "ang": "360 deg / 12",
                       "d": "-a + max(a, 3) % 4", "e": "pi * 2", "f": "sin(30)", "g": "2 ** 3"})
    assert vals["a"] == 10 and vals["b"] == 21.5 and vals["ang"] == 30
    assert vals["c"] == pytest.approx(math.sqrt(21.5 ** 2 - 4))
    assert vals["d"] == -8 and vals["e"] == pytest.approx(2 * math.pi) and vals["f"] == pytest.approx(0.5)
    assert vals["g"] == 8


@pytest.mark.parametrize("bad", [
    "().__class__.__bases__[0].__subclasses__()",
    "(1).__class__",
    "__import__('os').system('id')",
    "open('/etc/passwd')",
    "[x for x in (1, 2)]",
    "sqrt.__globals__",
    "lambda: 1",
    "'abc'",
    "a if 1 else 2",
    "9 ** 9 ** 9",
    "1" * 600,
    "sqrt(1, 2)",
    "1 / 0",
    "1e300 * 1e300",
])
def test_anything_but_arithmetic_is_refused(bad):
    with pytest.raises(ValueError):
        S.evaluate({"x": bad})


def test_unknown_names_and_loops_are_refused():
    with pytest.raises(ValueError):
        S.evaluate({"x": "y + 1"})
    with pytest.raises(ValueError, match="circular"):
        S.evaluate({"x": "y + 1", "y": "x + 1"})
