import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
def run(over,g=0.0):
    r=fem_transient_sliding_band(n_steps_per_period=72,n_periods=1.0,gamma_deg=float(g),
        I_phase_rms=85.0,mesh_size_mm=2.0,min_size_mm=0.1,n_sectors=4,
        nonlinear_iterations=14,coil_temp_c=120.0,geo_override=(over or None))
    return np.asarray(r.get("T_em_Nm") or r.get("torque_Nm"),float)
REC=dict(magnet_fill_up=0.56,magnet_up_gap=2.5,rotor_fill_r=2.0,magnet_fill_down=0.92); GAM=-15.0
MINR=dict(REC); MINR_G=0.0
print("baseline...",flush=True); Tb=run(None,0.0)
print("recommended...",flush=True); Tr=run(REC,GAM)
print("min-ripple...",flush=True); Tm=run(MINR,MINR_G)
def st(T):
    a=float(T.mean()); return a,100.0*(T.max()-T.min())/a
a0,r0=st(Tb); a1,r1=st(Tr); a2,r2=st(Tm)
th=lambda T: np.linspace(0,360,len(T),endpoint=False)
fig,ax=plt.subplots(figsize=(13,6))
ax.plot(th(Tb),Tb,color='#ef4444',lw=1.8,label=f'BASELINE         T_avg={a0:.2f}  ripple={r0:.1f}%')
ax.plot(th(Tm),Tm,color='#3b82f6',lw=1.6,ls='--',label=f'min-ripple (gamma=0)   T_avg={a2:.2f}  ripple={r2:.1f}%')
ax.plot(th(Tr),Tr,color='#10b981',lw=2.2,label=f'RECOMMENDED (gamma=-15) T_avg={a1:.2f}  ripple={r1:.1f}%')
ax.axhline(a0,color='#ef4444',ls=':',lw=.8); ax.axhline(a1,color='#10b981',ls=':',lw=.8)
ax.set_xlabel('electrical angle [deg]'); ax.set_ylabel('Torque [N.m]')
ax.set_title('Torque-ripple optimization (72-step sliding-band FEM, 85 Arms, 3950 rpm)')
ax.legend(loc='lower center'); ax.grid(alpha=.3); plt.tight_layout()
plt.savefig("ripple_before_after.png",dpi=120)
base=json.load(open("_ripple_baseline_params.json"))["geometry"]
print("\n=== RECOMMENDED design (no extra magnet) ===")
for k in ["magnet_fill_up","magnet_up_gap","magnet_fill_down","rotor_fill_r"]:
    print(f"  {k:18s} {base.get(k)} -> {REC[k]}")
print(f"  {'phase_offset_deg(gamma)':18s} 0.0 -> {GAM}")
print(f"\n  T_avg   {a0:.3f} -> {a1:.3f} N.m  ({100*(a1-a0)/a0:+.1f}%)")
print(f"  ripple  {r0:.2f} -> {r1:.2f} %     ({r1-r0:+.2f} pp,  {100*(1-r1/r0):.0f}% reduction)")
json.dump({"baseline":{"Tavg":a0,"ripple":r0},
           "recommended":{"Tavg":a1,"ripple":r1,"gamma":GAM,"over":REC},
           "min_ripple":{"Tavg":a2,"ripple":r2,"gamma":0.0,"over":MINR}},
          open("_ripple_final.json","w"),indent=2)
print("saved ripple_before_after.png",flush=True)
