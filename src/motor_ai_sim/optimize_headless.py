"""The Optimization tab's algorithm without the web process.

    python -m motor_ai_sim.optimize_headless request.json [--max-evals N]

One request file, the same body the tab sends (``DescentRequest``) plus the
machine it runs on.  The run uses the SAME assembly (``prepare_descent``), the
SAME worker (CMA-ES / gradient descent) and the SAME eval subprocess
(``refine_proc``) as ``POST /api/optimization/descent/start``; only the thread,
the job queue and the progress polling of the web process are left out.

Request::

    {"machine": {"geometry": {...complete...}, "materials": {...},
                 "winding": {...}, "simulation": {...}, "mesh": {...},
                 "replace_geometry": true},
     "mode": "descent" | "sweep",
     "descent": <the tab's DescentRequest body>,   # or "sweep": <its ScanRequest body>
     "frozen": ["air_gap", ...],          # optional: refused if also a variable
     "workers": 12,                       # optional: default = cpus of the job
     "max_evals": 20}                     # optional: computed evals in this run

The machine of the run is ONE folder, ``<request dir>/state/``:
``motor_config.yaml`` (the base config with geometry and materials REPLACED,
never merged, and the winding / simulation / mesh blocks of the request laid
over the base's), the four library files beside it, and every file the app
itself writes next to its config (``.last_descent.json``, ``.descent_runs/``,
``.opt_dataset.jsonl``, ``.warm_cache.npz``, the quote history).  The output is
therefore the app's own run format, nothing new; ``result.json`` is a copy of
the final state.

Resume and growth (same machine AND same application code: the state folder
records both and refuses another): every computed eval is kept in ``state/.headless_memo.jsonl``
under the exact arguments it was solved with.  Running the same request again
in the same folder replays the run (CMA-ES has a fixed seed): every eval found
in the memo returns at once, the first missing one is computed.  ``max_evals``
caps the COMPUTED evals of one invocation (memo hits are free), so a campaign
is proved with a handful of evals and then grown from the same folder.  With a
target torque the probe current of a candidate depends on which eval of the
previous generation finished last (as in the app); a replay that meets a
different probe simply computes that eval.

Stop: SIGTERM / SIGINT set the run's cancel flag and kill the running evals,
exactly as the tab's Stop does; the worker writes its checkpoint and returns.
Exit code 0 = finished, 3 = stopped (cancel or eval cap), 4 = stopped because an
eval was killed by the memory limit (exit -9), 1 = the run failed,
2 = REFUSED before any eval (the message names the missing input).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: library files the app reads from the folder of the machine config
LIBRARIES = ("materials_library.yaml", "bearings_library.yaml",
             "wire_stock.yaml", "fusion_param_map.yaml")
#: machine blocks laid over the base config's block of the same name
OVERLAY_BLOCKS = ("winding", "simulation", "mesh")
MEMO_FILE = ".headless_memo.jsonl"


class Refused(Exception):
    """A request that cannot run as written; the message names the input."""


def _base_config_path(req: Dict[str, Any], req_dir: Path) -> Path:
    b = (req.get("machine") or {}).get("base_config")
    if b:
        p = (req_dir / b).resolve()
    else:
        p = Path(os.environ.get("MOTOR_AI_SIM_BASE_CONFIG")
                 or "/app/config/motor_config.yaml")
    if not p.is_file():
        raise Refused(f"base config not found: {p} (set machine.base_config)")
    return p


def build_machine_config(req: Dict[str, Any], req_dir: Path,
                         state: Path) -> Dict[str, Any]:
    """The complete machine config of the run, before derived keys are
    refreshed (that needs the app, see ``_refresh_derived``)."""
    import yaml
    m = req.get("machine") or {}
    if "folder" in m:
        raise Refused("machine.folder (die + config bundle) is not supported yet; "
                      "give machine.geometry with replace_geometry true")
    if not isinstance(m.get("geometry"), dict) or not m["geometry"]:
        raise Refused("machine.geometry missing")
    if m.get("replace_geometry") is not True:
        raise Refused("machine.replace_geometry must be true: the geometry REPLACES "
                      "the config's, a merge keeps stale keys of another machine")
    for blk in ("materials", "winding", "simulation"):
        if not isinstance(m.get(blk), dict) or not m[blk]:
            raise Refused(f"machine.{blk} missing: the base config's {blk} block "
                          f"belongs to another machine")
    base_p = _base_config_path(req, req_dir)
    cfg = yaml.safe_load(base_p.read_text(encoding="utf-8")) or {}
    g = dict(m["geometry"])
    cfg["geometry"] = g
    cfg["materials"] = dict(m["materials"])
    for blk in OVERLAY_BLOCKS:
        if isinstance(m.get(blk), dict):
            merged = dict(cfg.get(blk) or {})
            merged.update(m[blk])
            cfg[blk] = merged
    # The simulation block mirrors the winding connection (the Simulation tab
    # writes both); a stale mirror of the base machine must not survive.
    sim, wnd = cfg.setdefault("simulation", {}), cfg.get("winding") or {}
    for k in ("connection", "star_delta"):
        if k in wnd and k in sim and k not in (m.get("simulation") or {}):
            sim[k] = wnd[k]
    # ...and the electrical frequency mirrors the speed (f = rpm * p / 120).
    msim = m.get("simulation") or {}
    try:
        poles = int(g["num_seg"]) * int(g["num_poles_per_segment"])
    except (KeyError, TypeError, ValueError):
        poles = int(g.get("num_poles") or 0)
    if "rpm" in msim and "frequency" not in msim and "frequency" in sim and poles:
        sim["frequency"] = round(float(msim["rpm"]) * poles / 120.0, 6)
    for lib in LIBRARIES:
        src = base_p.parent / lib
        if src.is_file() and not (state / lib).exists():
            shutil.copy2(src, state / lib)
    return cfg


def _refresh_derived(cfg: Dict[str, Any]) -> Dict[str, Any]:
    from motor_ai_sim.geometry.motor_geometry import refresh_derived_geometry
    g = dict(cfg["geometry"])
    if "num_seg" in g:
        if "num_slots_per_segment" in g:
            g["num_slots"] = int(g["num_seg"] * g["num_slots_per_segment"])
        if "num_poles_per_segment" in g:
            g["num_poles"] = int(g["num_seg"] * g["num_poles_per_segment"])
    cfg["geometry"] = refresh_derived_geometry(g)
    return cfg


#: the mesh block keys the Optimization preset sets in the machine's config (the optimiser's evals read them from there)
_MESH_BLOCK_KEYS = ("mesh_size_mm", "min_size_mm", "outer_air_factor", "gap_layers", "n_sectors")


def apply_optimization_preset(req: Dict[str, Any], cfg: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """THE OPTIMIZER ALWAYS RUNS THE OPTIMIZATION PRESET (owner 2026-11-17, mesh_presets.py).

    The preset is resolved for THIS machine and (1) written into the machine config's ``mesh`` block, where the evals read
    the sizes, the outer air, the sector, the gap rows and the per-part sizes (``coil_rel``), and (2) filled into the
    request body of ``mode`` for every key it leaves out.  A request body that STATES another value is refused, naming the
    key, the request's value and the preset's, unless the request carries ``preset_override_reason`` (then it runs, and the
    record says ``custom`` with the reason).  Returns the record stored with the run (name, version, custom, overridden,
    resolved values); ``req[mode]`` is replaced by the completed body."""
    from motor_ai_sim import mesh_presets as MP
    try:
        done = MP.fill_body("optimization", req[mode], cfg["geometry"], steps_key="steps_per_period")
    except MP.PresetError as exc:
        raise Refused(f"the Optimization preset cannot be resolved for this machine: {exc}")
    rec = done["record"]
    reason = str(req.get("preset_override_reason") or "").strip()
    if rec["overridden"] and not reason:
        raise Refused("the optimiser runs the Optimization preset (v%s) and the request states other settings: %s; "
                      "remove them from the request, or give 'preset_override_reason' to run them as custom" % (
                          rec["version"], "; ".join(f"{k} {v['requested']!r} (preset {v['preset']!r})"
                                                    for k, v in sorted(rec["overridden"].items()))))
    if reason:
        rec["override_reason"] = reason
    vals = rec["resolved"]
    mesh = dict(cfg.get("mesh") or {})
    stated = rec["overridden"]
    for k in _MESH_BLOCK_KEYS:
        if k not in stated:
            mesh[k] = vals[k]
    comp = dict(vals["component_mesh"])
    if "component_mesh" not in stated:
        mesh["component_mesh"] = comp
    cfg["mesh"] = mesh
    req[mode] = done["body"]
    return rec


def _write_yaml(p: Path, cfg: Dict[str, Any]) -> None:
    import yaml
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    os.replace(tmp, p)


def code_fingerprint() -> str:
    """Hash of the application's Python sources this process runs.  A memo
    filled by other code (a solver fix deployed in between) would replay
    answers the new code does not give, so a state folder is bound to it."""
    import motor_ai_sim
    root = Path(motor_ai_sim.__file__).resolve().parent
    h = hashlib.sha256()
    for f in sorted(root.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        h.update(str(f.relative_to(root)).encode())
        # line endings do not change the code (the image is built from CRLF files)
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def _cpus() -> int:
    try:
        q, per = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if q != "max":
            return max(1, int(float(q) / float(per)))
    except (OSError, ValueError):
        pass
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def cancel_run(O, kind: str, kill: bool = True) -> None:
    """The tab's Stop for a ``descent`` or a ``sweep`` (``scan``) run."""
    if kind == "sweep":
        with O._scan_lock:
            O._scan_state["cancel"] = True
        if kill:
            O._kill_live_evals("scan")
    else:
        with O._descent_lock:
            O._descent_state["cancel"] = True
        if kill:
            O._kill_live_evals("descent")


