/* PROBE ONLY — delete after the check.  Mounts the REAL DutyCycleEditor with
   every network read stubbed, so the found-regime layout can be looked at
   without an API restart (the route change needs one). */
import React from 'react';
import { createRoot } from 'react-dom/client';
import { createTheme, ThemeProvider } from '@mui/material';

import DutyCycleEditor from './components/thermal/DutyCycleEditor';

const DIE = 'CIANO28 85 20SW1200';
const CFG = 'L13';
const RATED = 'rated 120C wire 80C NdFeB';
const PEAK = 'peak 200C wire 120C NdFeB';

const CTX = { active: true, die: DIE, config: CFG, duty: PEAK };
const TREE = { dies: [{ name: DIE, configs: [{ name: CFG, duties: [
  { name: RATED, torque_nm: 3.0 },
  { name: PEAK, torque_nm: 7.6,
    duty_cycle: { kind: 'S3', duty: PEAK, cycle_s: 60, rest_duty: null,
                  t_start_c: 40, calibration_duty: RATED } },
] }] }] };

const N = ['winding', 'stator', 'rotor', 'magnet'] as const;
const t_s: number[] = [];
const series: Record<string, number[]> = { winding: [], stator: [], rotor: [],
                                           magnet: [] };
const hot: number[] = [];
const ON = 60 * 0.2161;
for (let i = 0; i <= 120; i += 1) {
  const t = (i / 120) * 60;
  t_s.push(Number(t.toFixed(3)));
  const up = t <= ON;
  const f = up ? t / ON : Math.exp(-(t - ON) / 18);
  series.winding.push(110 + 88 * (up ? f : f));
  series.stator.push(105 + 35 * (up ? f : f));
  series.rotor.push(104 + 7 * (up ? f : f));
  series.magnet.push(104 + 7 * (up ? f : f));
  hot.push(series.winding[i] + 1.3);
}

