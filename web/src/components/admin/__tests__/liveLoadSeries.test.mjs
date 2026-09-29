/** Series-shaping for the Admin · Overview live-load panel, tested without a
 * browser (`liveLoadSeries.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  mergeNodeSeries, mergeLoadSeries, isCollectingHistory, serverLabel, threadsUsed,
  cpuTooltipValue, memTooltipValue, ramNowLabel, parseServerSeriesKey, clusterLine,
  computeXAxis, RANGE_LOOKBACK_S,
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

test('memTooltipValue: GB first, then percent in parens, when the node\'s RAM total is known', () => {
  const total = 16 * 1024 ** 3;
  assert.equal(memTooltipValue(9, total), '1.4 / 16.0 GB (9.0 %)');
  assert.equal(memTooltipValue(9), '9.0 %');
});

test('ramNowLabel: same GB format as the tooltip, from real used/total bytes (not derived from a %)', () => {
  const used = 23.4 * 1024 ** 3, total = 62.7 * 1024 ** 3;
  assert.equal(ramNowLabel(used, total), '23.4 / 62.7 GB (37.3 %)');
});

test('ramNowLabel falls back to a dash when used or total is missing', () => {
  assert.equal(ramNowLabel(undefined, 1e9), '—');
  assert.equal(ramNowLabel(1e9, undefined), '—');
  assert.equal(ramNowLabel(0, 1e9), '—');
});

// ── parseServerSeriesKey: the regression this locks in ──────────────────────
// PR #61 split one combined tooltip into per-chart cpuTooltip/memTooltip
// closures and, in the split, looked nodes up by the RAW dataKey ("eu1_cpu")
// instead of the bare server id ("eu1") -- nodeMeta[key] was always
// undefined, so every per-server tooltip silently fell back to a bare "%"
// with no GB/threads, and the legend name showed the raw suffixed key. This
// pins the correct parse so that mistake can't be reintroduced unnoticed.
test('parseServerSeriesKey splits a per-server dataKey into id + kind', () => {
  assert.deepEqual(parseServerSeriesKey('eu1_cpu'), { id: 'eu1', kind: 'cpu' });
  assert.deepEqual(parseServerSeriesKey('eu1_mem'), { id: 'eu1', kind: 'mem' });
  assert.deepEqual(parseServerSeriesKey('eu-west-2_cpu'), { id: 'eu-west-2', kind: 'cpu' });
});

test('parseServerSeriesKey returns null for keys with no _cpu/_mem suffix', () => {
  // "cluster_cpu"/"cluster_mem" DO match this generic pattern (id="cluster")
  // -- callers check those two literal keys first, same as before this
  // function existed; this only documents what does NOT match at all.
  assert.equal(parseServerSeriesKey('alice'), null);
  assert.equal(parseServerSeriesKey('mesher_1'), null);
  assert.equal(parseServerSeriesKey('other'), null);
});

test('clusterLine formats the fleet total line', () => {
  assert.equal(clusterLine(16, 10.8), 'cluster: 16 threads, 10.8 used');
});

// ── computeXAxis: one shared [now-range, now] domain + tick set ────────────
// Owner: the three charts must share the same time domain and the same
// ticks -- each was previously computed per-chart from that chart's own
// data, so a different point count per chart (independent series merges)
// could drift the plotted time ranges apart.
test('computeXAxis domain is exactly [now - lookback, now] for the range', () => {
  const now = 1_700_000_000;
  for (const range of Object.keys(RANGE_LOOKBACK_S)) {
    const { domain } = computeXAxis(range, now);
    assert.deepEqual(domain, [now - RANGE_LOOKBACK_S[range], now]);
  }
});

test('computeXAxis ticks start and end exactly on the domain, evenly spaced between', () => {
  const now = 1_700_000_000;
  const { domain, ticks } = computeXAxis('1h', now, 5);
  assert.equal(ticks.length, 5);
  assert.equal(ticks[0], domain[0]);
  assert.equal(ticks[ticks.length - 1], domain[1]);
  const gap = ticks[1] - ticks[0];
  for (let i = 1; i < ticks.length; i++) assert.equal(ticks[i] - ticks[i - 1], gap);
});

test('computeXAxis is a pure function of (range, now): identical calls -> identical output', () => {
  const now = 1_700_000_000;
  assert.deepEqual(computeXAxis('24h', now), computeXAxis('24h', now));
});

test('computeXAxis clamps a silly tickCount to at least 2', () => {
  const { ticks } = computeXAxis('15m', 1000, 1);
  assert.equal(ticks.length, 2);
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