#: gap_layers counts element rows PER SIDE of the middle of the air gap.  Owner
#: 2026-11-16: every optimisation evaluation uses one row each side (1); final
#: points (reported / saved / stored) are solved at 2 outside the optimisation.
OPT_GAP_LAYERS = 1.0
MAX_GAP_LAYERS = 2.0


def refuse_gap_layers(gl, reason: Optional[str] = None) -> None:
    """``gl`` = ``_resolve_gap_layers(...)`` (value, source).  Below 1 and above
    2 are never valid; 2 only with an explicit request ``gap_layers_reason``."""
    v = float(gl[0])
    if v < OPT_GAP_LAYERS:
        raise Refused(f"gap_layers {v} ({gl[1]}) < 1: no gap row on a side")
    if v > MAX_GAP_LAYERS:
        raise Refused(f"gap_layers {v} ({gl[1]}) > 2: never more than 2 rows per side "
                      f"(owner 2026-11-16, hard rule)")
    if v > OPT_GAP_LAYERS and not (reason or "").strip():
        raise Refused(f"gap_layers {v} ({gl[1]}): optimisation evaluations use gap_layers 1 "
                      f"(one row each side of the gap middle, owner 2026-11-16); give "
                      f"'gap_layers_reason' in the request to run another value")


def refuse_clamped(pairs) -> None:
    """``pairs`` = (name, requested, what the app will use): a request the app
    would silently change is refused, naming the value."""
    bad = [f"{n} {a!r} -> {b!r}" for n, a, b in pairs
           if a is not None and b is not None and float(a) != float(b)]
    if bad:
        raise Refused("the app would clamp: " + "; ".join(bad))


