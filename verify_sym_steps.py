"""Compare symmetry (Full n_sectors=1 vs 1/4 n_sectors=4) x steps (60 vs 120).

Same operating point + mesh params as the user's config (gap_layers=1, pole_copy,
end_winding 1.51, gamma 28, 100 A); only n_sectors and steps vary. Dumps the loss
breakdown so we can see how the symmetry build AND the time resolution each move
the numbers.
"""
import json, urllib.request, urllib.parse
BASE = 'http://127.0.0.1:8001/api/simulation/physics/fem_transient'

def run(n_sectors, steps):
    p = {'restore':'false','n_steps_per_period':str(steps),'n_periods':'1',
         'gamma_deg':'28','I_phase_rms':'100','mesh_size_mm':'4.0','min_size_mm':'0.3',
         'outer_air_factor':'1.2','motion_band':'true','band_thickness_mm':'0.4',
         'gap_layers':'1','n_sectors':str(n_sectors),'stator_fillet_mm':'0',
         'sliding_band':'true','rotor_eddy':'true','demag':'false','torque_filter':'true',
         'pole_copy':'true','coil_temp_c':'120','end_winding_factor':'1.51',
         'component_mesh':'{}','include_frames':'false','run_id':f'sym_{n_sectors}_{steps}','fresh':'false'}
    d = json.loads(urllib.request.urlopen(BASE+'?'+urllib.parse.urlencode(p), timeout=600).read().decode())
    su = d.get('summary', {})
    return {'n_sectors':n_sectors, 'req_steps':steps,
            'backend_steps':d.get('n_steps_per_period'), 'n_slip':d.get('n_slip_nodes'),
            'T':round(d.get('T_avg_Nm',0),2), 'ripple':round(d.get('T_ripple_filt_pct',0),2),
            'eff':round(100*su.get('efficiency',0),2), 'P_loss':round(su.get('P_loss_total_W',0),1),
            'core':round(su.get('P_core_W',0),1), 'cu':round(su.get('P_stranded_W',0),1),
            'mag':round(su.get('P_solid_W',0),1)}

if __name__ == '__main__':
    for ns, st in [(1,60),(1,120),(4,60),(4,120)]:
        print(json.dumps(run(ns, st)), flush=True)
