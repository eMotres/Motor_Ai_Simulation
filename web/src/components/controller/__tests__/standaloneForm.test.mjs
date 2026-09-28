/**
 * The Controller tab's STANDALONE run (no motor, 2026-09-28) — its loud
 * validation and its wire body, tested without a browser.
 *
 * Verbatim copies of `standaloneProblems` / `standaloneSolveBody` from
 * controllerApi.ts (the module is TypeScript and reads `import.meta.env`,
 * which `node --test` cannot load — the same reason as controllerApi.test.mjs).
 * Changing those helpers has to change this file too.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const blank = v => (v === '' ? undefined : v);

function standaloneProblems(sa, s) {
  const out = [];
  if (!sa.on) return out;
  const need = (v, name) => {
    if (v === '' || v === undefined || v === null || !Number.isFinite(Number(v))) {
      out.push(`${name} is required for a standalone run`); return false;
    }
    return true;
  };
  if (need(sa.iPh, 'Phase current') && Number(sa.iPh) <= 0) out.push('Phase current must be positive');
  if (need(sa.f1, 'Fundamental frequency') && Number(sa.f1) <= 0) out.push('Fundamental frequency must be positive');
  if (need(sa.m, 'Modulation index') && Number(sa.m) <= 0) out.push('Modulation index must be positive');
  if (need(sa.pf, 'cos φ') && (Number(sa.pf) <= 0 || Number(sa.pf) > 1)) out.push('cos φ must be in (0, 1]');
  if (need(sa.nInv, 'Inverters') && ![1, 2].includes(Number(sa.nInv))) out.push('Inverters must be 1 or 2');
  if (need(s.vdc, 'DC link') && Number(s.vdc) <= 0) out.push('DC link must be positive');
  if (need(s.fsw, 'Carrier') && Number(s.fsw) <= 0) out.push('Carrier must be positive');
  if (sa.m !== '' && Number(sa.m) > (sa.scheme === 'svpwm' ? 2 / Math.sqrt(3) : 1) + 1e-9) {
    out.push(`m ${sa.m} is above the ${sa.scheme} linear limit — overmodulated`);
  }
  return out;
}

function standaloneSolveBody(base, sa) {
  const { topology: _t, set_split: _s, h_bridge_modulation: _h, mapping: _m, ...rest } = base;
  return {
    ...rest, standalone: true,
    n_inverters: blank(sa.nInv), phase_shift_deg: blank(sa.shift),
    i_phase_rms_A: blank(sa.iPh), f_elec_hz: blank(sa.f1),
    modulation_index: blank(sa.m), power_factor: blank(sa.pf),
    modulation_scheme: sa.scheme,
    // the same choice as the tab's PWM modulation — the typed scheme wins
    pwm_modulation: sa.scheme === 'svpwm' ? 'svpwm' : 'sine',
    power_direction: sa.direction === 'generator' ? 'generator' : 'motor',
  };
}

const CASE = { on: true, nInv: 2, shift: 30, iPh: 470, f1: 750, m: 0.9, pf: 0.9, scheme: 'svpwm' };

test('the customer case is complete', () => {
  assert.deepEqual(standaloneProblems(CASE, { vdc: 800, fsw: 15000 }), []);
});

test('off means nothing to check', () => {
  assert.deepEqual(standaloneProblems({ ...CASE, on: false, iPh: '' }, { vdc: '', fsw: '' }), []);
});

test('every missing value is named, never defaulted', () => {
  const p = standaloneProblems({ ...CASE, iPh: '', f1: '' }, { vdc: '', fsw: 15000 });
  assert.ok(p.some(x => x.startsWith('Phase current')));
  assert.ok(p.some(x => x.startsWith('Fundamental')));
  assert.ok(p.some(x => x.startsWith('DC link')));
});

test('cos φ above 1 and 3 inverters are refused', () => {
  const p = standaloneProblems({ ...CASE, pf: 1.2, nInv: 3 }, { vdc: 800, fsw: 15000 });
  assert.ok(p.includes('cos φ must be in (0, 1]'));
  assert.ok(p.includes('Inverters must be 1 or 2'));
});

test('overmodulation follows the scheme', () => {
  assert.deepEqual(standaloneProblems({ ...CASE, m: 1.1 }, { vdc: 800, fsw: 15000 }), []);
  assert.equal(standaloneProblems({ ...CASE, m: 1.1, scheme: 'spwm' },
                                  { vdc: 800, fsw: 15000 }).length, 1);
});

test('the wire body drops the motor-only fields and carries the typed point', () => {
  const b = standaloneSolveBody({ device: 'X', topology: 'one_3ph', set_split: 's',
    h_bridge_modulation: 'u', mapping: [], v_dc_V: 800, cooling: { mode: 'liquid' } }, CASE);
  assert.equal(b.standalone, true);
  assert.equal(b.topology, undefined);
  assert.equal(b.mapping, undefined);
  assert.equal(b.i_phase_rms_A, 470);
  assert.equal(b.n_inverters, 2);
  assert.equal(b.modulation_scheme, 'svpwm');
});

test('the power direction rides the standalone body, motor by default', () => {
  assert.equal(standaloneSolveBody({ topology: 'x' }, CASE).power_direction, 'motor');
  assert.equal(standaloneSolveBody({ topology: 'x' }, { ...CASE, direction: 'generator' })
    .power_direction, 'generator');
});
