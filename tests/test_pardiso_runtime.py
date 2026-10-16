"""No native imports: exercise environment propagation and discovery ownership."""
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace

import pytest

from motor_ai_sim.simulation import pardiso_runtime as runtime


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(runtime, "_discovery_attempted", False)
    monkeypatch.setattr(runtime, "_runtime_path", None)


def package(path):
    return SimpleNamespace(scipy_aliases=SimpleNamespace(
        pypardiso_solver=SimpleNamespace(libmkl=SimpleNamespace(_name=path))))


def test_discover_once_and_propagate_without_changing_parent_or_input(monkeypatch, tmp_path):
    dll = tmp_path/"mkl_rt.2.dll"
    dll.write_bytes(b"test stub; never loaded")
    calls = []
    def discover(name):
        calls.append(name)
        return package(str(dll))
    monkeypatch.setattr(runtime, "import_module", discover)
    original = dict(os.environ)
    supplied = {"MKL_NUM_THREADS": "1", "MOTOR_AI_SIM_CONFIG": "caller.yaml"}
    for _ in range(3):
        child = runtime.pardiso_subprocess_env(supplied)
        assert child == dict(supplied, PYPARDISO_MKL_RT=str(dll))
    assert calls == ["pypardiso"]
    assert supplied == {"MKL_NUM_THREADS": "1", "MOTOR_AI_SIM_CONFIG": "caller.yaml"}
    assert dict(os.environ) == original


@pytest.mark.parametrize("override", ["custom.dll", ""])
def test_explicit_override_is_preserved_without_discovery(monkeypatch, override):
    monkeypatch.setattr(runtime, "import_module", lambda _: pytest.fail("unexpected import"))
    supplied = {"PYPARDISO_MKL_RT": override}
    assert runtime.pardiso_subprocess_env(supplied) == supplied


def test_disabled_backend_does_not_load_native_package(monkeypatch):
    monkeypatch.setattr(runtime, "import_module", lambda _: pytest.fail("unexpected import"))
    assert runtime.pardiso_subprocess_env({"SB_NO_PARDISO": "1"}) == {"SB_NO_PARDISO": "1"}


@pytest.mark.parametrize("failure", [ImportError, OSError, AttributeError])
def test_failure_keeps_original_child_behavior_and_is_not_repeated(monkeypatch, failure):
    calls = []
    def discover(name):
        calls.append(name)
        raise failure("unavailable native package or unfamiliar loader")
    monkeypatch.setattr(runtime, "import_module", discover)
    assert runtime.pardiso_subprocess_env({"KEEP": "yes"}) == {"KEEP": "yes"}
    assert runtime.pardiso_subprocess_env({"KEEP": "yes"}) == {"KEEP": "yes"}
    assert calls == ["pypardiso"]


@pytest.mark.parametrize("path", ["mkl_rt.dll", None, "/missing/runtime/libmkl_rt.so"])
def test_only_existing_absolute_filename_can_be_propagated(monkeypatch, path):
    monkeypatch.setattr(runtime, "import_module", lambda _: package(path))
    assert runtime.pardiso_subprocess_env({}) == {}


def test_removed_cached_file_restores_normal_child_discovery(monkeypatch, tmp_path):
    dll = tmp_path/"libmkl_rt.so"
    dll.touch()
    monkeypatch.setattr(runtime, "import_module", lambda _: package(str(dll)))
    assert runtime.pardiso_subprocess_env({})["PYPARDISO_MKL_RT"] == str(dll)
    dll.unlink()
    assert runtime.pardiso_subprocess_env({}) == {}


def test_concurrent_launches_share_one_discovery(monkeypatch, tmp_path):
    dll = tmp_path/"mkl_rt.dll"
    dll.touch()
    calls = []
    def discover(name):
        calls.append(name)
        return package(str(dll))
    monkeypatch.setattr(runtime, "import_module", discover)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(runtime.pardiso_subprocess_env, [{"JOB": str(i)} for i in range(16)]))
    assert calls == ["pypardiso"]
    assert [env["JOB"] for env in results] == list(map(str, range(16)))
    assert all(env["PYPARDISO_MKL_RT"] == str(dll) for env in results)
