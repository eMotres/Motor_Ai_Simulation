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

/** Ask the in-app assistant. The backend proxies to Claude (key stays server-side). */
export async function askAssistant(messages: ChatMsg[]): Promise<{ reply: string; source: string }> {
  const r = await fetch(`${API}/api/support/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
