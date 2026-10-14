// Communication preferences API client (backend: routes/comm_prefs.py).  The Authorization header of the signed-in calls is added
// by the global fetch wrapper (apiAuth.ts); the token calls need no session.
import { normalizeTopics, type Topics } from './commPrefsLogic';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

export interface PrefsState {
  account: string;
  topics: Topics;
  unsubscribed_all: boolean;
  updated: string | null;
  text_version: string;
  nda: { signed: boolean; signable: boolean };
}

async function call(path: string, init?: RequestInit): Promise<PrefsState> {
  const r = await fetch(`${API}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(((j as { detail?: unknown }).detail as string | undefined) || `HTTP ${r.status}`);
  const s = j as PrefsState;
  return { ...s, topics: normalizeTopics(s.topics) };
}

const put = (path: string, body: unknown) => call(path, { method: 'PUT', body: JSON.stringify(body) });

export const getMine = () => call('/api/me/comm-prefs');
export const saveMine = (topics: Partial<Topics>, lang: string) => put('/api/me/comm-prefs', { topics, lang });
export const unsubscribeMine = (lang: string) => put('/api/me/comm-prefs', { unsubscribe_all: true, lang });

const tp = (token: string) => `/api/comm-prefs/${encodeURIComponent(token)}`;
export const getByToken = (token: string) => call(tp(token));
export const saveByToken = (token: string, topics: Partial<Topics>, lang: string) => put(tp(token), { topics, lang });
export const unsubscribeByToken = async (token: string, lang: string) => {
  await put(tp(token), { unsubscribe_all: true, lang });
  return getByToken(token);
};

/** "Request the NDA" - the existing support-ticket path (type account); signed in only. */
export async function fileTicket(t: { type: string; title: string; description: string }): Promise<void> {
  const r = await fetch(`${API}/api/support/tickets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(t),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(((j as { detail?: string }).detail) || `HTTP ${r.status}`);
  }
}
