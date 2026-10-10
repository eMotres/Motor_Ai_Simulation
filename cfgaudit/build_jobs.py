"""Build sandbox configs + job lists for the Configure audit (NOT committed)."""
import copy
import json
import math
import os
import sys

import yaml

SRV = sys.argv[1]          # local copy of server data
OUT = sys.argv[2]          # local staging dir -> uploaded to the sandbox
os.makedirs(OUT, exist_ok=True)

ident = yaml.safe_load(open(os.path.join(SRV, "identity_motor_config.yaml"), encoding="utf-8"))
presets = json.load(open(os.path.join(SRV, "motor_presets.json"), encoding="utf-8"))
W = os.path.join(SRV, "workspaces/c309c100cd421858/dies")

# ── machines ────────────────────────────────────────────────────────────────
p40 = presets["ciano14_40_new"]
M40 = dict(
    tag="m40", geo=dict(p40["geometry"]), conn="2S", sd="star",
    mats=dict(p40["materials"]), kend=2.19, gamma=10.0, I0=40.659, rpm0=13000.0,
    winding={"n_coils_per_phase": 4, "connection": "2S", "n_parallel": 1,
             "n_series": 2, "layers": 1, "star_delta": "star"},
)
die = yaml.safe_load(open(os.path.join(W, "CIANO10 200 opt/die.yaml"), encoding="utf-8"))
cf = yaml.safe_load(open(os.path.join(W, "CIANO10 200 opt/L155 motor.yaml"), encoding="utf-8"))
g155 = dict(die["geometry"])
g155.update(cf["geometry_overrides"])
M155 = dict(
    tag="l155", geo=g155, conn="2P", sd="delta", mats=dict(cf["materials"]),
    kend=1.355, gamma=15.0, I0=562.067, rpm0=14200.0,
    winding=dict(cf["winding"]), battery=dict(cf.get("battery") or {}),
)


