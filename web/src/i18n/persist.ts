// Persisting the interface language (docs/I18N.md).
//
// The SERVER preference (/api/me/preferences) is the user's; localStorage is
// this browser's fallback (signed out, backend down, or a 401/403).  Every
// storage and network access is guarded: a blocked storage or a failed PUT
// never stops the switch itself.
import { useEffect } from 'react';
import type { i18n as I18n } from 'i18next';
import { type Locale, normalizeLocale, readStoredLocale, writeStoredLocale } from './locale';

const API = ((import.meta.env?.VITE_API_URL as string | undefined) ?? 'http://localhost:8001').replace(/\/$/, '');

type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

/** A choice made while signed out (the landing switch): the server has not
 *  heard it yet.  In memory only — localStorage already keeps it for this
 *  browser; this just stops the sign-in sync from overwriting it. */
let unsyncedChoice: Locale | null = null;
export function pendingLocaleChoice(): Locale | null { return unsyncedChoice; }

async function putLocale(loc: Locale, fetchImpl?: FetchLike): Promise<boolean> {
  try {
    const f = fetchImpl ?? fetch;
    const r = await f(`${API}/api/me/preferences`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ locale: loc }),
    });
    return r?.ok !== false;
  } catch { return false; /* offline / signed out: localStorage keeps it */ }
}

/** Switch language now, remember it here, and (best effort) on the server. */
export async function setLocale(i18n: I18n, loc: Locale, opts: {
  remote?: boolean; fetchImpl?: FetchLike; storage?: Parameters<typeof writeStoredLocale>[1];
} = {}): Promise<void> {
  writeStoredLocale(loc, opts.storage);
  await i18n.changeLanguage(loc);
  if (opts.remote === false) return;
  unsyncedChoice = (await putLocale(loc, opts.fetchImpl)) ? null : loc;
}

/** Read the server preference; apply it when it names a shipped locale.
 *  Carry-over: a language picked on the landing before sign-in (not yet on the
 *  server), or any browser choice when the account has none, is sent up and
 *  kept; otherwise the account's own preference wins, as before. */
export async function applyServerLocale(i18n: I18n, fetchImpl: FetchLike = fetch): Promise<Locale | null> {
  try {
    const r = await fetchImpl(`${API}/api/me/preferences`);
    if (!r.ok) return null;
    const loc = normalizeLocale((await r.json())?.locale);
    const mine = unsyncedChoice ?? (loc ? null : readStoredLocale());
    if (mine) {
      if (mine !== i18n.language) await i18n.changeLanguage(mine);
      if (mine === loc || await putLocale(mine, fetchImpl)) unsyncedChoice = null;
      return mine;
    }
    if (loc && loc !== i18n.language) {
      writeStoredLocale(loc);
      await i18n.changeLanguage(loc);
    }
    return loc;
  } catch { return null; }
}

/** Once the API is ready for this user, adopt their stored language. */
export function useServerLocale(i18n: I18n, ready: boolean, userKey: string | null | undefined): void {
  useEffect(() => {
    if (!ready || !userKey) return;
    void applyServerLocale(i18n);
  }, [i18n, ready, userKey]);
}
