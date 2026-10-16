"""Run16-sized algebra-only benchmark for SlipMortarDerivativeAction."""

from __future__ import annotations

import json
import math
import statistics
import time
import tracemalloc

import numpy as np

from motor_ai_sim.simulation.p2_projection import SlipMortarDerivativeAction
from scripts.continuous_p2_mortar_trace_prototype import synthetic_sector


def benchmark():
    projection = synthetic_sector(n_ring=505, n_dof=17299, bc_sign=-1)
    spacing = math.pi/504
    field = np.zeros(projection.N)
    rng = np.random.default_rng(220)
    field[projection.vdof[projection.sring]] = rng.normal(size=projection.Nring)
    field[projection.vdof[projection.sring[-1]]] = -field[
        projection.vdof[projection.sring[0]]]
    for a, b in projection._re_pairs:
        edge = projection.edge_dof(int(projection.sring[a]),
                                   int(projection.sring[b]))
        field[edge] = rng.normal()

    tracemalloc.start()
    start = time.perf_counter()
    action = SlipMortarDerivativeAction(projection, spacing)
    traced_setup_s = time.perf_counter()-start
    action.motion_derivative(field, -505)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    start = time.perf_counter()
    action = SlipMortarDerivativeAction(projection, spacing)
    setup_s = time.perf_counter()-start
    times = []
    for _ in range(20):
        start = time.perf_counter()
        derivative = action.motion_derivative(field, -505)
        times.append(time.perf_counter()-start)
    return dict(n_ring=505, n_dof=projection.N,
                trace_coordinates=action.n_trace,
                setup_s=setup_s, traced_setup_s=traced_setup_s,
                action_median_s=statistics.median(times),
                action_min_s=min(times), action_max_s=max(times),
                traced_peak_bytes=peak,
                mass_nnz=action.mass_nnz,
                mass_storage_bytes=action.mass_storage_bytes,
                output_bytes=derivative.nbytes,
                nontrace_nonzero=int(np.count_nonzero(np.delete(
                    derivative, action._rotor_dofs))))


if __name__ == "__main__":
    print(json.dumps(benchmark(), indent=2))
