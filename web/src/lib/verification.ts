// Applicant verification (docs/APPLICANT_VERIFICATION.md): the pure helpers behind the status line, the NDA form's extra fields and the
// admin queue (no React, no fetch) so node --test can pin them.
//
// Signing the NDA online no longer opens the 3-D tools and the STEP download by itself.  The server answers every model list with
// `access`: { state, nda_signed, case, reply_url, requests }.  `state`:
//   ok           open (verified, or an administrator vouched for the account, or an admin)
//   nda          the NDA is not signed yet
//   in_progress  checks are running or our team is reviewing - the person only waits
//   need_info    we need one more thing from the person (a link to the reply page says what)
//   denied       an administrator closed the request

export type AccessState = 'ok' | 'nda' | 'in_progress' | 'need_info' | 'denied';

export interface AccessInfo {
  state: AccessState;
  nda_signed: boolean;
  case?: string | null;
  /** the personal, no-JavaScript reply page */
  reply_url?: string;
  /** codes of what is asked (website, corporate_email, person_page, ...) */
  requests?: string[];
  details?: { website?: string; purpose?: string; vat_no?: string; work_email?: string };
}

const STATES: readonly AccessState[] = ['ok', 'nda', 'in_progress', 'need_info', 'denied'];

/** The server's `access` object, tolerant: a missing or damaged one falls back to what `can_download` alone says. */
export function parseAccess(raw: unknown, canDownload: boolean): AccessInfo {
  const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const state = STATES.find((s) => s === o.state);
  if (!state) return canDownload ? { state: 'ok', nda_signed: true } : { state: 'nda', nda_signed: false };
  return {
    state, nda_signed: o.nda_signed === true || state !== 'nda',
    case: typeof o.case === 'string' ? o.case : null,
    reply_url: typeof o.reply_url === 'string' ? o.reply_url : undefined,
    requests: Array.isArray(o.requests) ? o.requests.filter((x): x is string => typeof x === 'string') : undefined,
    details: o.details && typeof o.details === 'object' ? (o.details as AccessInfo['details']) : undefined,
  };
}

/** Why the tools are locked: no NDA yet, or the NDA is signed and the verification is not complete. */
export type LockedKind = 'nda' | 'verification';
export const lockedKind = (a: AccessInfo): LockedKind => (a.nda_signed ? 'verification' : 'nda');

/** Only a web address may become a link (the reply URL comes from our server, but a link is never built from anything else). */
export function safeReplyUrl(url: string | undefined | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.toString() : null;
  } catch { return null; }
}

export interface StatusLine {
  /** i18n key under model3d:verification.* */
  key: 'inProgress' | 'needInfo' | 'denied';
  /** the reply page, when there is something the person can do */
  link: string | null;
}

/** The status line shown instead of "Sign NDA" once the NDA is signed; null = nothing to show (open, or no NDA yet). */
export function statusLine(a: AccessInfo): StatusLine | null {
  if (a.state === 'need_info') return { key: 'needInfo', link: safeReplyUrl(a.reply_url) };
  if (a.state === 'in_progress') return { key: 'inProgress', link: safeReplyUrl(a.reply_url) };
  if (a.state === 'denied') return { key: 'denied', link: null };
  return null;
}

/** What each request code is called in the UI (model3d:verification.ask.<code>). */
export const REQUEST_CODES = ['corporate_email', 'person_page', 'website', 'registration_number', 'vat_number', 'domain_mismatch',
  'role_clarification', 'business_licence', 'verify_account_email', 'free_text'] as const;
export type RequestCode = (typeof REQUEST_CODES)[number];
export const isRequestCode = (c: string): c is RequestCode => (REQUEST_CODES as readonly string[]).includes(c);

// -- the extra fields of the NDA form ---------------------------------------------------------------------------------

export interface IntakeForm { website: string; purpose: string; vat_no: string; work_email: string }
export const emptyIntake = (): IntakeForm => ({ website: '', purpose: '', vat_no: '', work_email: '' });

