"""Pull the completed sweep result, confirm settings, save a snapshot, and rank
the best designs (by efficiency, torque density, and lowest ripple)."""
import json, urllib.request

d = json.loads(urllib.request.urlopen('http://127.0.0.1:8001/api/optimization/scan/progress', timeout=15).read())
res = d.get('result') or {}
pts = res.get('points') or d.get('points') or []
base = res.get('baseline')

print('running =', d.get('running'), '| done =', d.get('done'), '/', d.get('total'), '| n_built =', res.get('n_built'))
print('steps_per_period (result) =', res.get('steps_per_period'))
print('scan_params =', json.dumps(res.get('scan_params')))
print('n points =', len(pts))

# save snapshot
with open('config/sweep_result_snapshot.json', 'w', encoding='utf-8') as fh:
    json.dump(res if res else {'points': pts, 'baseline': base}, fh)
print('snapshot -> config/sweep_result_snapshot.json')

def fmt(p):
    ov = p.get('overrides') or {}
    ovs = ' '.join(f'{k}={v}' for k, v in sorted(ov.items()))
    return (f"I={p.get('current_a'):.0f} g={p.get('gamma_deg'):.0f} | "
            f"eff={100*p.get('efficiency',0):.2f}% td={p.get('torque_per_mass_Nm_kg',0):.2f} "
            f"T={p.get('T_em_Nm',0):.1f} ripple={p.get('T_ripple_pct',0):.1f}% "
            f"V={p.get('V_peak',0):.0f} | {ovs}")

ok = [p for p in pts if p.get('efficiency')]
print('\n--- TOP 5 by EFFICIENCY ---')
for p in sorted(ok, key=lambda x: -x['efficiency'])[:5]:
    print(' ', fmt(p))
print('\n--- TOP 5 by TORQUE DENSITY ---')
for p in sorted(ok, key=lambda x: -x.get('torque_per_mass_Nm_kg', 0))[:5]:
    print(' ', fmt(p))
print('\n--- LOWEST 5 RIPPLE ---')
for p in sorted(ok, key=lambda x: x.get('T_ripple_pct', 1e9))[:5]:
    print(' ', fmt(p))
# torque near target 77.7 (within 3)
near = [p for p in ok if abs(p.get('T_em_Nm', 0) - 77.7) <= 3]
print(f'\n--- near target 77.7 N.m (+/-3): {len(near)} pts, best eff ---')
for p in sorted(near, key=lambda x: -x['efficiency'])[:5]:
    print(' ', fmt(p))
if base and base.get('efficiency'):
    print('\nBASELINE:', fmt(base))
print('\nranges: eff %.2f..%.2f%%  ripple %.1f..%.1f%%  td %.2f..%.2f' % (
    100*min(p['efficiency'] for p in ok), 100*max(p['efficiency'] for p in ok),
    min(p.get('T_ripple_pct',0) for p in ok), max(p.get('T_ripple_pct',0) for p in ok),
    min(p.get('torque_per_mass_Nm_kg',0) for p in ok), max(p.get('torque_per_mass_Nm_kg',0) for p in ok)))
