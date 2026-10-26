/** Product REVISIONS of a motor: Rev A, Rev B ... (owner 2026-10-27).
 *
 *  One revision letter is shown next to the motor's name in the project, in the Motors catalogue and in Production.  The server
 *  (`GET /api/motor-revisions/index`) lists the revisions the caller may see; these pure helpers key them by motor, build the
 *  texts and decide which button a variant offers.  No React, no window, no `import.meta`: `node --test` imports this file. */

/** One row of `GET /api/motor-revisions/index`. */
export interface RevisionRow {
  die: string;
  config: string;
  rev: string;
  lineage: string;
  /** the working revision of its lineage (the last one) */
  latest: boolean;
  /** replaced by a newer revision: read-only for good */
  frozen: boolean;
}

export type RevisionMap = Map<string, RevisionRow>;

/** Motors are matched case-insensitively (the catalogue spells a die one way, a project another). */
export const revisionKey = (die: string, config: string): string => `${String(die).toLowerCase()}\u0000${String(config).toLowerCase()}`;

const usable = (r: unknown): r is RevisionRow => {
  const x = r as Partial<RevisionRow> | null;
  return !!x && typeof x.die === 'string' && typeof x.config === 'string' && /^[A-Z]+$/.test(String(x.rev ?? ''));
};

export function buildRevisionMap(rows: readonly unknown[] | null | undefined): RevisionMap {
  const map: RevisionMap = new Map();
  for (const r of rows ?? []) if (usable(r)) map.set(revisionKey(r.die, r.config), r);
  return map;
}

/** The revision of a motor; a project motor may carry the alias `die` and a `canonical_die` - both are tried. */
export function revisionFor(map: RevisionMap | null | undefined, motor: { die: string; config: string; canonical_die?: string | null }): RevisionRow | null {
  if (!map || map.size === 0) return null;
  return map.get(revisionKey(motor.die, motor.config)) ?? (motor.canonical_die ? map.get(revisionKey(motor.canonical_die, motor.config)) ?? null : null);
}

/** "A" -> "B", "Z" -> "AA", "AZ" -> "BA" (spreadsheet letters, as the server counts them). */
export function nextRevLetter(rev: string): string {
  if (!/^[A-Z]+$/.test(rev)) return '';
  const digits = [...rev].map((c) => c.charCodeAt(0) - 64);
  let i = digits.length - 1;
  while (i >= 0) {
    if (digits[i] < 26) { digits[i] += 1; break; }
    digits[i] = 1;
    i -= 1;
  }
  if (i < 0) digits.unshift(1);
  return digits.map((d) => String.fromCharCode(64 + d)).join('');
}

/** "CIANO56 400_80 · L100 · Rev A" (plain text, no colour); the revision part is left out for a motor with none. */
export function motorTitleWithRevision(die: string, config: string, rev: string | null | undefined, revLabel: (rev: string) => string = (r) => `Rev ${r}`): string {
  return [die, config, rev ? revLabel(rev) : ''].filter(Boolean).join(' · ');
}

/** Which revision button a variant offers (staff only): "Assign Rev A" for a motor with none, "New revision" on the working revision
 *  of a lineage, nothing on a replaced (read-only) one. */
export function revisionAction(row: RevisionRow | null | undefined, staff: boolean): 'assign' | 'new' | null {
  if (!staff) return null;
  if (!row) return 'assign';
  return row.latest && !row.frozen ? 'new' : null;
}
