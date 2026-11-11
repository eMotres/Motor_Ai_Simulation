export interface CatalogDutyForStaleness {
  name: string;
  result?: { build_sig?: string | null } | null;
  runs?: Array<{ stale?: boolean | null }> | null;
}

/** One build signature, or every spelling of the same build (the tree's `build_sig_aliases`). */
export type CurrentBuildSigs = string | readonly (string | null | undefined)[] | null | undefined;

/**
 * Was a stored result (its `build_sig` stamp) computed on a build other than the
 * configuration's current one?  `current` is the tree's `build_sig_aliases` (every
 * spelling of the SAME build, routes/family._build_sigs: the default liner, enamel
 * and conductor named or left unnamed) or a bare `build_sig`.  Unknown on either
 * side is not "moved" (owner 2026-11-11: a catalog motor whose files only gained the
 * default materials' names opened on "⚠ rated / ⚠ peak").
 */
export function buildSigMoved(stamp: string | null | undefined, current: CurrentBuildSigs): boolean {
  if (!stamp) return false;
  const sigs = (typeof current === 'string' ? [current] : (current ?? []))
    .filter((s): s is string => !!s);
  return sigs.length > 0 && !sigs.includes(stamp);
}

/** Names every duty with a result explicitly known to belong to an older build. */
export function staleCatalogDutyNames(
  currentBuildSig: CurrentBuildSigs,
  duties: readonly CatalogDutyForStaleness[] | null | undefined,
): string[] {
  return (duties ?? [])
    .filter((duty) => {
      const staleResult = buildSigMoved(duty.result?.build_sig, currentBuildSig);
      return staleResult || (duty.runs ?? []).some((run) => run.stale === true);
    })
    .map((duty) => duty.name);
}
