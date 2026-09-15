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

// ── verbatim from coupledApi.ts ───────────────────────────────────────────
function couplingLine(c) {
  const m = c.magnet_temp_c == null ? null : `magnets ${c.magnet_temp_c.toFixed(0)} °C`;
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
  return [`winding ${c.coil_temp_c.toFixed(0)} °C`, m, mech,
    `${c.iterations} it.${c.converged ? '' : ' ⚠'}`, mechNote, modesTerm, critTerm]
    .filter(Boolean).join(' · ');
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
