"""gmsh runs out of process — licence separation guard + crash/timeout tests.

gmsh is GPL-2.0-or-later. Its exception list does not cover Intel MKL, which
this API process links (via pypardiso) under an AGPL section 7 exception we
grant for our own code. For that exception to hold, the API process must
never import gmsh or link libgmsh in-process: gmsh is a separate program,
``motor_ai_sim.simulation.gmsh_worker_main``, talked to over a pipe by
``motor_ai_sim.simulation.gmsh_worker``.

Two kinds of guard here:

  * STATIC (AST-based, no gmsh needed, always runs): every ``import gmsh`` in
    the source tree is lexically inside a worker-side function (name ending
    in ``_impl``, the ``_gmsh_start``/``_handshake`` plumbing helpers, or
    inside ``gmsh_worker_main.py`` itself) — never at module scope, which is
    what would make it run when the API process merely imports the module.
  * DYNAMIC (needs gmsh installed; skipped otherwise): actually calling a
    mesh-building wrapper must not pull gmsh into the CALLING process's
    ``sys.modules``, and a worker that raises, hard-crashes, or hangs must
    turn into a clear ``WorkerError`` — never a silent fallback.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "motor_ai_sim"
_WORKER_MAIN = _SRC / "simulation" / "gmsh_worker_main.py"

_HAS_GMSH = importlib.util.find_spec("gmsh") is not None

# Names of functions allowed to contain ``import gmsh`` (or be its ancestor
# in the AST). Worker-side implementations are named ``..._impl`` by
# convention; the rest are the small gmsh-plumbing helpers they call.
_ALLOWED_CONTAINERS = {"_gmsh_start", "_handshake"}


def _iter_source_files():
    for path in _SRC.rglob("*.py"):
        yield path


def _is_gmsh_import(node: ast.stmt) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name == "gmsh" or alias.name.startswith("gmsh.")
                   for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.module is not None and (
            node.module == "gmsh" or node.module.startswith("gmsh."))
    return False


def _enclosing_function_names(tree: ast.AST, target: ast.stmt) -> list:
    """Names of every function def that lexically contains ``target``."""
    stack: list = []
    found: list = []

    class _Visitor(ast.NodeVisitor):
        def generic_visit(self, node):
            is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_func:
                stack.append(node.name)
            if node is target:
                found.extend(stack)
            super().generic_visit(node)
            if is_func:
                stack.pop()

    _Visitor().visit(tree)
    return found


def test_no_module_level_gmsh_import_in_the_api_source_tree():
    """``import gmsh`` must never sit where a plain module import would run it."""
    offenders = []
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:  # ast.Module.body = TRUE top level only
            if _is_gmsh_import(node):
                offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
    assert not offenders, (
        "gmsh imported at module scope (would run when the API process just "
        f"imports the module, breaking the process boundary): {offenders}"
    )


def test_gmsh_import_only_inside_worker_side_functions():
    """Every (deferred, function-local) ``import gmsh`` must be worker-side code."""
    offenders = []
    for path in _iter_source_files():
        if path == _WORKER_MAIN:
            continue  # the worker process is allowed to import gmsh anywhere
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if not _is_gmsh_import(node):
                continue
            containers = _enclosing_function_names(tree, node)
            if not containers:
                continue  # caught by the module-scope test above
            innermost = containers[-1]
            ok = innermost.endswith("_impl") or innermost in _ALLOWED_CONTAINERS
            if not ok:
                offenders.append(
                    f"{path.relative_to(_ROOT)}:{node.lineno} inside {innermost!r}"
                )
    assert not offenders, (
        "gmsh imported inside a function that is not a worker-side "
        f"'*_impl' (or gmsh-plumbing helper): {offenders}\n"
        "Route the call through motor_ai_sim.simulation.gmsh_worker instead."
    )


def test_worker_main_never_imports_mkl_or_pardiso_at_module_scope():
    """The worker's own import graph must stay clear of the API's solver stack."""
    tree = ast.parse(_WORKER_MAIN.read_text(encoding="utf-8"), filename=str(_WORKER_MAIN))
    banned = ("mkl", "pypardiso", "pydiso")
    offenders = []
    for node in tree.body:
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for n in names:
            if any(n == b or n.startswith(b + ".") for b in banned):
                offenders.append(f"{_WORKER_MAIN.name}:{node.lineno} imports {n!r}")
    assert not offenders, offenders
    # And scipy.sparse direct solvers specifically (scipy itself is fine —
    # numpy/scipy array plumbing is not the licence concern, its PARDISO/
    # UMFPACK-wrapping solver entry points are).
    banned_attrs = {"spsolve", "splu", "factorized"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "scipy.sparse" in node.module:
            hit = {a.name for a in node.names} & banned_attrs
            assert not hit, f"{_WORKER_MAIN.name} imports sparse solver(s) {hit}"


@pytest.mark.skipif(not _HAS_GMSH, reason="gmsh not installed in this environment")
def test_calling_the_mesher_does_not_import_gmsh_in_the_caller(monkeypatch):
    """The strongest guard: actually run a mesh and check the CALLER's sys.modules."""
    monkeypatch.setenv("MOTOR_AI_SIM_GMSH_WORKER_MODE", "fresh")
    sys.path.insert(0, str(_ROOT / "src"))
    from shapely.geometry import Polygon

    from motor_ai_sim.simulation import gmsh_worker, mesher

    assert "gmsh" not in sys.modules, "test setup already polluted by an earlier import"

    square = Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])
    try:
        verts_mm, tris = mesher._mesh_single_polygon(square, mesh_size_mm=1.0, min_size_mm=0.3)
    finally:
        gmsh_worker.shutdown_worker()

    assert verts_mm.shape[0] == 2 and verts_mm.shape[1] > 0
    assert tris.shape[0] == 3 and tris.shape[1] > 0
    assert "gmsh" not in sys.modules, (
        "gmsh ended up imported in the CALLING process — the worker boundary leaked"
    )


