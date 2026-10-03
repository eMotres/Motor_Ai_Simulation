# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Licence isolation (owner decision 2026-10-03): gmsh (GPL) is never loaded
into a Python process that may load Intel MKL / PARDISO, and MKL is never
loaded into the gmsh worker.

A FRESH interpreter (tests/_gmsh_isolation_probe.py) plays the API process:
it imports the API app and every module of the package, loads MKL through
pypardiso where installed, and runs one case of EVERY gmsh path type through
its public wrapper — the legacy OCC 2-D mesher (single polygon and a full
cross-section), the gmsh CDT backend, the mechanical modal and rotor-stress
meshes, the static 3-D tetrahedral mesh, the static 3-D cross-section and the
banded 3-D section.  Asserted:

  * ``gmsh`` is not in the caller's ``sys.modules`` and no libgmsh is mapped
    into it, after all of that;
  * every path round-trips through the worker and returns a non-empty result;
  * the worker loaded gmsh and NO MKL / pypardiso module and no libmkl /
    libiomp shared object.

Plus the worker's own guarantees: it refuses to import MKL, it replays the
mesher's build trace into the caller, and it runs each call under the
caller's current environment.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_HAS_GMSH = importlib.util.find_spec("gmsh") is not None

pytestmark = pytest.mark.skipif(not _HAS_GMSH, reason="gmsh not installed")

PATHS = ("occ_2d_single_polygon", "occ_2d_full_section", "gmsh_cdt_backend",
         "mechanical_modal_stator", "mechanical_rotor_stress",
         "static3d_tet_sphere", "static3d_section_2d", "static3d_band_pieces")


@pytest.fixture(scope="module")
def probe():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_ROOT / "src"), str(_ROOT)] + ([env["PYTHONPATH"]]
                                            if env.get("PYTHONPATH") else []))
    r = subprocess.run([sys.executable, str(_ROOT / "tests" / "_gmsh_isolation_probe.py")],
                       cwd=str(_ROOT), env=env, capture_output=True, text=True,
                       timeout=1800)
    lines = [l for l in r.stdout.splitlines() if l.startswith("PROBE_JSON ")]
    assert r.returncode == 0 and lines, (r.stdout[-3000:], r.stderr[-4000:])
    return json.loads(lines[-1][len("PROBE_JSON "):])


def test_the_api_process_never_loads_gmsh(probe):
    assert probe["api_routes"] > 100
    assert probe["gmsh_after_imports"] is False, "a module imports gmsh at load"
    assert probe["gmsh_in_caller"] is False, "a wrapper imported gmsh in the caller"
    assert probe["libgmsh_in_caller"] == [], probe["libgmsh_in_caller"]


def test_every_package_module_imports(probe):
    # a module that cannot even be imported could hide a gmsh import
    assert probe["import_errors"] == {}, probe["import_errors"]


@pytest.mark.parametrize("path", PATHS)
def test_every_gmsh_path_round_trips_through_the_worker(probe, path):
    rec = probe["cases"][path]
    assert rec["ok"], rec


def test_the_worker_loads_gmsh_and_never_mkl(probe):
    w = probe["worker"]
    assert w["pid"] != probe["pid"]
    assert "gmsh" in w["modules"]
    bad_mods = [m for m in w["modules"]
                if m.split(".")[0] in ("pypardiso", "mkl", "pydiso", "intel_openmp")]
    assert bad_mods == [], bad_mods
    bad_so = [s for s in w["shared_objects"]
              if any(k in s.lower() for k in ("mkl", "iomp", "pardiso"))]
    assert bad_so == [], bad_so
    if w["shared_objects"]:                    # Linux: prove the probe can see libs
        assert any("gmsh" in s.lower() for s in w["shared_objects"])


def test_the_worker_refuses_to_import_mkl():
    from motor_ai_sim.simulation import gmsh_worker
    with pytest.raises(ImportError, match="blocked in the gmsh worker"):
        gmsh_worker.call("tests._gmsh_worker_crash_helpers:import_pypardiso")


def test_build_trace_crosses_the_boundary():
    """A fallback event recorded by a build in the worker lands in the
    caller's trace (the optimizer rejects on it)."""
    from motor_ai_sim.simulation import gmsh_worker
    from motor_ai_sim.simulation.mesher import _trace_reset, build_trace
    _trace_reset()
    gmsh_worker.call("tests._gmsh_worker_crash_helpers:record_trace",
                     args=("synthetic fallback", "synthetic note"))
    tr = build_trace()
    assert "synthetic fallback" in tr["events"]
    assert "synthetic note" in tr["notes"]
    assert tr["structured_gap_effective"] is True


def test_each_call_runs_under_the_callers_environment(monkeypatch):
    from motor_ai_sim.simulation import gmsh_worker
    gmsh_worker.handshake()                  # the worker exists before the change
    monkeypatch.setenv("MOTRES_ISOLATION_PROBE", "after-spawn")
    got = gmsh_worker.call("tests._gmsh_worker_crash_helpers:read_env",
                           args=("MOTRES_ISOLATION_PROBE",))
    assert got["value"] == "after-spawn"
    assert got["omp"] == "1"                 # thread count pinned for determinism
