/** Series-shaping for the Admin · Overview live-load panel, tested without a
 * browser (`liveLoadSeries.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeNodeSeries } from '../liveLoadSeries.ts';

test('merges per-node points by ts, prefixes keys, and means the cluster CPU', () => {
  const rows = mergeNodeSeries({
    eu1: [{ ts: 100, cpu: 20, mem: 40 }, { ts: 160, cpu: 60, mem: 50 }],
    eu2: [{ ts: 100, cpu: 80, mem: 10 }],
  });
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], { ts: 100, eu1_cpu: 20, eu1_mem: 40, eu2_cpu: 80, eu2_mem: 10, cluster_cpu: 50 });
  // only one node reported at ts=160 -> cluster mean is just that node's value
  assert.equal(rows[1].cluster_cpu, 60);
});

test('no nodes -> empty series, not a crash', () => {
  assert.deepEqual(mergeNodeSeries({}), []);
});

test('rows are sorted by ts even if nodes report out of order', () => {
  const rows = mergeNodeSeries({
    a: [{ ts: 200, cpu: 1, mem: 1 }, { ts: 100, cpu: 2, mem: 2 }],
  });
  assert.deepEqual(rows.map((r) => r.ts), [100, 200]);
});
