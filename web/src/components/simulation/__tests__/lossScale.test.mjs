// Loss view: ONE colour scale for all materials, true min/max, smooth ramp.
//
// Owner 2026-10-19, CIANO14 50 (L15, 49 A, 10 krpm): every copper strip was the
// same dark red.  The stored field has the gap-side strip 1.6x the far one
// (5.5e7 -> 8.6e7 W/m3, corner elements up to 2.7e8) but the old shared LOG scale
// had 11 bands over 3.6 decades and a 99.5-percentile ceiling (1.0e8), so the
// whole copper floor sat in the top band.  Fix: the range is the true min/max,
// the ramp is continuous, and the default axis is a power law (v^0.4), chosen so
// that copper strips AND the iron / magnet pattern are both readable.  The same
// colour is the same W/m3 in every material.
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
const THREE = {
  BufferGeometry: class { setAttribute() {} },
  BufferAttribute: class { constructor(a, n) { this.array = a; this.itemSize = n; } },
};
const mod = { exports: {} };
runInNewContext(js, {
  module: mod, exports: mod.exports, console,
  require: (name) => { if (name === 'three') return THREE; throw new Error(name); },
});
const { emOutputs, normaliser, LOSS_GAMMA } = mod.exports;

// One triangle per element, own vertices: nodal averaging cannot mix classes.
function payload(spec) {
  const vertices = [], triangles = [], dom = [], ld = [];
  for (const [d, vals] of spec) {
    for (const v of vals) {
      const i = vertices.length;
      vertices.push([i * 1e-3, 0], [i * 1e-3 + 5e-4, 0], [i * 1e-3, 5e-4]);
      triangles.push([i, i + 1, i + 2]);
      dom.push(d); ld.push(v);
    }
  }
  return {
    vertices, triangles, domain_per_tri: dom, loss_density_per_tri: ld,
    loss_density_unmodelled: ['air'], extent: [0, 1, 0, 1], outlines: [],
  };
}
const rep = (v, n = 30) => Array.from({ length: n }, () => v);
// owner's run: copper floor 5.5e7, gap-side strip 8.6e7, one corner 2.66e8;
// stator iron 1.3e6 .. 5.8e6, magnets ~1.2e6 with a tail from 5e3; air = 0
const P = payload([
  [0, rep(0)],
  [1, [...rep(1.3e6), ...rep(5.8e6, 5), 4.5e4]],
  [100, [...rep(1.2e6), 5.36e3]],
  [200, [...rep(5.5e7, 40), ...rep(8.6e7, 10), 2.66e8]],
]);
const scaleOf = (o = {}) => emOutputs(P, 'Loss', o).scale;

test('default is ONE continuous power-law scale over the TRUE min and max', () => {
  const sc = scaleOf();
  assert.equal(sc.mapping, 'power');
  assert.equal(sc.gamma, LOSS_GAMMA);
  assert.equal(sc.continuous, true);
  assert.equal(sc.vmax, 2.66e8);            // true max, not a percentile
  assert.equal(sc.vmin, 5.36e3);            // true min of the drawn, non-zero elements
  assert.match(sc.unit, /W\/m³/);
  assert.match(sc.unit, /one scale for all materials/);
  assert.match(sc.note, /ONE colour scale for all materials/);
});

test('the copper floor is not in the top of the ramp and the strips differ clearly', () => {
  const t = normaliser(scaleOf());
  const floor = t(5.5e7), gap = t(8.6e7);
  assert.ok(floor < 0.7, `floor at ${floor}`);
  assert.ok(gap - floor >= 0.08, `strip-to-strip change ${gap - floor}`);
  assert.ok(t(2.66e8) === 1);
});

test('iron and magnets keep a readable pattern on the same scale', () => {
  const t = normaliser(scaleOf());
  assert.ok(t(1.3e6) > 0.08, 'iron floor is not the bottom colour');
  assert.ok(t(5.8e6) - t(1.3e6) >= 0.06, 'tooth-tip vs yoke iron differ clearly');
});

test('why not linear or log: each hides one of the two', () => {
  const lin = normaliser(scaleOf({ lossScale: 'linear' }));
  const log = normaliser(scaleOf({ lossScale: 'log' }));
  assert.ok(lin(5.8e6) < 0.03, 'linear: all iron is the bottom colour');
  assert.ok(log(8.6e7) - log(5.5e7) < 0.05, 'log: copper strips are one colour');
});

test('legend ticks are the scale: edge(k) maps back to k / bands, ends are min and max', () => {
  const sc = scaleOf();
  const t = normaliser(sc);
  assert.equal(sc.edge(0), sc.vmin);
  assert.ok(Math.abs(sc.edge(sc.bands) - sc.vmax) < 1);
  let prev = -Infinity;
  for (let k = 0; k <= sc.bands; k++) {
    const e = sc.edge(k);
    assert.ok(e > prev); prev = e;
    assert.ok(Math.abs(t(e) - k / sc.bands) < 1e-9, `tick ${k}`);
  }
});

test('the old boolean still selects log / linear; every shared axis is continuous', () => {
  assert.equal(scaleOf({ logLoss: true }).mapping, 'log');
  assert.equal(scaleOf({ logLoss: false }).mapping, 'linear');
  for (const k of ['power', 'linear', 'log']) {
    const sc = scaleOf({ lossScale: k });
    assert.equal(sc.continuous, true);
    assert.equal(sc.vmax, 2.66e8);
  }
});

test('per-material stays an opt-in and is labelled as such', () => {
  const sc = scaleOf({ perMaterialLoss: true });
  assert.match(sc.unit, /per-material scale/);
  assert.ok(!sc.continuous);
});
