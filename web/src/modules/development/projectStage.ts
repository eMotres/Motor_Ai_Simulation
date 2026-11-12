/** The stage a member reads (owner 2026-11-12).  A project whose STORED status is `spec` and that has a submitted specification version
 *  shows as "Specification submitted".  This is a derived label only: the stored status and the staff status menu are untouched.
 *  Pure functions: `node --test` imports this. */

export interface SpecSubmitted { version: number; at: string | null }

/** The key under `status.*` to show: `spec_submitted` for a spec-stage project with a submitted version, else the stored status. */
export function shownStatus(status: string, specSubmitted?: SpecSubmitted | null): string {
  return status === 'spec' && !!specSubmitted && Number.isFinite(specSubmitted.version) ? 'spec_submitted' : status;
}

/** The date of the confirmation line, in UTC and the reader's language ("12 Nov 2026"); empty when the stamp is unusable. */
export function submittedDate(at: string | null | undefined, lang: string): string {
  const t = at ? new Date(at) : null;
  if (!t || Number.isNaN(t.getTime())) return '';
  return t.toLocaleDateString(lang === 'zh-CN' ? 'zh-CN' : 'en-GB', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
}
