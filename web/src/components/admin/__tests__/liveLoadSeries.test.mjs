/** Series-shaping for the Admin · Overview live-load panel, tested without a
 * browser (`liveLoadSeries.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  mergeNodeSeries, mergeLoadSeries, isCollectingHistory, serverLabel, threadsUsed,
  cpuTooltipValue, memTooltipValue, clusterLine,
} from '../liveLoadSeries.ts';

// ── mergeNodeSeries: separate CPU/RAM series per server (the "only CPU shows,
// RAM is missing/indistinguishable" bug: there was never a Line for `_mem`) ──
test('merges per-node points by ts, prefixes CPU/RAM keys separately, and means both across the cluster', () => {
  const rows = mergeNodeSeries({
    eu1: [{ ts: 100, cpu: 20, mem: 40 }, { ts: 160, cpu: 60, mem: 50 }],
    eu2: [{ ts: 100, cpu: 80, mem: 10 }],
  });
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    ts: 100, eu1_cpu: 20, eu1_mem: 40, eu2_cpu: 80, eu2_mem: 10,
    cluster_cpu: 50, cluster_mem: 25,
  });
  // only one node reported at ts=160 -> cluster means are just that node's values
  assert.equal(rows[1].cluster_cpu, 60);
  assert.equal(rows[1].cluster_mem, 50);
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

// ── per-server labels/tooltips: cores/threads from the node agent's sample ──
test('serverLabel shows threads and physical cores when both are known', () => {
  assert.equal(serverLabel('eu1', { threads: 16, cores: 8 }), 'eu1 (16 threads / 8 cores)');
});

test('serverLabel falls back to threads-only, then the bare id, as info goes missing', () => {
  assert.equal(serverLabel('eu1', { threads: 16 }), 'eu1 (16 threads)');
  assert.equal(serverLabel('eu1', {}), 'eu1');
});

test('threadsUsed multiplies threads by CPU %, or is null with no thread count', () => {
  assert.equal(threadsUsed(16, 72), 11.52);
  assert.equal(threadsUsed(undefined, 72), null);
  assert.equal(threadsUsed(0, 72), null);
});

test('cpuTooltipValue appends threads-used when threads are known', () => {
  assert.equal(cpuTooltipValue(72, 16), '72.0 % ≈ 11.5 threads used');
  assert.equal(cpuTooltipValue(72), '72.0 %');
});

test('memTooltipValue appends GB used/total when the node\'s RAM total is known', () => {
  const total = 16 * 1024 ** 3;
  assert.equal(memTooltipValue(9, total), '9.0 % ≈ 1.4 / 16.0 GB');
  assert.equal(memTooltipValue(9), '9.0 %');
});

test('clusterLine formats the fleet total line', () => {
  assert.equal(clusterLine(16, 10.8), 'cluster: 16 threads, 10.8 used');
});

// ── mergeLoadSeries: user CPU % + outside-app CPU % into one stacked chart ──
test('merges user and outside-app rows sharing a ts into one row', () => {
  const rows = mergeLoadSeries(
    [{ ts: 100, alice: 40, other: 5 }],
    [{ ts: 100, mesher_1: 60, host: 10 }],
  );
  assert.deepEqual(rows, [{ ts: 100, alice: 40, other: 5, mesher_1: 60, host: 10 }]);
});

test('a ts present in only one of the two series is kept as-is', () => {
  const rows = mergeLoadSeries(
    [{ ts: 100, alice: 40 }],
    [{ ts: 160, mesher_1: 60 }],
  );
  assert.deepEqual(rows, [{ ts: 100, alice: 40 }, { ts: 160, mesher_1: 60 }]);
});

test('mergeLoadSeries sorts by ts and handles empty inputs', () => {
  assert.deepEqual(mergeLoadSeries([], []), []);
  const rows = mergeLoadSeries([{ ts: 200, alice: 1 }], [{ ts: 100, host: 2 }]);
  assert.deepEqual(rows.map((r) => r.ts), [100, 200]);
});

test('a plain "other" from the user series is not clobbered by the outside-app overflow bucket', () => {
  // job_usage.user_load_series's overflow bucket is "other"; cluster_monitor's
  // is the distinct "outside-other" precisely so this merge cannot collide.
  const rows = mergeLoadSeries(
    [{ ts: 100, alice: 40, other: 5 }],
    [{ ts: 100, mesher_1: 60, 'outside-other': 3 }],
  );
  assert.deepEqual(rows, [{ ts: 100, alice: 40, other: 5, mesher_1: 60, 'outside-other': 3 }]);
});

// ── isCollectingHistory: "collecting since HH:MM" vs a broken-looking chart ─
test('short series soon after monitoring started -> collecting', () => {
  const now = 10_000;
  assert.equal(isCollectingHistory(1, now - 300, now), true);
  assert.equal(isCollectingHistory(0, now - 3599, now), true);
});

test('short series long after monitoring started -> genuinely idle, not "collecting"', () => {
  const now = 10_000;
  assert.equal(isCollectingHistory(1, now - 7200, now), false);
});

test('enough points -> never flagged as collecting, monitoringSince unknown -> never flagged', () => {
  const now = 10_000;
  assert.equal(isCollectingHistory(5, now - 60, now), false);
  assert.equal(isCollectingHistory(0, null, now), false);
});
