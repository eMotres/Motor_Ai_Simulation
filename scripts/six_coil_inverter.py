"""Inverter side of the six-coil study: one_3ph vs h_bridge on L155.

    python scripts/six_coil_inverter.py [<out.json>]

Uses ``inverter.losses.solve_controller`` as a library, with the operating
points §6 of docs/CONTROLLER_MODULE_2026-09-22.md printed (the duty's stored
coupled record, 2026-09-15/16).  For every topology it finds the smallest
parallel device count N with T_j <= 150 °C, on the default coldplate and, if
none exists up to N = 12, on the doubled plate §6 used (80 channels, 16 L/min).

THE 2P CORRECTION.  ``solve_controller``'s H-bridge takes the coil current to
be the phase current and the coil voltage to be phase voltage / coils per
phase — true for SERIES coils.  L155's winding is 2P: the two coils of a phase
are in PARALLEL, so each coil carries HALF the phase current at the FULL
phase voltage.  The per-coil bridge is therefore asked for
``i_phase_rms_A = I_phase / 2`` at the same AC power and power factor, which
makes the model's own ``v_coil = P/(pf·3·i·coils_per_phase)`` come out equal
to the phase voltage — the right coil voltage.  The uncorrected request (the
one §6 costed) is kept beside it for reference.
"""
import json
import math
import sys

from motor_ai_sim.inverter.losses import ControllerRefusal, solve_controller

V_DC = 750.4
BASE = dict(num_slots=12, num_poles=10, single_layer=True, star_delta="delta",
            device="IMCQ120R004M2H", v_dc_V=V_DC, f_carrier_hz=24000.0,
            dead_time_us=0.5, v_gs_on_V=18.0, v_gs_off_V=0.0, r_g_ext_ohm=2.3)
DUTIES = {
    "rated": dict(i_phase=314.3, p_ac=272.2e3, eta_shaft=0.9771, m=0.633,
                  rpm=14200.0),
    "peak": dict(i_phase=439.6, p_ac=439.5e3, eta_shaft=0.9746, m=0.943,
                 rpm=20000.0),
}
PLATES = {"default": {}, "doubled": {"mode": "liquid", "n_channels": 80,
                                     "flow_lpm": 16.0}}
T_TARGET = 150.0


def pf_of(d):
    i_leg = d["i_phase"] * math.sqrt(3.0)
    s = 3.0 * (d["m"] * V_DC / (2.0 * math.sqrt(2.0))) * i_leg
    return min(1.0, d["p_ac"] / s)


def size(req, n_max=12):
    for plate_name, plate in PLATES.items():
        for n in range(1, n_max + 1):
            try:
                r = solve_controller(dict(req, devices_parallel=n, cooling=plate))
            except ControllerRefusal:
                continue
            if r.get("feasible") is False:
                continue
            tj = r["thermal"]["t_j_max_c"]
            if tj <= T_TARGET:
                return n, plate_name, r
    return None, None, None


def row(name, n, plate, r, p_ac, eta_shaft, scale=1.0):
    if r is None:
        return {"topology": name, "N": None, "note": "no solution to N = 12"}
    L = r["losses"]
    tot = L["total_W"] * scale
    eta_inv = p_ac / (p_ac + tot)
    return {"topology": name, "N": n, "plate": plate,
            "devices": r["topology"]["n_devices"],
            "conduction_W": L["conduction_W"] * scale,
            "third_quadrant_W": L["third_quadrant_W"] * scale,
            "switching_W": L["switching_W"] * scale, "total_W": tot,
            "T_j_C": r["thermal"]["t_j_max_c"], "eta_inv": eta_inv,
            "eta_wall_to_shaft": eta_inv * eta_shaft}


def main(out=None):
    res = {}
    for dn, d in DUTIES.items():
        f_el = d["rpm"] / 60.0 * 5.0
        pf = pf_of(d)
        common = dict(BASE, p_ac_W=d["p_ac"], f_elec_hz=f_el, rpm=d["rpm"],
                      efficiency_shaft=d["eta_shaft"])
        rows = []
        n, pl, r = size(dict(common, topology="one_3ph", i_phase_rms_A=d["i_phase"],
                             modulation_index=d["m"]))
        rows.append(row("one_3ph (delta)", n, pl, r, d["p_ac"], d["eta_shaft"]))
        n, pl, r = size(dict(common, topology="h_bridge", i_phase_rms_A=d["i_phase"],
                             power_factor=pf))
        rows.append(row("h_bridge, series-coil reading (as §6)", n, pl, r, d["p_ac"],
                        d["eta_shaft"]))
        n, pl, r = size(dict(common, topology="h_bridge",
                             i_phase_rms_A=d["i_phase"] / 2.0, power_factor=pf))
        rows.append(row("h_bridge, 2P coil current (I/2)", n, pl, r, d["p_ac"],
                        d["eta_shaft"]))
        # the three-phase inverter given the H-bridge's own device count
        # (6 switches x 4·N_hb), so silicon is equal between the two
        n_hb = rows[-1]["N"]
        if n_hb:
            req = dict(common, topology="one_3ph", i_phase_rms_A=d["i_phase"],
                       modulation_index=d["m"], devices_parallel=4 * n_hb,
                       cooling=PLATES[rows[-1]["plate"]])
            r = solve_controller(req)
            rows.append(row("one_3ph (delta), same device count as h_bridge",
                            4 * n_hb, rows[-1]["plate"], r, d["p_ac"],
                            d["eta_shaft"]))
        # fault: one coil open, its bridge idle, the other five unchanged
        hb = next(x for x in rows if x["topology"].startswith("h_bridge, 2P"))
        if hb.get("N"):
            f = dict(hb)
            for k in ("conduction_W", "third_quadrant_W", "switching_W", "total_W"):
                f[k] = hb[k] * 5.0 / 6.0
            f.pop("eta_inv", None); f.pop("eta_wall_to_shaft", None)
            f["topology"] = "h_bridge 2P, coil 1 open (5 of 6 bridges)"
            f["note"] = ("losses x 5/6: the five healthy bridges keep their "
                         "current; eta is formed on the fault case's own power "
                         "in the study, not here")
            rows.append(f)
        res[dn] = {"power_factor": pf, "rows": rows}
    s = json.dumps(res, indent=1)
    if out:
        open(out, "w").write(s)
    print(s)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
