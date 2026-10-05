// Support: the in-app assistant (the backend proxies to the AI provider) and the
// user's tickets, stored by the backend (POST/GET /api/support/tickets; the
// caller's identity comes from the Authorization header the app attaches, never
// from the body).  Admins read every user's tickets via /api/admin/tickets.
//
// Everything a user reports goes through the assistant (owner 2026-10-05): it
// answers, asks for what is missing and proposes a TICKET DRAFT that the user
// confirms.  The pure rules (what a reply means, the draft's life) live in
// ./supportFlow so they can be tested alone.
import { errorMessageOf } from './supportContext';
import {
  chatReplyFrom, type ChatMsg, type ChatReply, type TicketPayload, type TicketType,
} from './supportFlow';

export { chatReplyFrom };
export type { ChatMsg, ChatReply, TicketPayload, TicketType };
export type { TicketDraft } from './supportFlow';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export interface Ticket {
  id: string;
  type: TicketType;
  title: string;
  description: string;
  status: string;     // open | in_progress | resolved | closed
  createdAt?: number | null;   // ms epoch
}

/** File a ticket as the signed-in user: the (confirmed, possibly edited) draft,
 *  the conversation it came from and the session snapshot. */
export async function submitTicket(t: TicketPayload): Promise<Ticket> {
  const r = await fetch(`${API}/api/support/tickets`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: t.type, title: t.title.trim(), description: t.description.trim(),
      conversation: t.conversation, context: t.context,
    }),
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { detail = errorMessageOf(await r.json(), detail); } catch { /* keep status */ }
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

/** Ask the in-app assistant. The backend proxies to the provider (key stays server-side);
 *  `context` is the sanitised session snapshot the model reads as hidden text. */
export async function askAssistant(messages: ChatMsg[], context?: Record<string, unknown>): Promise<ChatReply> {
  const r = await fetch(`${API}/api/support/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, ...(context ? { context } : {}) }),
  });
  let body: unknown = null;
  try { body = await r.json(); } catch { body = null; }
  const out = chatReplyFrom(r.status, body);
  if (!out) throw new Error(`HTTP ${r.status}`);
  return out;
}
