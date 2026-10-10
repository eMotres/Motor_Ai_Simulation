"""FEM vs tuner comparison table (NOT committed).
usage: python analyze.py RES.jsonl TUNER.json DELTA(0/1)"""
import json
import math
import sys

res = {}
for ln in open(sys.argv[1], encoding="utf-8"):
    d = json.loads(ln)
    res[d["id"]] = d
tun = {t["id"]: t for t in json.load(open(sys.argv[2], encoding="utf-8"))}
delta = sys.argv[3] == "1"
kV = 1 / math.sqrt(3) if delta else 1.0


def fem(jid):
    d = res.get(jid)
    if not d or not d.get("ok"):
        return None
    r = d["r"]
    m = d["meta"]
    I, rpm = m["I"], m["rpm"]
    Rst = r.get("s_R_phase_eq_star_ohm") or r["R_phase_ohm"] * (1 / 3 if delta else 1)
    Pdc = 3 * I * I * Rst
    Pcu = r["P_cu_W"]
    Pfe = r["P_fe_W"]
    Pmag = (r.get("P_mag_eddy_W") or 0) + (r.get("P_shaft_eddy_W") or 0)
    T = abs(r["T_avg_Nm"])
    Pm = T * rpm * math.pi / 30
    nl = res.get(jid + "_nl")
    emf = nl["r"]["V_peak"] * kV if nl and nl.get("ok") else None
    return dict(T=T, Vemf=emf, VL=r.get("s_V_line_peak_V"), R=Rst, Pdc=Pdc,
                Pac=Pcu - Pdc, Pcu=Pcu, Pfe=Pfe, Pmag=Pmag,
                Ptot=Pcu + Pfe + Pmag, eta=Pm / (Pm + Pcu + Pfe + Pmag), wall=d["wall_s"])


def tn(jid):
    t = tun[jid]
    I = res[jid]["meta"]["I"]
    Pdc = 3 * I * I * t["R"]
    return dict(T=t["T"], Vemf=t["Vemf"], VL=t["Vph"] * math.sqrt(3), R=t["R"],
                Pdc=Pdc, Pac=t["Pcu"] - Pdc, Pcu=t["Pcu"], Pfe=t["Pfe"],
                Pmag=t["Pmag"], Ptot=t["Ploss"], eta=t["eta"])


keys = ["T", "Vemf", "VL", "Pdc", "Pac", "Pcu", "Pfe", "Pmag", "Ptot", "eta"]
base = [k for k in tun if k.endswith("_base")][0]
fb, tb = fem(base), tn(base)
rows = []
for jid in tun:
    f = fem(jid)
    if f is None:
        continue
    t = tn(jid)
    row = {"id": jid, "corner": res[jid]["meta"].get("corner")}
    for k in keys:
        if f.get(k) is None or t.get(k) is None:
            row[k] = None
            continue
        if k == "eta":
            row[k] = round(100 * (t[k] - f[k]), 2)          # pp
            row[k + "_law"] = round(100 * ((t[k] - tb[k]) - (f[k] - fb[k])), 2)
            continue
        row[k] = round(100 * (t[k] / f[k] - 1), 1) if abs(f[k]) > 1e-9 else None
        # scaling-law error: tuner ratio vs FEM ratio (removes the base offset)
        if abs(fb.get(k) or 0) > 1e-9 and abs(tb.get(k) or 0) > 1e-9 and abs(f[k]) > 1e-9:
            row[k + "_law"] = round(100 * ((t[k] / tb[k]) / (f[k] / fb[k]) - 1), 1)
    row["fem"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()}
    row["tuner"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()}
    rows.append(row)
print(json.dumps(rows, indent=1))
