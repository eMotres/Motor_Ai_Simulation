"""The eval-pool sizing follows the box (owner 2026-11-16): the cap, the solo-seed threads and SMT counting come from the
environment, the defaults are exactly today's numbers, and the budget is still shared between concurrent jobs."""
from __future__ import annotations

import os

from motor_ai_sim.routes import optimization as opt

ENVS = ("FEM_SCAN_WORKERS", "FEM_SCAN_WORKERS_CAP", "FEM_SOLO_SEED_THREADS_CAP", "FEM_SCAN_SMT")


def _clean():
    for k in ENVS:
        os.environ.pop(k, None)


def test_the_rule_is_pure_and_unchanged():
    assert opt._workers_for(12, 8) == 8          # the workstation
    assert opt._workers_for(8, 8) == 6           # the EU box (8 physical cores)
    assert opt._workers_for(2, 8) == 2           # never below 2
    assert opt._workers_for(24, 22) == 22        # US with SMT threads and its own cap
    assert opt._workers_for(12, 8) == 8 and opt._workers_for(12, 22) == 10   # a bigger cap alone changes nothing past cores - 2


def test_defaults_are_todays_numbers():
    _clean()
    try:
        assert opt._scan_workers_cap() == 8
        assert opt._solo_threads_for(16, 8) == 5          # EU: 16 // 3
        assert opt._solo_threads_for(32, 8) == 8          # US default: capped
        assert opt._solo_threads_for(32, 12) == 10        # raised cap
        assert opt._solo_seed_threads() == max(1, min(8, (os.cpu_count() or 8) // 3))
    finally:
        _clean()


def test_env_overrides_and_bad_values_fall_back():
    _clean()
    try:
        os.environ["FEM_SCAN_WORKERS_CAP"] = "22"
        assert opt._scan_workers_cap() == 22
        for bad in ("0", "-3", "x", ""):
            os.environ["FEM_SCAN_WORKERS_CAP"] = bad
            assert opt._scan_workers_cap() == 8
    finally:
        _clean()


def test_a_faked_us_topology_gives_22_workers_shared_between_jobs():
    _clean()
    orig_aff, orig_phys = getattr(os, "sched_getaffinity", None), opt._physical_cores_available
    try:
        os.environ["FEM_SCAN_SMT"] = "1"
        os.environ["FEM_SCAN_WORKERS_CAP"] = "22"
        os.sched_getaffinity = lambda _pid: set(list(range(0, 12)) + list(range(16, 28)))   # 24 threads
        opt._physical_cores_available = lambda: 12
        assert opt._sizing_units() == 24
        assert opt._optimizer_worker_budget() == 22
        assert opt._optimizer_share(1) == 22
        assert opt._optimizer_share(2) == 11
        os.environ.pop("FEM_SCAN_SMT")                     # without SMT counting: physical cores only
        assert opt._sizing_units() == 12 and opt._optimizer_worker_budget() == 10
        os.environ["FEM_SCAN_WORKERS"] = "5"               # an explicit operator number is literal
        assert opt._optimizer_share(2) == 5
    finally:
        opt._physical_cores_available = orig_phys
        if orig_aff is not None:
            os.sched_getaffinity = orig_aff
        _clean()


def test_eu_defaults_leave_the_budget_as_before():
    _clean()
    orig = opt._physical_cores_available
    try:
        opt._physical_cores_available = lambda: 8          # AX42 container
        assert opt._optimizer_worker_budget() == 6
        assert opt._optimizer_share(2) == 3
        opt._physical_cores_available = lambda: 12         # workstation
        assert opt._optimizer_worker_budget() == 8
    finally:
        opt._physical_cores_available = orig
        _clean()
