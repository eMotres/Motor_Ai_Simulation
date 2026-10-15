export interface CatalogDutyForStaleness {
  name: string;
  result?: { build_sig?: string | null } | null;
  runs?: Array<{ stale?: boolean | null }> | null;
}

/** Names every duty with a result explicitly known to belong to an older build. */
export function staleCatalogDutyNames(
  currentBuildSig: string | null | undefined,
  duties: readonly CatalogDutyForStaleness[] | null | undefined,
): string[] {
  return (duties ?? [])
    .filter((duty) => {
      const resultSig = duty.result?.build_sig;
      const staleResult = Boolean(resultSig && currentBuildSig && resultSig !== currentBuildSig);
      return staleResult || (duty.runs ?? []).some((run) => run.stale === true);
    })
    .map((duty) => duty.name);
}
