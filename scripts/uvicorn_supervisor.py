"""Tiny supervisor: keeps uvicorn alive by relaunching it on every exit.

Windows-only quirk: scikit-fem + gmsh + llvmlite occasionally JIT a section
of code that crashes the whole process with
   "LLVM ERROR: IMAGE_REL_AMD64_ADDR32NB relocation requires an ordered
    section layout"
This bypasses Python exception handling, so the only safe recovery is a
process restart.  Run this supervisor instead of uvicorn directly:

    python scripts/uvicorn_supervisor.py

The script prints "[supervisor] restart N (exit=...)" on each respawn and
re-uses the same port 8001 the rest of the app expects.
"""
import os, sys, time, subprocess

PORT = int(os.environ.get("UVICORN_PORT", "8001"))
CMD = [
    sys.executable, "-m", "uvicorn",
    "motor_ai_sim.api:app",
    "--port", str(PORT),
    "--host", "127.0.0.1",
    "--log-level", "info",
]

def main() -> None:
    print(f"[supervisor] starting uvicorn on :{PORT}", flush=True)
    restarts = 0
    while True:
        t0 = time.time()
        try:
            proc = subprocess.Popen(CMD)
            exit_code = proc.wait()
        except KeyboardInterrupt:
            print("[supervisor] Ctrl-C — exiting", flush=True)
            try: proc.terminate()
            except Exception: pass
            return
        except Exception as e:
            print(f"[supervisor] spawn failed: {e}", flush=True)
            exit_code = -1

        dur = time.time() - t0
        restarts += 1
        print(f"[supervisor] uvicorn exited code={exit_code} after {dur:.1f}s — "
              f"restart #{restarts} in 1.5 s", flush=True)
        # if the server keeps dying instantly something else is wrong;
        # back off a bit so we don't spin at 100 % CPU.
        time.sleep(1.5 if dur > 5 else 5.0)


if __name__ == "__main__":
    main()
