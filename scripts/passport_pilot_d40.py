"""Motor passport v1 — Ø40 pilot, STAGE 1 (2-D, sine, no 3-D, no PWM).

Machines: CIANO14 40 new / L12 (6S) and L20 (12S), owner workspace data
copied read-only into the sandbox.  Spec: docs/PASSPORT_ALGORITHM.md v1.1.

Runs ONLY in the server sandbox container (all FEM).  Sub-commands:

  prepare   M0: resolve + freeze + hash every input; write the per-machine
            sandbox configs and ``<out>/<M>/snapshot.json`` BEFORE any run.
  run       execute the stages of one machine (resumable; one JSON line per
            finished job in ``<out>/<M>/results.jsonl``):
              calib     hot no-load full period (d-axis calibration, psi_PM hot)
              probe     timings + gap-rule check at the rated point
              hot       hot static grid (adaptive MTPA bracket + FW arm + anchors)
              demag     retention probe at every hot grid point
              cold      cold 20 °C set (no-load, MTPA line, FW, short circuit)
              loss      settled loss trajectory (TDM + demag) + mech losses
              checks    off-grid checks, independent torque, time step, duties
  assemble  build the passport record per machine (JSON) + the pilot report.

Layout inside the container:
  /work/src         this branch's src (read-only mount)
  /work/inputs      die40/{die.yaml,L12.yaml,L20.yaml}, ws_motor_config.yaml,
                    materials_library.yaml, bearings_library.yaml (copies)
  /work/shared      SHARED_ROOT = copies of the libraries
  /work/ws_<M>      MOTOR_AI_SIM_CONFIG dir of machine M (sandbox only)
  /work/out/<M>     results
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

MACHINES = {
    # Owner 2026-10-05: L12 = 6S version, L20 = 12S version.
    "L12": {"config": "L12", "rated_duty": "rated", "peak_duty": "peak",
            "version": "6S", "owner_bus_V": [18.0, 22.2, 25.2],
            "owner_rated": {"rpm": 13000.0, "I_arms": 42.78},
            "owner_peak": {"rpm": 14400.0, "I_arms": 48.79}},
    "L20": {"config": "L20", "rated_duty": "rated", "peak_duty": None,
            "version": "12S", "owner_bus_V": [36.0, 44.4, 50.4],
            "owner_rated": {"rpm": 13000.0, "I_arms": 52.55},
            "owner_peak": None,
            # No owner peak duty for L20: the loss grid's top current row and
            # the card's peak row use L12's owner peak/rated current ratio.
            "peak_rule": {"ratio": 48.79 / 42.78,
                          "source": "L12 owner peak/rated current ratio 48.79/42.78 "
                                    "applied to I0 (labelled assumption; the L20 "
                                    "card's peak needs an owner duty)"}},
}
DIE = "CIANO14 40 new"
WORKERS_DEFAULT = 6


def _yaml():
    import yaml
    return yaml


def _load_yaml(p: Path) -> Any:
    with open(p, encoding="utf-8") as fh:
        return _yaml().safe_load(fh)


def _sha(p: Path) -> str:
    from motor_ai_sim.passport_v1.snapshot import file_sha256
    return file_sha256(p)


# ─────────────────────────────────────────────────────────────────────────────
#  prepare (M0)
# ─────────────────────────────────────────────────────────────────────────────

def cmd_prepare(a: argparse.Namespace) -> None:
    work = Path(a.work)
    inp = work / "inputs"
    shared = work / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    for f in ("materials_library.yaml", "bearings_library.yaml"):
        dst = shared / f
        if dst.exists() and _sha(dst) == _sha(inp / f):
            continue
        if dst.exists():
            dst.chmod(0o644)
            dst.unlink()
        shutil.copy2(inp / f, dst)
    ws_tpl = _load_yaml(inp / "ws_motor_config.yaml")
    die = _load_yaml(inp / "die40" / "die.yaml")
    lib = _load_yaml(shared / "materials_library.yaml")
    # Parts a configuration does not name and that are not liner/enamel are
    # taken from the owner's workspace config (what a duty load leaves in
    # place); the L12 rated run's assignment_sig names the same shaft grade.
    fb = dict(ws_tpl.get("materials") or {})
    for M, spec in MACHINES.items():
        os.environ["MOTOR_AI_SIM_CONFIG"] = str(work / "inputs" / "ws_motor_config.yaml")
        from motor_ai_sim.passport_v1 import snapshot as S
        cfg = _load_yaml(inp / "die40" / f"{spec['config']}.yaml")
        src = {
            "die.yaml": _sha(inp / "die40" / "die.yaml"),
            f"{spec['config']}.yaml": _sha(inp / "die40" / f"{spec['config']}.yaml"),
            "ws_motor_config.yaml": _sha(inp / "ws_motor_config.yaml"),
            "materials_library.yaml": _sha(shared / "materials_library.yaml"),
            "bearings_library.yaml": _sha(shared / "bearings_library.yaml"),
            "origin": ("/srv/motres/workspaces/c309c100cd421858/dies/CIANO14 40 new/ "
                       "+ /srv/motres/shared/{materials,bearings}_library.yaml, "
                       "copied read-only 2026-10-05"),
        }
        snap = S.machine_snapshot(
            tag=M, die=die, cfg=cfg, rated_duty=spec["rated_duty"],
            peak_duty=spec["peak_duty"], materials_lib=lib,
            fallback_parts=fb,
            fallback_source="owner workspace motor_config.yaml (left in place by a duty load)",
            source_files=src,
            owner_inputs={"version": spec["version"], "bus_V": spec["owner_bus_V"],
                          "rated": spec["owner_rated"], "peak": spec["owner_peak"],
                          "decided": "owner 2026-10-05 brief",
                          **({"peak_rule": spec["peak_rule"]}
                             if spec.get("peak_rule") else {})})
        # Owner inputs must agree with the stored files (fail closed).
        bat = snap["battery"]
        if [bat["v_min"], bat["v_nom"], bat["v_max"]] != spec["owner_bus_V"]:
            raise SystemExit(f"{M}: battery {bat} disagrees with the owner's bus")
        rd = snap["rated_duty"]
        if (abs(rd["rpm"] - spec["owner_rated"]["rpm"]) > 1e-9
                or abs(rd["current_arms"] - spec["owner_rated"]["I_arms"]) > 1e-9):
            raise SystemExit(f"{M}: rated duty {rd['rpm']} / {rd['current_arms']} "
                             "disagrees with the owner's")
        if spec["owner_peak"]:
            pk = snap["peak_duty"]
            if (abs(pk["rpm"] - spec["owner_peak"]["rpm"]) > 1e-9
                    or abs(pk["current_arms"] - spec["owner_peak"]["I_arms"]) > 1e-9):
                raise SystemExit(f"{M}: peak duty disagrees with the owner's")
        # sandbox machine config
        ws = work / f"ws_{M}"
        ws.mkdir(parents=True, exist_ok=True)
        c = copy.deepcopy(ws_tpl)
        c["geometry"] = {k: v for k, v in snap["geometry"].items()}
        c["winding"] = dict(snap["winding"])
        c["materials"] = {k: v["name"] for k, v in snap["materials"].items()}
        msh = dict(c.get("mesh") or {})
        for k in ("mesh_size_mm", "min_size_mm", "outer_air_factor", "gap_layers",
                  "n_sectors"):
            msh[k] = snap["mesh"][k]
        c["mesh"] = msh
        sim = dict(c.get("simulation") or {})
        sim.update(max_current=rd["current_arms"], current_a=rd["current_arms"],
                   rpm=rd["rpm"], gamma_deg=rd["gamma_deg"],
                   phase_offset_deg=rd["gamma_deg"], coil_temp_c=rd["coil_temp_c"],
                   end_winding_factor=rd["end_winding_factor"],
                   connection=snap["winding"]["connection"],
                   star_delta=snap["winding"].get("star_delta", "star"),
                   mode="motor", daxis_deg=None, drive="current",
                   steps_per_period=rd["steps_per_period"])
        c["simulation"] = sim
        with open(ws / "motor_config.yaml", "w", encoding="utf-8") as fh:
            _yaml().safe_dump(c, fh, sort_keys=False, allow_unicode=True)
        snap["sandbox_config_sha256"] = _sha(ws / "motor_config.yaml")
        snap["snapshot_sha256"] = S.snapshot_hash(snap)
        od = work / "out" / M
        od.mkdir(parents=True, exist_ok=True)
        with open(od / "snapshot.json", "w", encoding="utf-8") as fh:
            json.dump(snap, fh, indent=1, default=str)
        print(f"{M}: snapshot {snap['snapshot_sha256'][:16]} sigs "
              + " ".join(f"{k}={v[:10]}" for k, v in snap["signatures"].items()))


# ─────────────────────────────────────────────────────────────────────────────
#  run — the job pool
# ─────────────────────────────────────────────────────────────────────────────

class Runner:
    """Process pool over one machine's jobs.  Spawned single-thread workers
    (env inherited); results appended as one JSON line per job; resumable."""

    def __init__(self, od: Path, snap: Mapping[str, Any], workers: int):
        self.od = od
        self.snap = snap
        self.workers = int(workers)
        self.res_path = od / "results.jsonl"
        self.done: Dict[str, Dict[str, Any]] = {}
        if self.res_path.exists():
            for ln in open(self.res_path, encoding="utf-8"):
                try:
                    r = json.loads(ln)
                except Exception:            # noqa: BLE001
                    continue
                if r.get("ok"):
                    self.done[r["id"]] = r
        self._pool = None

    def pool(self):
        if self._pool is None:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            from motor_ai_sim.passport_v1.jobs import worker_init
            mats = {k: v["name"] for k, v in self.snap["materials"].items()}
            self._pool = ProcessPoolExecutor(
                max_workers=self.workers, mp_context=mp.get_context("spawn"),
                initializer=worker_init, initargs=(mats,))
        return self._pool

    def close(self):
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def run(self, jobs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Solve every job not done yet; return {id: record} for ALL jobs."""
        from motor_ai_sim.passport_v1.jobs import run_job
        todo = [j for j in jobs if j["id"] not in self.done]
        if todo:
            stop = self.od / "stop"
            futs = {}
            pool = self.pool()
            for j in todo:
                futs[pool.submit(run_job, j, str(self.od))] = j
            from concurrent.futures import as_completed
            for f in as_completed(futs):
                j = futs[f]
                try:
                    rec = f.result()
                except Exception as e:      # noqa: BLE001 — pool crash
                    rec = {"id": j["id"], "ok": False, "meta": j.get("meta"),
                           "error": f"pool: {type(e).__name__}: {e}"}
                with open(self.res_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
                if rec.get("ok"):
                    self.done[rec["id"]] = rec
                print(f"  {rec['id']}: ok={rec.get('ok')} {rec.get('wall_s', 0):.1f} s"
                      + ("" if rec.get("ok") else "  " + str(rec.get("error"))[:300]),
                      flush=True)
            if stop.exists():
                raise SystemExit("stop file present")
        out = {}
        for j in jobs:
            if j["id"] in self.done:
                out[j["id"]] = self.done[j["id"]]
        return out


def _snap(od: Path) -> Dict[str, Any]:
    with open(od / "snapshot.json", encoding="utf-8") as fh:
        return json.load(fh)


def _state(od: Path) -> Dict[str, Any]:
    p = od / "state.json"
    if p.exists():
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _save_state(od: Path, st: Mapping[str, Any]) -> None:
    tmp = od / "state.json.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1, default=str)
    os.replace(tmp, od / "state.json")


def _baseline(work: Path, a: argparse.Namespace) -> Dict[str, Any]:
    """B9 record of THIS invocation: code commit + dirty flag (written by the
    sync step into /work/code/COMMIT), source-tree digest, image digest,
    mounts, env (threads / BLAS / SB_*), library versions, mesher."""
    from motor_ai_sim.passport_v1.snapshot import baseline_block
    cf = work / "code" / "COMMIT"
    commit, dirty = "unknown", True
    if cf.exists():
        parts = cf.read_text(encoding="utf-8").split()
        commit = parts[0] if parts else "unknown"
        dirty = (len(parts) > 1 and parts[1] == "dirty")
    b = baseline_block(code_commit=commit, code_dirty=dirty,
                       src_root=work / "code" / "src",
                       image_digest=os.environ.get("PASSPORT_IMAGE_DIGEST", "unknown"),
                       mounts={"/work": "/opt/motres/compute/passport-d40-20261005/work"},
                       extra={"invocation": {"cmd": a.cmd,
                                             "machine": getattr(a, "machine", None),
                                             "stages": getattr(a, "stages", None),
                                             "workers": getattr(a, "workers", None),
                                             "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}})
    return b


def cmd_run(a: argparse.Namespace) -> None:
    work = Path(a.work)
    M = a.machine
    od = work / "out" / M
    snap = _snap(od)
    st = _state(od)
    from motor_ai_sim.passport_v1 import stages
    st.setdefault("baseline_runs", []).append(_baseline(work, a))
    _save_state(od, st)
    R = Runner(od, snap, a.workers)
    t0 = time.time()
    try:
        for stage in a.stages.split(","):
            ts = time.time()
            print(f"== {M} stage {stage}", flush=True)
            fn = getattr(stages, "stage_" + stage)
            fn(R, snap, st)
            _save_state(od, st)
            st.setdefault("stage_wall_s", {})[stage] = (
                st.get("stage_wall_s", {}).get(stage, 0.0) + time.time() - ts)
            _save_state(od, st)
    finally:
        R.close()
        print(f"== {M} done in {time.time() - t0:.0f} s", flush=True)


def cmd_assemble(a: argparse.Namespace) -> None:
    from motor_ai_sim.passport_v1 import card
    work = Path(a.work)
    recs = {}
    for M in MACHINES:
        od = work / "out" / M
        if not (od / "snapshot.json").exists():
            continue
        st = _state(od)
        if "checks_plan" not in st:
            print(f"{M}: stages not complete — skipped")
            continue
        st["baseline_assemble"] = _baseline(work, a)
        recs[M] = card.build_record(od, _snap(od), st, machine_meta=MACHINES[M])
        with open(od / f"passport_{M}.json", "w", encoding="utf-8") as fh:
            json.dump(recs[M], fh, indent=1, default=str)
        print(f"{M}: record written")
    from motor_ai_sim.passport_v1 import report_md
    budget = {}
    for M in recs:
        od = work / "out" / M
        n_ok, n_fail, cpu = 0, 0, 0.0
        for ln in open(od / "results.jsonl", encoding="utf-8"):
            r = json.loads(ln)
            cpu += float(r.get("wall_s") or 0.0)
            if r.get("ok"):
                n_ok += 1
            else:
                n_fail += 1
        st = _state(od)
        budget[M] = {"runs": n_ok, "failed": n_fail, "fem_cpu_h": cpu / 3600.0,
                     "container_wall_h": sum((st.get("stage_wall_s") or {}).values()) / 3600.0}
    md = report_md.render(recs, budget=budget)
    with open(work / "out" / "PASSPORT_PILOT_D40_STAGE1_2026-10-05.md", "w",
              encoding="utf-8") as fh:
        fh.write(md)
    with open(work / "out" / "budget.json", "w", encoding="utf-8") as fh:
        json.dump(budget, fh, indent=1)
    print("report written", budget)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default="/work")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    r = sub.add_parser("run")
    r.add_argument("--machine", required=True, choices=sorted(MACHINES))
    r.add_argument("--stages", required=True)
    r.add_argument("--workers", type=int, default=WORKERS_DEFAULT)
    sub.add_parser("assemble")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        os.environ["MOTOR_AI_SIM_CONFIG"] = str(Path(a.work) / f"ws_{a.machine}" / "motor_config.yaml")
    {"prepare": cmd_prepare, "run": cmd_run, "assemble": cmd_assemble}[a.cmd](a)


if __name__ == "__main__":
    main()
