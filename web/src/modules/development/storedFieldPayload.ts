/** Pure helpers of the read-only field viewer (stored maps of a computed point).
 *
 *  The server sends each stored map shaped like the payload its tab's adapter draws (`routes/motor_view` ->
 *  `development/motor_view.field_payload`).  Records stored since 2026-10-26 carry the very outlines the live tab drew; an
 *  older record carries none, and then the part boundaries are derived from the mesh here:
 *
 *    an edge is an outline when the triangles on its two sides belong to different parts (or it is the outer border);
 *    drawn as plain line SEGMENTS, so no polygon ordering is needed.
 *
 *  THE VERTICES ARE WELDED FIRST (by coordinate).  A stored mesh is not one connected mesh by index: the mechanical mesh is
 *  SPLIT (the nodes of a contact interface are duplicated, one set per part) and a sector answer is replicated by copying the
 *  sector with its own nodes, so a seam between two copies is a pair of index-distinct edges.  Read by index, the seams and
 *  cut lines of the sector look like part borders - a zigzag of straight lines from pole to pole down to the hub (owner screen
 *  2026-10-26).  Welded, a seam is an edge with the same part on both sides (not drawn) and an interface is an edge with two
 *  different parts (drawn once).  No React, no window: `node --test` imports this file. */

type Pt = [number, number];

/** Weld vertices by coordinate: `ids[i]` is the welded id of vertex i, `xy[id]` its position.  The bucket is
 *  `rel` x the largest coordinate (default 2e-4: ~8 um on a 40 mm rotor) - far below any mesh edge, far above float32 noise. */
export function weldVertices(vertices: ReadonlyArray<ArrayLike<number>>, rel = 2e-4): { ids: Int32Array; xy: Pt[] } {
  let big = 0;
  for (const v of vertices) big = Math.max(big, Math.abs(v[0]), Math.abs(v[1]));
  const q = big > 0 ? big * rel : 1;
  const ids = new Int32Array(vertices.length);
  const seen = new Map<string, number>();
  const xy: Pt[] = [];
  for (let i = 0; i < vertices.length; i++) {
    const key = `${Math.round(vertices[i][0] / q)}|${Math.round(vertices[i][1] / q)}`;
    let id = seen.get(key);
    if (id === undefined) { id = xy.length; seen.set(key, id); xy.push([vertices[i][0], vertices[i][1]]); }
    ids[i] = id;
  }
  return { ids, xy };
}

/** Edges of the mesh where the part changes, as pairs of vertex indices (each edge once).  Index based: weld first
 *  (`outlineLoops` does) when the mesh may be split or replicated.  `groupOf` maps a domain tag to the part it belongs to
 *  (default: the tag itself); `draw` can veto an edge from the groups on its two sides (`undefined` = mesh border). */
export function partBoundaryEdges(
  triangles: ReadonlyArray<ArrayLike<number>>, domains: ArrayLike<number>, nVertices: number,
  groupOf: (domain: number) => number = (d) => d,
  draw: (a: number, b: number | undefined) => boolean = () => true,
): Array<[number, number]> {
  const sides = new Map<number, number[]>();
  const key = (a: number, b: number) => (a < b ? a * nVertices + b : b * nVertices + a);
  for (let ti = 0; ti < triangles.length; ti++) {
    const t = triangles[ti];
    const group = groupOf(domains[ti] ?? 0);
    for (const [p, q] of [[t[0], t[1]], [t[1], t[2]], [t[2], t[0]]] as Array<[number, number]>) {
      if (p === q) continue;                         // degenerate after welding
      const k = key(p, q);
      const s = sides.get(k);
      if (s) s.push(group); else sides.set(k, [group]);
    }
  }
  const out: Array<[number, number]> = [];
  sides.forEach((groups, k) => {
    const border = groups.length === 1;
    if (!border && groups[0] === groups[1]) return;
    if (!draw(groups[0], border ? undefined : groups[1])) return;
    const a = Math.floor(k / nVertices);
    out.push([a, k - a * nVertices]);
  });
  return out;
}

export interface OutlineOptions { groupOf?: (domain: number) => number; draw?: (a: number, b: number | undefined) => boolean }

/** The outline as the viewer draws it: one two-point loop per boundary edge (the renderer closes every loop onto itself,
 *  so an unchained edge draws exactly that edge - see fieldView.outlinesFromMesh).  Vertices are welded first. */
export function outlineLoops(
  vertices: ReadonlyArray<ArrayLike<number>>, triangles: ReadonlyArray<ArrayLike<number>>, domains: ArrayLike<number>,
  scale = 1, opts: OutlineOptions = {},
): Pt[][] {
  const { ids, xy } = weldVertices(vertices);
  const welded = triangles.map((t) => [ids[t[0]], ids[t[1]], ids[t[2]]]);
  return partBoundaryEdges(welded, domains, xy.length, opts.groupOf, opts.draw).map(([a, b]) => [
    [xy[a][0] * scale, xy[a][1] * scale] as Pt,
    [xy[b][0] * scale, xy[b][1] * scale] as Pt,
  ]);
}

