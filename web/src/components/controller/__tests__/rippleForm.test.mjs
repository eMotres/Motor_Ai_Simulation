/**
 * The Controller tab's PWM-ripple inputs (2026-09-28): loud validation, the
 * solve-body fields and the save/restore round trip, tested without a browser.
 *
 * Verbatim copies of `rippleProblems` / `rippleSolveFields` / `rippleForSave`
 * / `rippleFromSettings` from controllerApi.ts (TypeScript + import.meta.env
 * — the same reason as controllerApi.test.mjs). Change both together.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const blank = v => (v === '' ? undefined : v);
const toFormNumber = v => (v === null || v === undefined) ? '' : v;
const toSaveNumber = v => (v === '' ? null : v);

const DEFAULT_RIPPLE = {
  ld: '', lq: '', lsub: '', lxyPct: '', l0: '', neutral: 'isolated',
  interleave: 0, thdLimit: '',
};

function rippleFromSettings(block) {
  if (!block) return DEFAULT_RIPPLE;
  return {
    ld: toFormNumber(block.ripple_l_d_uH), lq: toFormNumber(block.ripple_l_q_uH),
    lsub: toFormNumber(block.ripple_l_sub_uH), lxyPct: toFormNumber(block.ripple_l_xy_pct),
    l0: toFormNumber(block.ripple_l_zero_uH),
    neutral: block.ripple_neutral || DEFAULT_RIPPLE.neutral,
    interleave: block.carrier_interleave_deg ?? DEFAULT_RIPPLE.interleave,
    thdLimit: toFormNumber(block.thd_limit_pct),
  };
}

function rippleForSave(r) {
  return {
    ripple_l_d_uH: toSaveNumber(r.ld), ripple_l_q_uH: toSaveNumber(r.lq),
    ripple_l_sub_uH: toSaveNumber(r.lsub), ripple_l_xy_pct: toSaveNumber(r.lxyPct),
    ripple_l_zero_uH: toSaveNumber(r.l0), ripple_neutral: r.neutral || null,
    carrier_interleave_deg: toSaveNumber(r.interleave),
    thd_limit_pct: toSaveNumber(r.thdLimit),
  };
}

function rippleSolveFields(r) {
  return {
    ripple_l_d_uH: blank(r.ld), ripple_l_q_uH: blank(r.lq),
    ripple_l_sub_uH: blank(r.lsub), ripple_l_xy_pct: blank(r.lxyPct),
    ripple_l_zero_uH: blank(r.l0), ripple_neutral: r.neutral || undefined,
    carrier_interleave_deg: blank(r.interleave), thd_limit_pct: blank(r.thdLimit),
  };
}

function rippleProblems(r, nInverters) {
  const out = [];
  const pos = (v, name) => {
    if (v !== '' && !(Number(v) > 0)) out.push(`${name} must be positive`);
  };
  pos(r.ld, 'L_d'); pos(r.lq, 'L_q'); pos(r.lsub, 'L commutating');
  pos(r.lxyPct, 'L_xy'); pos(r.l0, 'L_0'); pos(r.thdLimit, 'THD limit');
  if (r.interleave !== '' && !(Number(r.interleave) >= 0 && Number(r.interleave) <= 180)) {
    out.push('Carrier interleave must be 0-180°');
  }
  const hasL = r.ld !== '' || r.lsub !== '';
  if (nInverters === 2 && hasL && r.lxyPct === '') {
    out.push('L_xy is required for two inverters (ripple)');
  }
  if (nInverters === 2 && r.neutral === 'common' && r.l0 === '') {
    out.push('L_0 is required with the two neutrals tied');
  }
  return out;
}

const REF = { ...DEFAULT_RIPPLE, ld: 90, lxyPct: 20 };

test('the reference dual 3-phase case is complete', () => {
  assert.deepEqual(rippleProblems(REF, 2), []);
});

test('two inverters with an L but no L_xy is refused loudly', () => {
  assert.match(rippleProblems({ ...REF, lxyPct: '' }, 2).join(), /L_xy is required/);
  assert.deepEqual(rippleProblems({ ...REF, lxyPct: '' }, 1), []);
});

test('tied neutrals need L_0; bad numbers are named', () => {
  assert.match(rippleProblems({ ...REF, neutral: 'common' }, 2).join(), /L_0 is required/);
  const bad = rippleProblems({ ...REF, ld: -1, interleave: 200, thdLimit: 0 }, 2).join();
  assert.match(bad, /L_d must be positive/);
  assert.match(bad, /0-180/);
  assert.match(bad, /THD limit must be positive/);
});

test('blank boxes vanish from the solve body (the duty resolves L)', () => {
  const b = JSON.parse(JSON.stringify(rippleSolveFields(DEFAULT_RIPPLE)));
  assert.deepEqual(b, { ripple_neutral: 'isolated', carrier_interleave_deg: 0 });
  assert.equal(rippleSolveFields(REF).ripple_l_xy_pct, 20);
});

test('save -> restore round trip', () => {
  const r = { ...REF, lq: 120, thdLimit: 8, interleave: 90, neutral: 'common', l0: 10 };
  assert.deepEqual(rippleFromSettings(rippleForSave(r)), r);
  assert.deepEqual(rippleFromSettings({}), { ...DEFAULT_RIPPLE });
});
