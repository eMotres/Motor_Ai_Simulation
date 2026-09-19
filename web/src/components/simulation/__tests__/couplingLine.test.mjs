// node --test — the one LINE the Electromagnetic summary shows for a coupled
// run, and the tooltip behind it (coupledApi.couplingLine / couplingTooltip).
//
// The repo's convention for node tests: `node --test` cannot load the TS
// modules, so the pure functions under test are re-stated here verbatim and
// kept in sync — see lib/__tests__/machineBearings.test.mjs, which does the
// same for the same reason.
//
// WHAT IS WORTH TESTING is not string formatting; it is one rule.  On
// 2026-09-08 the coupled loop started iterating the BEARING temperature with
// the winding and the magnet, so the line grew a mechanical term — and a
// machine that names no bearings must show NO such term rather than "0 W".  An
// unknown mechanical loss printed as zero is an efficiency nobody measured, and
// this line is the most-read place it could appear.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// ── verbatim from coupledApi.ts ───────────────────────────────────────────
function couplingLine(c) {
  const limPart = c.mode === 'limited' ? String(c.limited?.part ?? '') : '';
  const atLimit = (part) => (limPart === part ? ' (at the limit)' : '');
  const m = c.magnet_temp_c == null ? null
    : `magnets ${c.magnet_temp_c.toFixed(0)} °C${atLimit('magnet')}`;
  const w = c.P_mech_extra_W;
  const mech = w == null ? null : `mechanical ${w.toFixed(w < 10 ? 1 : 0)} W`;
  const mb = c.mechanical;
  const mechNote = !mb ? null
    : mb.ok === false ? 'mechanics ⚠'
    : mb.contact_fallback ? `${String(mb.contact_fallback.pair ?? 'joint').replace('_', '–')} solved ${mb.contact_fallback.to ?? 'bonded'}`
    : null;
  const mo = mb?.modes;
  const modesTerm = !mo ? null
    : mo.ok === false ? 'modes ⚠'
    : mo.f1_hz == null ? null
    : `f₁ ${fmtHz(mo.f1_hz)}${(mo.n_flagged ?? 0) > 0 ? ' ⚠' : ''}`;
  const cr = mb?.critical_speeds;
  const critTerm = !cr ? null
    : cr.ok === false ? 'criticals ⚠'
    : cr.first_forward_rpm == null ? null
    : `crit ${fmtRpm(cr.first_forward_rpm)}${(cr.n_forward_below_rated ?? 0) > 0
        || (cr.first_forward_margin_pct != null && cr.first_forward_margin_pct < 10) ? ' ⚠' : ''}`;
  return [`winding ${c.coil_temp_c.toFixed(0)} °C${atLimit('winding')}`, m, mech,
    c.mode === 'limited'
      ? (limPart === 'winding' || limPart === 'magnet'
          ? `${c.iterations} it.` : `${c.iterations} it. · at the limit`)
      : `${c.iterations} it.${c.converged ? '' : ' ⚠'}`,
    regimeTerm(c.duty_cycle),
    mechNote, modesTerm, critTerm]
    .filter(Boolean).join(' · ');
}

function regimeTerm(r) {
  if (!r) return null;
  const flag = r.feasible === false || r.fits_requested === false ? ' ⚠' : '';
  if (r.kind === 'S2') {
    const t = r.t_on_allowable_s;
    return t == null ? null : `pull ${g1(t)} s${flag}`;
  }
  const ed = r.ed_allowable_pct;
  if (ed == null) return null;
  const cyc = r.ed_cycle_s ?? r.cycle_s;
  return `ED ${g1(ed)} %${cyc == null ? '' : ` of ${g1(cyc)} s`}${flag}`;
}

function coupledRegimeNotice(c) {
  const r = c?.duty_cycle;
  if (!r) return null;
  if (r.feasible !== false && r.fits_requested !== false) return null;
  return `Duty cycle: ${r.note || 'the duty does not fit'}`;
}

