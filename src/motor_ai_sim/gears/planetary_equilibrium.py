"""Carrier-frame two-mesh equilibrium with a planet/carrier friction pair.

All port torques act ON the assembly. Bearing power is N*M*abs(wp-wc).
It is consumed between meshes, not charged to a driving shaft. Shaft drag
is specified at its actual member; its opposite reaction acts on housing.
"""
import math
from .losses import PlanetaryEfficiencyResult, solve_planetary_power_flow
from .common import require_number


def solve_local_planetary(*, u, eta_sp, eta_pr, omegas, planet_rel_rad_s,
                         fixed_member, known_member, known_torque_Nm,
                         planet_bearing_power_W=0., shaft_drag_power_W=None):
    require_number('u', u, positive=True)
    require_number('known_torque_Nm', known_torque_Nm)
    require_number('planet_bearing_power_W', planet_bearing_power_W, minimum=0.)
    for eta in (eta_sp, eta_pr):
        require_number('mesh efficiency', eta, positive=True)
        if eta > 1.: raise ValueError('mesh efficiency exceeds one')
    for w in omegas.values(): require_number('angular velocity', w)
    vs = omegas['sun'] - omegas['carrier']
    vr = omegas['ring'] - omegas['carrier']
    if max(abs(w) for w in omegas.values()) < 1e-12:
        return solve_planetary_power_flow(u=u, eta0_sr=1., fixed_member=fixed_member,
            omega_s_rad_s=0., omega_r_rad_s=0., omega_c_rad_s=0.,
            known_member=known_member, known_torque_Nm=known_torque_Nm)
    if abs(vr) < 1e-12 or abs(vs) < 1e-12 or abs(planet_rel_rad_s) < 1e-12:
        raise ValueError('Nonzero relative mesh speeds required')
    drag = {k: 0. for k in omegas}
    for k, p in (shaft_drag_power_W or {}).items():
        require_number('shaft drag power', p, minimum=0.)
        if abs(omegas[k]) < 1e-12 and p > 0.: raise ValueError('Drag power on stationary shaft')
        drag[k] = p / omegas[k] if abs(omegas[k]) > 1e-12 else 0.
    B = planet_bearing_power_W
    eta = eta_sp * eta_pr
    selected = None
    for forward in (True, False):
        # Tr = A*Ts + C follows directly from the two relative mesh powers.
        A = u*eta if forward else u/eta
        C = eta_pr*B/vr if forward else B/(eta_pr*vr)
        known_mesh = known_torque_Nm - drag[known_member]
        ts = (known_mesh if known_member == 'sun' else
              (known_mesh-C)/A if known_member == 'ring' else
              -(known_mesh+C)/(1.+A))
        tr = A*ts+C
        core = {'sun': ts, 'ring': tr, 'carrier': -ts-tr}
        if forward:
            sp_in = ts*vs
            pr_in = eta_sp*sp_in-B
            admissible = min(sp_in, pr_in) >= -1e-10
            planet_in, planet_out = eta_sp*sp_in, pr_in
        else:
            pr_in = tr*vr
            sp_in = eta_pr*pr_in-B
            admissible = min(pr_in, sp_in) >= -1e-10
            planet_in, planet_out = eta_pr*pr_in, sp_in
        selected = (forward, core, sp_in, pr_in, planet_in, planet_out, admissible)
        if admissible: break
    forward, core, sp_in, pr_in, planet_in, planet_out, admissible = selected
    torques = {k: core[k]+drag[k] for k in core}
    torques['housing_drag'] = -math.fsum(drag.values())
    powers = {k: torques[k]*omegas.get(k, 0.) for k in torques}
    moving = [k for k in core if k != fixed_member]
    driving = max(moving, key=lambda k: powers[k])
    driven = min(moving, key=lambda k: powers[k])
    pin, pout = powers[driving], -powers[driven]
    mesh_loss = (1.-eta_sp)*sp_in+(1.-eta_pr)*pr_in
    loss = mesh_loss+B+sum(drag[k]*omegas[k] for k in core)
    admissible = admissible and pin >= -1e-10 and pout >= -1e-10
    state = 'valid' if admissible else 'failed'
    # Torque on the planet opposes relative spin; carrier receives its opposite.
    mp = -B/planet_rel_rad_s
    mc = -mp
    planet_mesh = (planet_in-planet_out)/planet_rel_rad_s
    carrier_tooth = -core['carrier']-mc
    return PlanetaryEfficiencyResult(
        fixed_member=fixed_member, driving_member=driving, driven_member=driven,
        stationary_carrier_ratio_u=u, eta0_sr=eta, eta0_rs=eta,
        active_branch='sun_to_ring_rel' if forward else 'ring_to_sun_rel',
        member_torques_Nm=torques, member_speeds_rad_s=omegas,
        member_powers_W=powers, power_in_W=pin, power_out_useful_W=pout,
        power_loss_mesh_W=mesh_loss, power_loss_total_W=loss,
        efficiency_mesh_only=None,  # Removing bearing friction changes the solution.
        efficiency_total=pout/pin if pin > 0. and admissible else None,
        state=state, method='CarrierFrame_TwoMesh_PlanetBearingEquilibrium',
        flags=[] if admissible else ['inadmissible_local_power_flow'],
        applicability_evidence={
            'mesh_torques_Nm': core, 'shaft_drag_torques_Nm': drag,
            'sun_planet_input_power_W': sp_in, 'planet_ring_input_power_W': pr_in,
            'planet_bearing_power_W': B,
            'planet_bearing_torque_on_planets_Nm': mp,
            'planet_bearing_torque_on_carrier_Nm': mc,
            'planet_mesh_torque_Nm': planet_mesh, 'carrier_tooth_torque_Nm': carrier_tooth,
            'sun_equilibrium_residual_Nm': torques['sun']-drag['sun']-core['sun'],
            'planet_equilibrium_residual_Nm': planet_mesh+mp,
            'carrier_equilibrium_residual_Nm': torques['carrier']-drag['carrier']+carrier_tooth+mc,
            'torque_sum_total_Nm': math.fsum(torques.values()),
            'power_balance_residual_W': pin-pout-loss,
        })


