// Points of a scatter chart that land on the same spot.
//
// A sweep can return two designs with the same (N·m/kg, η) — at the display
// precision or exactly (owner 2026-10-30: a length sweep at one pinned k_end
// gave 90 mm and 95 mm identical numbers, and every 90 mm marker vanished under
// its 95 mm twin).  Each member of such a group gets its index and the group
// size, so the chart draws them side by side around the true position and the
// tooltip says how many sit there.

export interface Coincidence {
  /** 0-based position of this point inside its group. */
  i: number;
  /** Number of points at this spot (≥ 2). */
  n: number;
}

/** Group points whose x and y agree to `digits` decimals (the chart's own labels).
 *  Returns one entry per input point: null when it is alone at its spot. */
export function coincidentGroups(pts: { x: number; y: number }[], digits = 2): (Coincidence | null)[] {
  const key = (p: { x: number; y: number }) =>
    `${Number(p.x).toFixed(digits)}|${Number(p.y).toFixed(digits)}`;
  const count = new Map<string, number>();
  for (const p of pts) { const k = key(p); count.set(k, (count.get(k) ?? 0) + 1); }
  const seen = new Map<string, number>();
  return pts.map(p => {
    const k = key(p);
    const n = count.get(k) ?? 1;
    if (n < 2) return null;
    const i = seen.get(k) ?? 0;
    seen.set(k, i + 1);
    return { i, n };
  });
}

/** Horizontal pixel offset of a group member: spread evenly around the true spot. */
export function coincidentOffsetPx(c: Coincidence | null | undefined, stepPx = 7): number {
  if (!c || c.n < 2) return 0;
  return (c.i - (c.n - 1) / 2) * stepPx;
}
