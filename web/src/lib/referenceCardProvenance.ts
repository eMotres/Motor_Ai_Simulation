/** Keep only the server-pinned candidate identity used by reviewed metadata. */
export function referenceCandidateProvenance(value: unknown): { candidate_sha256: string | null } | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = (value as Record<string, unknown>).candidate_sha256;
  return { candidate_sha256: typeof candidate === 'string' ? candidate : null };
}

/** Read provenance from the catalog card wrapper used by the API.  A legacy
 * top-level field remains accepted, but conflicting or malformed duplicate
 * fields fail closed so a different card cannot inherit temperature metadata.
 */
export function referenceCandidateProvenanceFromCatalogEntry(
  value: unknown,
): { candidate_sha256: string | null } | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const entry = value as Record<string, unknown>;
  const passportWrapper = entry.passport;
  const wrapper = passportWrapper !== null && typeof passportWrapper === 'object'
    && !Array.isArray(passportWrapper)
    ? passportWrapper as Record<string, unknown>
    : null;
  const sources: unknown[] = [];
  if (Object.prototype.hasOwnProperty.call(entry, 'reference_provenance')
      && entry.reference_provenance != null) sources.push(entry.reference_provenance);
  if (wrapper && Object.prototype.hasOwnProperty.call(wrapper, 'reference_provenance')
      && wrapper.reference_provenance != null) sources.push(wrapper.reference_provenance);
  if (!sources.length) return null;

  const parsed = sources.map(referenceCandidateProvenance);
  if (parsed.some((item) => item === null || !item.candidate_sha256)) return null;
  const candidates = new Set(parsed.map((item) => item!.candidate_sha256!.toLowerCase()));
  if (candidates.size !== 1) return null;
  return { candidate_sha256: parsed[0]!.candidate_sha256 };
}
