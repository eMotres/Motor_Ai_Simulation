/**
 * node --test — Mesh tab "Max element size" slider bounds.
 * 2026-09-28: Ø12 CIANO14 (floor 0.50 mm) — slider steps 0.02 mm but the chip
 * printed one decimal, so moves were invisible and the slider read as dead.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { meshSliderBounds, meshSliderValue, meshSizeLabel }
  from '../../../lib/meshSliderBounds.ts';

test('tiny-motor floor gives a live range below the cap', () => {
  const b = meshSliderBounds(0.5);
  assert.equal(b.max, 0.5);
  assert.ok(b.min < b.max && b.min >= 0.05);
  assert.ok(b.step > 0 && b.step <= (b.max - b.min));
  assert.equal(b.decimals, 2);
});

test('every step is visible on the chip', () => {
  const b = meshSliderBounds(0.5);
  const labels = new Set();
  for (let v = b.min; v <= b.max + 1e-9; v += b.step) labels.add(meshSizeLabel(v, b));
  const n = Math.floor((b.max - b.min) / b.step + 1e-9) + 1;
  assert.equal(labels.size, n);
  assert.equal(meshSizeLabel(0.32, b), '0.32 mm');
});

test('value sent is shown unchanged inside the range, clamped outside', () => {
  const b = meshSliderBounds(0.5);
  assert.equal(meshSliderValue(0.3, b), 0.3);
  assert.equal(meshSliderValue(4, b), 0.5);
  assert.equal(meshSliderValue(0.001, b), b.min);
});

test('very small floor never collapses min > max', () => {
  const b = meshSliderBounds(0.1);
  assert.ok(b.min <= b.max && b.step > 0);
});

test('no floor yet -> legacy range', () => {
  for (const f of [null, undefined, 0, -1, NaN]) {
    const b = meshSliderBounds(f);
    assert.deepEqual([b.min, b.max, b.step], [1.5, 8, 0.5]);
  }
});

test('big motor keeps one decimal', () => {
  const b = meshSliderBounds(8);
  assert.equal(b.decimals, 1);
  assert.equal(b.max, 8);
});
