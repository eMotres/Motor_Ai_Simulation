"""The solve pool's worker process (``python -m motor_ai_sim.solve_pool_child``).

One interpreter per solve (see :mod:`motor_ai_sim.solve_pool`).  The wire is
length-prefixed pickles: the task arrives on stdin, then optional control
frames (``("threads", n)``); progress, the result or the error go back on the
ORIGINAL stdout.  File descriptor 1 is re-pointed at stderr before anything
else runs, so a stray ``print`` in the solver can never corrupt the protocol.

Import-light on purpose: numpy and the solver are imported by the target,
after the BLAS/MKL thread variables the parent put in the environment are
already in place.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from typing import Any, Dict, Optional


class ChildContext:
    """What a target sees of the pool: progress out, thread changes in."""

    def __init__(self, out, has_progress: bool) -> None:
        self._out = out
        self._lock = threading.Lock()
        self.has_progress = bool(has_progress)
        self.threads = _env_threads()
        self.threads_history = [self.threads]
        self._want: Optional[int] = None
        self._limiter = None

    # called from the control reader thread
    def request_threads(self, n: int) -> None:
        self._want = max(1, int(n))

    def apply_threads(self) -> None:
        """Apply a pending width change — on the SOLVING thread, between
        frames, where no BLAS call is in flight."""
        want = self._want
        if want is None or want == self.threads:
            return
        self._want = None
        try:
            from threadpoolctl import threadpool_limits
            self._limiter = threadpool_limits(limits=int(want))
            self.threads = int(want)
            self.threads_history.append(self.threads)
        except Exception:                               # noqa: BLE001
            pass

    def tick(self, *args, **kwargs) -> None:
        """A progress hook for a caller that asked for no progress: it only
        applies a pending width change, so a solve can still be re-threaded."""
        self.apply_threads()

    def progress(self, *args, **kwargs) -> None:
        self.apply_threads()
        try:
            send(self._out, ("progress", tuple(args), dict(kwargs)), self._lock)
        except (BrokenPipeError, OSError):
            os._exit(3)                                 # parent gone

    def send(self, msg) -> None:
        send(self._out, msg, self._lock)


def _env_threads() -> int:
    try:
        return max(1, int(os.environ.get("MKL_NUM_THREADS") or 1))
    except ValueError:
        return 1


def send(out, msg, lock: Optional[threading.Lock] = None) -> None:
    from motor_ai_sim.solve_pool import write_frame
    if lock is None:
        write_frame(out, msg)
        return
    with lock:
        write_frame(out, msg)


def _lower_priority() -> None:
    """``nice`` to the pool's level on POSIX (idempotent: the parent may
    already have started us through ``nice``).  Windows: the parent's
    creation flags already did it."""
    if os.name == "nt":
        return
    try:
        want = int(os.environ.get("SOLVE_POOL_NICE") or 10)
        cur = os.getpriority(os.PRIO_PROCESS, 0)
        if cur < want:
            os.setpriority(os.PRIO_PROCESS, 0, want)
    except Exception:                                   # noqa: BLE001
        pass


def _apply_context(ctx: Dict[str, Any], stack) -> None:
    """Re-create the caller's workspace, identity, request-scoped settings
    and parsed config in this bare interpreter."""
    from pathlib import Path
    from motor_ai_sim import config as _CFG
    from motor_ai_sim import workspace as _WSP
    if ctx.get("default_config_path"):
        _CFG.DEFAULT_CONFIG_PATH = Path(ctx["default_config_path"])
    w = ctx.get("workspace")
    if w:
        ws = _WSP.Workspace(id=w["id"], email=w["email"], root=Path(w["root"]),
                            shared_root=Path(w["shared_root"]),
                            config_file=(Path(w["config_file"]) if w.get("config_file")
                                         else None),
                            is_process=bool(w.get("is_process")))
        stack.enter_context(_WSP.use_workspace(ws))
    if ctx.get("caller") is not None:
        stack.enter_context(_WSP.use_caller(ctx["caller"]))
    if ctx.get("write_layer"):
        stack.enter_context(_WSP.use_write_layer(ctx["write_layer"]))
    import importlib
    for key, value in (ctx.get("vars") or {}).items():
        mod_name, attr = key.split(":", 1)
        try:
            getattr(importlib.import_module(mod_name), attr).set(value)
        except Exception:                               # noqa: BLE001
            pass
    if ctx.get("tri_budget"):
        from motor_ai_sim.simulation import geo_mesh as _GM
        _GM.set_tri_budget(int(ctx["tri_budget"]))
    c = ctx.get("config")
    if c and c.get("data") is not None:
        data = c["data"]
        if getattr(_CFG, "HAS_OMEGACONF", False):
            from omegaconf import OmegaConf
            data = OmegaConf.create(data)
        _CFG._config_cache[str(c["path"])] = [data, c.get("mtime"), time.time()]


def _resolve(target: str):
    import importlib
    mod_name, fn_name = target.split(":", 1)
    return getattr(importlib.import_module(mod_name), fn_name)


def _peak_rss() -> int:
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(kb) * (1 if sys.platform == "darwin" else 1024)
    except Exception:                                   # noqa: BLE001
        pass
    try:
        import psutil
        mi = psutil.Process().memory_info()
        return int(getattr(mi, "peak_wset", 0) or mi.rss)
    except Exception:                                   # noqa: BLE001
        return 0


def main() -> int:
    _lower_priority()
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    inp = sys.stdin.buffer
    from motor_ai_sim.solve_pool import read_frame
    try:
        task = read_frame(inp)
    except EOFError:
        return 2
    _, target, payload, ctx_data, has_progress = task
    ctx = ChildContext(out, has_progress)

    def _control() -> None:
        try:
            while True:
                msg = read_frame(inp)
                if msg and msg[0] == "threads":
                    ctx.request_threads(int(msg[1]))
        except Exception:                               # noqa: BLE001
            # EOF while solving = the API process is gone: do not become an
            # orphan burning a core for nobody.
            os._exit(3)
    threading.Thread(target=_control, daemon=True, name="solve-pool-control").start()

    import contextlib
    import pickle
    with contextlib.ExitStack() as stack:
        try:
            _apply_context(ctx_data or {}, stack)
            value = _resolve(target)(payload, ctx)
        except BaseException as exc:                    # noqa: BLE001
            tb = traceback.format_exc()
            try:
                blob = pickle.dumps(exc, protocol=pickle.HIGHEST_PROTOCOL)
                pickle.loads(blob)
            except Exception:                           # noqa: BLE001
                blob = None
            ctx.send(("error", type(exc).__name__, str(exc), tb, blob,
                      time.process_time()))
            _finish(out)
            return 1
    try:
        ctx.send(("result", value, time.process_time(), _peak_rss()))
    except Exception as exc:                            # noqa: BLE001
        ctx.send(("error", type(exc).__name__,
                  "the solve finished but its result could not be sent back: %s" % exc,
                  traceback.format_exc(), None, time.process_time()))
        _finish(out)
        return 1
    _finish(out)
    return 0


def _finish(out) -> None:
    try:
        out.flush()
    except Exception:                                   # noqa: BLE001
        pass
    try:
        sys.stderr.flush()
    except Exception:                                   # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Targets
# ─────────────────────────────────────────────────────────────────────────────

def em_transient_eval_target(payload: Dict[str, Any], ctx: ChildContext) -> Dict[str, Any]:
    """``fem_solver_2d.em_transient_eval`` plus the cache updates it made."""
    from motor_ai_sim.simulation import fem_solver_2d as _FS
    kw = dict(payload["kwargs"])
    # Always a hook, so the load rule can re-thread the solve between frames;
    # progress only travels back when the caller asked for it.  The solver
    # only calls the hook (and swallows its errors), so the answer is the same
    # with or without it (docs/SOLVE_POOL_2026-09-29.md, equality check).
    kw["progress_cb"] = ctx.progress if ctx.has_progress else ctx.tick
    before = dict(_FS._DAXIS_CACHE)
    result = _FS.em_transient_eval(**kw)
    echo: Dict[str, Any] = {}
    try:
        wc = _FS._SB_WARM_CACHE.get("last")
        if wc is not None:
            echo["warm_last"] = wc
    except Exception:                                   # noqa: BLE001
        pass
    new = {k: v for k, v in _FS._DAXIS_CACHE.items()
           if k not in before or before[k] != v}
    if new:
        echo["daxis"] = new
    return {"result": result, "echo": echo, "threads": ctx.threads_history}


# Diagnostics: the unit tests and a health check drive the pool with these,
# so the scheduling machinery is exercised without a FEM import.

def diag_echo(payload: Any, ctx: ChildContext) -> Dict[str, Any]:
    from motor_ai_sim import workspace as _WSP
    from motor_ai_sim import config as _CFG
    out = {"payload": payload, "pid": os.getpid(),
           "mkl_threads": os.environ.get("MKL_NUM_THREADS"),
           "omp_threads": os.environ.get("OMP_NUM_THREADS"),
           "child_flag": os.environ.get("SOLVE_POOL_CHILD"),
           "ws_id": _WSP.workspace().id,
           "config_path": str(_CFG.config_path()),
           "caller": _WSP.caller()}
    try:
        from motor_ai_sim import material_context as _MC
        out["materials"] = _MC.get_request_materials()
    except Exception:                                   # noqa: BLE001
        pass
    if os.name != "nt":
        out["nice"] = os.getpriority(os.PRIO_PROCESS, 0)
    else:
        try:
            import psutil
            out["nice"] = int(psutil.Process().nice())
        except Exception:                               # noqa: BLE001
            out["nice"] = None
    return out


def diag_progress(payload: Dict[str, Any], ctx: ChildContext) -> Dict[str, Any]:
    n = int(payload.get("n", 5))
    dt = float(payload.get("dt", 0.05))
    for i in range(n):
        ctx.progress(i + 1, n, "diag")
        time.sleep(dt)
    return {"n": n, "threads": list(ctx.threads_history)}


def diag_sleep(payload: Dict[str, Any], ctx: ChildContext) -> Dict[str, Any]:
    """Sleep ``s`` seconds; optionally start a grandchild first and write its
    pid to ``pid_file`` (the tree-kill test)."""
    if payload.get("pid_file"):
        import subprocess
        g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        with open(payload["pid_file"], "w") as fh:
            fh.write("%d %d" % (os.getpid(), g.pid))
    t_end = time.time() + float(payload.get("s", 1.0))
    while time.time() < t_end:
        if payload.get("progress"):
            ctx.progress(None, None)
        time.sleep(0.05)
    return {"slept": payload.get("s"), "threads": list(ctx.threads_history)}


def diag_crash(payload: Dict[str, Any], ctx: ChildContext) -> None:
    sys.stderr.write("diag_crash: exiting hard\n")
    sys.stderr.flush()
    os._exit(int(payload.get("code", 137)))


def diag_raise(payload: Dict[str, Any], ctx: ChildContext) -> None:
    raise ValueError(str(payload.get("msg", "diag")))


def diag_burn(payload: Dict[str, Any], ctx: ChildContext) -> Dict[str, Any]:
    """Spin one core for ``s`` seconds (CPU accounting test)."""
    t_end = time.process_time() + float(payload.get("s", 1.0))
    x = 0
    while time.process_time() < t_end:
        x += 1
    return {"iters": x}


if __name__ == "__main__":
    sys.exit(main())
