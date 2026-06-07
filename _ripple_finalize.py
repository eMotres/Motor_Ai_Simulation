"""Finalize: take the round-3 best-balance design, render before/after torque
waveform + param diff vs the recorded baseline.  Does NOT change config — just
reports; applying is a separate explicit step."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

def run(over, gamma=0.0):
    r=fem_transient_sliding_band(n_steps_per_period=72,n_periods=1.0,gamma_deg=float(gamma),
        I_phase_rms=85.0,mesh_size_mm=2.0,min_size_mm=0.1,n_sectors=4,
        nonlinear_iterations=14,coil_temp_c=120.0,geo_override=(over or None))
    T=np.asarray(r.get("T_em_Nm") or r.get("torque_Nm"),float)
    return T

res=json.load(open("_ripple_opt3_results.json"))
best=res["best_balance"]; over=best["over"]; gamma=best["gamma"]
base_params=json.load(open("_ripple_baseline_params.json"))["geometry"]

print("Running baseline + best waveforms...", flush=True)
Tb=run(None,0.0); To=run(over,gamma)
def stat(T):
    Tavg=float(T.mean()); rip=100.0*(T.max()-T.min())/Tavg
    return Tavg,rip,float(T.min()),float(T.max())
Ta0,Rp0,_,_=stat(Tb); Ta1,Rp1,_,_=stat(To)

th=np.linspace(0,360,len(Tb),endpoint=False)
fig,ax=plt.subplots(figsize=(12,6))
ax.plot(th,Tb,'-',color='#ef4444',lw=1.8,label=f'BASELINE  T_avg={Ta0:.2f}  ripple={Rp0:.1f}%')
ax.plot(np.linspace(0,360,len(To),endpoint=False),To,'-',color='#10b981',lw=2.0,
        label=f'OPTIMIZED  T_avg={Ta1:.2f}  ripple={Rp1:.1f}%  (gamma={gamma})')
ax.axhline(Ta0,color='#ef4444',ls=':',lw=0.8); ax.axhline(Ta1,color='#10b981',ls=':',lw=0.8)
ax.set_xlabel('electrical angle [deg]'); ax.set_ylabel('Torque [N.m]')
ax.set_title('Torque ripple optimization — baseline vs optimized (72-step sliding-band FEM)')
ax.legend(loc='lower center'); ax.grid(alpha=0.3)
plt.tight_layout(); plt.savefig("ripple_before_after.png",dpi=120)
print("saved ripple_before_after.png", flush=True)

# Param diff table
keys=set(over)|{"magnet_fill_up","magnet_up_gap","magnet_fill_down","rotor_fill_r","magnet_height","air_gap","phase_offset_deg"}
print("\n=== PARAMETER CHANGES (baseline -> optimized) ===")
for k in sorted(keys):
    b=base_params.get(k); o=over.get(k, b)
    if k=="phase_offset_deg": b=0.0; o=gamma
    mark=" <==" if (o is not None and b is not None and abs(float(o)-float(b))>1e-9) else ""
    print(f"  {k:20s} {str(b):>8} -> {str(o):>8}{mark}")
print(f"\n  T_avg   {Ta0:.3f} -> {Ta1:.3f} N.m  ({100*(Ta1-Ta0)/Ta0:+.1f}%)")
print(f"  ripple  {Rp0:.2f} -> {Rp1:.2f} %      ({Rp1-Rp0:+.2f} pp)")
json.dump({"baseline":{"Tavg":Ta0,"ripple":Rp0},"optimized":{"Tavg":Ta1,"ripple":Rp1,"gamma":gamma,"over":over}},
          open("_ripple_final.json","w"),indent=2)
