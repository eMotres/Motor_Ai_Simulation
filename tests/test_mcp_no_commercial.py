"""NO-PRICE RULE on the MCP (owner 2026-11-02): no tool answer carries a cost, price, margin, markup, quote or currency
field - the same kind of guard as the geometry denylist, applied to EVERY tool answer (public and signed-in) by
``mcp_app``.  No network, no solver."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from motor_ai_sim import mcp_discovery as D
from motor_ai_sim import mcp_tools as T


@pytest.mark.parametrize("leak", [
    {"price_per_motor": 1.0},
    {"summary": {"margin_pct": 20.0}},
    {"rows": [{"part_id": "a", "unit_price": 5.0}]},
    {"machines": [{"x": {"order_cost_total": 1}}]},
    {"quote_id": "q-1"},
    {"currency": "EUR"},
    {"markup": 0.1},
    {"Cost": 3},
])
def test_commercial_fields_are_refused_anywhere(leak):
    with pytest.raises(AssertionError, match="commercial field leaked"):
        T.assert_no_commercial({"ok": True, "nested": [leak]})
    with pytest.raises(AssertionError):
        T._checked({"entry": leak})            # the existing per-tool check runs it too


def test_engineering_margins_and_plain_fields_pass():
    ok = {"fits": [{"die": "d", "torque_margin_pct": 12.5, "speed_margin_pct": 3.0, "mass_kg": 1.2,
                    "efficiency_pct": 93.1, "losses_w": {"copper": 10.0}}]}
    assert T.no_commercial(ok) is ok


def test_every_public_tool_answer_passes_the_guard():
    for answer in (D.describe_service(), D.capabilities(), D.calculation_types(), D.how_to_authenticate(),
                   D.start_sign_up()):
        T.assert_no_commercial(answer)
    for row in D.calculation_types().get("calculation_types", []):
        cid = row.get("id") if isinstance(row, dict) else None
        if cid:
            try:
                T.assert_no_commercial(D.input_requirements(cid))
            except ValueError:
                pass                        # a type without an input catalogue


def test_the_signed_in_tool_runner_applies_the_guard(monkeypatch):
    from motor_ai_sim import mcp_app as A
    monkeypatch.setattr(A, "_principal_from", lambda ctx: SimpleNamespace(email="x@example.test"))
    assert A._run(lambda p: {"torque_nm": 1.0}, None) == {"torque_nm": 1.0}
    with pytest.raises(AssertionError, match="commercial field leaked"):
        A._run(lambda p: {"design": {"cost": {"total": 9.0}}}, None)
