// Evaluate the Configure tuner (web/src/lib/motorScaling.ts, unmodified) at
// the audit corners.  usage: node tuner_eval.mjs passport.json corners.json
// passport.json = the stored {passport, fit, geo, poles, slots} card blob.
// corners.json  = [{id, L, N, h, I, rpm}]  (nP fixed at the passport's).
import { readFileSync } from 'node:fs';
import { scaleMotor, maxCurrent } from '../web/src/lib/motorScaling.ts';

const card = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const corners = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const p2d = { ...card.passport, end3d: null };   // 2-D numbers (FEM is 2-D)
const p3d = card.passport;
const out = [];
for (const c of corners) {
  const k = { N: c.N, L_mm: c.L, wireH_mm: c.h, nP: p2d.nP0, I_A: c.I, rpm: c.rpm };
  const r = scaleMotor(p2d, k, card.poles);
  const r3 = scaleMotor(p3d, k, card.poles);
  out.push({ id: c.id, T: r.T_Nm, Vemf: r.Vemf_peak_V, Vph: r.Vphase_peak_V, R: r.R_ohm,
             Pcu: r.P_cu_W, Pfe: r.P_fe_W, Pmag: r.P_mag_W, Ploss: r.P_loss_W,
             eta: r.efficiency, k3d: r3.k_end3d, T3d: r3.T_Nm, Imax: maxCurrent(p2d, k),
             Pcu_dc: 3 * c.I * c.I * r.R });
}
console.log(JSON.stringify(out, null, 1));
