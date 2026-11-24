import test from 'node:test';
import assert from 'node:assert/strict';
const hooks = await import('node:module');
if (typeof hooks.registerHooks === 'function') {
  hooks.registerHooks({
    resolve(specifier, context, nextResolve) {
      try { return nextResolve(specifier, context); } catch (err) {
        if (/^\.{1,2}\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
        throw err;
      }
    },
  });
}
const { groupFailures, geometryGroupText, violationWhat } = await import('../sweepFailures.ts');

const EN = { sweepFailGeometry: 'geometry rejected: {what}', sweepFailOverlap: '{a} overlap {b}', sweepFailOverlapSelf: '{a} overlap each other',
  sweepFailPart_magnet: 'magnets', sweepFailPart_rotor: 'the rotor', sweepFailBound: 'a size does not fit its bound' };
const tr = (k, a = {}) => (EN[k] ?? k).replace(/\{(\w+)\}/g, (_, n) => String(a[n]));

const rej = (v, op, code = 'magnet_overlaps_magnet') => ({ feasible: false, geometry_rejected: true, overrides: { magnet_fill_up: v }, op_index: op,
  error: '2 geometry violations\n  • Magnet 1 and Magnet 2 overlap by 0.4 mm²', geometry_violations: [{ code, likely_params: ['magnet_fill_up'] }] });
const ok = (v) => ({ feasible: true, overrides: { magnet_fill_up: v } });

test('rejected geometries are named by class with the values that always fail, the raw reason kept', () => {
  const pts = [ok(1.0), ok(1.0), rej(1.5, 0), rej(1.5, 1), rej(2.0, 0), rej(2.0, 1)];
  const groups = groupFailures(pts, tr);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].cls, 'geometry');
  assert.equal(groups[0].n, 4);
  assert.equal(geometryGroupText(groups[0], tr), 'geometry rejected: magnets overlap each other (magnet_fill_up 1.5 and 2)');
  assert.match(groups[0].full, /Magnet 1 and Magnet 2 overlap/);
});

test('different reason classes are separate groups, not one mesh code for all', () => {
  const pts = [ok(1.0), rej(1.5, 0), rej(2.0, 0, 'magnet_overlaps_rotor'),
    { feasible: false, error: 'timeout after 600 s', overrides: { magnet_fill_up: 1.0 } }];
  const groups = groupFailures(pts, tr);
  assert.deepEqual(groups.map(g => g.cls).sort(), ['geometry', 'geometry', 'timeout']);
  assert.ok(groups.some(g => g.what === 'magnets overlap the rotor'));
});

test('a wrapped solver error is peeled to its code', () => {
  const g = groupFailures([{ feasible: false, error: 'HTTPException: 422: tdm_mesh_not_periodic: x', overrides: {} }], tr);
  assert.equal(g[0].cls, 'build');
  assert.equal(g[0].reason, 'tdm_mesh_not_periodic');
  assert.match(g[0].hint, /Periodic/);
});

test('violationWhat reads an unknown code as words', () => {
  assert.equal(violationWhat('iron_disconnected', tr), 'iron disconnected');
});