def refuse_magnet_temperature(O, mode_body: Dict[str, Any], machine_sim: Dict[str, Any],
                              mode: str) -> None:
    """Optimiser evals solve the magnet at its CARD temperature unless the
    request sets ``magnet_temp_c``; the machine's simulation.magnet_temp_c is
    never read.  A machine temperature that differs from the card, with no
    request field to carry it, would be silently replaced: refuse."""
    want = machine_sim.get("magnet_temp_c")
    if want is None or mode_body.get("magnet_temp_c") is not None:
        return
    from motor_ai_sim.simulation.fem_solver_2d import _run_magnet_temp_c
    card = _run_magnet_temp_c(None)
    if card is None or abs(float(card) - float(want)) > 1e-6:
        hint = " (set 'magnet_temp_c' in the %s body)" % ("sweep" if mode == "sweep" else "descent")
        raise Refused(f"machine.simulation.magnet_temp_c {want} but the evals would solve "
                      f"the magnet card at {card}" + hint)


# ── eval memo: resume by replay ──────────────────────────────────────────────
class EvalMemo:
    """Wraps ``routes.optimization._subprocess_eval``: an eval already solved
    with the same arguments on the same machine returns its stored answer, a
    new one is computed by the app's own function and stored.  Stops counting
    (and returns ``cancelled``) after ``max_evals`` computed evals."""

    #: failures that say nothing about the design and must be re-solved
    _TRANSIENT = ("cancelled", "timeout", "worker exited", "source_changed")

    def __init__(self, O, path: Path, max_evals: Optional[int], kind: str = "descent"):
        self.O, self.path, self.max_evals, self.kind = O, path, max_evals, kind
        self.orig = O._subprocess_eval
        self.lock = threading.Lock()
        self.memo: Dict[str, Any] = {}
        self.computed = self.hits = 0
        self.capped = False
        self.oom = 0
        self.stopped_for_oom = False
        self.retried = 0
        if path.is_file():
            for ln in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(ln)
                    self.memo[row["k"]] = row["v"]
                except (ValueError, KeyError):
                    continue

    def stopping(self) -> bool:
        st, lk = ((self.O._scan_state, self.O._scan_lock) if self.kind == "sweep"
                  else (self.O._descent_state, self.O._descent_lock))
        with lk:
            return bool(st.get("cancel"))

    def key(self, overrides, current_a, steps, coil_temp_c, kw) -> str:
        kw = {k: v for k, v in kw.items() if k not in ("owner", "_log")}
        fp = self.O._config_fingerprint(tuple(sorted(overrides or {})))
        payload = [fp, sorted((overrides or {}).items()), float(current_a),
                   int(steps), float(coil_temp_c), sorted(kw.items())]
        cr = self.O._RUN_COIL_REL.get()
        if cr is not None:          # the run's wire cell is part of the identity; an entry stored without one is never an answer to it
            payload.append(["coil_rel", round(float(cr), 4)])
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str)
                              .encode()).hexdigest()

    #: a result above this ring self-check is not reused on a replay (re-solved)
    SELF_CHECK_MAX = 0.5

    def _transient(self, res: Dict[str, Any]) -> bool:
        if res.get("ok"):
            r = res.get("res") or {}
            # Kept for this run, never replayed into another: a solve that
            # needed the P2 cut-edge repair (#702) or whose two-ring
            # self-check is wild says more about its mesh than its design.
            if r.get("p2_cut_edge_weld"):
                return True
            try:
                if float(r.get("ripple_self_check_rel") or 0.0) > self.SELF_CHECK_MAX:
                    return True
            except (TypeError, ValueError):
                pass
            return False
        if res.get("cancelled") or res.get("source_changed"):
            return True
        err = str(res.get("error") or "")
        return any(t in err for t in self._TRANSIENT)

    def __call__(self, overrides, current_a, steps, coil_temp_c, *a, **kw):
        if a:          # positional extras: n_periods, gamma_deg, ... (by name below)
            names = ("n_periods", "gamma_deg", "mesh_size_mm", "min_size_mm",
                     "n_sectors", "_log", "pole_copy", "torque_filter")
            kw.update(dict(zip(names, a)))
        k = self.key(overrides, current_a, steps, coil_temp_c, kw)
        with self.lock:
            if k in self.memo:
                self.hits += 1
                return json.loads(json.dumps(self.memo[k]))
            if self.max_evals is not None and self.computed >= self.max_evals:
                self.capped = True
                cancel_run(self.O, self.kind, kill=False)
                return {"ok": False, "cancelled": True, "error": "cancelled: eval cap"}
            self.computed += 1
        res = self.orig(overrides, current_a, steps, coil_temp_c, **kw)
        if isinstance(res, dict) and res.get("error") == "timeout" and not self.stopping():
            # The hang cap is priced from the folder's measured median, which a
            # fresh state folder does not have yet (cap = 3x the frame estimate,
            # ~10 min); on a loaded host a healthy first-wave eval can pass it.
            # A timeout says nothing about the design: solve it once more, by
            # then the median exists and the cap is 4x it.
            print("RETRY: eval timed out (hang cap %s); solving it once more"
                  % ("before any median" if not self.retried else "again"), flush=True)
            self.retried += 1
            res = self.orig(overrides, current_a, steps, coil_temp_c, **kw)
        if isinstance(res, dict) and "exited with code -9" in str(res.get("error") or ""):
            # SIGKILL from outside = the job's memory limit, as a rule.  The app
            # files it as a failed design and goes on (an MTPA sweep then picks
            # its angle from the survivors); a campaign must not.  Stop loudly.
            self.oom += 1
            if not self.stopped_for_oom:
                self.stopped_for_oom = True
                print("STOP: an eval was killed (exit -9, the memory limit of the job?): "
                      "fewer workers or more memory; the evals already done stay in "
                      "the memo", flush=True)
                cancel_run(self.O, self.kind)
        if isinstance(res, dict) and not self._transient(res):
            with self.lock:
                self.memo[k] = res
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"k": k, "v": res, "call": {
                        "overrides": overrides, "current_a": current_a, "steps": steps,
                        "coil_temp_c": coil_temp_c, **kw}}, default=str) + "\n")
        return res


