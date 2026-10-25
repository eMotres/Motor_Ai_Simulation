"""The final-validation hang cap is priced at the final's own cost (2026-10-25: Ø400 baseline A killed at 2097 s)."""
from motor_ai_sim.routes.optimization import eval_hang_cap, _FINAL_PURPOSE


def test_search_evals_keep_their_cap():
    assert eval_hang_cap(60.0, 50.0, "optimization") == 300.0
    assert eval_hang_cap(600.0, 100.0, "optimization") == 2400.0
    assert eval_hang_cap(5000.0, 100.0, "optimization") == 3600.0


def test_a_final_resolve_gets_six_times_the_search_price_up_to_four_hours():
    # Ø400: search evals ~90 s median -> old cap 360 s; a 2097 s final must fit now
    assert eval_hang_cap(90.0, 80.0, _FINAL_PURPOSE) >= 2097.0
    assert eval_hang_cap(5000.0, 100.0, _FINAL_PURPOSE) == 4 * 3600.0
    assert eval_hang_cap(10.0, 10.0, _FINAL_PURPOSE) >= eval_hang_cap(10.0, 10.0, "optimization")
