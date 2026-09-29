// Pure series-shaping for the Admin · Overview live-load panel — no React,
// no fetch, so it can be unit-tested with plain node:test.
export interface NodePoint { ts: number; cpu: number; mem: number }

/** One row per distinct ts across all nodes, with `${nodeId}_cpu` / `${nodeId}_mem`
 *  keys (both series, drawn separately -- CPU solid, RAM dashed, in the "CPU % /
 *  RAM % per server" chart) plus `cluster_cpu` / `cluster_mem` means over the
 *  nodes that reported at that ts. */
export function mergeNodeSeries(nodes: Record<string, NodePoint[]>): Record<string, number>[] {
  const merged: Record<number, Record<string, number>> = {};
  for (const [id, pts] of Object.entries(nodes)) {
    for (const p of pts) {
      const row = (merged[p.ts] ??= { ts: p.ts });
      row[`${id}_cpu`] = p.cpu;
      row[`${id}_mem`] = p.mem;
    }
  }
  const nodeIds = Object.keys(nodes);
  const mean = (row: Record<string, number>, suffix: string) => {
    const vals = nodeIds.map((id) => row[`${id}_${suffix}`]).filter((v) => v !== undefined);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : undefined as unknown as number;
  };
  return Object.values(merged)
    .sort((a, b) => a.ts - b.ts)
    .map((row) => ({ ...row, cluster_cpu: mean(row, 'cpu'), cluster_mem: mean(row, 'mem') }));
}

/** node id -> its physical-core / logical-thread / total-RAM info, as reported
 *  by /api/admin/load/live's `nodes_now` (all optional: an older node agent,
 *  or one whose /proc/cpuinfo lacks physical/core ids, may not have them). */
export interface NodeCoreInfo { threads?: number; cores?: number; memTotal?: number }

/** "eu1 (16 threads / 8 cores)" -- or "eu1 (16 threads)" if the physical core
 *  count is unknown, or just "eu1" if even the thread count is unknown (no
 *  sample yet). Used for the CPU/RAM series' legend + tooltip names. */
export function serverLabel(id: string, info: NodeCoreInfo): string {
  const { threads, cores } = info;
  if (!threads) return id;
  return cores ? `${id} (${threads} threads / ${cores} cores)` : `${id} (${threads} threads)`;
}

/** threads * cpu% / 100 -- "threads used" for a CPU % reading, or null if the
 *  thread count is unknown (nothing to multiply). */
export function threadsUsed(threads: number | undefined, cpuPct: number): number | null {
  return threads ? (threads * cpuPct) / 100 : null;
}

/** CPU % -> "72.0 % ≈ 11.5 threads used" (tooltip value), or just the
 *  plain percentage if the thread count is unknown. */
export function cpuTooltipValue(cpuPct: number, threads?: number): string {
  const used = threadsUsed(threads, cpuPct);
  return used == null ? `${cpuPct.toFixed(1)} %` : `${cpuPct.toFixed(1)} % ≈ ${used.toFixed(1)} threads used`;
}

const gbFmt = (bytes: number) => (bytes / 1024 ** 3).toFixed(1);

/** "23.4 / 62.7 GB (37.0 %)" -- the shared RAM-in-GB format for both the
 *  chart tooltip and the per-server strip's latest-value label (owner:
 *  "Tooltip and the latest-value label show ..." -- same wording, same
 *  format, one function). */
function ramLabel(usedBytes: number, totalBytes: number): string {
  const pct = totalBytes ? (100 * usedBytes) / totalBytes : 0;
  return `${gbFmt(usedBytes)} / ${gbFmt(totalBytes)} GB (${pct.toFixed(1)} %)`;
}

/** RAM % (from history -- only the percentage is stored per point) -> the
 *  shared GB label, using the node's CURRENT total RAM (nodes_now.mem_total;
 *  total capacity essentially never changes between samples) to recover an
 *  absolute GB figure -- or just the plain percentage if the node's total
 *  RAM is unknown (no nodes_now entry yet). */
export function memTooltipValue(memPct: number, memTotalBytes?: number): string {
  if (!memTotalBytes) return `${memPct.toFixed(1)} %`;
  return ramLabel((memTotalBytes * memPct) / 100, memTotalBytes);
}

