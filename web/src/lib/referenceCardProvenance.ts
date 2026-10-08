/** Keep only the server-pinned candidate identity used by reviewed metadata. */
export function referenceCandidateProvenance(value: unknown): { candidate_sha256: string | null } | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = (value as Record<string, unknown>).candidate_sha256;
  return { candidate_sha256: typeof candidate === 'string' ? candidate : null };
}
