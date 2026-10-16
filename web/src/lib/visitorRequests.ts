/**
 * visitorRequests — the pure half of the Admin "visitor requests" inbox.
 *
 * A signed-out visitor talks to the in-app assistant on the landing page; when
 * they leave contact details the backend files a structured request. This module
 * holds the rules the UI reads those records with, with no fetch and no React,
 * so `web/src/lib/__tests__/visitorRequests.test.mjs` can state them.
 *
 * TIME UNIT: every timestamp on these records is epoch SECONDS (a float), the
 * unit the Python backend writes. The AdminPanel's own `fmtDate` takes
 * MILLIseconds — do not feed one to the other. `fmtWhen` below is the seconds one.
 */

/** One turn of a logged visitor conversation. */
export interface VisitorChatTurn {
  ts: number;
  user: string;
  reply: string;
  source: string;
  /** Non-null when the rate limiter refused this turn. */
  limit: 'ip_burst' | 'ip_day' | 'global_day' | null;
}

/** One visitor conversation of a given day. */
export interface VisitorConversation {
  conv: string;
  ip_hash: string;
  ua: string;
  started: number;
  last: number;
  turns: VisitorChatTurn[];
}

/** A day's log as the backend serves it. */
export interface VisitorChatsResponse {
  day: string;
  days: string[];
  count: number;
  conversations: VisitorConversation[];
}

/** One turn of the transcript carried on a filed request. */
export interface VisitorRequestTurn {
  role: string;
  content: string;
}

/** A filed access request — a visitor who left their contact details. */
export interface VisitorRequest {
  id: string;
  ts: number;
  updated: number;
  name: string;
  company: string;
  email: string;
  note: string;
  status: RequestStatus;
  ip_hash: string;
  ua: string;
  /** How many repeat requests from the same address were folded in (>= 1). */
  merged: number;
  transcript: VisitorRequestTurn[];
}

/** The inbox as the backend serves it. */
export interface VisitorRequestsResponse {
  count: number;
  new: number;
  requests: VisitorRequest[];
}

export const REQUEST_STATUSES = ['new', 'contacted', 'invited', 'declined'] as const;
export type RequestStatus = (typeof REQUEST_STATUSES)[number];

/** Length of the one-line summary before it is cut. */
const SUMMARY_MAX = 120;

/**
 * How many requests are still untouched — the number on the Admin tab badge.
 *
 * The caller feeds this a PARSED HTTP BODY, which on a 401/403/500 is whatever
 * the error page happened to be. Anything that is not an array of records is
 * simply "no new requests", never a throw inside a render.
 */
export function newRequestCount(requests: VisitorRequest[] | null | undefined): number {
  if (!Array.isArray(requests)) return 0;
  let n = 0;
  for (const r of requests) if (r && r.status === 'new') n += 1;
  return n;
}

/** Cut to `max` characters on a word boundary where there is one, with an ellipsis. */
function clip(s: string, max: number): string {
  const t = s.trim().replace(/\s+/g, ' ');
  if (t.length <= max) return t;
  const cut = t.slice(0, max - 1);
  const sp = cut.lastIndexOf(' ');
  return `${(sp > max * 0.6 ? cut.slice(0, sp) : cut).trimEnd()}…`;
}

/**
 * The one line the list row shows: what this visitor wants.
 *
 * The note is what the assistant distilled, so it wins. Without one, the last
 * thing the visitor actually typed is the next best evidence, and only a request
 * with neither falls back to the placeholder.
 */
export function requestSummary(r: VisitorRequest | null | undefined): string {
  const note = typeof r?.note === 'string' ? r.note.trim() : '';
  if (note) return clip(note, SUMMARY_MAX);
  const turns = Array.isArray(r?.transcript) ? r.transcript : [];
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const t = turns[i];
    if (t && t.role === 'user' && typeof t.content === 'string' && t.content.trim()) {
      return clip(t.content, SUMMARY_MAX);
    }
  }
  return '(no message)';
}

/** "Jane Doe · ACME Robotics" — whichever parts exist, else the e-mail. */
export function contactLine(r: VisitorRequest | null | undefined): string {
  const parts = [r?.name, r?.company]
    .map((s) => (typeof s === 'string' ? s.trim() : ''))
    .filter((s) => s.length > 0);
  if (parts.length) return parts.join(' · ');
  const email = typeof r?.email === 'string' ? r.email.trim() : '';
  return email || '(no contact)';
}

/** Epoch SECONDS → a short local date + time. `—` when there is no timestamp. */
export function fmtWhen(ts?: number | null): string {
  if (typeof ts !== 'number' || !Number.isFinite(ts) || ts <= 0) return '—';
  return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
}
