"""Regression guard for the CAD expression evaluator (``motor_ai_sim.cad.store._safe_eval``).

Expressions come from uploaded CAD files, so they must stay arithmetic only.  A builtins-free ``eval`` is escapable through
attribute access (security audit 2026-10-18); these tests keep the parse-and-whitelist evaluator from being loosened later.
"""
import pytest

from motor_ai_sim.cad.store import _safe_eval

NS = {"a": 2.0, "b": 3.0, "n": 4}


@pytest.mark.parametrize("expr,expected", [
    ("1 + 2 * 3", 7),
    ("a * b - 1", 5.0),
    ("-a + +b", 1.0),
    ("a ** 2", 4.0),
    ("n // 3 + n % 3", 2),
    ("sqrt(16) + abs(-1)", 5.0),
    ("max(a, b, 1) - min(a, b)", 1.0),
    ("floor(2.7) + ceil(2.1)", 5),
])
def test_arithmetic_still_works(expr, expected):
    assert _safe_eval(expr, NS) == pytest.approx(expected)


@pytest.mark.parametrize("expr", [
    # attribute access - the classic escape from a builtins-free eval
    "().__class__",
    "(1).__class__.__bases__[0].__subclasses__()",
    "a.real",
    # names and calls outside the whitelist
    "__import__('os').system('true')",
    "open('/etc/passwd')",
    "eval('1')",
    "exec('x=1')",
    "getattr(a, 'real')",
    "unknown_name + 1",
    "sqrt",                       # a function object is not a number
    # calls with keywords, lambdas, comprehensions, containers, subscripts, strings, booleans, comparisons
    "max(a, key=abs)",
    "(lambda: 1)()",
    "[x for x in (1, 2)]",
    "[1, 2][0]",
    "{'k': 1}['k']",
    "'abc'",
    "True",
    "a if b else 1",
    "a < b",
    "a and b",
    # walrus / starred / f-string
    "(z := 1)",
    "max(*[1, 2])",
    "f'{a}'",
])
def test_unsupported_nodes_are_rejected(expr):
    with pytest.raises(ValueError):
        _safe_eval(expr, NS)


@pytest.mark.parametrize("expr", [
    "9 ** 999",                   # power out of range
    "1e300 * 1e300",              # not finite
    "1 / 0",
    "a" + " + a" * 300,           # longer than the length limit
    "max(1,2,3,4,5,6,7,8,9)",     # too many arguments
])
def test_resource_and_value_guards(expr):
    with pytest.raises(ValueError):
        _safe_eval(expr, NS)


def test_callable_in_namespace_is_not_a_value():
    with pytest.raises(ValueError):
        _safe_eval("f", {"f": print})


# -- FreeCAD's argument separator ``;`` (min(a; b)): converted on tokens, inside calls only ----------------------------------

def test_semicolon_argument_separator_inside_calls_evaluates():
    from motor_ai_sim.cad.store import evaluate, freecad_call_separators
    assert freecad_call_separators("min(a; b)") == "min(a, b)"
    assert freecad_call_separators("2 * ceil(min(x; (20*1.0)) / (2*1.0) - 1e-06)") == "2 * ceil(min(x, (20*1.0)) / (2*1.0) - 1e-06)"
    assert freecad_call_separators("max(min(a; b); 3)") == "max(min(a, b), 3)"
    assert freecad_call_separators("a + b") == "a + b"                        # nothing to do: untouched
    # the real Params-sheet form (CIANO14 40 L12 / L20, CIANO28 85: holder screw length rounded to stock)
    v = evaluate({"req": "23.2 mm",
                  "screw": "2 * ceil(min(req; 20 mm) / 2 mm - 1e-06) * 1 mm + 5 * ceil(max(req; 20 mm) / 5 mm - 1e-06) * 1 mm - 20 mm"})
    assert v["screw"] == pytest.approx(2 * 10 + 5 * 5 - 20)
    assert evaluate({"a": "2", "b": "3", "c": "max(a; b; 1) - min(a; b)"})["c"] == pytest.approx(1.0)


@pytest.mark.parametrize("expr", [
    "a; b",                      # a statement separator at the top level
    "1; __import__('os')",
    "(a; b)",                    # inside a plain group, not a call
    "min((a; b))",               # a group nested in a call: still not an argument separator
    "a;",
    ";",
    "min(a; b)\n; 1",            # multi-line
])
def test_semicolon_outside_a_call_is_rejected(expr):
    from motor_ai_sim.cad.store import evaluate, freecad_call_separators
    with pytest.raises(ValueError):
        freecad_call_separators(expr)
    with pytest.raises(ValueError):
        evaluate({"a": "2", "b": "3", "x": expr})


@pytest.mark.parametrize("expr", [
    "getattr(a; 'real')",        # converted to a call the whitelist still refuses
    "min(a; b).__class__",
    "eval('1'; 2)",
    "min(a; lambda: 1)",
])
def test_the_whitelist_still_decides_after_the_conversion(expr):
    from motor_ai_sim.cad.store import _safe_eval, freecad_call_separators
    with pytest.raises(ValueError):
        _safe_eval(freecad_call_separators(expr), {**NS, "min": min})
