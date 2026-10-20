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
