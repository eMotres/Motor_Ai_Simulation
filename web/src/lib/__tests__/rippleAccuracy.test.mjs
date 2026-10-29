/**
 * node --test — lib/rippleAccuracy.ts: which torque ripple may be quoted
 * (owner 2026-10-29, option 1).  The web twin of motor_ai_sim/ripple_accuracy.py
 * (tests/test_ripple_refine.py pins the same cases there).
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { quotedRipple, canRefineRipple, RIPPLE_REFINE_LAYERS } from '../rippleAccuracy.ts';

const BASE = { T_ripple_pct: 3.3, ripple_mesh_limited: true,
               ripple_self_check_rel: 0.52, gap_layers_effective: 1 };

test('a failed ring check makes the ripple not accurate and refinable', () => {
  const q = quotedRipple(BASE);
  assert.deepEqual([q.value, q.accurate, q.refined, q.layers, q.ringError], [3.3, false, false, 1, 0.52]);
  assert.equal(canRefineRipple(BASE), true);
  assert.equal(RIPPLE_REFINE_LAYERS, 3);
});

test('the refined ripple wins and keeps the base beside it', () => {
  const s = { ...BASE, ripple_refined: { T_ripple_pct: 1.9, passed: true, gap_layers_per_side: 3,
    base_gap_layers_per_side: 1, self_check_rel_to_ripple_scale: 0.03 } };
  const q = quotedRipple(s);
  assert.deepEqual([q.value, q.accurate, q.refined, q.layers, q.baseValue, q.baseLayers],
    [1.9, true, true, 3, 3.3, 1]);
  assert.equal(canRefineRipple(s), false);
});

test('a passing or unchecked run is quoted as is, never offered a refine', () => {
  assert.equal(canRefineRipple({ T_ripple_pct: 2, ripple_mesh_limited: false }), false);
  const q = quotedRipple({}, 4.2);
  assert.deepEqual([q.value, q.accurate], [4.2, null]);
  assert.equal(canRefineRipple({}), false);
});
