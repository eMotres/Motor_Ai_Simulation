// Client of the legal routes (src/motor_ai_sim/routes/legal.py): the texts (public), the signed-in account's acceptance status, the
// acceptance of the current Terms by an existing account and the Platform Agreement accepted at the start of a simulation.
import { agreementPayload, signupPayload, type AcceptLabels, type AgreementForm, type LegalLang, type LegalStatus } from './legalLogic';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

export interface LegalText { doc: string; version: string; lang: LegalLang; text: string; sha256: string }

/** One text in one language; throws with the HTTP status in the message. */
export async function fetchLegalText(doc: string, lang: LegalLang, signal?: AbortSignal): Promise<LegalText> {
  const r = await fetch(`${API}/api/legal/${encodeURIComponent(doc)}?lang=${encodeURIComponent(lang)}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json() as Promise<LegalText>;
}

export interface LegalStatusFull extends LegalStatus { privacy_version?: string; agreement_version?: string }

/** The signed-in account's state; null on any failure (the dialog is simply not shown - nothing else depends on it). */
export async function fetchLegalStatus(): Promise<LegalStatusFull | null> {
  try {
    const r = await fetch(`${API}/api/legal/status`);
    return r.ok ? ((await r.json()) as LegalStatusFull) : null;
  } catch { return null; }
}

/** The country codes of the acceptance form (the NDA / agreement list), fetched once per page load; [] when the call fails. */
let countriesPromise: Promise<string[]> | null = null;
export function fetchCountries(): Promise<string[]> {
  if (!countriesPromise) {
    countriesPromise = fetch(`${API}/api/legal/countries`)
      .then((r) => (r.ok ? r.json() : { countries: [] }))
      .then((j: { countries?: string[] }) => (Array.isArray(j.countries) ? j.countries : []))
      .catch(() => []);
    void countriesPromise.then((c) => { if (!c.length) countriesPromise = null; });          // a failed call may be retried
  }
  return countriesPromise;
}

async function postJson(path: string, body: unknown): Promise<Record<string, unknown>> {
  const r = await fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({})) as { detail?: unknown };
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : `HTTP ${r.status}`);
  return j as Record<string, unknown>;
}

/** The "We updated the Terms of Use and the Privacy Policy" line: the existing account accepts with one click.  Records a Terms-only line,
 *  source "existing", with the label as shown.  Throws with the server's message. */
export async function acceptCurrentTerms(version: string, lang: LegalLang, label?: string): Promise<void> {
  await postJson('/api/legal/accept', { ...signupPayload(true, version, label), lang });
}

/** The Platform Agreement dialog at the start of a simulation: records source "simulation_start" (mode FREE, or PRIVATE requested which
 *  also places the no-training hold).  Throws with the server's message. */
export async function acceptAgreement(form: AgreementForm, version: string, lang: LegalLang, labels: AcceptLabels = {}): Promise<void> {
  await postJson('/api/legal/accept-agreement', agreementPayload(form, version, lang, labels));
}

// "Dismiss" lasts for the browser session (the tab): remembered as the e-mail of the account it was dismissed for.
const DISMISS_KEY = 'mas.terms.dismissed';

export function dismissedFor(): string | null {
  try { return sessionStorage.getItem(DISMISS_KEY); } catch { return null; }
}

export function rememberDismissed(email: string): void {
  try { if (email) sessionStorage.setItem(DISMISS_KEY, email); } catch { /* private mode: shown again next load */ }
}