def unavailable_planetary_flow(*, u, omegas, fixed_member, known_member, known_torque_Nm, state):
    """Static ideal torque balance only; moving powers unavailable without mesh loss.

    The torque seed permits downstream diagnostic geometry/stress records, never
    a qualified equilibrium or efficiency result. No substitute eta is evaluated.
    """
    ts = (known_torque_Nm if known_member == 'sun' else known_torque_Nm/u
          if known_member == 'ring' else -known_torque_Nm/(1.+u))
    torques = {'sun': ts, 'ring': u*ts, 'carrier': -(1.+u)*ts}
    standstill = max(abs(w) for w in omegas.values()) < 1e-12
    unknown = 0. if standstill else math.nan
    return PlanetaryEfficiencyResult(
        fixed_member=fixed_member, driving_member='', driven_member='',
        stationary_carrier_ratio_u=u, eta0_sr=None, eta0_rs=None,
        active_branch='standstill' if standstill else 'unavailable',
        member_torques_Nm=torques, member_speeds_rad_s=omegas,
        member_powers_W={k: unknown for k in torques},
        power_in_W=unknown, power_out_useful_W=unknown,
        power_loss_mesh_W=unknown, power_loss_total_W=unknown,
        efficiency_mesh_only=None, efficiency_total=None, state=state,
        flags=['mesh_efficiency_unavailable', 'static_ideal_torque_balance_diagnostic_only'] +
              (['zero_speed_efficiency_undefined'] if standstill else []),
        missing_inputs=['available_mesh_efficiencies'])
