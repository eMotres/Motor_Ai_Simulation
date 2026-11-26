"""Full-motion blank bounds in the shared translated chain coordinate system [mm]."""
from .geometry import analyze_pair
from .common import require_number


def chain_bounds(stages, *, stage_gap_mm=5.):
    from .gearbox import PairStageSpec
    require_number('stage_gap_mm', stage_gap_mm, minimum=0.)
    boxes = []
    x = z = 0.

    def add(spec, dim, cx, cz, orbit=0.):
        radius = (.5*dim.d_f_mm + (spec.rim_thickness_mm or 2.5*spec.m_n_mm)
                  if spec.is_internal else .5*dim.d_a_mm)
        r = radius+orbit
        boxes.append(((cx-r, -r, cz), (cx+r, r, cz+spec.b_mm)))

    for st in stages:
        if isinstance(st, PairStageSpec):
            p = analyze_pair(st.gear1, st.gear2, centre_distance_mm=st.centre_distance_mm)
            add(st.gear1,p.gear1,x,z)
            add(st.gear2,p.gear2,x+p.a_w_mm,z)
            x += p.a_w_mm
            z += max(st.gear1.b_mm,st.gear2.b_mm)+stage_gap_mm
        else:
            sp = analyze_pair(st.sun,st.planet)
            pr = analyze_pair(st.planet2 or st.planet,st.ring)
            second = z+max(st.sun.b_mm,st.planet.b_mm) if st.planet2 else z
            add(st.sun,sp.gear1,x,z)
            add(st.planet,sp.gear2,x,z,sp.a_w_mm)
            add(st.ring,pr.gear2,x,second)
            if st.planet2: add(st.planet2,pr.gear1,x,second,sp.a_w_mm)
            z = max(b[1][2] for b in boxes)+stage_gap_mm
    low = tuple(min(b[0][i] for b in boxes) for i in range(3))
    high = tuple(max(b[1][i] for b in boxes) for i in range(3))
    return {'min_mm': low, 'max_mm': high,
            'size_mm': tuple(high[i]-low[i] for i in range(3)),
            'stage_gap_mm': stage_gap_mm, 'housing_allowance_mm': 0.,
            'basis': 'translated blank extents; full planetary orbit; no housing allowance'}