def _progress_line(st: Dict[str, Any], memo: EvalMemo) -> str:
    b = st.get("best") or {}
    m = b.get("metrics") or {}
    return ("[%s] phase=%s round=%s iter=%s/%s evals=%s computed=%d memo=%d "
            "best F=%s eff=%s T/m=%s T=%s" % (
                time.strftime("%H:%M:%S", time.gmtime()), st.get("phase"),
                st.get("walk_round"), st.get("iter"), st.get("max_iters"),
                st.get("n_evals"), memo.computed, memo.hits, b.get("F"),
                m.get("efficiency"), m.get("torque_per_mass"), m.get("T_em_Nm")))


def run_descent(O, body: Dict[str, Any], *, frozen: List[str] = (),
                log=print, poll_s: float = 15.0,
                memo: Optional[EvalMemo] = None,
                gap_reason: Optional[str] = None) -> Dict[str, Any]:
    """Prepare and run one descent in THIS process with the route's own
    functions; returns the final ``_descent_state``.  Raises ``Refused``."""
    req = O.DescentRequest(**body)
    try:
        O._refuse_die_defining(req)
    except Exception as e:  # noqa: BLE001 - the route's 422
        raise Refused(str(getattr(e, "detail", e)))
    plan = O.prepare_descent(req)
    if plan["dropped"]:
        raise Refused("variables the app would drop: " + "; ".join(
            f"{d['name']} ({d['reason']})" for d in plan["dropped"]))
    if not plan["var_specs"]:
        raise Refused("no whitelisted variables with a usable range to descend")
    clash = sorted(set(frozen) & {s["name"] for s in plan["var_specs"]})
    if clash:
        raise Refused("frozen keys listed as variables: " + ", ".join(clash))
    gl = O._resolve_gap_layers(req.gap_layers)
    refuse_gap_layers(gl, gap_reason)
    if req.algorithm.strip().lower() == "cmaes":
        try:
            import cma  # noqa: F401
        except ImportError as e:
            raise Refused(f"cma package not installed: {e}")
    ep = plan["init_state"]["eval_params"]
    refuse_clamped([("steps_per_period", req.steps_per_period, plan["response"]["steps_per_period"]),
                    ("max_iters", req.max_iters, plan["response"]["max_iters"]),
                    ("mesh_size_mm", req.mesh_size_mm, ep["mesh_size_mm"]),
                    ("min_size_mm", req.min_size_mm, ep["min_size_mm"])]
                   + ([("max_rounds", req.max_rounds, plan["response"]["max_rounds"])]
                      if req.auto_expand else []))
    log("plan: " + json.dumps(plan["response"], default=str))
    O.begin_descent_state(plan)
    t = threading.Thread(target=plan["worker"], args=plan["args"],
                         kwargs=plan["kwargs"], name="headless-descent")
    t.start()
    last = None
    while t.is_alive():
        t.join(poll_s)
        with O._descent_lock:
            st = dict(O._descent_state)
        sig = (st.get("phase"), st.get("iter"), st.get("n_evals"), st.get("walk_round"))
        if sig != last:
            last = sig
            log(_progress_line(st, memo) if memo is not None else str(sig))
    with O._descent_lock:
        return dict(O._descent_state)


