// AeroStator Platform Services and Data Agreement: the pure helpers behind the dialog and the 428 retry (no React, no fetch of
// its own) so node --test can pin them.  Backend: motor_ai_sim/platform_agreement.py, routes/platform_agreement.py.
//
// The agreement is accepted by a COMPANY before a development starts.  The server answers a gated action with HTTP 428
// {detail: {code: 'agreement_required', version}}; lib/apiAuth.ts hands that to `handleAgreementRequired`, which asks the host
// component (components/agreement/AgreementHost) to open the dialog and, once accepted, repeats the action.
import { norm, renderNda, type NdaValues } from './nda';

export const AGREEMENT_MODES = ['FREE', 'PRIVATE'] as const;
export type AgreementMode = (typeof AGREEMENT_MODES)[number];
export const COMPANY_FIELDS = ['company', 'reg_no', 'country', 'address'] as const;
export type CompanyField = (typeof COMPANY_FIELDS)[number];
export type CompanyFacts = Record<CompanyField, string>;

export interface AgreementAcceptance {
  id: string; iso: string; mode: AgreementMode; version: string; company?: string; signatory?: string; title?: string;
  scope?: 'company' | 'account'; ai_training_allowed?: boolean; private_pending_payment?: boolean;
}

export interface AgreementStatus {
  enforced: boolean; required: boolean; accepted: boolean; exempt: 'staff' | 'motres' | null;
  version?: string; modes?: AgreementMode[]; scope?: 'company' | 'account'; acceptance?: AgreementAcceptance | null;
  company?: CompanyFacts & { known: boolean; complete: boolean };
  admin?: { installed: boolean; version: string | null; placeholder: boolean; disabled: boolean };
}

/** Does this answer mean "accept the agreement first"?  HTTP 428 with the server's own code, nothing else. */
export function isAgreementRequired(status: number, body: unknown): boolean {
  if (status !== 428 || !body || typeof body !== 'object') return false;
  const d = (body as { detail?: unknown }).detail;
  return !!d && typeof d === 'object' && (d as { code?: unknown }).code === 'agreement_required';
}

/** Should the account menu offer the agreement?  Users only when it is enforced and applies to them; admins always (they see the install state). */
export function showsAgreement(s: AgreementStatus | null): boolean {
  if (!s) return false;
  return s.admin !== undefined || (s.enforced && !s.exempt);
}

/** The three parts of the quiet line "Agreement: FREE · v1.0 · accepted 2026-10-27" (the words around them come from i18n). */
export function acceptedDate(iso: string): string {
  return /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(0, 10) : iso;
}

export function agreementParts(acc: Pick<AgreementAcceptance, 'mode' | 'version' | 'iso'>): { mode: string; version: string; date: string } {
  return { mode: acc.mode, version: acc.version, date: acceptedDate(acc.iso) };
}

/** The admin company row: "FREE · v1.0" (quiet text, no colours), "—" when the company has not accepted. */
export function companyAgreementText(a: { mode?: string; version?: string } | null | undefined): string {
  return a && a.mode ? `${a.mode} · ${a.version ?? ''}`.trim() : '—';
}

export interface AgreementForm extends CompanyFacts {
  mode: AgreementMode | ''; signer: string; title: string; business: boolean; authority: boolean;
}

export const emptyAgreementForm = (facts?: Partial<CompanyFacts>): AgreementForm => ({
  mode: '', signer: '', title: '', business: false, authority: false,
  company: facts?.company ?? '', reg_no: facts?.reg_no ?? '', country: facts?.country ?? '', address: facts?.address ?? '',
});

export type AgreementError = 'required' | 'country' | 'business' | 'authority' | 'mode';

/** Field errors (keys are field names); empty = may press Accept.  The company fields are asked only where the server does not know them. */
export function agreementErrors(form: AgreementForm, known: Partial<CompanyFacts>, countries: readonly string[]): Record<string, AgreementError> {
  const e: Record<string, AgreementError> = {};
  if (!AGREEMENT_MODES.includes(form.mode as AgreementMode)) e.mode = 'mode';
  if (!norm(form.signer)) e.signer = 'required';
  if (!norm(form.title)) e.title = 'required';
  for (const k of COMPANY_FIELDS) if (!known[k] && !norm(form[k])) e[k] = 'required';
  if (!known.country && norm(form.country) && !countries.includes(form.country.trim().toUpperCase())) e.country = 'country';
  if (!form.business) e.business = 'business';
  if (!form.authority) e.authority = 'authority';
  return e;
}

