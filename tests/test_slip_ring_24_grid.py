from motor_ai_sim.simulation.fem_solver_2d import _ring_on_24_grid


def test_40_steps_on_a_144_ring_goes_to_240():
    assert _ring_on_24_grid(40, 144, 14, 4) == 240


def test_result_is_on_the_grid_and_a_multiple_of_the_steps():
    for req in (32, 40, 50, 64, 72, 100):
        for base in (120, 144, 168):
            r = _ring_on_24_grid(req, base, 14, 4)
            assert r % 24 == 0 and r % req == 0 and r >= base
            assert (14 * r) % 4 == 0


def test_already_on_grid_request_is_untouched():
    assert _ring_on_24_grid(48, 144, 14, 4) == 144
