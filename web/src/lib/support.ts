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
  chatReplyFrom, forServer, fromHistory, type ChatMsg, type ChatReply, type SpecCard, type TicketPayload, type TicketType,
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

/** A random id of this browser tab's help conversation (never derived from the IP or the account): the server groups the turns of one
 *  conversation by it for service analysis. */
export function conversationId(): string {
  try {
    let id = window.sessionStorage.getItem('support.sid');
    if (!id) { id = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`; window.sessionStorage.setItem('support.sid', id); }
    return id;
  } catch { return ''; }
}

/** Where the chat runs (owner 2026-11-11: ONE assistant): a project's Specification turns on the spec mode, `fill` its fill mode. */
export interface AskPlace { projectId?: string; fill?: boolean }

/** Ask the in-app assistant. The backend proxies to the provider (key stays server-side);
 *  `context` is the sanitised session snapshot the model reads as hidden text. */
export async function askAssistant(messages: ChatMsg[], context?: Record<string, unknown>, lang?: string, place?: AskPlace): Promise<ChatReply> {
  const r = await fetch(`${API}/api/support/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      messages: forServer(messages), ...(context ? { context } : {}), ...(lang ? { lang } : {}), ...(conversationId() ? { sid: conversationId() } : {}),
      ...(place?.projectId ? { place: { project_id: place.projectId, view: 'specification' }, fill: place.fill !== false } : {}),
    }),
  });
  let body: unknown = null;
  try { body = await r.json(); } catch { body = null; }
  const out = chatReplyFrom(r.status, body);
  if (!out) throw new Error(`HTTP ${r.status}`);
  return out;
}

/** The kept conversation of the signed-in user: of a project's Specification, or the help chat elsewhere. */
export async function loadConversation(projectId?: string): Promise<ChatMsg[]> {
  const url = projectId ? `${API}/api/projects/${encodeURIComponent(projectId)}/spec/assistant/history` : `${API}/api/support/history`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return fromHistory((await r.json()).messages);
}

/** Clear the user's own view of a conversation (the service archive keeps it). */
export async function clearConversation(projectId?: string): Promise<void> {
  const url = projectId ? `${API}/api/projects/${encodeURIComponent(projectId)}/spec/assistant/history` : `${API}/api/support/history`;
  const r = await fetch(url, { method: 'DELETE' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

/** Create the project from a kept hand-off (after the Platform Agreement, or after a visitor signed up).  The app's fetch layer shows
 *  the agreement dialog on a 428 and repeats this call once. */
export async function createFromHandoff(token: string): Promise<{ project: { id: string; title: string }; applied: SpecCard[] }> {
  const r = await fetch(`${API}/api/support/handoff/${encodeURIComponent(token)}/create`, { method: 'POST' });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { detail = errorMessageOf(await r.json(), detail); } catch { /* keep status */ }
    throw new Error(detail);
  }
  return r.json();
}
