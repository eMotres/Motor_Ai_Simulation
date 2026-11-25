/** The Sweep's failure banner: failed designs grouped by REASON CLASS, each class named plainly
 *  ("geometry rejected: magnets overlap (magnet_fill_up 1.5 and 2.0)") instead of one mesh error code for all.
 *  Pure functions (`node --test` imports this file).  `tr(key, args)` is the simulation-namespace translator. */

export type Tr = (key: string, args?: Record<string, string | number>) => string;

export interface FailGroup {
  cls: 'geometry' | 'timeout' | 'nonphysical' | 'build';
  /** stable key: the first violation code for a rejected geometry, else the peeled error code */
  key: string;
  n: number;
  /** geometry class only: the plain name, e.g. "magnets overlap" */
  what: string;
  /** "magnet_fill_up 1.5 and 2.0; magnet_height 3" - the swept values at which EVERY point fails, or '' */
  culprits: string;
  /** the raw first reason (full text with the overlap pairs for a rejected geometry), kept for the tooltip */
  full: string;
  /** peeled short reason of a non-geometry class */
  reason: string;
  hint: string;
}

const PARTS = ['magnet', 'coil', 'rotor', 'shaft', 'stator', 'liner', 'sleeve'];

/** "magnet_overlaps_rotor" -> "magnets overlap rotor"; an unknown code reads as its words. */
export function violationWhat(code: string, tr: Tr): string {
  const m = /^([a-z]+)_overlaps_([a-z]+)$/.exec(code || '');
  if (m && PARTS.includes(m[1]) && PARTS.includes(m[2])) {
    const a = tr('sweepFailPart_' + m[1]);
    return m[1] === m[2] ? tr('sweepFailOverlapSelf', { a }) : tr('sweepFailOverlap', { a, b: tr('sweepFailPart_' + m[2]) });
  }
  if (code === 'bound') return tr('sweepFailBound');
  return String(code || 'geometry').replace(/_/g, ' ');
}

const joinValues = (vals: number[]): string =>
  vals.length === 2 ? `${vals[0]} and ${vals[1]}` : vals.join(', ');

const round3 = (v: number) => Math.round(v * 1000) / 1000;

function peel(raw: string): { full: string; reason: string; hint: string } {
  let full = String(raw || '').trim();
  for (let i = 0; i < 6; i++) {
    const m = full.match(/^(?:[A-Za-z]+(?:Error|Exception)|\d{3}|sliding-band transient failed|solver\.em_transient failed)\s*:\s*/);
    if (!m) break;
    full = full.slice(m[0].length);
  }
  const code = full.match(/^([a-z][a-z0-9_]+):\s/);
  const reason = (code ? code[1] : full).slice(0, 140);
  const hint = reason === 'tdm_mesh_not_periodic'
    ? ' The magnet eddy-current method needs identical poles: Mesh tab -> Pole/slot mesh = Periodic, or turn Template iron on.'
    : '';
  return { full, reason, hint };
}

function classOf(p: any, tr: Tr): { cls: FailGroup['cls']; key: string; what: string; full: string; reason: string; hint: string } {
  if (p?.geometry_rejected === true) {
    const v = Array.isArray(p.geometry_violations) && p.geometry_violations.length ? p.geometry_violations[0] : null;
    const code = v?.code ? String(v.code) : 'geometry';
    return { cls: 'geometry', key: code, what: violationWhat(code, tr), full: String(p.error || ''), reason: '', hint: '' };
  }
  const { full, reason, hint } = peel(p?.error);
  if (/^timeout/i.test(reason)) return { cls: 'timeout', key: 'timeout', what: '', full, reason, hint };
  if (/non-physical/i.test(reason)) return { cls: 'nonphysical', key: 'nonphysical', what: '', full, reason, hint };
  return { cls: 'build', key: reason, what: '', full, reason, hint };
}

/** Group every failed point (feasible === false) by its reason class; biggest group first. */
export function groupFailures(points: any[], tr: Tr): FailGroup[] {
  const pts = (points || []).filter(p => p);
  const failed = pts.filter(p => p.feasible === false);
  const groups = new Map<string, { g: FailGroup; pts: any[]; params: Set<string> }>();
  for (const p of failed) {
    const c = classOf(p, tr);
    const id = `${c.cls}|${c.key}`;
    let e = groups.get(id);
    if (!e) {
      e = { g: { cls: c.cls, key: c.key, n: 0, what: c.what, culprits: '', full: c.full, reason: c.reason, hint: c.hint }, pts: [], params: new Set() };
      groups.set(id, e);
    }
    e.g.n++; e.pts.push(p);
    for (const v of (Array.isArray(p.geometry_violations) ? p.geometry_violations : []))
      if (v?.code === c.key) (v.likely_params || []).forEach((k: string) => e!.params.add(String(k)));
  }
  const keys = new Set<string>();
  pts.forEach(p => Object.keys(p.overrides || {}).forEach(k => keys.add(k)));
  for (const e of groups.values()) {
    const inGroup = new Set(e.pts);
    const parts: string[] = [];
    keys.forEach(k => {
      if (e.params.size && !e.params.has(k)) return;
      const byVal = new Map<number, { tot: number; bad: number; mine: number }>();
      pts.forEach(p => {
        const raw = (p.overrides || {})[k];
        if (raw == null) return;
        const val = Math.round(Number(raw) * 1e4) / 1e4;
        const r = byVal.get(val) || { tot: 0, bad: 0, mine: 0 };
        r.tot++; if (p.feasible === false) r.bad++; if (inGroup.has(p)) r.mine++;
        byVal.set(val, r);
      });
      const bad = [...byVal.entries()].filter(([, r]) => r.tot > 0 && r.bad === r.tot && r.mine > 0)
        .map(([v]) => round3(v)).sort((a, b) => a - b);
      const someGood = [...byVal.values()].some(r => r.bad < r.tot);
      if (bad.length && someGood) parts.push(`${k} ${joinValues(bad)}`);
    });
    e.g.culprits = parts.join('; ');
  }
  return [...groups.values()].map(e => e.g).sort((a, b) => b.n - a.n);
}

/** The plain one-line name of a geometry group: "geometry rejected: magnets overlap (magnet_fill_up 1.5 and 2.0)". */
export function geometryGroupText(g: FailGroup, tr: Tr): string {
  const head = tr('sweepFailGeometry', { what: g.what });
  return g.culprits ? `${head} (${g.culprits})` : head;
}