/** Same rule as the server (textutil.normalize_website): a plausible public web address, else null. */
export function normalizeWebsite(raw: string): string | null {
  const t = raw.trim();
  if (!t || t.length > 300 || /\s/.test(t)) return null;
  const u = /:\/\//.test(t) ? t : `https://${t}`;
  const m = /^(https?):\/\/([^/?#:@]+)(:\d{1,5})?([/?#].*)?$/i.exec(u);
  if (!m) return null;
  const host = m[2].toLowerCase().replace(/^\.+|\.+$/g, '');
  if (!host.includes('.') || /^[0-9.]+$/.test(host) || /\.(local|internal|localhost|lan)$/.test(host)) return null;
  return `${m[1].toLowerCase()}://${host}${m[3] ?? ''}${m[4] ?? ''}`;
}

export const validEmail = (s: string): boolean => /^[^@\s<>"',;]{1,64}@[^@\s<>"',;]{1,255}\.[^@\s<>"',;.]{2,}$/.test(s.trim());

export type IntakeError = 'required' | 'invalid';

/** Field errors of the form: website and purpose are asked for (a gap is still only a question for the server, but the form is
 *  complete when it is signed); the work e-mail and VAT number are optional and checked only when given. */
export function intakeErrors(f: IntakeForm): Partial<Record<keyof IntakeForm, IntakeError>> {
  const e: Partial<Record<keyof IntakeForm, IntakeError>> = {};
  if (!f.website.trim()) e.website = 'required';
  else if (!normalizeWebsite(f.website)) e.website = 'invalid';
  if (!f.purpose.trim()) e.purpose = 'required';
  if (f.work_email.trim() && !validEmail(f.work_email)) e.work_email = 'invalid';
  return e;
}

/** The body fields the NDA sign call carries for the verification. */
export function intakeBody(f: IntakeForm): IntakeForm {
  return { website: f.website.trim(), purpose: f.purpose.replace(/\s+/g, ' ').trim(), vat_no: f.vat_no.trim(), work_email: f.work_email.trim() };
}

// -- the admin queue ----------------------------------------------------------------------------------------------------

export const CASE_STATUSES = ['MORE_INFORMATION_REQUIRED', 'MANUAL_REVIEW', 'PENDING', 'VERIFIED', 'DENIED'] as const;
export type CaseStatus = (typeof CASE_STATUSES)[number];

export interface QueueRow {
  id: string; user: string; status: string; company: string; country: string; applicant: string; updated_iso: string; access: boolean;
}

/** Queue order: what needs a person first (manual review), then waiting for the applicant, running, then decided; newest first inside. */
const ORDER: Record<string, number> = { MANUAL_REVIEW: 0, MORE_INFORMATION_REQUIRED: 1, PENDING: 2, VERIFIED: 3, DENIED: 4 };
export function sortQueue<T extends Pick<QueueRow, 'status' | 'updated_iso'>>(rows: readonly T[]): T[] {
  return [...rows].sort((a, b) => (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9) || b.updated_iso.localeCompare(a.updated_iso));
}

/** Which admin actions make sense for a status (the server still checks). */
export function adminActions(status: string): Array<'approve' | 'requestInfo' | 'deny' | 'rerun'> {
  if (status === 'DENIED') return ['approve', 'rerun'];
  if (status === 'VERIFIED') return ['requestInfo', 'deny', 'rerun'];
  return ['approve', 'requestInfo', 'deny', 'rerun'];
}

/** A reason / question must say something. */
export const reasonOk = (s: string): boolean => s.replace(/\s+/g, ' ').trim().length >= 3;

export type ChipTone = 'ok' | 'warn' | 'bad' | 'info';
export function statusTone(status: string): ChipTone {
  return status === 'VERIFIED' ? 'ok' : status === 'DENIED' ? 'bad' : status === 'MANUAL_REVIEW' ? 'warn' : 'info';
}
