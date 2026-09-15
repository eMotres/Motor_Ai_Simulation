// Resolving a DUTY's remembered materials against the machine and the library.
//
// lib/dutySettings.ts stores what a duty ASKED FOR (`{part: material}`, `null`
// = "the machine's own").  This module decides what the solve may actually USE,
// and it is deliberately the ONLY place that decides: the ?mat= payload
// (MaterialOverrideSync) and the Simulation badge both call `effectiveAssignment`,
// so the badge cannot drift from what is sent.
//
// The rules, all of them "when in doubt, the machine's own material":
//   • a part the machine does not have          → dropped;
//   • a name that has left the library           → ignored (skip, machine wins);
//   • a name from ANOTHER CATEGORY than the part's current material (steel for
//     a magnet) → ignored;
//   • the MAGNET additionally keeps the grade-family rule of
//     lib/magnetVariants.ts: a duty may pick another TEMPERATURE of the
//     machine's grade, never another grade.  Changing the grade in Materials
//     therefore always wins over a duty's stale temperature pick.
//
// A rejection is loud in the console (once per part+name) and visible in the UI
// by construction — the badge and the Materials tab then show the MACHINE's
// value, which is what the solve will use.  Nothing here throws: a stale duty
// must never be able to break the payload.

import { effectiveMagnet } from './magnetVariants';

/** Structurally what `useMaterialsLibrary` returns: category → name → record.
 *  Typed here rather than imported so `lib/` keeps not depending on `components/`. */
export type LibraryLike = Record<string, Record<string, unknown> | undefined> | null | undefined;

const CATEGORIES = ['steel', 'magnet', 'conductor', 'insulator', 'coolant'] as const;

/** Which library category a material name lives in (null = not in the library:
 *  a built-in the library does not list, `air`, a deleted record). */
function categoryOf(name: string, library: LibraryLike): string | null {
  if (!name || !library) return null;
  for (const c of CATEGORIES) if (library[c] && name in library[c]!) return c;
  return null;
}

const warned = new Set<string>();
function warnOnce(part: string, want: string, why: string): void {
  const k = `${part}:${want}:${why}`;
  if (warned.has(k)) return;
  warned.add(k);
  try {
    console.warn(`[duty materials] ignoring '${want}' for ${part} — ${why}; `
      + 'the solve uses the machine\'s own material for that part');
  } catch { /* no console */ }
}

/**
 * The assignment the solve must use = the machine's, with this duty's usable
 * picks laid over it.
 *
 * Returns the SAME OBJECT when nothing applies — that reference identity is
 * what keeps the ?mat= JSON byte-identical (and every backend cache key that
 * hashes it honest) for a duty whose materials are the machine's.
 */
export function effectiveAssignment<T extends Record<string, string | undefined>>(
  machine: T,
  stored: Record<string, string | null> | null | undefined,
  library: LibraryLike,
): T {
  if (!machine || !stored) return machine;
  let out: Record<string, string | undefined> | null = null;
  for (const [part, raw] of Object.entries(stored)) {
    if (!raw) continue;                       // null = "the machine's own"
    if (!(part in machine)) continue;         // unknown part → dropped, silently
    const base = String(machine[part] ?? '');
    const want = String(raw);
    if (want === base) continue;              // agrees with the machine
    let eff = base;
    if (part === 'magnet') {
      // Same grade, another temperature record — magnetVariants owns that rule.
      eff = effectiveMagnet(base, want, Object.keys(library?.magnet ?? {}));
      if (eff === base) warnOnce(part, want, 'not a library record of the '
        + `machine's magnet grade ('${base}')`);
    } else {
      const cat = categoryOf(base, library);
      if (!cat) warnOnce(part, want, `the machine's own '${base}' is not in the `
        + 'library, so there is no category to check the pick against');
      else if (categoryOf(want, library) !== cat)
        warnOnce(part, want, `not a '${cat}' record in the current library`);
      else eff = want;
    }
    if (eff !== base) { out = out ?? { ...machine }; out[part] = eff; }
  }
  return (out ?? machine) as T;
}

/** A stable, order-independent stamp of the ASSIGNMENT inside a ?mat= payload.
 *  Stamped onto a fresh run (TransientCharts) and compared on the summary card,
 *  so a change to ANY part — including the ones that have no mass row of their
 *  own (the liner, the enamel) — dims the result it did not solve.
 *  '' means "unknown", which never raises staleness on its own. */
export function assignmentSignature(matJson: string | null | undefined): string {
  try {
    const a = JSON.parse(String(matJson ?? '')).assignment;
    if (!a || typeof a !== 'object') return '';
    return Object.keys(a).sort().map(k => `${k}=${a[k]}`).join('|');
  } catch { return ''; }
}
