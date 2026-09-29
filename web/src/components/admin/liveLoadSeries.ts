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

/** RAM % -> "9.0 % ≈ 1.4 / 16.0 GB" (tooltip value), or just the plain
 *  percentage if the node's total RAM is unknown. */
export function memTooltipValue(memPct: number, memTotalBytes?: number): string {
  if (!memTotalBytes) return `${memPct.toFixed(1)} %`;
  const totalGB = memTotalBytes / 1024 ** 3;
  return `${memPct.toFixed(1)} % ≈ ${((totalGB * memPct) / 100).toFixed(1)} / ${totalGB.toFixed(1)} GB`;
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