function g1(v) {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function fmtHz(f) {
  return f >= 10000 ? `${(f / 1000).toFixed(1)} kHz` : `${Math.round(f)} Hz`;
}

function fmtRpm(v) {
  const r = v >= 10000 ? Math.round(v / 100) * 100 : Math.round(v / 10) * 10;
  return `${r.toLocaleString('en-US').replace(/,/g, ' ')} rpm`;
}

function couplingTooltip(c) {
  const rows = (c.history || []).map(h => {
    const inn = `${h.T_coil_in.toFixed(1)} °C`
      + (h.T_magnet_in == null ? '' : ` / ${h.T_magnet_in.toFixed(1)} °C`);
    const out = h.T_coil_out == null ? '—'
      : `${h.T_coil_out.toFixed(1)} °C`
        + (h.T_magnet_out == null ? '' : ` / ${h.T_magnet_out.toFixed(1)} °C`);
    const hot = h.T_magnet_max == null ? ''
      : `, hottest magnet ${h.T_magnet_max.toFixed(1)} °C`;
    const mech = h.P_mech_extra_W == null ? ''
      : `, bearings + windage ${h.P_mech_extra_W.toFixed(1)} W`
        + (h.bearing_temp_c == null ? ''
           : ` at ${h.bearing_temp_c.toFixed(0)} °C`);
    return `${h.iter}. solved at ${inn} → ${out}${hot}${mech}`;
  });
  return rows;
}

// ── fixtures ──────────────────────────────────────────────────────────────
const WITH_BEARINGS = {
  coil_temp_c: 128.4, magnet_temp_c: 163.2, iterations: 3, converged: true,
  P_mech_extra_W: 62.7,
  history: [
    { iter: 1, T_coil_in: 120.0, T_magnet_in: 150.0, T_coil_out: 131.2,
      T_magnet_out: 165.0, T_magnet_max: 171.4, P_mech_extra_W: 60.1,
      bearing_temp_c: 88.0 },
  ],
};

const NO_BEARINGS = {
  coil_temp_c: 128.4, magnet_temp_c: 163.2, iterations: 3, converged: true,
  P_mech_extra_W: null,
  history: [
    { iter: 1, T_coil_in: 120.0, T_magnet_in: 150.0, T_coil_out: 131.2,
      T_magnet_out: 165.0, T_magnet_max: 171.4, P_mech_extra_W: null,
      bearing_temp_c: null },
  ],
};

test('the coupled line carries the mechanical loss the loop converged on', () => {
  assert.equal(couplingLine(WITH_BEARINGS),
    'winding 128 °C · magnets 163 °C · mechanical 63 W · 3 it.');
});

test('a machine with no bearings shows NO mechanical term, not 0 W', () => {
  const line = couplingLine(NO_BEARINGS);
  assert.equal(line, 'winding 128 °C · magnets 163 °C · 3 it.');
  assert.ok(!line.includes('mechanical'));
  assert.ok(!line.includes('0 W'));
});

test('a small mechanical loss keeps a decimal, a large one does not', () => {
  // 0 W and 63 W are both "63" at zero decimals; a 4 W bearing pair on a small
  // motor would read "4 W" and lose the only digit that distinguishes it from
  // nothing at all.
  assert.ok(couplingLine({ ...WITH_BEARINGS, P_mech_extra_W: 4.2 })
    .includes('mechanical 4.2 W'));
  assert.ok(couplingLine({ ...WITH_BEARINGS, P_mech_extra_W: 143.6 })
    .includes('mechanical 144 W'));
});

test('a run that did not settle still says so after the mechanical term', () => {
  const line = couplingLine({ ...WITH_BEARINGS, converged: false });
  assert.ok(line.endsWith('3 it. ⚠'), line);
  assert.ok(line.includes('mechanical 63 W'));
});

// 2026-09-13: the same coupled run now leaves the ring modes and the critical
// speeds (user: "чтобы к отчёту было всё готово"), so the line carries the
// first frequency and the first forward critical — and NOTHING for a run that
// did not ask for mechanics, never "0 Hz".
const WITH_MECH = {
  ...WITH_BEARINGS,
  mechanical: {
    ok: true, sf_min: 1.31, rpm: 20900,
    modes: { ok: true, body: 'rotor', n_modes: 12, f1_hz: 2104.3, f1_order: 2, n_flagged: 0 },
    critical_speeds: { ok: true, rated_rpm: 20900, first_forward_rpm: 48312,
                       first_forward_margin_pct: 56.7, n_forward_below_rated: 0 },
  },
};

test('the line carries the first mode and the first forward critical', () => {
  assert.equal(couplingLine(WITH_MECH),
    'winding 128 °C · magnets 163 °C · mechanical 63 W · 3 it. · f₁ 2104 Hz · crit 48 300 rpm');
});

test('a run without the mechanical step shows neither term', () => {
  const line = couplingLine(WITH_BEARINGS);
  assert.ok(!line.includes('f₁') && !line.includes('crit'), line);
});

test('a flagged separation, a critical below rated, or a refusal each raise ⚠', () => {
  const flagged = { ...WITH_MECH, mechanical: { ...WITH_MECH.mechanical,
    modes: { ...WITH_MECH.mechanical.modes, n_flagged: 1 } } };
  assert.ok(couplingLine(flagged).includes('f₁ 2104 Hz ⚠'));
  const below = { ...WITH_MECH, mechanical: { ...WITH_MECH.mechanical,
    critical_speeds: { ...WITH_MECH.mechanical.critical_speeds,
                       first_forward_rpm: 18400, first_forward_margin_pct: -13.6, n_forward_below_rated: 1 } } };
  assert.ok(couplingLine(below).includes('crit 18 400 rpm ⚠'));
  const refused = { ...WITH_MECH, mechanical: { ...WITH_MECH.mechanical,
    critical_speeds: { ok: false, error: 'impossible shaft line' } } };
  assert.ok(couplingLine(refused).endsWith('criticals ⚠'));
});

test('five-digit frequencies read in kHz', () => {
  const hi = { ...WITH_MECH, mechanical: { ...WITH_MECH.mechanical,
    modes: { ...WITH_MECH.mechanical.modes, f1_hz: 12430 } } };
  assert.ok(couplingLine(hi).includes('f₁ 12.4 kHz'));
});

test('each tooltip row names the bearing temperature it was billed at', () => {
  const [row] = couplingTooltip(WITH_BEARINGS);
  assert.ok(row.includes('bearings + windage 60.1 W at 88 °C'), row);
  // …and the row of a machine with no bearings gains nothing.
  const [bare] = couplingTooltip(NO_BEARINGS);
  assert.ok(!bare.includes('bearings'), bare);
  assert.ok(bare.includes('hottest magnet 171.4 °C'), bare);
});


// ── THE REGIME (2026-09-16) ──────────────────────────────────────────────────
// On an S2/S3 duty the loop no longer iterates to the temperature this point
// would reach if the pull never ended: it FINDS the duty ratio the limits allow
// and feeds back the temperatures at it.  Two rules are worth pinning — the
// card carries the ratio, and "does not fit" travels to the Run button as an
// ANSWER (the `Duty cycle:` prefix `lib/runNotice` reads as the info kind)
// rather than as a failure.
const S3_FITS = {
  coil_temp_c: 131, magnet_temp_c: 111, iterations: 3, converged: true,
  duty_cycle: { kind: 'S3', duty: 'peak', ed_allowable_pct: 21.6,
                ed_requested_pct: 20, ed_cycle_s: 60, fits_requested: true,
                feasible: true, note: 'it fits.' },
};
const S3_OVER = {
  ...S3_FITS,
  duty_cycle: { ...S3_FITS.duty_cycle, ed_requested_pct: 25,
                fits_requested: false,
                note: '21.6 % of a 60 s cycle (13 s on) is allowable; the 25 % '
                    + 'asked for does NOT fit under it.' },
};
const S2_RUN = {
  ...S3_FITS,
  duty_cycle: { kind: 'S2', duty: 'pull', t_on_allowable_s: 26.6,
                requested_t_on_s: 2, fits_requested: true, feasible: true },
};

test('the card carries the ratio the machine can hold', () => {
  assert.ok(couplingLine(S3_FITS).includes('ED 21.6 % of 60 s'));
  assert.ok(!couplingLine(S3_FITS).includes('⚠'), 'a ratio that fits is not a flag');
  assert.ok(couplingLine(S3_OVER).includes('ED 21.6 % of 60 s ⚠'));
  assert.ok(couplingLine(S2_RUN).includes('pull 26.6 s'));
});

test('a continuous duty grows no regime term at all', () => {
  const { duty_cycle, ...s1 } = S3_FITS;
  assert.ok(!couplingLine(s1).includes('ED'));
  assert.equal(coupledRegimeNotice(s1), null);
});

test('only a duty that does NOT fit reaches the Run button', () => {
  assert.equal(coupledRegimeNotice(S3_FITS), null);
  const n = coupledRegimeNotice(S3_OVER);
  assert.ok(n.startsWith('Duty cycle: '), 'the prefix runNotice classifies on');
  assert.ok(n.includes('does NOT fit under it.'));
});

// ── THE MACHINE AT THE LIMIT (owner 2026-09-18) ─────────────────────────────
// «так и расчёт тогда должен быть при катушках в 200 градусов, а не 184»: a
// `limits` run's final pass is solved with the limiting part exactly AT its
// limit, so the record's temperature IS the limit and the line says so on that
// term — the reader then knows the torque, the losses and R beside it are
// those of a 200 °C winding.  No ⚠: the loop was asked to stop, it did not
// fail to settle.
const L13_PEAK_LIMITED = {
  coil_temp_c: 200.0, magnet_temp_c: 45.18, iterations: 2, converged: false,
  P_mech_extra_W: 1.8, mode: 'limited', solve_to: 'limits',
  limited: { part: 'winding', limit_c: 200,
             temperatures_at_limit: { winding: 183.5, magnet: 43.78 },
             em_pass_at: { coil_c: 200.0, magnet_c: 45.18 } },
  history: [
    { iter: 1, T_coil_in: 200.0, T_magnet_in: 120.0, T_coil_out: 409.0,
      T_magnet_out: 95.0, T_magnet_max: 96.1, P_mech_extra_W: 1.8, bearing_temp_c: 40 },
    { iter: 2, phase: 'limit', T_coil_in: 200.0, T_magnet_in: 45.18,
      T_coil_out: null, T_magnet_out: null, T_magnet_max: 45.18,
      P_mech_extra_W: 1.8, bearing_temp_c: 43.6 },
  ],
};

test('a limited record says the winding IS at the limit, and drops the ⚠', () => {
  assert.equal(couplingLine(L13_PEAK_LIMITED),
    'winding 200 °C (at the limit) · magnets 45 °C · mechanical 1.8 W · 2 it.');
});

test('when the magnets limit, the words move to the magnet term', () => {
  const byMagnet = { ...L13_PEAK_LIMITED, coil_temp_c: 177.5, magnet_temp_c: 180,
    limited: { ...L13_PEAK_LIMITED.limited, part: 'magnet', limit_c: 180 } };
  assert.equal(couplingLine(byMagnet),
    'winding 178 °C · magnets 180 °C (at the limit) · mechanical 1.8 W · 2 it.');
});

test('a record limited by another part keeps the words on the iteration term', () => {
  const bySeat = { ...L13_PEAK_LIMITED, coil_temp_c: 150, magnet_temp_c: 60,
    limited: { ...L13_PEAK_LIMITED.limited, part: 'bearing', limit_c: 120 } };
  const line = couplingLine(bySeat);
  assert.ok(line.endsWith('2 it. · at the limit'), line);
  assert.ok(!line.includes('(at the limit)'), line);
  assert.ok(!line.includes('⚠'), line);
});

test('the shipped source prints the same words', () => {
  const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)),
                                '..', 'coupledApi.ts'), 'utf8');
  const fn = src.slice(src.indexOf('export function couplingLine'),
                       src.indexOf('export function regimeTerm'));
  assert.ok(fn.includes("' (at the limit)'"), fn);
  assert.ok(fn.includes("c.mode === 'limited'"), fn);
});

test('no feasible ratio at all is reported too', () => {
  const none = { ...S3_FITS, duty_cycle: {
    ...S3_FITS.duty_cycle, feasible: false, ed_allowable_pct: 0,
    fits_requested: false, note: 'no duty ratio is allowable at this point.' } };
  assert.ok(couplingLine(none).includes('ED 0 % of 60 s ⚠'));
  assert.equal(coupledRegimeNotice(none),
    'Duty cycle: no duty ratio is allowable at this point.');
});
