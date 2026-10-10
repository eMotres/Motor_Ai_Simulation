"""AC-copper matrix: coupled FEM vs tuner (NOT committed).  usage: acm.py res_m40.jsonl res_m40ac.jsonl card.json"""
import json, subprocess, sys, os, math
base = [json.loads(l) for l in open(sys.argv[1]) if '"m40_base_duty"' in l][0]
rows = [base] + [json.loads(l) for l in open(sys.argv[2])]
card = sys.argv[3]
cs = [{"id": r["id"], "L": r["meta"]["L"], "N": r["meta"]["N"], "h": r["meta"]["h"],
       "I": r["meta"]["I"], "rpm": r["meta"]["rpm"]} for r in rows if r.get("ok")]
CF = os.path.abspath("acm_corners.json"); json.dump(cs, open(CF, "w"))
here = os.path.dirname(os.path.abspath(__file__))
out = subprocess.run(["node", os.path.join(here, "tuner_eval.mjs"), card, CF],
                     capture_output=True, text=True, cwd=here).stdout
tun = {t["id"]: t for t in json.loads(out)}
def fem(r):
    x = r["r"]; I = r["meta"]["I"]; R = x.get("s_R_phase_eq_star_ohm") or x["R_phase_ohm"]
    dc = 3 * I * I * R; ac = x["P_cu_W"] - dc
    rot = (x.get("s_P_solid_W") or 0)
    tot = x["P_cu_W"] + x["P_fe_W"] + rot
    return dict(T=abs(x["T_avg_Nm"]), dc=dc, ac=ac, acs=x.get("P_cu_ac_solve_W"), fe=x["P_fe_W"], rot=rot, tot=tot)
fb = fem(base); tb = tun[base["id"]]
print("| point (h, N, I, rpm) | FEM T | FEM AC [W] | tuner AC [W] | AC err | AC law err | FEM rotor [W] | FEM total | tuner total | total err | total law err |")
for r in rows:
    if not r.get("ok") or r["id"] not in tun: continue
    f = fem(r); t = tun[r["id"]]; m = r["meta"]
    tac = t["Pcu"] - 3 * m["I"] ** 2 * t["R"]
    ttot = t["Ploss"] - t["Pmag"] + f["rot"]   # same rotor loss both sides: copper+iron test
    print(f"| {m['h']}, {m['N']}, {m['I']:.1f}, {m['rpm']:.0f} | {f['T']:.4f} | {f['ac']:.2f} | {tac:.2f} | {100*(tac/f['ac']-1):+.0f} % | "
          f"{100*((tac/(tb['Pcu']-3*base['meta']['I']**2*tb['R']))/(f['ac']/fb['ac'])-1):+.0f} % | {f['rot']:.2f} | {f['tot']:.2f} | {t['Ploss']:.2f} | "
          f"{100*(t['Ploss']/f['tot']-1):+.1f} % | {100*((t['Ploss']/tb['Ploss'])/(f['tot']/fb['tot'])-1):+.1f} % |")
