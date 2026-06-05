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
CHUNK        = 150     # steps per burst
COOLDOWN_S   = 4.0     # always pause between bursts
TEMP_PAUSE_C = 82      # if GPU hotter than this, wait until it cools
TEMP_RESUME_C = 72     # resume once back below this
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


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    cfg = SimConfig.from_motor_config()
    cfg.layer_size = 64          # gentle: smaller net
    cfg.num_layers = 4
    cfg.batch_size_interior = 1024
    cfg.batch_size_boundary = 512
    gp = params_from_config()

    solver = MagnetostaticsSolver2D(cfg, gp)
    solver.build()               # assemble domain once

    from physicsnemo.sym.solver import Solver
    hist = []
    t0 = time.time()
    write_progress({"running": True, "step": 0, "max_steps": total, "torque_pinn": 0.0,
                    "torque_fem": TORQUE_FEM, "b_max": 0.0, "gpu_temp": gpu_temp(),
                    "history": hist})
    try:
        os.remove(RESULT)
    except OSError:
        pass

    done = 0
    while done < total:
        # ── thermal guard: wait if the GPU is too hot ────────────────────────
        t = gpu_temp()
        waited = 0
        while t >= TEMP_PAUSE_C and waited < 120:
            write_progress({"running": True, "step": done, "max_steps": total,
                            "torque_pinn": hist[-1]["torque"] if hist else 0.0,
                            "torque_fem": TORQUE_FEM, "b_max": hist[-1].get("b_max", 0.0) if hist else 0.0,
                            "gpu_temp": t, "cooling": True, "history": hist})
            time.sleep(5); waited += 5; t = gpu_temp()
            if t <= TEMP_RESUME_C:
                break

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