/** What goes into POST /api/agreement/accept (known company data is not sent: the server uses its own). */
export function acceptBody(form: AgreementForm, known: Partial<CompanyFacts>, version: string, lang: string): Record<string, unknown> {
  const body: Record<string, unknown> = {
    mode: form.mode, signer: norm(form.signer), title: norm(form.title), business: form.business, authority: form.authority, version, lang,
  };
  for (const k of COMPANY_FIELDS) if (!known[k]) body[k] = norm(form[k]);
  return body;
}

/** The text as the server renders it: the client's data, the chosen mode, signer, date; an unknown placeholder stays as written. */
export function renderAgreement(
  text: string, facts: CompanyFacts, form: Pick<AgreementForm, 'mode' | 'signer' | 'title'>, email: string, date: string, version: string, blank = '______',
): string {
  const v: Record<string, unknown> = {
    company: norm(facts.company) || blank, reg_no: norm(facts.reg_no) || blank, country: norm(facts.country).toUpperCase() || blank,
    address: norm(facts.address) || blank, signatory: norm(form.signer) || blank, title: norm(form.title) || blank,
    email, date, version, mode: form.mode || blank, country_is_cn_hk: ['CN', 'HK'].includes(norm(facts.country).toUpperCase()),
  };
  return renderNda(text, v as unknown as NdaValues);
}

// -- the 428 retry -------------------------------------------------------------------------------------------------------

export type AgreementHost = (detail: { version?: string }) => Promise<boolean>;
let host: AgreementHost | null = null;
let pending: Promise<boolean> | null = null;

/** The mounted AgreementHost registers itself here (null on unmount). */
export function setAgreementHost(h: AgreementHost | null): void {
  host = h;
}

/** Ask the user to accept; concurrent gated calls share ONE dialog.  Resolves true when accepted, false when cancelled or no host is mounted. */
export function requestAgreement(detail: { version?: string } = {}): Promise<boolean> {
  if (!host) return Promise.resolve(false);
  if (!pending) {
    pending = host(detail).catch(() => false).finally(() => { pending = null; });
  }
  return pending;
}

/** Does this answer mean "accept the updated terms first"?  HTTP 428 `terms_required`: the owner's deadline (env TERMS_REQUIRED_FROM)
 *  has passed and the account has no acceptance of the current Terms of Use, so a new simulation cannot start. */
export function isTermsGate(status: number, body: unknown): boolean {
  if (status !== 428 || !body || typeof body !== 'object') return false;
  const d = (body as { detail?: unknown }).detail;
  return !!d && typeof d === 'object' && (d as { code?: unknown }).code === 'terms_required';
}

export type TermsHost = (detail: { version?: string; from?: string }) => Promise<boolean>;
let termsHost: TermsHost | null = null;
let termsPending: Promise<boolean> | null = null;

/** The mounted TermsUpdateHost registers itself here (null on unmount). */
export function setTermsHost(h: TermsHost | null): void {
  termsHost = h;
}

/** Open the "Updated terms" dialog; concurrent gated calls share ONE dialog.  Resolves true when accepted, false on "Later" or when no host is mounted. */
export function requestTerms(detail: { version?: string; from?: string } = {}): Promise<boolean> {
  if (!termsHost) return Promise.resolve(false);
  if (!termsPending) {
    termsPending = termsHost(detail).catch(() => false).finally(() => { termsPending = null; });
  }
  return termsPending;
}

/** `res` is the answer to a gated call: when it is the 428 `agreement_required`, open the Platform Agreement dialog; when it is the
 *  428 `terms_required`, open the "Updated terms" dialog; after acceptance repeat the call once via `retry`.  Any other answer (or a
 *  cancel) is returned untouched, so the caller sees the 428 and explains it itself. */
export async function handleAgreementRequired(res: Response, retry: () => Promise<Response>, ask: typeof requestAgreement = requestAgreement,
  askTerms: typeof requestTerms = requestTerms): Promise<Response> {
  if (res.status !== 428) return res;
  let body: unknown = null;
  try { body = await res.clone().json(); } catch { /* not JSON */ }
  if (isTermsGate(res.status, body)) {
    const d = (body as { detail: { version?: string; from?: string } }).detail;
    return (await askTerms({ version: d.version, from: d.from })) ? retry() : res;
  }
  if (!isAgreementRequired(res.status, body)) return res;
  const detail = (body as { detail: { version?: string } }).detail;
  return (await ask({ version: detail.version })) ? retry() : res;
}
