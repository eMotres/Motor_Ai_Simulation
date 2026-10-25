/** Pure helpers of the read-only field viewer (stored maps of a computed point).
 *
 *  The server sends each stored map shaped like the payload its tab's adapter draws (`routes/motor_view` ->
 *  `development/motor_view.field_payload`); a stored map carries no outlines, so the part boundaries are derived here
 *  from the mesh itself: an edge belongs to the outline when the triangles on its two sides are of different parts
 *  (or it is the mesh boundary).  No React, no window: `node --test` imports this file. */

type Pt = [number, number];

/** Edges of the mesh where the part changes, as pairs of vertex indices (each edge once). */
export function partBoundaryEdges(triangles: ReadonlyArray<ArrayLike<number>>, domains: ArrayLike<number>, nVertices: number): Array<[number, number]> {
  const sides = new Map<number, number[]>();
  const key = (a: number, b: number) => (a < b ? a * nVertices + b : b * nVertices + a);
  for (let ti = 0; ti < triangles.length; ti++) {
    const t = triangles[ti];
    const dom = domains[ti] ?? 0;
    for (const [p, q] of [[t[0], t[1]], [t[1], t[2]], [t[2], t[0]]] as Array<[number, number]>) {
      const k = key(p, q);
      const s = sides.get(k);
      if (s) s.push(dom); else sides.set(k, [dom]);
    }
  }
  const out: Array<[number, number]> = [];
  sides.forEach((doms, k) => {
    if (doms.length === 1 || doms[0] !== doms[1]) {
      const a = Math.floor(k / nVertices);
      out.push([a, k - a * nVertices]);
    }
  });
  return out;
}

/** The outline as the viewer draws it: one two-point loop per boundary edge (the renderer closes every loop onto
 *  itself, so an unchained edge draws exactly that edge - see fieldView.outlinesFromMesh). */
export function outlineLoops(vertices: ReadonlyArray<ArrayLike<number>>, triangles: ReadonlyArray<ArrayLike<number>>, domains: ArrayLike<number>, scale = 1): Pt[][] {
  return partBoundaryEdges(triangles, domains, vertices.length).map(([a, b]) => [
    [vertices[a][0] * scale, vertices[a][1] * scale] as Pt,
    [vertices[b][0] * scale, vertices[b][1] * scale] as Pt,
  ]);
}

/** Thermal / EM payloads keep outlines as `{domain, loops}[]` (metres). */
export function outlineEntries(vertices: ReadonlyArray<ArrayLike<number>>, triangles: ReadonlyArray<ArrayLike<number>>, domains: ArrayLike<number>): Array<{ domain: number; loops: Pt[][] }> {
  const loops = outlineLoops(vertices, triangles, domains, 1);
  return loops.length ? [{ domain: 0, loops }] : [];
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