def write_cfg(m):
    c = copy.deepcopy(ident)
    c["geometry"] = dict(m["geo"])
    c["winding"] = dict(m["winding"])
    sim = c.setdefault("simulation", {})
    sim.update(max_current=m["I0"], current_a=m["I0"], rpm=m["rpm0"],
               phase_offset_deg=m["gamma"], gamma_deg=m["gamma"],
               coil_temp_c=120.0, end_winding_factor=m["kend"],
               connection=m["conn"], star_delta=m["sd"], mode="motor",
               daxis_deg=None, eddy=False, rotor_eddy=True, demag=True)
    d = os.path.join(OUT, "cfg", m["tag"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "motor_config.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(c, fh, sort_keys=False, allow_unicode=True)


def solve(m, jid, *, L=None, N=None, h=None, I=None, rpm=None, noload=False,
          steps=24, mesh=5.0, eddy=False, rotor_eddy=True, demag=True,
          kend=None, n_periods=1.0, extra=None, meta=None, nsec=2):
    g = dict(m["geo"])
    if L is not None:
        g["motor_length"] = L
    if N is not None:
        g["num_wires_per_slot"] = N
    if h is not None:
        g["wire_height"] = h
    kw = dict(n_steps_per_period=steps, n_periods=n_periods, gamma_deg=m["gamma"],
              mode="motor", I_phase_rms=(0.0 if noload else (I if I is not None else m["I0"])),
              mesh_size_mm=mesh, n_sectors=nsec, sliding_band=True,
              rotor_eddy=(False if noload else rotor_eddy),
              demag=(False if noload else demag), eddy=eddy,
              geo=g, rpm=(rpm if rpm is not None else m["rpm0"]),
              star_delta=m["sd"], connection=m["conn"],
              end_winding_factor=(kend if kend is not None else m["kend"]),
              ledger=False)
    if extra:
        kw.update(extra)
    return {"id": jid, "kind": "solve", "materials": m["mats"], "kw": kw,
            "meta": dict(meta or {}, L=g["motor_length"], N=g["num_wires_per_slot"],
                         h=g["wire_height"], I=kw["I_phase_rms"], rpm=kw["rpm"],
                         kend=kw["end_winding_factor"], noload=noload)}


def kend_at(m, L):
    """Physical end-turn length held fixed: k(L) = 1 + (k0-1)·L0/L."""
    L0 = float(m["geo"]["motor_length"])
    return 1.0 + (m["kend"] - 1.0) * L0 / L


def corners(m, Nlo, Nhi):
    L0 = float(m["geo"]["motor_length"])
    h0 = float(m["geo"]["wire_height"])
    I0, r0 = m["I0"], m["rpm0"]
    J = [solve(m, f"{m['tag']}_base", meta={"corner": "base"}),
         solve(m, f"{m['tag']}_base_nl", noload=True, meta={"corner": "base"})]
    for f in (0.5, 2.0):
        L = L0 * f
        J.append(solve(m, f"{m['tag']}_L{f}", L=L, kend=kend_at(m, L),
                       meta={"corner": f"L x{f}"}))
        J.append(solve(m, f"{m['tag']}_L{f}_nl", L=L, kend=kend_at(m, L), noload=True,
                       meta={"corner": f"L x{f}"}))
    for N in (Nlo, Nhi):
        J.append(solve(m, f"{m['tag']}_N{N}", N=N, meta={"corner": f"N {N}"}))
        J.append(solve(m, f"{m['tag']}_N{N}_nl", N=N, noload=True, meta={"corner": f"N {N}"}))
    for f in (0.7, 1.3):
        J.append(solve(m, f"{m['tag']}_h{f}", h=round(h0 * f, 4), meta={"corner": f"h x{f}"}))
    for f in (0.5, 1.5):
        J.append(solve(m, f"{m['tag']}_rpm{f}", rpm=r0 * f, meta={"corner": f"rpm x{f}"}))
    for f in (0.5, 1.3):
        J.append(solve(m, f"{m['tag']}_I{f}", I=I0 * f, meta={"corner": f"I x{f}"}))
    return J


def timing(m, duty_mesh, duty_min, duty_nsec):
    """Owner's addendum: wall time of ONE magnetostatic (id,iq) point and of
    ONE settled eddy-transient point, at the duty mesh."""
    ex = {"min_size_mm": duty_min}
    return [
        # magnetostatic point: 6 rotor positions over one cogging cycle
        # (30 deg elec on both machines: lcm(12,2p)/pp = 12 cycles/period)
        solve(m, f"{m['tag']}_t_ms6", steps=72, n_periods=1.0 / 12.0, mesh=duty_mesh,
              rotor_eddy=False, demag=False, eddy=False, nsec=duty_nsec, extra=ex,
              meta={"timing": "magnetostatic dq point, 6 positions / cogging cycle"}),
        # ripple-grade point: 12 positions over one cogging cycle
        solve(m, f"{m['tag']}_t_ms12", steps=144, n_periods=1.0 / 12.0, mesh=duty_mesh,
              rotor_eddy=False, demag=False, eddy=False, nsec=duty_nsec, extra=ex,
              meta={"timing": "magnetostatic dq point, 12 positions / cogging cycle"}),
        # settled coupled-eddy transient (duty-grade)
        solve(m, f"{m['tag']}_t_eddy", steps=36, n_periods=1.0, mesh=duty_mesh,
              rotor_eddy=True, demag=False, eddy=True, nsec=duty_nsec, extra=ex,
              meta={"timing": "coupled eddy transient, 36 steps, settled"}),
    ]


# ── 40 mm ───────────────────────────────────────────────────────────────────
write_cfg(M40)
# N0 = 7 rows; slot fits 8 rows at h0 (the tuner caps the slider there), so
# 1.5x turns (10.5) is NOT buildable at the same wire; 5 = 0.71x, 8 = 1.14x
# is the physical max; 10 is solved anyway as the owner's corner (wire over
# the slot top is only a fit problem, the 2-D solve still runs if the
# geometry validator lets it through).
J40 = corners(M40, 5, 10) + [solve(M40, "m40_N8", N=8, meta={"corner": "N 8 (slot max)"}),
                             solve(M40, "m40_N8_nl", N=8, noload=True, meta={"corner": "N 8 (slot max)"})]
J40 += [solve(M40, "m40_base_duty", mesh=1.0, steps=36, eddy=True, extra={"min_size_mm": 0.3},
              meta={"corner": "base @ duty mesh + coupled eddy"})]
J40 += timing(M40, 1.0, 0.3, 2)
json.dump(J40, open(os.path.join(OUT, "jobs_m40.json"), "w"), indent=1)

# ── L155 ────────────────────────────────────────────────────────────────────
write_cfg(M155)
J155 = [{"id": "l155_passport", "kind": "passport", "materials": M155["mats"],
         "kw": {"machine": {"geometry": M155["geo"], "connection": M155["conn"],
                            "star_delta": M155["sd"], "materials": M155["mats"],
                            "end_winding_factor": M155["kend"]},
                "I0": M155["I0"], "gamma_deg": M155["gamma"], "rpm0": M155["rpm0"],
                "mode": "motor", "pwm": "off", "role": "motor",
                "battery": M155["battery"],
                # coarse rung (the catalog route's coarse=true): compute budget
                "base_steps": 6, "sweep_steps": 6,
                "rpms": [7100.0, 14200.0, 21300.0]}}]
J155 += [dict(j, kw=dict(j["kw"], n_steps_per_period=12)) if j["kind"] == "solve" else j
         for j in corners(M155, 7, 15)]
J155 += [solve(M155, "l155_base_duty", mesh=4.0, steps=36, eddy=True, extra={"min_size_mm": 0.3},
               meta={"corner": "base @ duty mesh + coupled eddy"})]
J155 += timing(M155, 4.0, 0.3, 2)
json.dump(J155, open(os.path.join(OUT, "jobs_l155.json"), "w"), indent=1)
print(len(J40), len(J155))


# ── MTPA study (owner 2026-09-30: MTPA line only) ───────────────────────────
def mtpa(m, tag):
    J = []
    for fI in (0.5, 1.0, 1.5, 2.0):
        for gam in (-5.0, 5.0, 15.0, 25.0, 35.0):
            j = solve(m, f"{tag}_I{fI}_g{gam:+.0f}", I=m["I0"] * fI, steps=72,
                      n_periods=1.0 / 12.0, rotor_eddy=False, demag=False, eddy=False,
                      meta={"mtpa": True, "fI": fI, "gamma": gam})
            j["kw"]["gamma_deg"] = gam
            J.append(j)
    # field-weakening arm (owner 2026-09-30 amendment) + off-grid check points
    extra = [(fI, g) for fI in (1.0, 2.0) for g in (50.0, 65.0, 80.0)]
    extra += [(0.75, 20.0), (0.75, 57.5), (1.25, 72.5), (1.75, 10.0), (1.25, 42.5)]
    for fI, gam in extra:
        j = solve(m, f"{tag}_I{fI}_g{gam:+.1f}", I=m["I0"] * fI, steps=72,
                  n_periods=1.0 / 12.0, rotor_eddy=False, demag=False, eddy=False,
                  meta={"mtpa": True, "fI": fI, "gamma": gam,
                        "check": fI in (0.75, 1.25, 1.75)})
        j["kw"]["gamma_deg"] = gam
        J.append(j)
    return J


import shutil
for m, tag in ((M40, "m40mtpa"), (M155, "l155mtpa")):
    d = os.path.join(OUT, "cfg", tag)
    os.makedirs(d, exist_ok=True)
    shutil.copy(os.path.join(OUT, "cfg", m["tag"], "motor_config.yaml"), d)
    json.dump(mtpa(m, tag), open(os.path.join(OUT, f"jobs_{tag}.json"), "w"), indent=1)


# ── AC-copper matrix, coupled eddy with resolved strands (owner addendum) ───
def acm(m, tag, pts, mesh, rot_pair):
    J = []
    N0 = float(m["geo"]["num_wires_per_slot"])
    for h, N, frpm in pts:
        I = m["I0"] * N0 / N          # constant ampere-turns = ~rated torque
        J.append(solve(m, f"{tag}_h{h}_N{N}_r{frpm}", h=h, N=N, I=I, rpm=m["rpm0"] * frpm,
                       steps=36, mesh=mesh, eddy=True, extra={"min_size_mm": 0.3},
                       meta={"acm": True, "frpm": frpm}))
    # rotor-loss pair: fewer turns at the SAME NI, post-processed eddy
    Nr = rot_pair
    J.append(solve(m, f"{tag}_rotpair_N{Nr}", N=Nr, I=m["I0"] * N0 / Nr,
                   meta={"corner": f"N {Nr} same NI"}))
    return J


d = os.path.join(OUT, "cfg", "m40ac")
os.makedirs(d, exist_ok=True)
shutil.copy(os.path.join(OUT, "cfg", "m40", "motor_config.yaml"), d)
pts40 = [(0.6, 7, 1.5), (0.42, 7, 1.0), (0.74, 7, 1.0), (0.74, 7, 1.5),
         (0.6, 5, 1.0), (0.6, 8, 1.0), (0.6, 8, 1.5)]
json.dump(acm(M40, "m40ac", pts40, 1.0, 5), open(os.path.join(OUT, "jobs_m40ac.json"), "w"), indent=1)