@pytest.mark.skipif(not _HAS_GMSH, reason="gmsh not installed in this environment")
def test_worker_handshake_reports_provenance(monkeypatch):
    monkeypatch.setenv("MOTOR_AI_SIM_GMSH_WORKER_MODE", "fresh")
    sys.path.insert(0, str(_ROOT / "src"))
    from motor_ai_sim.simulation import gmsh_worker

    info = gmsh_worker.handshake()
    assert info.get("gmsh_api_version"), info
    assert "gmsh" not in sys.modules


def test_worker_reports_a_clear_error_on_a_python_exception(monkeypatch):
    monkeypatch.setenv("MOTOR_AI_SIM_GMSH_WORKER_MODE", "fresh")
    sys.path.insert(0, str(_ROOT / "src"))
    from motor_ai_sim.simulation import gmsh_worker

    # the SAME exception type the in-process call raised (so callers keep
    # their handling), chained to the WorkerError that carries the worker's
    # traceback
    with pytest.raises(ValueError) as exc_info:
        gmsh_worker.call("tests._gmsh_worker_crash_helpers:raise_value_error")
    assert "deliberate failure" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, gmsh_worker.WorkerError)


def test_worker_hard_crash_raises_crash_error_not_a_silent_fallback(monkeypatch):
    monkeypatch.setenv("MOTOR_AI_SIM_GMSH_WORKER_MODE", "fresh")
    sys.path.insert(0, str(_ROOT / "src"))
    from motor_ai_sim.simulation import gmsh_worker

    with pytest.raises(gmsh_worker.WorkerCrashError):
        gmsh_worker.call("tests._gmsh_worker_crash_helpers:hard_crash")


def test_worker_timeout_kills_the_process_and_raises(monkeypatch):
    monkeypatch.setenv("MOTOR_AI_SIM_GMSH_WORKER_MODE", "fresh")
    sys.path.insert(0, str(_ROOT / "src"))
    from motor_ai_sim.simulation import gmsh_worker

    t0 = time.monotonic()
    with pytest.raises(gmsh_worker.WorkerTimeoutError):
        gmsh_worker.call(
            "tests._gmsh_worker_crash_helpers:sleep_forever",
            args=(30.0,),
            timeout=1.0,
        )
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, f"timeout was not enforced promptly ({elapsed:.1f}s)"


def test_no_fallback_mesher_is_reachable_after_a_worker_error(monkeypatch):
    """Fail closed: a worker error must propagate, not degrade into another mesher."""
    monkeypatch.setenv("MOTOR_AI_SIM_GMSH_WORKER_MODE", "fresh")
    sys.path.insert(0, str(_ROOT / "src"))
    from motor_ai_sim.simulation import gmsh_worker

    # A bogus func_ref proves `call` does not quietly return a degraded
    # result for anything it cannot run.
    with pytest.raises(gmsh_worker.WorkerError):
        gmsh_worker.call("tests._gmsh_worker_crash_helpers:this_function_does_not_exist")
