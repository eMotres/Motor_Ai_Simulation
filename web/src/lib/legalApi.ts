// Client of the legal routes (src/motor_ai_sim/routes/legal.py): the texts (public), the signed-in account's acceptance status and
// the acceptance of the current version by an existing account.
import { getSessionSid } from './localAuth';
import { acceptancePayload, type AcceptLabels, type AcceptanceForm, type LegalLang, type LegalStatus } from './legalLogic';

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

/** The existing account fills the block and ticks the box: records source "existing" (with the optional earlier-data election and the
 *  labels as shown).  Throws with the server's message. */
export async function acceptCurrentTerms(version: string, lang: LegalLang, form: AcceptanceForm, labels: AcceptLabels = {}): Promise<void> {
  const r = await fetch(`${API}/api/legal/accept`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...acceptancePayload({ ...form, ticked: true }, version, { existing: true, labels }), lang }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({})) as { detail?: string };
    throw new Error(j.detail || `HTTP ${r.status}`);
  }
}

// "Later" lasts until the next sign-in: it is remembered per SESSION id (a new sign-in is a new session).
const LATER_KEY = 'mas.terms.later';

export function laterSession(): string | null {
  try { return sessionStorage.getItem(LATER_KEY); } catch { return null; }
}

export function rememberLater(): void {
  try { const sid = getSessionSid(); if (sid) sessionStorage.setItem(LATER_KEY, sid); } catch { /* private mode: asked again next load */ }
}
