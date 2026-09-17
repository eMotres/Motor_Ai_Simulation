// Support: AI chat (via the backend Claude proxy) + user-filed tickets in
// Firestore (users/{uid}/tickets/{id}), mirroring the saved-designs pattern.
// Admins read every user's tickets via the backend /api/admin/tickets.
import {
  collection, doc, getDocs, setDoc, query, orderBy, serverTimestamp,
} from 'firebase/firestore';
import { db } from './firebase';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export type TicketType = 'bug' | 'feature' | 'question';

export interface Ticket {
  id: string;
  type: TicketType;
  title: string;
  description: string;
  status: string;     // open | in_progress | resolved | closed
  createdAt?: unknown;
}

const col = (uid: string) => collection(db!, 'users', uid, 'tickets');

/** File a support ticket (bug / feature / question) for the signed-in user. */
export async function submitTicket(
  uid: string, email: string | null, t: { type: TicketType; title: string; description: string },
): Promise<void> {
  const id = `t_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  await setDoc(doc(col(uid), id), {
    type: t.type, title: t.title.trim(), description: t.description.trim(),
    email: email ?? null, status: 'open', createdAt: serverTimestamp(),
  });
}

/** The signed-in user's own tickets, newest first. */
export async function listMyTickets(uid: string): Promise<Ticket[]> {
  const snap = await getDocs(query(col(uid), orderBy('createdAt', 'desc')));
  return snap.docs.map((d) => ({ id: d.id, ...(d.data() as Omit<Ticket, 'id'>) }));
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