const RESULT = {
  kind: 'duty_cycle', die: DIE, configuration: CFG, duty: PEAK,
  spec: {
    kind: 'S3', cycle_s: 60, duration_s: 60, ed_pct: 21.61, ed_given: false,
    rest_duty: null, calibration_duty: RATED, t_start_c: 40,
    note: 'intermittent duty with NO duty ratio stated',
    segments: [{ duty: PEAK, t_s: 12.97, rpm: 1000, total_W: 686.9 },
               { duty: null, t_s: 47.03, rpm: 0, total_W: 0 }],
  },
  network: { calibration_duty: RATED, C_total_J_per_K: 171.7,
             hot_spot_offset_K: 1.3 },
  cycle: {
    converged: true, n_cycles: 12, residual_K: 0.03,
    peak_c: { winding: 198.6, stator: 140.7, rotor: 111.5, magnet: 111.2 },
    min_c: { winding: 110, stator: 105, rotor: 104, magnet: 104 },
    mean_c: { winding: 149.5, stator: 116.6, rotor: 110.3, magnet: 110.7 },
    winding_hot_peak_c: 199.9, winding_hot_mean_c: 150.8,
    hot_spot_offset_K: 1.3, closure_pct: 0.02,
    energy_in_J: 9200, energy_out_J: 9198, stored_J: 2,
    segments: [[0, 12.97, PEAK], [12.97, 60, null]] as [number, number,
                                                        string | null][],
    t_s, T_c: series, winding_hot_c: hot,
    P_W: Object.fromEntries(N.map((n) => [n, t_s.map(() => 0)])),
    n_samples: t_s.length, n_solved: 2400,
    note: 'the cycle map’s fixed point',
  },
  split: { stator_side_W: 58.4, rotor_side_W: 2.1, stator_pct: 96.5,
           rotor_pct: 3.5, housing_W: 2.7, mount_W: 38.4,
           winding_end_faces_W: 9.5, stator_end_faces_W: 4.1,
           rotor_end_faces_W: 1.4, magnet_end_faces_W: 0.7, bore_W: 0.7,
           shaft_ends_W: 0, gap_W: -0.3, generated_total_W: 60.5,
           closure_pct: 0.02 },
  limits: {
    winding_limit_c: 200, magnet_limit_c: null,
    winding_limit_note: 'the project’s insulation class, judged on the HOT SPOT',
    magnet_limit_note: 'the magnet cards carry no maximum operating temperature',
    s2_time_to_limit_s: 26.621, s2_limiting_part: 'winding',
    s2_note: 'winding reaches 200 °C after 26.6 s from 40 °C',
    s2_from_rated_s: 20.672, s2_from_rated_part: 'winding',
    s2_from_rated_note: `the same pull, started from the calibration duty '${RATED}'’s own steady map`,
    s2_from_cycle_mean_s: 14.2, s2_from_cycle_mean_part: 'winding',
    s2_from_cycle_mean_note: 'the same pull, started from the MEAN state',
    ed_allowable_pct: 21.61, ed_requested_pct: null,
    ed_limiting_part: 'winding', limiting_part: 'winding',
    ed_found: true, ed_cycle_s: 60,
    ed_found_note: 'the cycle integrated, drawn and stored below is the one at the ALLOWABLE duty ratio',
    ed_note: 'at 21.6 % duty the winding peak sits on its limit',
    ed_curve: [[5, 96], [25, 219], [50, 320], [75, 380], [100, 410]],
    at_allowable: {
      winding_hot_peak_c: 199.88, winding_hot_mean_c: 150.82,
      magnet_peak_c: 111.24,
      peak_c: { winding: 198.58, stator: 140.69, rotor: 111.45,
                magnet: 111.24 },
      mean_c: { winding: 149.52, stator: 116.59, rotor: 110.27,
                magnet: 110.74 },
    },
    ed_vs_cycle: [
      { cycle_s: 10, ed_allowable_pct: 31.59, t_on_s: 3.159,
        limiting_part: 'winding', winding_hot_peak_c: 198.17,
        magnet_peak_c: 133.67 },
      { cycle_s: 30, ed_allowable_pct: 26.54, t_on_s: 7.962,
        limiting_part: 'winding', winding_hot_peak_c: 199.38,
        magnet_peak_c: 122.69 },
      { cycle_s: 60, ed_allowable_pct: 21.49, t_on_s: 12.894,
        limiting_part: 'winding', winding_hot_peak_c: 199.2,
        magnet_peak_c: 110.93 },
      { cycle_s: 120, ed_allowable_pct: 14.88, t_on_s: 17.856,
        limiting_part: 'winding', winding_hot_peak_c: 199.73,
        magnet_peak_c: 97.46 },
      { cycle_s: 300, ed_allowable_pct: 6.72, t_on_s: 20.16,
        limiting_part: 'winding', winding_hot_peak_c: 198.67,
        magnet_peak_c: 82.59 },
    ],
  },
  elapsed_s: 41.2, solve_time_s: 40.1, cached: true,
  geometry_fingerprint: 'probe',
};

const json = (data: unknown) =>
  Promise.resolve(new Response(JSON.stringify(data),
    { status: 200, headers: { 'content-type': 'application/json' } }));

const real = window.fetch.bind(window);
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const u = String(typeof input === 'string' ? input : (input as Request).url
                   ?? input);
  if (u.includes('/api/family/context')) return json(CTX);
  if (u.includes('/api/family/tree')) return json(TREE);
  if (u.includes('/api/thermal/duty_cycle/last')) {
    return json({ has_result: true, live_geometry_fingerprint: 'probe',
                  duty_cycle: { result: RESULT, params: {},
                                geometry_fingerprint: 'probe',
                                computed_at: '2026-09-15T12:00:00+00:00' } });
  }
  if (u.includes('/api/')) return json({});
  return real(input as RequestInfo, init);
}) as typeof window.fetch;

try { localStorage.removeItem('motor.dutyCycles.v1'); } catch { /* ignore */ }

const theme = createTheme({ palette: { mode: 'dark' } });
createRoot(document.getElementById('root')!).render(
  <ThemeProvider theme={theme}><DutyCycleEditor /></ThemeProvider>,
);
