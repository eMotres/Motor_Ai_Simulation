/** Activity digests (docs/DIGEST.md): the pure parts of the settings block and the Admin list - node-testable, no React.
 *
 *  Owner rules 2026-10-26: only two modes exist, `approve` (default: the digest waits in Admin until the owner sends it) and
 *  `off`.  There is no automatic mode, so the UI offers none and refuses to build one. */

export const DIGEST_MODES = ['approve', 'off'] as const;
export const DIGEST_FREQUENCIES = ['daily', 'weekly'] as const;
export const DIGEST_RECIPIENTS = ['all', 'chosen'] as const;
export type DigestMode = (typeof DIGEST_MODES)[number];
export type DigestFrequency = (typeof DIGEST_FREQUENCIES)[number];
export type DigestRecipients = (typeof DIGEST_RECIPIENTS)[number];

export interface DigestMember { email: string; role: string; opted_out: boolean }
export interface DigestSettingsView {
  scope: string; title: string; mode: DigestMode; frequency: DigestFrequency; recipients: DigestRecipients;
  chosen: string[]; optouts: string[]; members: DigestMember[];
  will_receive: Array<{ email: string; role: string; lang: string }>;
  not_receiving: Array<{ email: string; reason: string }>;
  pending: { id: string; items: number; updated_at: string } | null;
  last_sent: string | null; active: boolean; updated_by?: string; updated_at?: string;
}

export type DigestTarget = { kind: 'project'; id: string } | { kind: 'production'; die: string; config: string };

export function settingsPath(target: DigestTarget): string {
  return target.kind === 'project'
    ? `/api/digest/settings/project/${encodeURIComponent(target.id)}`
    : `/api/digest/settings/production/${encodeURIComponent(target.die)}/${encodeURIComponent(target.config)}`;
}

/** A change the form may send.  Anything that is not one of the two modes is refused here too (the server refuses it as well). */
export function settingsChange(view: Pick<DigestSettingsView, 'mode' | 'frequency' | 'recipients' | 'chosen'>,
  change: Partial<{ mode: string; frequency: string; recipients: string; chosen: string[] }>): Record<string, unknown> | null {
  const out: Record<string, unknown> = {};
  if (change.mode !== undefined) { if (!(DIGEST_MODES as readonly string[]).includes(change.mode)) return null; out.mode = change.mode; }
  if (change.frequency !== undefined) { if (!(DIGEST_FREQUENCIES as readonly string[]).includes(change.frequency)) return null; out.frequency = change.frequency; }
  if (change.recipients !== undefined) {
    if (!(DIGEST_RECIPIENTS as readonly string[]).includes(change.recipients)) return null;
    out.recipients = change.recipients;
    if (change.recipients === 'chosen' && change.chosen === undefined && view.chosen.length === 0) out.chosen = [];
  }
  if (change.chosen !== undefined) out.chosen = change.chosen.map((e) => e.trim().toLowerCase()).filter(Boolean);
  return Object.keys(out).length ? out : null;
}

export function toggleChosen(chosen: string[], email: string, on: boolean): string[] {
  const e = email.trim().toLowerCase();
  const rest = chosen.filter((x) => x !== e);
  return on ? [...rest, e].sort() : rest.sort();
}

/** Is this member listed among the people the digest goes to under the current settings? */
export function isListed(view: Pick<DigestSettingsView, 'recipients' | 'chosen'>, email: string): boolean {
  return view.recipients === 'all' || view.chosen.includes(email.trim().toLowerCase());
}

export interface DigestSummary {
  id: string; scope: string; kind: 'project' | 'production'; title: string; title_zh?: string;
  status: 'pending' | 'partial' | 'sent' | 'discarded' | 'superseded';
  period: { start: string; end: string }; updated_at: string; items: number; recipients: number; bilingual: boolean; subject: string;
  sent?: { at: string; by: string; sent: number; failed: number }; discarded?: { at: string; by: string };
  last_error?: { at: string; failed: number };
  partial?: { at: string; by: string; sent: number; failed: number; outstanding: string[] };
}

/** Drafts that can still be sent (waiting, or partly sent) first, newest on top, then the history: what the Admin list shows. */
export function splitDrafts(rows: DigestSummary[]): { pending: DigestSummary[]; history: DigestSummary[] } {
  const byNewest = (a: DigestSummary, b: DigestSummary) => b.updated_at.localeCompare(a.updated_at);
  const open = (r: DigestSummary) => r.status === 'pending' || r.status === 'partial';
  return { pending: rows.filter(open).sort(byNewest), history: rows.filter((r) => !open(r)).sort(byNewest) };
}

/** The ONLY request body that sends a digest: an explicit approval of EXACTLY the text previewed (its content hash).  Kept as a
 *  function so a test can pin it; without a hash there is no body. */
export function approveBody(contentHash: string): { approve: true; content_hash: string } {
  if (typeof contentHash !== 'string' || !/^[0-9a-f]{64}$/.test(contentHash)) throw new Error('approval needs the content hash of the previewed text');
  return { approve: true, content_hash: contentHash };
}
