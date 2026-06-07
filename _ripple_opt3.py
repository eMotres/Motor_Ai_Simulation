"""Round 3 — combined objective: MAX torque at MIN ripple.
FoM = T_avg - 0.12*ripple_pp  (torque-leaning balance; 1 N.m ~= 8pp ripple).
Adds torque levers: gamma (MTPA, the big one), magnet_height, air_gap; then
re-balances the ripple levers at the higher-torque operating point.
Tracks the full Pareto (T_avg vs ripple) and reports min-ripple / max-torque /
best-FoM designs."""
import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
LOG=open("_ripple_opt_log.txt","a",buffering=1)
def out(s): print(s,flush=True); LOG.write(s+"\n")
ALL=[]   # (Tavg, ripple, gamma, over, tag)
def ev(over, gamma=0.0):
    t0=time.time()
    r=fem_transient_sliding_band(n_steps_per_period=72,n_periods=1.0,gamma_deg=float(gamma),
        I_phase_rms=85.0,mesh_size_mm=2.0,min_size_mm=0.1,n_sectors=4,
        nonlinear_iterations=14,coil_temp_c=120.0,geo_override=(over or None))
    T=np.asarray(r.get("T_em_Nm") or r.get("torque_Nm"),float)
    Tavg=float(T.mean()); rip=100.0*(T.max()-T.min())/Tavg if Tavg else 1e9
    return Tavg,rip,round(time.time()-t0,1)
def fom(Ta,Rp): return Ta - 0.12*Rp

# Base = round-2 best if available else round-1 best
try: base=json.load(open("_ripple_opt2_results.json"))["best_over"]
except Exception: base={"magnet_fill_up":0.52,"magnet_up_gap":2.5,"rotor_fill_r":2.0}
bg=0.0
bT,bR,dt=ev(base,bg)
ALL.append((bT,bR,bg,dict(base),"r3-start"))
out(f"\n==== ROUND 3 (max torque @ min ripple) ====  base={json.dumps(base)} gamma={bg}")
out(f"   start: T_avg={bT:.3f} ripple={bR:.2f}% FoM={fom(bT,bR):.3f} ({dt}s)\n")
bF=fom(bT,bR)

def sweep_geo(pn, vals):
    global base,bT,bR,bF,bg
    out(f"-- R3 sweep {pn} (best={base.get(pn,'cfg')}) FoM={bF:.3f} --")
    loc=None
    for v in vals:
        over=dict(base); over[pn]=v
        try: Ta,Rp,dt=ev(over,bg)
        except Exception as e: out(f"   {pn}={v}: FAIL {str(e)[:50]}"); continue
        F=fom(Ta,Rp); ALL.append((Ta,Rp,bg,dict(over),f"{pn}={v}"))
        out(f"   {pn}={v:<6} T_avg={Ta:6.3f} ripple={Rp:6.2f}% FoM={F:6.3f} ({dt}s)")
        if F > (loc[3] if loc else bF) + 0.02: loc=(pn,v,Ta,Rp,F) if False else (pn,v,Ta,Rp,F)
        if loc is None or F>loc[4]: loc=(pn,v,Ta,Rp,F)
    if loc and loc[4]>bF+0.02:
        pn,v,Ta,Rp,F=loc; base[pn]=v; bT,bR,bF=Ta,Rp,F
        out(f"   -> adopt {pn}={v}  FoM={bF:.3f} (T_avg={bT:.3f} ripple={bR:.2f}%)\n")
    else: out(f"   -> keep {pn}  FoM={bF:.3f}\n")

# 1) gamma MTPA (torque lever, no remesh)
out("-- R3 sweep gamma (MTPA — max torque) --")
gloc=None
for g in [-30,-25,-20,-15,-10,-5,0,5]:
    try: Ta,Rp,dt=ev(base,g)
    except Exception as e: out(f"   gamma={g}: FAIL {str(e)[:50]}"); continue
    F=fom(Ta,Rp); ALL.append((Ta,Rp,g,dict(base),f"gamma={g}"))
    out(f"   gamma={g:<4} T_avg={Ta:6.3f} ripple={Rp:6.2f}% FoM={F:6.3f} ({dt}s)")
    if gloc is None or F>gloc[2]: gloc=(g,Ta,F,Rp)
if gloc and gloc[2]>bF+0.02:
    bg,bT,bF,bR=gloc[0],gloc[1],gloc[2],gloc[3]; out(f"   -> adopt gamma={bg} FoM={bF:.3f} (T_avg={bT:.3f} ripple={bR:.2f}%)\n")
else: out(f"   -> keep gamma={bg}\n")

# 2) torque-magnitude levers
sweep_geo("magnet_height",[16,17,18])
sweep_geo("air_gap",[0.45,0.5])
# 3) re-balance ripple levers at the new operating point
sweep_geo("magnet_fill_up",[0.48,0.52,0.56])
sweep_geo("magnet_up_gap",[2.4,2.5,2.6])

# Pareto analysis
ALL2=[a for a in ALL if a[0]>0]
minrip=min(ALL2,key=lambda a:a[1]); maxtq=max(ALL2,key=lambda a:a[0]); bestF=max(ALL2,key=lambda a:fom(a[0],a[1]))
def show(tag,a): out(f"   {tag:12s} T_avg={a[0]:.3f}  ripple={a[1]:.2f}%  gamma={a[2]}  FoM={fom(a[0],a[1]):.3f}  [{a[4]}]  {json.dumps(a[3])}")
out("\n==== ROUND 3 DONE ====")
show("min-ripple",minrip); show("max-torque",maxtq); show("best-balance",bestF)
json.dump({"best_balance":{"Tavg":bestF[0],"ripple":bestF[1],"gamma":bestF[2],"over":bestF[3]},
           "min_ripple":{"Tavg":minrip[0],"ripple":minrip[1],"gamma":minrip[2],"over":minrip[3]},
           "max_torque":{"Tavg":maxtq[0],"ripple":maxtq[1],"gamma":maxtq[2],"over":maxtq[3]},
           "all":[{"Tavg":a[0],"ripple":a[1],"gamma":a[2],"over":a[3],"tag":a[4]} for a in ALL2]},
          open("_ripple_opt3_results.json","w"),indent=2)
out("FINAL(best-balance): "+json.dumps(bestF[3])+f"  gamma={bestF[2]}")
