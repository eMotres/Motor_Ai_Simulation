"""Concurrent publication tests for the candidate's real warm-cache writer."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import zipfile

import numpy as np


_CANDIDATE = Path(__file__).resolve().parents[1]
_CANDIDATE_SRC = _CANDIDATE / "src"


def _repo_root_from(path: Path) -> Path:
    for ancestor in path.resolve().parents:
        if (ancestor / ".git").exists():
            return ancestor
    raise RuntimeError("could not find repository root above %s" % path)


def _load_candidate_solver(source_file: str | None = None):
    """Load the solver adjacent to this test, including its candidate helpers."""
    path = Path(source_file) if source_file else (
        _CANDIDATE_SRC / "motor_ai_sim/simulation/fem_solver_2d.py")
    source_root = path.parents[2]  # .../src
    project_root = _repo_root_from(path)
    repo_source = project_root / "src"
    for entry in (str(project_root), str(repo_source), str(source_root)):
        while entry in sys.path:
            sys.path.remove(entry)
    sys.path[:0] = [str(repo_source), str(project_root), str(source_root)]
    if "tests.conftest" not in sys.modules:
        conftest_path = project_root / "tests/conftest.py"
        conf_spec = importlib.util.spec_from_file_location(
            "candidate_publication_conftest", conftest_path)
        conf_module = importlib.util.module_from_spec(conf_spec)
        conf_spec.loader.exec_module(conf_module)
        for loaded_name in list(sys.modules):
            if loaded_name == "motor_ai_sim" or loaded_name.startswith("motor_ai_sim."):
                del sys.modules[loaded_name]
    import motor_ai_sim.simulation as package
    try:
        importlib.import_module("motor_ai_sim.simulation.time_periodic")
    except ImportError:
        print("simulation_package", package.__file__, list(package.__path__),
              "sys_path", sys.path[:5], flush=True)
        raise
    for leaf in ("safe_warm_cache", "fem_solver_2d"):
        module_path = path.parent / (leaf + ".py")
        name = "motor_ai_sim.simulation." + leaf
        current = sys.modules.get(name)
        if current is not None and Path(current.__file__).resolve() == module_path.resolve():
            continue
        spec = importlib.util.spec_from_file_location(name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        setattr(package, leaf, module)
    return sys.modules["motor_ai_sim.simulation.fem_solver_2d"]


def _cache(marker: float) -> dict:
    """A valid, internally correlated safe BDF2 cache payload."""
    return {
        "safe_schema": "sb_safe_warm_seed_v2",
        "safe_identity": "test-identity-%g" % marker,
        "edcon_fp": "test-circuit-%g" % marker,
        "eddy_complete": True,
        "meta": {"nspp": 8, "npd": 1.0 + marker / 100,
                 "conn": "np1", "temp": 20.0 + marker,
                 "mscale": 1.0, "ts": "bdf2"},
        "doflocs": np.full((4, 2), marker),
        "dof_region": np.array([0, 0, 1, 1], dtype=np.int8),
        "A": np.full(4, marker), "A2": np.full(4, marker + 1),
        "Ued": np.full(2, marker + 2),
        "Ib_prev": (np.full(2, marker + 3), np.full(2, marker + 4)),
        "Ib_prev2": (np.full(2, marker + 5), np.full(2, marker + 6)),
        "solid": np.full(3, marker + 7),
        "solid_grp": {"cu": np.full(3, marker + 8)},
        "I": marker + 9, "rpm": marker + 10, "gam": marker + 11,
        "geo_fp": "geometry-%g" % marker, "nspp_req": 8, "nsect": 2,
        "demag_history": {"marker": marker},
    }


def _set_cache_path(fs, path: Path, monkeypatch) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fs, "_warm_cache_path", lambda: path)
    monkeypatch.setattr(fs, "_SB_WARM_CACHE", {})


def _assert_one_publisher(path: Path, expected_markers: set[float]) -> None:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
    with np.load(path, allow_pickle=False) as data:
        marker = float(data["A"][0])
        assert marker in expected_markers
        assert np.all(data["doflocs"] == marker)
        assert np.all(data["A"] == marker)
        assert np.all(data["A2"] == marker + 1)
        assert np.all(data["Ued"] == marker + 2)
        assert np.all(data["Ib_prev_net"] == marker + 3)
        assert np.all(data["Ib_prev_imposed"] == marker + 4)
        assert np.all(data["Ib_prev2_net"] == marker + 5)
        assert np.all(data["Ib_prev2_imposed"] == marker + 6)
        assert np.all(data["solid"] == marker + 7)
        assert np.all(data["solid_grp_cu"] == marker + 8)
        assert float(data["I"]) == marker + 9
        assert float(data["rpm"]) == marker + 10
        assert float(data["gam"]) == marker + 11
        assert str(data["safe_identity"]) == "test-identity-%g" % marker


def test_same_process_overlapping_publications_are_atomic_and_leave_no_temps(
        tmp_path, monkeypatch):
    fs = _load_candidate_solver()
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    destination = tmp_path / ".warm_cache.npz"
    _set_cache_path(fs, destination, monkeypatch)

    entered = threading.Event()
    release = threading.Event()
    original_write_array = np.lib.format.write_array
    blocked_once = set()

    def controlled_write_array(*args, **kwargs):
        if threading.current_thread().name == "publisher-one" and not blocked_once:
            blocked_once.add(True)
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test did not release first publisher")
        return original_write_array(*args, **kwargs)

    monkeypatch.setattr(np.lib.format, "write_array", controlled_write_array)
    errors = []

    def publish(marker):
        try:
            fs._warm_cache_store(_cache(marker))
        except BaseException as exc:  # captured for assertion in test thread
            errors.append(exc)

    first = threading.Thread(target=publish, args=(100.0,), name="publisher-one")
    second = threading.Thread(target=publish, args=(200.0,), name="publisher-two")
    first.start()
    try:
        assert entered.wait(10), "first publisher never reached NPZ member write"
        second.start()
        second.join(10)
        assert not second.is_alive(), "second publication exceeded bounded wait"
    finally:
        release.set()
        first.join(10)
        if first.is_alive():
            raise AssertionError("first publication exceeded bounded wait")
        if second.ident is not None and second.is_alive():
            second.join(1)
    assert not errors
    _assert_one_publisher(destination, {100.0, 200.0})
    assert list(tmp_path.glob(".warm_cache.npz.*")) == []


def test_separate_process_overlapping_publications_are_atomic_and_clean(
        tmp_path, monkeypatch):
    fs = _load_candidate_solver()
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    destination = tmp_path / ".warm_cache.npz"
    _set_cache_path(fs, destination, monkeypatch)
    source_file = str(Path(fs.__file__).resolve())
    ready = tmp_path / "publisher-one-ready"
    release = tmp_path / "publisher-one-release"
    child_script = r'''
import importlib.util, os, sys
from pathlib import Path
import numpy as np
source_file, destination, marker, ready, release, test_file, pause = sys.argv[1:]
spec = importlib.util.spec_from_file_location("publication_test_module", test_file)
test_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(test_module)
fs = test_module._load_candidate_solver(source_file)
os.environ["SB_SAFE_WARM_CACHE"] = "1"
fs._warm_cache_path = lambda: Path(destination)
fs._SB_WARM_CACHE.clear()
original = np.lib.format.write_array
did_pause = False
def controlled(*args, **kwargs):
    global did_pause
    if pause == "1" and not did_pause:
        did_pause = True
        Path(ready).write_text("ready")
        import time
        deadline = time.monotonic() + 12
        while not Path(release).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not Path(release).exists():
            raise TimeoutError("parent did not release process publisher")
    return original(*args, **kwargs)
np.lib.format.write_array = controlled
fs._warm_cache_store(test_module._cache(float(marker)))
'''
    args = [sys.executable, "-c", child_script, source_file,
            str(destination), "300", str(ready), str(release),
            str(Path(__file__).resolve()), "1"]
    child_env = os.environ.copy()
    child_env.update({
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
        "MKL_THREADING_LAYER": "SEQUENTIAL",
        "PYPARDISO_MKL_RT": str(
            Path(sys.executable).parent / "Library/bin/mkl_rt.3.dll"),
    })
    process_options = {"creationflags": 0x4000} if os.name == "nt" else {}
    first = subprocess.Popen(args, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True,
                             env=child_env, **process_options)
    second = None
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not ready.exists():
            out, err = first.communicate(timeout=5)
            raise AssertionError("first process never reached NPZ member write: "
                                 + out + err)
        second_args = args.copy()
        second_args[second_args.index("300")] = "400"
        second_args[-1] = "0"
        second = subprocess.Popen(second_args, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True,
                                  env=child_env, **process_options)
        try:
            stdout2, stderr2 = second.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            second.terminate()
            stdout2, stderr2 = second.communicate(timeout=5)
            raise AssertionError("second process exceeded bounded wait: " + stderr2)
        assert second.returncode == 0, stdout2 + stderr2
    finally:
        release.write_text("release")
        try:
            stdout1, stderr1 = first.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            first.terminate()
            stdout1, stderr1 = first.communicate(timeout=5)
            raise AssertionError("first process exceeded bounded wait: " + stderr1)
        if second is not None and second.poll() is None:
            second.terminate()
            second.communicate(timeout=5)
    assert first.returncode == 0, stdout1 + stderr1
    _assert_one_publisher(destination, {300.0, 400.0})
    assert list(tmp_path.glob(".warm_cache.npz.*")) == []


def test_failed_publication_removes_its_temporary_file(tmp_path, monkeypatch):
    fs = _load_candidate_solver()
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    destination = tmp_path / ".warm_cache.npz"
    _set_cache_path(fs, destination, monkeypatch)
    original_savez = np.savez

    def fail_after_temp_created(*args, **kwargs):
        raise OSError("injected serialization failure")

    monkeypatch.setattr(np, "savez", fail_after_temp_created)
    fs._warm_cache_store(_cache(500.0))
    monkeypatch.setattr(np, "savez", original_savez)
    assert not destination.exists()
    assert list(tmp_path.glob(".warm_cache.npz.*")) == []
