// The pure rules of the help assistant's conversation: what a chat answer means,
// and the life of a ticket DRAFT the assistant proposes (edit, confirm, send).
//
// No imports (a node test loads this file itself, see lib/__tests__/supportFlow.test.mjs).
// The widget (components/support/SupportWidget.tsx) renders; this decides.
//
// The flow (owner 2026-10-05: every question and bug goes through the assistant):
//   user writes -> assistant answers; when it has a bug / feature / account issue
//   / unanswerable question it ends its reply with a TICKET DRAFT, which arrives
//   here as `ticketDraft` -> the widget shows it as a card -> the user may edit
//   it -> Send posts it WITH the conversation and the session context.  Nothing
//   is ever filed without that press.

export type TicketType = 'bug' | 'feature' | 'question' | 'account';
export const TICKET_TYPES: readonly TicketType[] = ['bug', 'feature', 'question', 'account'];
export const MAX_TITLE = 120;
export const MAX_DESCRIPTION = 4000;

export interface ChatMsg { role: 'user' | 'assistant'; content: string; }
export interface TicketDraft { type: TicketType; title: string; description: string; }
export interface ChatReply { reply: string; source: string; ticketDraft: TicketDraft | null; }

/** A draft from the server, or null when it is not a usable one (a title is required). */
export function normalizeDraft(x: unknown): TicketDraft | null {
  const d = x as { type?: unknown; title?: unknown; description?: unknown } | null;
  if (!d || typeof d !== 'object') return null;
  const title = typeof d.title === 'string' ? d.title.trim().slice(0, MAX_TITLE) : '';
  if (!title) return null;
  const type = TICKET_TYPES.includes(d.type as TicketType) ? (d.type as TicketType) : 'question';
  const description = typeof d.description === 'string' ? d.description.trim().slice(0, MAX_DESCRIPTION) : '';
  return { type, title, description };
}

/**
 * What one /api/support/chat response MEANS.
 *
 * A 429 from this endpoint is an ANSWER, not a failure: the backend rate-limits
 * an anonymous visitor (per IP, and the anonymous audience as a whole) and
 * sends back a written, polite notice instead of calling the provider. Printing
 * the widget's generic "I couldn't answer just now" over it would throw
 * away the one sentence that tells the visitor what to do next — so any status
 * that carries a `reply` string is shown as the assistant's message, and only a
 * response WITHOUT one is an error.  The exception is a reply that is only a
 * ticket draft (empty text): the card IS the answer.
 */
export function chatReplyFrom(status: number, body: unknown): ChatReply | null {
  const b = body as { reply?: unknown; source?: unknown; ticketDraft?: unknown } | null;
  const reply = typeof b?.reply === 'string' ? b.reply.trim() : '';
  const ticketDraft = normalizeDraft(b?.ticketDraft);
  if (!reply && !ticketDraft) return null;
  if (status >= 200 && status < 300) return { reply, source: String(b?.source ?? ''), ticketDraft };
  if (status === 429) return { reply, source: String(b?.source ?? 'rate_limited'), ticketDraft: null };
  return null;
}

// ── the draft card ───────────────────────────────────────────────────────────
export type DraftStatus = 'editing' | 'sending' | 'sent' | 'error';

export interface DraftState {
  draft: TicketDraft;
  status: DraftStatus;
  /** the filed ticket's id once sent */
  ticketId?: string;
  /** the server's refusal text, when status is 'error' */
  error?: string;
  /** the assistant's reply was only the draft (no text of its own): the card says "I prepared a ticket" itself */
  intro?: boolean;
}

export interface FlowState {
  /** the conversation as it goes to the model and into a ticket (no greeting) */
  msgs: ChatMsg[];
  draft: DraftState | null;
}

export const initialFlow = (): FlowState => ({ msgs: [], draft: null });

export const addUserMessage = (s: FlowState, text: string): FlowState =>
  ({ ...s, msgs: [...s.msgs, { role: 'user', content: text }] });

/** An answer arrives: its text joins the conversation (when there is any) and a
 *  draft opens the card.  There is one card at a time: a newer draft replaces
 *  the one before it, whatever state that one was in. */
export function receiveReply(s: FlowState, r: ChatReply): FlowState {
  const msgs = r.reply ? [...s.msgs, { role: 'assistant' as const, content: r.reply }] : s.msgs;
  if (!r.ticketDraft) return { ...s, msgs };
  return { msgs, draft: { draft: r.ticketDraft, status: 'editing', intro: !r.reply } };
}

/** The user edits a field of the card. Only while it can still be sent. */
export function editDraft(s: FlowState, patch: Partial<TicketDraft>): FlowState {
  const d = s.draft;
  if (!d || (d.status !== 'editing' && d.status !== 'error')) return s;
  const next: TicketDraft = { ...d.draft };
  if (patch.type !== undefined && TICKET_TYPES.includes(patch.type)) next.type = patch.type;
  if (patch.title !== undefined) next.title = String(patch.title).slice(0, MAX_TITLE);
  if (patch.description !== undefined) next.description = String(patch.description).slice(0, MAX_DESCRIPTION);
  return { ...s, draft: { draft: next, status: 'editing', intro: d.intro } };
}

export const canSend = (s: FlowState): boolean =>
  !!s.draft && (s.draft.status === 'editing' || s.draft.status === 'error')
  && s.draft.draft.title.trim().length > 0;

export const dismissDraft = (s: FlowState): FlowState => (s.draft ? { ...s, draft: null } : s);

/** What POST /api/support/tickets receives: the (edited) draft, the conversation
 *  and the session context.  The identity is never in it - the server reads it
 *  from the caller's token. */
export interface TicketPayload {
  type: TicketType; title: string; description: string;
  conversation: ChatMsg[]; context: Record<string, unknown>;
}

export function buildTicketPayload(s: FlowState, context: Record<string, unknown>): TicketPayload | null {
  if (!canSend(s) || !s.draft) return null;
  const d = s.draft.draft;
  return {
    type: d.type, title: d.title.trim(), description: d.description.trim(),
    conversation: s.msgs.slice(-30), context,
  };
}

/**
 * Press Send: mark the card as sending, post, then mark it sent or failed.
 * `onState` receives every state in order, so a React `setState` can be passed
 * straight in.  Returns the final state.  A second press while sending, or on a
 * card with no title, does nothing.
 */
export async function runConfirm(
  s: FlowState,
  submit: (p: TicketPayload) => Promise<{ id?: string }>,
  context: Record<string, unknown>,
  onState: (s: FlowState) => void,
): Promise<FlowState> {
  const payload = buildTicketPayload(s, context);
  if (!payload || !s.draft) return s;
  const sending: FlowState = { ...s, draft: { ...s.draft, status: 'sending', error: undefined } };
  onState(sending);
  let done: FlowState;
  try {
    const t = await submit(payload);
    done = { ...sending, draft: { ...sending.draft!, status: 'sent', ticketId: t?.id } };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    done = { ...sending, draft: { ...sending.draft!, status: 'error', error: msg } };
  }
  onState(done);
  return done;
}
