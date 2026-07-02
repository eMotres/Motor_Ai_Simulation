"""Thermal sanity: steady 2-D thermal on the STRUCTURED-slot stator half with
the liner as a REAL meshed barrier vs the FREE-slot lumped model.  Injects the
same copper loss; checks T_max is finite/reasonable and the winding is hotter
than the iron (the liner barrier working)."""
import _use40  # noqa
import sys, math
import numpy as np
import logging
logging.basicConfig(level=logging.WARNING)
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.simulation.thermal_solver_2d import solve_steady_thermal
from motor_ai_sim.cadquery_geometry import CadQueryMotor

DOM_AIR, DOM_STATOR, DOM_COIL, DOM_AIRGAP = 0, 1, 2, 3
DOM_ROTOR, DOM_SHAFT, DOM_BAND, DOM_OUTER = 5, 6, 7, 8
DOM_WIRE_INS, DOM_SLOT_INS = 9, 10


def build(structured):
    m = CadQueryMotor()
    polys = F._simplify_polys(m.get_2d_polygons(0.0), tol_mm=0.005,
                              stator_fillet_mm=0.0, band_mode="merged",
                              structured_gap=False)
    ins = float(m.parameters["insulation_thickness"]); dy = float(m.parameters["wire_spacing_y"])
    ms, ts, cs, mr, tr, cr = F._build_sliding_band_meshes(
        polys, 0.0, 1.5, min_size_mm=max(0.02, min(ins, dy) / 2), outer_air_factor=1.3,
        band_thickness_mm=0.4, n_sectors=-1, geo_cfg=m.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0, gap_layers=2.0,
        component_mesh_mm={"stator": 2.0}, full_ring=True, pole_copy=False,
        structured_slot=structured)
    return m, np.asarray(ms.p), np.asarray(ms.t), np.asarray(ts)


def thermal(m, P, T, tags, structured):
    # solve_steady_thermal wants P (2, N) and T (3, M) — pass as-is.
    n = T.shape[1]
    # conductivities [W/m·K]
    k_steel = 25.0; k_cu = 400.0; k_liner = 0.14; k_enamel = 0.12; k_air = 0.03
    slot_k_lumped = 0.5          # homogenised winding+liner (free path proxy)
    slot_k_winding = 1.5         # winding bulk only (meshed path)
    k_elem = np.full(n, k_air)
    is_coil = (tags == DOM_COIL) | (tags >= 200)
    k_elem[tags == DOM_STATOR] = k_steel
    if structured:
        k_elem[is_coil] = slot_k_winding
        k_elem[tags == DOM_SLOT_INS] = k_liner
        k_elem[tags == DOM_WIRE_INS] = k_enamel
    else:
        k_elem[is_coil] = slot_k_lumped
    # inject copper loss: 150 W over the copper volume (2-D density W/m^3 via area*L)
    q_elem = np.zeros(n)
    a = P[:, T[0]]; b = P[:, T[1]]; c = P[:, T[2]]
    area = 0.5 * np.abs((b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]))  # m^2
    L = float(m.parameters.get("motor_length", 50.0)) * 1e-3
    cu_area = area[is_coil].sum()
    P_cu = 150.0
    q_elem[is_coil] = P_cu / max(cu_area * L, 1e-12)
    r_house = float(m.parameters["stator_outer_radius"]) * 1e-3
    r_ro = float(m.parameters["rotor_outer_radius"]) * 1e-3
    r_si = float(m.parameters["stator_inner_radius"]) * 1e-3
    th = solve_steady_thermal(
        P, T, tags, k_elem, q_elem,
        drop_tags=[DOM_OUTER, DOM_AIRGAP, DOM_BAND, DOM_AIR],
        r_housing_m=r_house, rotor_outer_m=r_ro, stator_inner_m=r_si,
        gap_k=k_air, h_conv=100.0, t_ambient=40.0)
    Tn = np.asarray(th["T_node"]); ts_o = np.asarray(th["triangles"], int)
    tg_o = np.asarray(th["cell_tags"], int)
    Tel = Tn[ts_o].mean(1)
    cu = (tg_o == DOM_COIL) | (tg_o >= 200)
    st = (tg_o == DOM_STATOR)
    Tcu = Tel[cu].max() if cu.any() else float("nan")
    Tst = Tel[st].max() if st.any() else float("nan")
    return Tcu, Tst, float(Tn.max())


if __name__ == "__main__":
    print("=== thermal sanity: FREE(lumped) vs STRUCT(meshed liner), P_cu=150W ===")
    for structured in (False, True):
        m, P, T, tags = build(structured)
        Tcu, Tst, Tmax = thermal(m, P, T, tags, structured)
        lbl = "STRUCT" if structured else "FREE"
        print(f"{lbl:7s}: T_max={Tmax:.1f}C  winding_max={Tcu:.1f}C  "
              f"stator_max={Tst:.1f}C  rise(cu-iron)={Tcu-Tst:.1f}K", flush=True)
