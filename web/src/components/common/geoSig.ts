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

/** Stable signature of a geometry parameter set: the numeric fields only, sorted. */
export function geoSignature(g: Record<string, unknown> | null | undefined): string {
  try {
    return Object.entries(g || {})
      .filter(([, v]) => typeof v === 'number')
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([k, v]) => `${k}:${v}`).join('|');
  } catch { return ''; }
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
