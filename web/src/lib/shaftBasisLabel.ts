// When a shaft-power basis label is shown (coordinator decision for PR #900, 2026-11-26).
//
// A record is "marked" when it is on the shaft-side convention (`shaft_conv === 1` /
// `shaft_convention === 'fe_on_shaft_k3d_v1'`); every other record is an earlier run.
// - a marked record always names its basis;
// - an earlier record shown on its own (or among earlier ones only) keeps its earlier text and shape: no label,
//   no column, no suffix;
// - when marked and earlier records are shown together in one table, chart or list, both name their basis.

export const SHAFT_CONVENTION = 'fe_on_shaft_k3d_v1';

/** True when a record (or a numbers-only row) is on the shaft-side convention. */
export function isMarked(r: { shaft_conv?: unknown; shaft_convention?: unknown } | null | undefined): boolean {
  return !!r && (r.shaft_conv === 1 || r.shaft_convention === SHAFT_CONVENTION);
}

/** True when the marks of the records shown together hold both kinds. */
export function basesMixed(marks: Iterable<boolean>): boolean {
  let marked = false;
  let earlier = false;
  for (const m of marks) {
    if (m) marked = true; else earlier = true;
  }
  return marked && earlier;
}

/** True when any record shown together is marked: a basis column / label is shown at all. */
export function anyMarked(marks: Iterable<boolean>): boolean {
  for (const m of marks) if (m) return true;
  return false;
}

/** Which basis word a record gets: 'current' (marked), 'earlier' (an earlier record next to marked ones), or null
 *  (an earlier record among earlier ones only: no label). */
export function basisTag(marked: boolean, mixed: boolean): 'current' | 'earlier' | null {
  if (marked) return 'current';
  return mixed ? 'earlier' : null;
}
