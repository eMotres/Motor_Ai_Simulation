/**
 * Six-phase winding helpers (2026-09-28), tested without a browser.
 *
 * Verbatim copies of `sixPhaseProblem` / `normalizeSet1Paths` / `sixPhaseLine`
 * / `sixPhaseCells` from ../sixPhase.ts (TypeScript — node cannot import it,
 * the same reason as the controller tests). Change both together.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

function sixPhaseProblem(nParallel) {
  const n = Math.round(Number(nParallel) || 0);
  if (n < 2 || n % 2) {
    return `6 phases split the parallel paths into two sets — this connection has ${n} path${n === 1 ? '' : 's'}; pick one with an even number (e.g. 2S-2P)`;
  }
  return null;
}

function normalizeSet1Paths(text) {
  const t = String(text ?? '').trim();
  if (t === '') return '';
  const parts = t.split(/[\s,;]+/).filter(Boolean);
  if (!parts.every(p => /^\d+$/.test(p))) return null;
  return Array.from(new Set(parts.map(Number))).sort((a, b) => a - b).join(',');
}

function sixPhaseLine(w) {
  if (!w || Number(w.phases ?? 3) !== 6) return '';
  if (w.six_phase_error) return `⚠ ${w.six_phase_error}`;
  const s = w.six_phase;
  if (!s) return '';
  return `set 1: path ${s.set1_paths.join(', ')} · set 2: path ${s.set2_paths.join(', ')} · in phase · neutrals ${s.neutrals}`;
}

const f = (v, d) => v == null || !Number.isFinite(v) ? '—' : v.toFixed(d);

function sixPhaseCells(r) {
  if (!r) return null;
  const ind = r.inductances;
  const s1 = (r.sets ?? []).find(s => s.set === 1);
  return {
    lxy: ind?.Lxy_mH != null ? f(ind.Lxy_mH, 4) : '—',
    lxyPct: ind?.Lxy_pct_of_Ld != null ? `${f(ind.Lxy_pct_of_Ld, 1)} % of L_d,set` : '',
    perSet: s1 ? `${f(s1.I_phase_rms_A, 1)} A · ${f(s1.V1_phase_peak_V, 1)} V` : '—',
  };
}

test('odd or single parallel paths are refused before sending', () => {
  assert.match(sixPhaseProblem(1), /1 path;/);
  assert.match(sixPhaseProblem(3), /even number/);
  assert.equal(sixPhaseProblem(2), null);
  assert.equal(sixPhaseProblem(4), null);
});

test('set-1 paths are normalised, junk is rejected', () => {
  assert.equal(normalizeSet1Paths(' 3, 1 ;1 '), '1,3');
  assert.equal(normalizeSet1Paths(''), '');
  assert.equal(normalizeSet1Paths('1,a'), null);
});

test('the line shows the sets, or the backend reason', () => {
  assert.equal(sixPhaseLine({ phases: 3 }), '');
  assert.equal(sixPhaseLine({ phases: 6, six_phase: {
    set1_paths: [1], set2_paths: [2], paths_per_set: 1, neutrals: 'isolated' } }),
  'set 1: path 1 · set 2: path 2 · in phase · neutrals isolated');
  assert.match(sixPhaseLine({ phases: 6, six_phase_error: 'needs even paths' }), /^⚠ needs/);
});

test('result cells: L_xy and the per-set current / voltage', () => {
  const c = sixPhaseCells({ inductances: { Lxy_mH: 0.002637, Lxy_pct_of_Ld: 96.64 },
    sets: [{ set: 1, I_phase_rms_A: 30, V1_phase_peak_V: 2.75 }] });
  assert.deepEqual(c, { lxy: '0.0026', lxyPct: '96.6 % of L_d,set', perSet: '30.0 A · 2.8 V' });
  assert.equal(sixPhaseCells(null), null);
  assert.equal(sixPhaseCells({}).lxy, '—');
});