def _sweep_line(st: Dict[str, Any], memo: Optional[EvalMemo]) -> str:
    return ("[%s] sweep done=%s/%s cached=%s computed=%s memo=%s error=%s" % (
        time.strftime("%H:%M:%S", time.gmtime()), st.get("done"), st.get("total"),
        st.get("cached"), memo.computed if memo else "-", memo.hits if memo else "-",
        st.get("error")))


def run_sweep(O, body: Dict[str, Any], *, frozen: List[str] = (),
              log=print, poll_s: float = 15.0,
              memo: Optional[EvalMemo] = None,
              gap_reason: Optional[str] = None) -> Dict[str, Any]:
    """Prepare and run one Sweep in THIS process with the route's own
    functions; returns the final ``_scan_state``.  Raises ``Refused``.  A
    re-run in the same folder serves the finished points from the app's own
    eval cache (``.scan_cache.jsonl``)."""
    req = O.ScanRequest(**body)
    try:
        O._refuse_die_defining(req)
    except Exception as e:  # noqa: BLE001 - the route's 422
        raise Refused(str(getattr(e, "detail", e)))
    plan = O.prepare_scan(req)
    if plan["dropped"]:
        raise Refused("variables the app would drop: " + "; ".join(
            f"{d['name']} ({d['reason']})" for d in plan["dropped"]))
    if not plan["variables"]:
        raise Refused("no scan variables")
    clash = sorted(set(frozen) & {v["name"] for v in plan["variables"]})
    if clash:
        raise Refused("frozen keys listed as variables: " + ", ".join(clash))
    gl = O._resolve_gap_layers(req.gap_layers)
    refuse_gap_layers(gl, gap_reason)
    a = plan["args"]
    refuse_clamped([("steps_per_period", req.steps_per_period, a[2]),
                    ("max_geometries", req.max_geometries, a[5]),
                    ("mesh_size_mm", req.mesh_size_mm, a[8]),
                    ("min_size_mm", req.min_size_mm, a[9]),
                    ("gap_layers", gl[0], a[13]),
                    ("end_winding_factor", req.end_winding_factor, a[14])])
    log("plan: " + json.dumps({**plan["response"], "variables": plan["variables"],
                               "operating_points": plan["ops"], **plan["kwargs"]}, default=str))
    with O._scan_lock:
        O._scan_state.update(plan["init_state"])
    from motor_ai_sim import machine_binding as MB
    kw = {**plan["kwargs"], "binding": MB.MachineBinding(
        lambda keys: O._config_fingerprint(keys), what="FEM scan")}
    t = threading.Thread(target=O._scan_worker, args=plan["args"], kwargs=kw,
                         name="headless-sweep")
    t.start()
    last = None
    while t.is_alive():
        t.join(poll_s)
        with O._scan_lock:
            st = dict(O._scan_state)
        sig = (st.get("done"), st.get("total"), st.get("error"))
        if sig != last:
            last = sig
            log(_sweep_line(st, memo))
    with O._scan_lock:
        return dict(O._scan_state)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="motor_ai_sim.optimize_headless")
    ap.add_argument("request")
    ap.add_argument("--max-evals", type=int, default=None,
                    help="computed evals in this invocation (memo hits are free)")
    ap.add_argument("--workers", type=int, default=None)
    ns = ap.parse_args(argv)

    def say(msg: str) -> None:
        print(msg, flush=True)

    req_p = Path(ns.request).resolve()
    req = json.loads(req_p.read_text(encoding="utf-8"))
    state = (req_p.parent / str(req.get("state_dir") or "state")).resolve()
    state.mkdir(parents=True, exist_ok=True)
    cfg_p = state / "motor_config.yaml"
    try:
        mode = str(req.get("mode") or "descent")
        if mode not in ("descent", "sweep"):
            raise Refused(f"mode {mode!r}: 'descent' or 'sweep'")
        if not isinstance(req.get(mode), dict):
            raise Refused(f"request.{mode} (the tab's request body) missing")
        cfg = build_machine_config(req, req_p.parent, state)
        workers = int(ns.workers or req.get("workers") or _cpus())
        if workers > _cpus():
            raise Refused(f"workers {workers} > cpus of this job {_cpus()}")
    except Refused as e:
        say(f"REFUSED: {e}")
        return 2
    # THE MACHINE BEFORE THE APP: config.py fixes its default path when the
    # package is first imported (``python -m`` already did that), so a process
    # that was started on another config re-executes itself on this one.
    from motor_ai_sim import config as C
    if Path(str(C.DEFAULT_CONFIG_PATH)).resolve() != cfg_p:
        if os.environ.get("_HEADLESS_REEXEC") == "1":
            say(f"REFUSED: the app still reads {C.DEFAULT_CONFIG_PATH}, not {cfg_p}")
            return 2
        if not cfg_p.exists():
            _write_yaml(cfg_p, cfg)
        env = dict(os.environ, MOTOR_AI_SIM_CONFIG=str(cfg_p),
                   FEM_SCAN_WORKERS=str(workers), _HEADLESS_REEXEC="1")
        env.pop("SHARED_ROOT", None)
        sys.stdout.flush()
        os.execve(sys.executable, [sys.executable, "-m", "motor_ai_sim.optimize_headless",
                                   *(argv if argv is not None else sys.argv[1:])], env)
    os.environ["FEM_SCAN_WORKERS"] = str(workers)
    cfg = _refresh_derived(cfg)
    try:
        preset_rec = apply_optimization_preset(req, cfg, mode)
    except Refused as e:
        say(f"REFUSED: {e}")
        return 2
    say("preset: %s v%s%s" % (preset_rec['name'], preset_rec['version'], ' (custom)' if preset_rec['custom'] else ''))
    machine_fp = hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str)
                                .encode()).hexdigest()[:16]
    code_fp = code_fingerprint()
    stamp = state / ".headless_machine.json"
    if stamp.is_file():
        old = json.loads(stamp.read_text(encoding="utf-8"))
        if old.get("machine_fp") != machine_fp:
            say(f"REFUSED: state folder {state} belongs to another machine "
                f"({old.get('machine_fp')} != {machine_fp}); use a new folder")
            return 2
        if old.get("code_fp") != code_fp:
            say(f"REFUSED: state folder {state} was filled by other application code "
                f"({old.get('code_fp')} != {code_fp}); its evals would be replayed "
                f"as answers of this code - use a new folder")
            return 2
    _write_yaml(cfg_p, cfg)
    stamp.write_text(json.dumps({"machine_fp": machine_fp, "code_fp": code_fp,
                                 "request": str(req_p)}, indent=1), encoding="utf-8")
    C.get_config(reload=True)
    from motor_ai_sim.routes import optimization as O
    live = C.get_config()
    bad = [k for k, v in cfg["geometry"].items() if live["geometry"].get(k) != v]
    if Path(str(C.config_path())).resolve() != cfg_p or bad:
        say(f"REFUSED: the app reads {C.config_path()}, keys differing: {bad[:8]}")
        return 2
    say(f"machine {machine_fp}: {cfg_p} (workers {workers}, "
        f"pool {O._scan_worker_count()})")

    try:
        refuse_magnet_temperature(O, req[mode], (req.get("machine") or {}).get("simulation") or {},
                                  mode)
    except Refused as e:
        say(f"REFUSED: {e}")
        return 2
    memo = EvalMemo(O, state / MEMO_FILE, req.get("max_evals") if ns.max_evals is None
                    else ns.max_evals, kind=mode)
    O._subprocess_eval = memo
    say(f"memo: {len(memo.memo)} stored evals, cap {memo.max_evals}")

    stopped = [False]

    def _stop(signum, _frame):
        stopped[0] = True
        say(f"signal {signum}: stopping (checkpoint is written by the worker)")
        cancel_run(O, mode)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        final = (run_sweep if mode == "sweep" else run_descent)(
            O, dict(req[mode]), frozen=list(req.get("frozen") or []), log=say, memo=memo,
            gap_reason=req.get("gap_layers_reason"))
    except Refused as e:
        say(f"REFUSED: {e}")
        return 2
    final.pop("cancel", None)
    # keep the stamp the RUN wrote (digest, wire cell, what the solve verified); the fill record of this runner goes beside it
    final["headless_preset_fill"] = preset_rec
    if not isinstance(final.get("mesh_preset"), dict):
        final["mesh_preset"] = preset_rec
    (req_p.parent / "result.json").write_text(
        json.dumps(O._json_sane(final), indent=1, default=str), encoding="utf-8")
    try:
        import resource
        say("PEAK_RSS_GB of one eval subprocess: %.2f" % (
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0 / 1024.0))
    except (ImportError, OSError):
        pass
    if mode == "sweep":
        say(_sweep_line(final, memo))
    else:
        say(_progress_line(final, memo))
        say(f"final_validation_status={final.get('final_validation_status')} "
            f"apply_eligible={final.get('apply_eligible')} error={final.get('error')}")
    if memo.stopped_for_oom:
        say(f"STOPPED: {memo.oom} eval(s) killed by the memory limit")
        return 4
    if memo.capped or stopped[0]:
        return 3
    return 1 if final.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
