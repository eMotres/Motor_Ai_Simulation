/** Series-shaping for the Admin · Overview live-load panel, tested without a
 * browser (`liveLoadSeries.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  mergeNodeSeries, mergeLoadSeries, isCollectingHistory, serverLabel, threadsUsed,
  cpuTooltipValue, memTooltipValue, ramNowLabel, parseServerSeriesKey, clusterLine,
  computeXAxis, RANGE_LOOKBACK_S, sortByField, levelColor, pctCell, computeNodeTotals,
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

// ── sortByField: the "Now" process-monitor table's click-to-sort ───────────
test('sortByField sorts numerically, descending and ascending', () => {
  const rows = [{ id: 'a', v: 30 }, { id: 'b', v: 80 }, { id: 'c', v: 10 }];
  assert.deepEqual(sortByField(rows, (r) => r.v, 'desc').map((r) => r.id), ['b', 'a', 'c']);
  assert.deepEqual(sortByField(rows, (r) => r.v, 'asc').map((r) => r.id), ['c', 'a', 'b']);
});

test('sortByField sorts strings too (user/job/state/client/node columns)', () => {
  const rows = [{ id: 1, u: 'carol' }, { id: 2, u: 'alice' }, { id: 3, u: 'bob' }];
  assert.deepEqual(sortByField(rows, (r) => r.u, 'asc').map((r) => r.id), [2, 3, 1]);
});

test('sortByField puts null/undefined values last in BOTH directions', () => {
  const rows = [{ id: 'a', v: 5 }, { id: 'b', v: null }, { id: 'c', v: 9 }, { id: 'd', v: undefined }];
  assert.deepEqual(sortByField(rows, (r) => r.v, 'desc').map((r) => r.id), ['c', 'a', 'b', 'd']);
  assert.deepEqual(sortByField(rows, (r) => r.v, 'asc').map((r) => r.id), ['a', 'c', 'b', 'd']);
});

test('sortByField is a stable sort (equal values keep their relative order)', () => {
  const rows = [{ id: 1, v: 5 }, { id: 2, v: 5 }, { id: 3, v: 5 }];
  assert.deepEqual(sortByField(rows, (r) => r.v, 'desc').map((r) => r.id), [1, 2, 3]);
});

test('sortByField does not mutate the input array', () => {
  const rows = [{ id: 'a', v: 1 }, { id: 'b', v: 2 }];
  const copy = [...rows];
  sortByField(rows, (r) => r.v, 'desc');
  assert.deepEqual(rows, copy);
});

// ── levelColor / pctCell: the mini bars' green/yellow/red thresholds ───────
test('levelColor: green below 50, yellow 50-79, red 80+', () => {
  assert.equal(levelColor(0), '#4ade80');
  assert.equal(levelColor(49.9), '#4ade80');
  assert.equal(levelColor(50), '#fbbf24');
  assert.equal(levelColor(79.9), '#fbbf24');
  assert.equal(levelColor(80), '#f87171');
  assert.equal(levelColor(150), '#f87171');               // > 100 % possible: multi-core rate
});

test('levelColor is a muted neutral for an unknown value', () => {
  assert.equal(levelColor(null), 'var(--text-4)');
  assert.equal(levelColor(undefined), 'var(--text-4)');
});

test('pctCell formats or falls back to a dash', () => {
  assert.equal(pctCell(37.6), '38 %');
  assert.equal(pctCell(0), '0 %');
  assert.equal(pctCell(null), '—');
  assert.equal(pctCell(undefined), '—');
});

// ── computeNodeTotals: the "Now" table's per-node totals row ───────────────
test('computeNodeTotals: CPU sums job rows (apportioned shares), residual is out-of-app', () => {
  const t = computeNodeTotals(70, 40, [20, 15], [30, 30]);
  assert.equal(t.cpuTotal, 70);
  assert.equal(t.cpuApp, 35);                              // 20 + 15, summed
  assert.equal(t.cpuOutside, 35);                           // 70 - 35 residual
  assert.equal(t.cpuIdle, 30);                               // 100 - 70
});

test('computeNodeTotals: MEM takes the MAX of job rows, never the sum (shared RSS)', () => {
  // three jobs, each reporting the SAME shared process-tree RSS (30 %) --
  // summing would wrongly claim 90 % of RAM is "app jobs".
  const t = computeNodeTotals(50, 30, [10, 10, 10], [30, 30, 30]);
  assert.equal(t.memApp, 30);
  assert.equal(t.memOutside, 0);                             // 30 - 30, clamped
  assert.equal(t.memIdle, 70);
});

test('computeNodeTotals: no running jobs -> app is 0, all measured load is "out of app"', () => {
  const t = computeNodeTotals(65, 20, [], []);
  assert.equal(t.cpuApp, 0);
  assert.equal(t.cpuOutside, 65);
  assert.equal(t.memApp, 0);
  assert.equal(t.memOutside, 20);
});

test('computeNodeTotals: clamps a negative residual to 0 instead of going negative', () => {
  // pathological: job rows read MORE than the node's own reported total
  // (sampling skew) -- outside/idle must never show as negative.
  const t = computeNodeTotals(40, 40, [50], [60]);
  assert.equal(t.cpuOutside, 0);
  assert.equal(t.memOutside, 0);
});
