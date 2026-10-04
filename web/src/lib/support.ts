// Support: AI chat (via the backend Claude proxy) + user-filed tickets stored by
// the backend (POST/GET /api/support/tickets; the caller's identity comes from
// the Authorization header the app attaches, never from the body).
// Admins read every user's tickets via /api/admin/tickets.
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export type TicketType = 'bug' | 'feature' | 'question';

export interface Ticket {
  id: string;
  type: TicketType;
  title: string;
  description: string;
  status: string;     // open | in_progress | resolved | closed
  createdAt?: number | null;   // ms epoch
}

/** File a support ticket (bug / feature / question) as the signed-in user. */
export async function submitTicket(
  t: { type: TicketType; title: string; description: string },
): Promise<Ticket> {
  const r = await fetch(`${API}/api/support/tickets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: t.type, title: t.title.trim(), description: t.description.trim() }),
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { const b = await r.json(); if (typeof b?.detail === 'string') detail = b.detail; } catch { /* keep status */ }
    throw new Error(detail);
  }
  return (await r.json()).ticket as Ticket;
}

/** The signed-in user's own tickets, newest first. */
export async function listMyTickets(): Promise<Ticket[]> {
  const r = await fetch(`${API}/api/support/tickets`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return ((await r.json()).tickets ?? []) as Ticket[];
}

export interface ChatMsg { role: 'user' | 'assistant'; content: string; }

export interface ChatReply { reply: string; source: string; }

/**
 * What one /api/support/chat response MEANS.
 *
 * A 429 from this endpoint is an ANSWER, not a failure: the backend rate-limits
 * an anonymous visitor (per IP, and the anonymous audience as a whole) and
 * sends back a written, polite notice instead of calling the provider. Printing
 * the widget's generic "Sorry — I couldn't answer just now" over it would throw
 * away the one sentence that tells the visitor what to do next — so any status
 * that carries a `reply` string is shown as the assistant's message, and only a
 * response WITHOUT one is an error.
 *
 * Kept pure (no fetch) so `__tests__/supportReply.test.mjs` can state the rule.
 */
export function chatReplyFrom(status: number, body: unknown): ChatReply | null {
  const b = body as { reply?: unknown; source?: unknown } | null;
  const reply = typeof b?.reply === 'string' ? b.reply.trim() : '';
  if (!reply) return null;
  if (status >= 200 && status < 300) return { reply, source: String(b?.source ?? '') };
  if (status === 429) return { reply, source: String(b?.source ?? 'rate_limited') };
  return null;
}

/** Ask the in-app assistant. The backend proxies to the provider (key stays server-side). */
export async function askAssistant(messages: ChatMsg[]): Promise<ChatReply> {
  const r = await fetch(`${API}/api/support/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  });
  let body: unknown = null;
  try { body = await r.json(); } catch { body = null; }
  const out = chatReplyFrom(r.status, body);
  if (!out) throw new Error(`HTTP ${r.status}`);
  return out;
}
