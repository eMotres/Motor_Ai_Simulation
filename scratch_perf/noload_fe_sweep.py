"""Computed NO-LOAD (I = 0) loss curve of the active 150 mm 24s/28p machine.

Runs the P2 sliding-band transient in-process at I_phase_rms = 0 for a ladder of
speeds spanning the measured range and beyond, and records the iron loss
(P_fe_avg_W, with its hyst/eddy/excess breakdown) plus the rotor eddy losses
(magnet + shaft), which are part of any MEASURED free-run power and therefore
must be on the table next to the iron.

Coarse-but-honest settings = the Simulation-tab defaults (mesh 4 mm, 3 gap
layers, 4 sectors, geo_mesh + iron_template, P2), with enough steps per
electrical period to resolve the slot dB/dt series.

Usage:  python scratch_perf/noload_fe_sweep.py [steps] [rpm,rpm,...]
Appends one JSON record per speed to scratch_perf/noload_fe_sweep.json.
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
for _v in ("MKL_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 48
RPMS = [float(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else \
    [1000.0, 1500.0, 2000.0, 2500.0, 3000.0, 4000.0]
OUT = ROOT / "scratch_perf" / f"noload_fe_sweep_{STEPS}.json"

from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

rows = []
if OUT.exists():
    try:
        rows = json.loads(OUT.read_text())
    except Exception:
        rows = []
done = {round(float(r["rpm"]), 3) for r in rows}

for rpm in RPMS:
    if round(rpm, 3) in done:
        print(f"[skip] {rpm} rpm already computed", flush=True)
        continue
    t0 = time.time()
    print(f"[run ] {rpm} rpm, I=0, {STEPS} steps ...", flush=True)
    d = fem_transient_sliding_band(
        n_steps_per_period=STEPS,
        n_periods=1.0,
        gamma_deg=0.0,
        I_phase_rms=0.0,             # ← FREE RUN: no stator current at all
        rpm=rpm,
        mesh_size_mm=4.0,
        min_size_mm=0.3,
        outer_air_factor=1.3,
        gap_layers=3.0,
        n_sectors=4,
        coil_temp_c=120.0,
        eddy=False,
        rotor_eddy=True,             # field-based magnet + shaft eddy (post-process)
        demag=False,
        iron_template=True,
        geo_mesh=True,
        element_order=2,
    )
    rec = {
        "rpm": rpm,
        "steps": STEPS,
        "f_elec_Hz": d.get("f_elec_Hz") or d.get("frequency_Hz"),
        "P_fe_avg_W": d.get("P_fe_avg_W"),
        "P_fe_terms": d.get("P_fe_terms"),
        # resistance-limited post-process (rotor_eddy=True path) — these ARE in
        # any measured free-run power, so they belong beside the iron
        "P_mag_avg_W": d.get("P_mag_honest_W"),
        "P_shaft_avg_W": d.get("P_shaft_honest_W"),
        "P_loss_total_avg_W": d.get("P_loss_total_avg_W"),
        "T_avg_Nm": d.get("T_avg_Nm"),
        "V_peak_V": d.get("V_peak"),
        "picard_converged": d.get("picard_converged"),
        "picard_resid_max": d.get("picard_resid_max"),
        "wall_s": round(time.time() - t0, 1),
    }
    # keep whatever else looks like an average power so nothing is lost
    for k, v in d.items():
        if k.startswith("P_") and k.endswith("_W") and k not in rec \
                and isinstance(v, (int, float)):
            rec[k] = v
    rows.append(rec)
    OUT.write_text(json.dumps(rows, indent=2))
    print("[done] " + json.dumps({k: rec[k] for k in
          ("rpm", "f_elec_Hz", "P_fe_avg_W", "P_mag_avg_W", "P_shaft_avg_W",
           "wall_s")}), flush=True)

print("WROTE", OUT)