/** RAM used/total (from nodes_now -- real bytes, not derived from a %) ->
 *  the shared GB label for the per-server strip's live value, or "—" if
 *  either figure is missing (older node agent / no sample yet). */
export function ramNowLabel(usedBytes?: number, totalBytes?: number): string {
  if (!usedBytes || !totalBytes) return '—';
  return ramLabel(usedBytes, totalBytes);
}

/** `${id}_cpu` / `${id}_mem` -> `{ id, kind }`, or null for a key that isn't
 *  a per-server series (e.g. `cluster_cpu`, a user name, a container name).
 *  Used to look a server up in `nodeMeta` from a chart's `dataKey` -- a
 *  regression here once silently broke every per-server tooltip/legend (the
 *  lookup fell through to "unknown", so it quietly showed bare "%" with no
 *  GB/threads and a raw "eu1_cpu" name instead of "eu1 (...) CPU"), which is
 *  exactly why this parsing lives in one tested function instead of being
 *  re-hand-rolled per chart. */
export function parseServerSeriesKey(key: string): { id: string; kind: 'cpu' | 'mem' } | null {
  const m = /^(.+)_(cpu|mem)$/.exec(key);
  return m ? { id: m[1], kind: m[2] as 'cpu' | 'mem' } : null;
}

/** "cluster: 16 threads, 10.8 used" -- the cluster-total line above the
 *  per-server strip, from cluster.cores / cluster.cpu_used_cores (both
 *  already thread-denominated: see cluster_monitor.list_nodes). */
export function clusterLine(threads: number, used: number): string {
  return `cluster: ${threads} threads, ${used.toFixed(1)} used`;
}

/** Combines the per-user CPU % series and the out-of-app (container/host) CPU %
 *  series into one row set by `ts`, so both stack in the same "CPU by user and
 *  process" chart. Each input row already carries its own keys (user names /
 *  container names / "other" / "app-overhead"); this only merges by ts. */
export function mergeLoadSeries(
  userSeries: Record<string, number>[],
  outsideSeries: Record<string, number>[],
): Record<string, number>[] {
  const merged: Record<number, Record<string, number>> = {};
  for (const row of userSeries) merged[row.ts] = { ...(merged[row.ts] ?? {}), ...row };
  for (const row of outsideSeries) merged[row.ts] = { ...(merged[row.ts] ?? {}), ...row };
  return Object.values(merged).sort((a, b) => a.ts - b.ts);
}

/** "collecting since HH:MM" instead of a chart that just looks empty/broken:
 *  true when there's too little history yet to trust the picture. */
export function isCollectingHistory(seriesLength: number, monitoringSince: number | null,
                                    now: number = Date.now() / 1000): boolean {
  if (seriesLength >= 3) return false;
  return monitoringSince != null && now - monitoringSince < 3600;
}

//: range label -> lookback seconds. Mirrors cluster_monitor.RANGE_LOOKBACK_S
// (kept as a separate frontend constant, same values, since the two never
// need to change in lock-step and importing across the Python/TS boundary
// isn't a thing here).
export const RANGE_LOOKBACK_S: Record<'15m' | '1h' | '24h' | '7d', number> = {
  '15m': 900, '1h': 3600, '24h': 86400, '7d': 7 * 86400,
};

export interface XAxisSpec { domain: [number, number]; ticks: number[] }

/** The ONE [now-range, now] domain + tick set shared by all three live-load
 *  charts (owner: "the three charts... must have... the same time ticks;
 *  the same time domain must be shared by all three"). Computed once, fed
 *  identically to every chart's XAxis (`type="number"`, not the default
 *  "category" -- category spacing is by array index, so charts with a
 *  different point count would drift apart even with the same domain).
 *  `tickCount` >= 2; interior ticks are evenly spaced, domain ends always
 *  included exactly (so "now" and "range ago" both always land on a tick). */
export function computeXAxis(range: keyof typeof RANGE_LOOKBACK_S, now: number,
                             tickCount = 5): XAxisSpec {
  const start = now - RANGE_LOOKBACK_S[range];
  const n = Math.max(2, Math.floor(tickCount));
  const ticks = Array.from({ length: n }, (_, i) => Math.round(start + ((now - start) * i) / (n - 1)));
  return { domain: [start, now], ticks };
}
