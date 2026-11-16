from motor_ai_sim.simulation.fem_solver_2d import _boundary_strict


def test_default_is_strict(monkeypatch):
    monkeypatch.delenv("SB_BOUNDARY_STRICT", raising=False)
    assert _boundary_strict() is True


def test_zero_restores_warn_and_go(monkeypatch):
    monkeypatch.setenv("SB_BOUNDARY_STRICT", "0")
    assert _boundary_strict() is False
    monkeypatch.setenv("SB_BOUNDARY_STRICT", "1")
    assert _boundary_strict() is True
