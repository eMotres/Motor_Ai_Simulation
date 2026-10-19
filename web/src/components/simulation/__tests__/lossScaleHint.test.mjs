// Loss view, shared colour scale: a material pinned in the top band must be
// reported.  Owner 2026-10-19, CIANO14 50 (L15, 49 A, 10 krpm): the solved
// copper map HAS the gradient (gap-side strip 8.6e7 W/m3 vs 5.5e7 far side,
// corners to 2.7e8) but on the shared log scale all of it lands in the top band
// (11 bands over ~3.6 decades, lower edge of the top band 5.2e7), so every
// strip was one dark red.  The picture is honest; the hint is what tells the
// reader to switch to "per-material".
//
// Executes the real fieldView.ts (transpiled), no copy of the logic.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const src = readFileSync(new URL('../fieldView.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const mod = { exports: {} };
runInNewContext(js, {
  module: mod, exports: mod.exports, console,
  require: (name) => { if (name === 'three') return {}; throw new Error(name); },
});
const { lossScaleHint, N_BANDS } = mod.exports;

// deterministic pseudo-random in [0,1)
let seed = 12345;
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
const logUni = (lo, hi) => Math.pow(10, Math.log10(lo) + rnd() * (Math.log10(hi) - Math.log10(lo)));

/** A small machine: iron + magnets (log-spread) + `nCu` copper elements drawn by `cu()`. */
function payload(cu, nCu = 400) {
  const dom = [], ld = [];
  const add = (d, n, f) => { for (let i = 0; i < n; i++) { dom.push(d); ld.push(f()); } };
  add(0, 300, () => 0);                       // air: exactly zero, never drawn
  add(1, 800, () => logUni(5e4, 5e6));        // stator iron
  add(5, 500, () => logUni(5e3, 2e6));        // rotor iron
  add(100, 400, () => logUni(9e3, 2e7));      // magnet
  add(200, nCu, cu);                          // copper
  return {
    triangles: dom.map(() => [0, 0, 0]), domain_per_tri: dom,
    loss_density_per_tri: ld, loss_density_unmodelled: ['air'],
  };
}
const opts = { logLoss: true, perMaterialLoss: false };

test('copper pinned in the top band of the shared log scale is reported', () => {
  // the owner's numbers: DC floor 5.5e7, gap-side strips up to 8.6e7, corners 2.7e8
  const p = payload(() => 5.2e7 + rnd() * 5e7 + (rnd() < 0.01 ? 1.5e8 : 0), 800);
  const h = lossScaleHint(p, opts);
  assert.ok(h, 'hint expected');
  assert.equal(h.name, 'copper');
  assert.ok(h.topBandShare >= 0.75, `top-band share ${h.topBandShare}`);
  assert.ok(h.spread >= 1.3, `spread ${h.spread}`);
  assert.equal(N_BANDS, 11);
});

test('per-material mode never carries the hint (the structure is visible there)', () => {
  const p = payload(() => 5.2e7 + rnd() * 5e7);
  assert.equal(lossScaleHint(p, { logLoss: true, perMaterialLoss: true }), null);
});

test('a copper map with no structure of its own is not flagged', () => {
  const p = payload(() => 5.5e7 * (1 + 0.02 * rnd()));
  assert.equal(lossScaleHint(p, opts), null);
});

test('copper in the middle of the scale (not saturated) is not flagged', () => {
  // copper well below the top band, magnets/iron define the range
  const p = payload(() => 2e5 * (1 + 2 * rnd()));
  assert.equal(lossScaleHint(p, opts), null);
});

test('the linear shared scale is judged on its own top band', () => {
  // On a linear axis the top band starts at ~91 % of the range, so copper with
  // a floor at 55 % of vmax is NOT pinned there: no hint.
  const p = payload(() => 5.5e7 + rnd() * 3e7 + (rnd() < 0.1 ? 1e8 : 0));
  assert.equal(lossScaleHint(p, { logLoss: false, perMaterialLoss: false }), null);
});
