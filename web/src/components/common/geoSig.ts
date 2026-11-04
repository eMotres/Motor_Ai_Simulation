/**
 * ONE definition of "which machine is this?" for the whole frontend.
 *
 * Every cached / persisted RESULT in this app (the last transient in
 * localStorage, the summary card, a Compare point, a motor preset) is only
 * meaningful next to the geometry it was produced for.  The recurring bug class
 * — numbers from the previous motor surviving a preset switch and a reload, and
 * being read as the current machine's — is what happens when a stored result
 * carries no machine stamp, or carries one that each consumer spells its own
 * way.  So the signature is computed HERE and nowhere else.
 *
 * The source of truth is the store geometry, because that is exactly what the
 * fetch interceptor sends as `geo=` on every compute request (`setGeoGetter` in
 * motorStore) — i.e. the machine the backend actually solves.  Comparing a
 * result's stamp against `liveGeoSig()` therefore answers "were these numbers
 * produced by the machine on screen?" and nothing weaker.
 *
 * This module imports NOTHING (no store, no settings helpers): motorStore
 * already imports motorSettings, so any import back into the store from here
 * would close a module cycle around a zustand `create()` that runs at import
 * time.  The store pushes its getter in via `setGeoSigGetter` instead.
 */

/** Stable signature of a geometry parameter set: the numeric fields only, sorted.
 *  The one flag that is a different machine, `stator_outer_notches`, is stamped
 *  as `stator_outer_notches:0` when it is OFF and not at all when on (absent = on),
 *  so every stamp of a notched machine is the string it always was — the same
 *  rule as the server's `routes.presets._geo_sig`. */
export function geoSignature(g: Record<string, unknown> | null | undefined): string {
  try {
    const entries: Array<[string, unknown]> = Object.entries(g || {})
      .filter(([k, v]) => typeof v === 'number' && k !== 'stator_outer_notches');
    if (g && g.stator_outer_notches === false) entries.push(['stator_outer_notches', 0]);
    return entries
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([k, v]) => `${k}:${v}`).join('|');
  } catch { return ''; }
}

/** Parse a stamp back into numbers; unreadable pairs are skipped. */
function parseStamp(sig: string): Map<string, number> {
  const out = new Map<string, number>();
  for (const pair of sig.split('|')) {
    const i = pair.indexOf(':');
    if (i <= 0) continue;
    const v = Number(pair.slice(i + 1));
    if (Number.isFinite(v)) out.set(pair.slice(0, i), v);
  }
  return out;
}

/**
 * Are these two stamps different MACHINES?  Never compare stamps as strings: the
 * same geometry reaches the store from the load's PUT answer and from a GET, and
 * a value like 52.95 against 52.94999999999999, or a key only one answer carries,
 * made an unchanged machine read as "another build" and dimmed the dashboard on
 * every point switch until F5 (owner 2026-11-04).  Keys BOTH stamps carry, relative
 * tolerance 1e-6, the same rule as `dutyGeometryDiff.geoSigDiffRows` and the
 * server's `duty_geometry_diff`.  `stator_outer_notches` is stamped only when off,
 * so a missing key there IS a difference.
 */
export function geoSigsDiffer(a: string | null | undefined, b: string | null | undefined): boolean {
  if (a === b) return false;
  const A = parseStamp(String(a ?? ''));
  const B = parseStamp(String(b ?? ''));
  if (A.has('stator_outer_notches') !== B.has('stator_outer_notches')) return true;
  for (const [k, v] of A) {
    const w = B.get(k);
    if (w !== undefined && Math.abs(v - w) > 1e-6 * Math.max(1, Math.abs(w))) return true;
  }
  return false;
}

let _getter: (() => string) | null = null;

/** motorStore registers the live-geometry reader here (see its bottom). */
export function setGeoSigGetter(fn: () => string): void { _getter = fn; }

/**
 * Signature of the machine currently loaded, for non-React callers.
 * Returns '' when the store has not registered yet — treat that as UNKNOWN, not
 * as "changed": a stamp check must never flag a mismatch it cannot prove.
 */
export function liveGeoSig(): string {
  try { return _getter ? _getter() : ''; } catch { return ''; }
}

/**
 * Did `stamp` come from a different machine than the one loaded now?
 * FALSE whenever either side is unknown — an unstamped legacy result is
 * reported by its own means (op-point drift, backend `stale` flag), never by a
 * guess made here.
 */
export function geoStaleAgainstLive(stamp: string | null | undefined): boolean {
  const live = liveGeoSig();
  if (!stamp || !live) return false;
  return stamp !== live;
}
