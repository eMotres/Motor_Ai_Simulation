"""Web-driven, THERMALLY-GENTLE PINN training (runs in the WSL venv: CUDA +
physicsnemo-sym).  Trains in small CHUNKS with cool-down pauses and a GPU-temp
guard so a laptop GPU never runs flat-out long enough to overheat.  After each
chunk it evaluates the torque and writes a live progress JSON the Windows backend
serves to the web UI.

Launched by the backend:
  wsl.exe bash -lc "source ~/motor_ai_sim_env/.venv/bin/activate && \
     cd /mnt/c/.../motor_ai_sim && LD_LIBRARY_PATH=/usr/lib/wsl/lib \
     PYTHONPATH=src python scripts/pinn_train_web.py TOTAL_STEPS"
"""
import sys, os, json, time, logging, subprocess
logging.basicConfig(level=logging.WARNING, format="%(message)s")  # quiet physicsnemo

from motor_ai_sim.simulation.solver_2d import MagnetostaticsSolver2D, SimConfig
from motor_ai_sim.simulation.geometry_2d import params_from_config

ROOT     = "/mnt/c/Users/vadim/Projects/motor_ai_sim"
PROGRESS = os.path.join(ROOT, "pinn_progress.json")
RESULT   = os.path.join(ROOT, "pinn_result.json")

# ── thermal guard ─────────────────────────────────────────────────────────────
CHUNK        = 100     # steps per burst (short → guard can act sooner)
COOLDOWN_S   = 5.0     # always pause between bursts
# The GPU targets ~87 C (throttles there, undamaged); the laptop froze at ~90 C.
# So pause a touch under the GPU target, leaving ~5 C margin to the freeze point.
TEMP_PAUSE_C = 84      # if GPU hotter than this, wait until it cools
TEMP_RESUME_C = 78     # resume once back below this
TORQUE_FEM   = 24.87   # FEM baseline for the live comparison


def gpu_temp():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            timeout=5).decode().strip().splitlines()[0]
        return int(out)
    except Exception:
        return -1


def write_progress(d):
    tmp = PROGRESS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, PROGRESS)


MAX_COOL_WAIT = 600   # s — give up (abort) rather than ever train a hot GPU


def wait_until_cool(done, total, hist):
    """Block until the GPU is below the resume threshold, writing a 'cooling'
    progress so the UI shows WHY it's paused.  Returns True if cool enough to
    proceed, or False to ABORT (never train a hot GPU)."""
    t = gpu_temp()
    waited = 0
    while t >= TEMP_PAUSE_C:
        write_progress({"running": True, "step": done, "max_steps": total,
                        "torque_pinn": hist[-1]["torque"] if hist else 0.0,
                        "torque_fem": TORQUE_FEM,
                        "b_max": hist[-1].get("b_max", 0.0) if hist else 0.0,
                        "gpu_temp": t, "cooling": True, "history": hist})
        if waited >= MAX_COOL_WAIT:
            return False
        time.sleep(5); waited += 5; t = gpu_temp()
    return True


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    cfg = SimConfig.from_motor_config()
    cfg.layer_size = 64          # gentle: smaller net
    cfg.num_layers = 4
    cfg.batch_size_interior = 1024
    cfg.batch_size_boundary = 512
    gp = params_from_config()

    hist = []
    t0 = time.time()
    write_progress({"running": True, "step": 0, "max_steps": total, "torque_pinn": 0.0,
                    "torque_fem": TORQUE_FEM, "b_max": 0.0, "gpu_temp": gpu_temp(),
                    "history": hist})
    try:
        os.remove(RESULT)
    except OSError:
        pass

    def abort():
        write_progress({"running": False, "done": False, "aborted": True,
                        "reason": f"GPU stayed >= {TEMP_PAUSE_C} C — cool it (fans/undervolt) and retry",
                        "step": 0, "max_steps": total, "gpu_temp": gpu_temp(),
                        "torque_pinn": hist[-1]["torque"] if hist else 0.0,
                        "torque_fem": TORQUE_FEM, "history": hist})
        print("PINN_ABORT gpu_too_hot", flush=True)

    # don't even BUILD the domain (touches the GPU) while it's hot
    if not wait_until_cool(0, total, hist):
        abort(); return

    solver = MagnetostaticsSolver2D(cfg, gp)
    solver.build()               # assemble domain once
    from physicsnemo.sym.solver import Solver

    done = 0
    while done < total:
        # thermal guard — ABORT (never train) if the GPU won't cool below resume
        if not wait_until_cool(done, total, hist):
            abort(); return

        target = min(done + CHUNK, total)
        cfg.max_steps = target
        s = Solver(cfg=solver._make_modulus_cfg(), domain=solver._domain)
        s.solve()                                  # resumes from network_dir checkpoint
        done = target

        res = solver._postprocess(s)
        tq = float(res.get("torque_Nm") or 0.0)
        bmax = float(res.get("B_max_T") or 0.0)
        hist.append({"step": done, "torque": round(tq, 3), "b_max": round(bmax, 3)})
        write_progress({"running": True, "step": done, "max_steps": total,
                        "torque_pinn": round(tq, 3), "torque_fem": TORQUE_FEM,
                        "b_max": round(bmax, 3), "gpu_temp": gpu_temp(),
                        "sec": round(time.time() - t0), "history": hist})
        time.sleep(COOLDOWN_S)                      # let the GPU breathe

    out = {"torque_Nm": tq, "B_max_T": bmax, "B_mean_T": float(res.get("B_mean_T") or 0.0),
           "P_cu_W": float(res.get("P_cu_total_W") or 0.0),
           "efficiency_pct": res.get("efficiency_pct"), "steps": total}
    with open(RESULT, "w") as f:
        json.dump(out, f)
    write_progress({"running": False, "done": True, "step": total, "max_steps": total,
                    "torque_pinn": round(tq, 3), "torque_fem": TORQUE_FEM,
                    "b_max": round(bmax, 3), "gpu_temp": gpu_temp(),
                    "sec": round(time.time() - t0), "history": hist})
    print("PINN_DONE " + json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
