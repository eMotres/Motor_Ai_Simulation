"""Fixed annulus geometry must not cache any field-dependent torque state."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import numpy as np
import pytest
import skfem
from skfem import Basis, ElementTriP1, ElementTriP2, MeshTri

from motor_ai_sim.simulation.field_ops import (
    MU0, _arkkio_torque_p2, _p2_B_at_quad, _prepare_arkkio_torque_p2,
)


def original_torque(mesh, A_vec, basis, r_in_m, r_out_m, stack_length_m):
    """Pre-cache implementation retained as an independent numerical oracle."""
    P, T = mesh.p, mesh.t
    cx = (P[0, T[0]] + P[0, T[1]] + P[0, T[2]]) / 3.0
    cy = (P[1, T[0]] + P[1, T[1]] + P[1, T[2]]) / 3.0
    rc = np.hypot(cx, cy)
    gap_idx = np.where((rc >= r_in_m) & (rc <= r_out_m))[0]
    if gap_idx.size == 0:
        return 0.0
    gb = Basis(mesh, basis.elem, elements=gap_idx)
    Bx, By, dx = _p2_B_at_quad(gb, A_vec)
    X = gb.global_coordinates().value
    r = np.sqrt(X[0] ** 2 + X[1] ** 2)
    cosp, sinp = X[0] / r, X[1] / r
    Br = Bx * cosp + By * sinp
    Bph = -Bx * sinp + By * cosp
    val = float(np.sum(dx * r * Br * Bph))
    return stack_length_m / (MU0 * (r_out_m - r_in_m)) * val


def make_mesh(order=2, scale=1.):
    mesh = MeshTri.init_tensor(np.linspace(-.02, .025, 19) * scale,
                               np.linspace(-.023, .017, 17) * scale)
    basis = Basis(mesh, ElementTriP1() if order == 1 else ElementTriP2())
    return mesh, basis


@pytest.mark.parametrize("order", [1, 2])
@pytest.mark.parametrize("radii", [(.006, .015), (.01, .011), (.021, .03)])
def test_prepared_and_public_wrapper_are_bit_identical_for_fresh_fields(order, radii):
    mesh, basis = make_mesh(order)
    torque = _prepare_arkkio_torque_p2(mesh, basis, *radii, .03)
    rng = np.random.default_rng(1909)
    x, y = basis.doflocs
    fields = [np.zeros(basis.N), .01*x + .02*y, x*y + .2*x*x,
              rng.normal(0., 1e-3, basis.N)]
    for field in fields:
        expected = original_torque(mesh, field, basis, *radii, .03)
        assert torque(field) == expected
        assert _arkkio_torque_p2(mesh, field, basis, *radii, .03) == expected
    # The same array identity with different contents is a NEW field too.
    field[:] = rng.normal(0., 1e-3, basis.N)
    assert torque(field) == original_torque(mesh, field, basis, *radii, .03)


@pytest.mark.parametrize("radii", [(1., 2.), (.02, .01), (1., 1.)])
def test_empty_and_inverted_annuli_keep_zero_without_reading_field(radii):
    mesh, basis = make_mesh()
    torque = _prepare_arkkio_torque_p2(mesh, basis, *radii, .03)
    # The old helper exits before inspecting A when there are no elements.
    assert torque(None) == 0.0
    assert _arkkio_torque_p2(mesh, None, basis, *radii, .03) == 0.0


def test_occupied_zero_width_ring_keeps_existing_error_behavior():
    mesh, basis = make_mesh()
    P, T = mesh.p, mesh.t
    cx = (P[0, T[0]] + P[0, T[1]] + P[0, T[2]]) / 3.0
    cy = (P[1, T[0]] + P[1, T[1]] + P[1, T[2]]) / 3.0
    radius = float(np.hypot(cx, cy)[0])
    field = np.zeros(basis.N)
    torque = _prepare_arkkio_torque_p2(mesh, basis, radius, radius, .03)
    with pytest.raises(ZeroDivisionError):
        original_torque(mesh, field, basis, radius, radius, .03)
    with pytest.raises(ZeroDivisionError):
        torque(field)
    with pytest.raises(ZeroDivisionError):
        _arkkio_torque_p2(mesh, field, basis, radius, radius, .03)


def test_geometry_is_prepared_once_while_fields_stay_fresh(monkeypatch):
    mesh, basis = make_mesh()
    fields = [np.random.default_rng(seed).normal(size=basis.N) for seed in (1, 2, 3)]
    expected = [original_torque(mesh, field, basis, .006, .015, .03)
                for field in fields]
    constructions = []

    def counted_basis(*args, **kwargs):
        constructions.append(1)
        return Basis(*args, **kwargs)

    monkeypatch.setattr(skfem, "Basis", counted_basis)
    torque = _prepare_arkkio_torque_p2(mesh, basis, .006, .015, .03)
    assert len(constructions) == 1

    def no_coordinate_rebuild(*args, **kwargs):
        raise AssertionError("fixed polar coordinates rebuilt during a frame")

    monkeypatch.setattr(Basis, "global_coordinates", no_coordinate_rebuild)
    assert [torque(field) for field in fields] == expected
    assert len(constructions) == 1


def test_independent_concurrent_and_nested_evaluators_do_not_share_state():
    cases = []
    for order, scale, length in [(1, 1., .02), (2, 1.4, .04)]:
        mesh, basis = make_mesh(order, scale)
        radii = (.006*scale, .015*scale)
        field = np.random.default_rng(order).normal(size=basis.N)
        expected = [original_torque(mesh, field*factor, basis, *radii, length)
                    for factor in (1., 2., -.5)]
        cases.append((_prepare_arkkio_torque_p2(mesh, basis, *radii, length),
                      field, expected))
    rendezvous = Barrier(2)

    def worker(case):
        torque, field, expected = case
        results = []
        for factor in (1., 2., -.5):
            rendezvous.wait(timeout=10)
            results.append(torque(field*factor))
        assert results == expected

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, case) for case in cases]
        for future in futures:
            future.result(timeout=15)
    # Calling another prepared geometry between outer frames cannot overwrite it.
    outer, field, expected = cases[0]
    assert outer(field) == expected[0]
    assert cases[1][0](cases[1][1]) == cases[1][2][0]
    assert outer(field) == expected[0]
