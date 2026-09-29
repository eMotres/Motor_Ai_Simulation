// Pure series-shaping for the Admin · Overview live-load panel — no React,
// no fetch, so it can be unit-tested with plain node:test.
export interface NodePoint { ts: number; cpu: number; mem: number }

/** One row per distinct ts across all nodes, with `${nodeId}_cpu` / `${nodeId}_mem`
 *  keys plus a `cluster_cpu` mean over the nodes that reported at that ts. */
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
  return Object.values(merged)
    .sort((a, b) => a.ts - b.ts)
    .map((row) => {
      const cpus = nodeIds.map((id) => row[`${id}_cpu`]).filter((v) => v !== undefined);
      return { ...row, cluster_cpu: cpus.length ? cpus.reduce((a, b) => a + b, 0) / cpus.length : undefined as unknown as number };
    });
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
