// The Configure tab's "one machine at a time" guard (owner 2026-09-29: a
// draft link showed the open machine's header, an unrelated "last matched"
// reference passport, and that passport's tiles computed with the open
// machine's raw slider values — three different motors on one page).
//
// Pure, side-effect-free so it can be unit-tested with `node --test` and
// reused by ConfiguratorPanel for both the live-machine path and the
// agent-draft path.

/** The cross-section identity a reference passport or a loaded machine
 *  carries — die/config differ in spelling between the two, so machines are
 *  matched by the STAMPED LAMINATION instead: slot count and pole count. */
export interface MachineSig {
  numSlots: number | null | undefined;
  numPoles: number | null | undefined;
}

/** True only when both signatures are fully known numbers and agree. Absent
 *  fields (nulls from a draft that predates this field, NaN from a bad read)
 *  never compare equal — an unknown signature is not a match. */
export function sigMatches(a: MachineSig, b: MachineSig): boolean {
  return Number.isFinite(a.numSlots) && Number.isFinite(a.numPoles)
      && Number.isFinite(b.numSlots) && Number.isFinite(b.numPoles)
      && a.numSlots === b.numSlots && a.numPoles === b.numPoles;
}

/** Resolve an agent draft's OWN reference card (its `reference_motor_id`),
 *  refusing it the moment its signature disagrees with the draft's own
 *  `starting_point` — never a stand-in from another machine's passport, even
 *  the id the server sent. Returns undefined when there is no usable card,
 *  which the caller must treat as "no configurator model", not "use whatever
 *  was on screen before". */
export function resolveDraftTarget<T extends { id: string; geo: MachineSig }>(
  refs: readonly T[],
  referenceMotorId: string | null | undefined,
  startingPoint: { slots: number | null | undefined; poles: number | null | undefined },
): T | undefined {
  if (!referenceMotorId) return undefined;
  const t = refs.find((r) => r.id === `cat:${referenceMotorId}`);
  if (!t) return undefined;
  const known = Number.isFinite(startingPoint.slots) && Number.isFinite(startingPoint.poles);
  if (known && !sigMatches(t.geo, { numSlots: startingPoint.slots, numPoles: startingPoint.poles })) {
    return undefined;
  }
  return t;
}

/** Whether the Configurator has one consistent machine to compute, or must
 *  show the empty state instead of any result tile.
 *  - Viewing an opened draft: blocked unless that draft resolved its OWN
 *    reference card (`resolveDraftTarget` above).
 *  - Otherwise (the loaded/open machine): blocked unless the live geometry
 *    was matched to a reference passport by cross-section (`liveMatched`). */
export function isBlocked(opts: { draftOpen: boolean; hasDraftTarget: boolean; liveMatched: boolean }): boolean {
  return opts.draftOpen ? !opts.hasDraftTarget : !opts.liveMatched;
}
