// Communication preferences: pure helpers (no DOM, no network, no imports) so node --test can run them.
// Backend: src/motor_ai_sim/comm_prefs.py - the topic ids and the wording keys must match it (tests/test_comm_prefs.py checks).

export const COMM_TOPICS = ['product_updates', 'new_motors', 'engineering_content', 'co_development'] as const;
export type CommTopic = (typeof COMM_TOPICS)[number];
export type Topics = Record<CommTopic, boolean>;

/** Every box is UNTICKED by default (GDPR: no pre-ticked consent). */
export function emptyTopics(): Topics {
  return { product_updates: false, new_motors: false, engineering_content: false, co_development: false };
}

/** Only a literal `true` counts; anything else (missing, "true", 1) is unticked. */
export function normalizeTopics(raw: unknown): Topics {
  const out = emptyTopics();
  if (raw && typeof raw === 'object') {
    for (const t of COMM_TOPICS) out[t] = (raw as Record<string, unknown>)[t] === true;
  }
  return out;
}

/** The topics whose value differs from what is saved: the body of a PUT (nothing to send = nothing to save). */
export function changedTopics(saved: Topics, draft: Topics): Partial<Topics> {
  const out: Partial<Topics> = {};
  for (const t of COMM_TOPICS) if (saved[t] !== draft[t]) out[t] = draft[t];
  return out;
}

/** Sign-up: only what the person ticked is sent (an unticked box is the default, not a decision). */
export function tickedTopics(draft: Topics): Partial<Topics> {
  const out: Partial<Topics> = {};
  for (const t of COMM_TOPICS) if (draft[t]) out[t] = true;
  return out;
}

export function anyTicked(draft: Topics): boolean {
  return COMM_TOPICS.some((t) => draft[t]);
}

/** /preferences and /zh-cn/preferences, with or without a trailing slash. */
export function isPreferencesPath(pathname: unknown): boolean {
  return typeof pathname === 'string' && /^\/(zh-cn\/)?preferences\/?$/i.test(pathname);
}

export interface PrefsLink { token: string; action: '' | 'unsubscribe' }

/** `?t=<signed token>[&action=unsubscribe]` from the mailed link. */
export function readLink(search: unknown): PrefsLink {
  try {
    const q = new URLSearchParams(typeof search === 'string' ? search : '');
    const token = (q.get('t') ?? '').trim();
    const action = token && q.get('action') === 'unsubscribe' ? 'unsubscribe' : '';
    return { token, action };
  } catch { return { token: '', action: '' }; }
}

/** Mirrors routes/comm_prefs.mask: `v***@motresres.com`. */
export function maskEmail(email: string): string {
  const [local, dom] = String(email ?? '').split('@');
  return dom ? `${local.slice(0, 1)}***@${dom}` : '***';
}

export type NdaCta = 'signed' | 'sign' | 'request' | 'signin_sign' | 'signin_request';

/** What the NDA card offers: sign online (signing is open), request it (it is not), or sign in first. */
export function ndaCta(nda: { signed: boolean; signable: boolean }, signedIn: boolean): NdaCta {
  if (nda.signed) return 'signed';
  if (!signedIn) return nda.signable ? 'signin_sign' : 'signin_request';
  return nda.signable ? 'sign' : 'request';
}

/** The ticket the "Request the NDA" button files through the existing support-ticket path (read by the team, so English). */
export function ndaRequestTicket(lang: string): { type: 'account'; title: string; description: string } {
  return {
    type: 'account',
    title: 'NDA request',
    description: 'Please send me the non-disclosure agreement so that my account can use the 3-D tools and STEP downloads. '
      + `Requested from the communication preferences page (interface language: ${lang === 'zh-CN' ? 'zh-CN' : 'en'}).`,
  };
}