/** Thermal / EM payloads keep outlines as `{domain, loops}[]` (metres). */
export function outlineEntries(
  vertices: ReadonlyArray<ArrayLike<number>>, triangles: ReadonlyArray<ArrayLike<number>>, domains: ArrayLike<number>,
  opts: OutlineOptions = {},
): Array<{ domain: number; loops: Pt[][] }> {
  const loops = outlineLoops(vertices, triangles, domains, 1, opts);
  return loops.length ? [{ domain: 0, loops }] : [];
}

/** The undirected segment set of some loops (each loop closed), keyed by quantised end points - for comparing two
 *  renderings of the same outline (the live tab's polygons against the ones derived from the stored mesh). */
export function segmentSet(loops: ReadonlyArray<ReadonlyArray<ArrayLike<number>>>, quantum = 1e-6): Set<string> {
  const r = (v: number) => Math.round(v / quantum);
  const pt = (p: ArrayLike<number>) => `${r(p[0])},${r(p[1])}`;
  const out = new Set<string>();
  for (const loop of loops) {
    if (loop.length < 2) continue;
    for (let i = 0; i < loop.length; i++) {
      const a = pt(loop[i]), b = pt(loop[(i + 1) % loop.length]);
      if (a !== b) out.add(a < b ? `${a}|${b}` : `${b}|${a}`);
    }
  }
  return out;
}

/** One line of the simulated parameters under a map: operating point, mesh, solver, date.  Missing parts are left out. */
export function parametersLine(parts: { rpm?: number | null; currentA?: number | null; gammaDeg?: number | null; provenance?: string }): string {
  const bits: string[] = [];
  if (typeof parts.rpm === 'number' && Number.isFinite(parts.rpm)) bits.push(`${Math.round(parts.rpm)} rpm`);
  if (typeof parts.currentA === 'number' && Number.isFinite(parts.currentA)) bits.push(`${parts.currentA.toFixed(1)} A`);
  if (typeof parts.gammaDeg === 'number' && Number.isFinite(parts.gammaDeg)) bits.push(`${parts.gammaDeg.toFixed(1)}°`);
  if (parts.provenance) bits.push(parts.provenance);
  return bits.join(' · ');
}

// ── crop to the solved part (owner 2026-10-27: "show only the coloured part") ───────────────────────────────────────────

export type Extent = [number, number, number, number];

/** Bounding box [xmin, xmax, ymin, ymax] of the DRAWN triangles that carry a value, grown by `margin` x the larger span on every
 *  side (default 4 %).  `positions` are the geometry's xyz triplets (3 vertices per triangle, display units); `values` the raw
 *  value per drawn vertex (same order) - a triangle with a non-finite vertex value carries none and does not count.  `null` when
 *  nothing is drawn, so the caller keeps the output's own (whole) extent. */
export function drawnExtent(positions: ArrayLike<number> | null | undefined, values?: ArrayLike<number> | null, margin = 0.04): Extent | null {
  if (!positions || positions.length < 9) return null;
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  const nTri = Math.floor(positions.length / 9);
  for (let t = 0; t < nTri; t++) {
    const tri = t * 3;
    let ok = true;
    for (let k = 0; k < 3 && ok; k++) {
      const s = (tri + k) * 3;
      if (!Number.isFinite(positions[s]) || !Number.isFinite(positions[s + 1])) ok = false;
      if (values && tri + k < values.length && !Number.isFinite(values[tri + k])) ok = false;
    }
    if (!ok) continue;
    for (let k = 0; k < 3; k++) {
      const s = (tri + k) * 3;
      const x = positions[s], y = positions[s + 1];
      if (x < xmin) xmin = x; if (x > xmax) xmax = x;
      if (y < ymin) ymin = y; if (y > ymax) ymax = y;
    }
  }
  if (!Number.isFinite(xmin) || !(xmax > xmin) || !(ymax > ymin)) return null;
  const pad = Math.max(xmax - xmin, ymax - ymin) * margin;
  return [xmin - pad, xmax + pad, ymin - pad, ymax + pad];
}

/** True when `crop` is clearly smaller than `whole` (under 90 % of its area): only then is there something to switch between. */
export function isCropped(whole: Extent | null | undefined, crop: Extent | null | undefined): boolean {
  if (!whole || !crop) return false;
  const area = (e: Extent) => Math.max(0, e[1] - e[0]) * Math.max(0, e[3] - e[2]);
  const w = area(whole);
  return w > 0 && area(crop) < 0.9 * w;
}

/** The extent a map is framed to: the solved part by default, the output's own (whole-model) extent on request or when there is
 *  nothing to crop to. */
export function mapExtent(whole: Extent | null | undefined, crop: Extent | null | undefined, showWhole: boolean): Extent | null {
  if (showWhole || !crop) return whole ?? crop ?? null;
  return crop;
}
