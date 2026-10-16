"""Same Full-vs-1/4 x 60-vs-120 matrix, but FORCE a real recompute (fresh=true)
and time the wall-clock of each, so we can compare compute cost too.
"""
import json, time, urllib.request, urllib.parse
BASE = 'http://127.0.0.1:8001/api/simulation/physics/fem_transient'

def run(n_sectors, steps):
    p = {'restore':'false','n_steps_per_period':str(steps),'n_periods':'1',
         'gamma_deg':'28','I_phase_rms':'100','mesh_size_mm':'4.0','min_size_mm':'0.3',
         'outer_air_factor':'1.2','motion_band':'true','band_thickness_mm':'0.4',
         'gap_layers':'1','n_sectors':str(n_sectors),'stator_fillet_mm':'0',
         'sliding_band':'true','rotor_eddy':'true','demag':'false','torque_filter':'true',
         'pole_copy':'true','coil_temp_c':'120','end_winding_factor':'1.51',
         'component_mesh':'{}','include_frames':'false','run_id':f'tm_{n_sectors}_{steps}','fresh':'true'}
    t0 = time.perf_counter()
    d = json.loads(urllib.request.urlopen(BASE+'?'+urllib.parse.urlencode(p), timeout=900).read().decode())
    dt = time.perf_counter() - t0
    su = d.get('summary', {})
    return {'sym':('Full(1)' if n_sectors==1 else '1/4(4)'), 'steps':steps,
            'seconds':round(dt,1), 'eff':round(100*su.get('efficiency',0),2),
            'P_loss':round(su.get('P_loss_total_W',0),0), 'ripple':round(d.get('T_ripple_filt_pct',0),1),
            'T':round(d.get('T_avg_Nm',0),2)}

if __name__ == '__main__':
    for ns, st in [(1,60),(1,120),(4,60),(4,120)]:
        print(json.dumps(run(ns, st)), flush=True)
