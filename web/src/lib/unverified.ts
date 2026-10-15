// An account whose e-mail is not confirmed yet is signed in at once ("confirm later") but refused a short list of actions
// (src/motor_ai_sim/verified_gate.py): the server answers 403 {code: "email_unverified", params: {email, gate}}.
// Pure, no imports: web/src/lib/__tests__/unverified.test.mjs runs it.

/** The window event lib/apiAuth.ts dispatches when the server refuses an action for this reason. */
export const UNVERIFIED_EVENT = 'email-unverified';

/** The server limits resending to 3 an hour per address; the button also waits this long between clicks. */
export const RESEND_COOLDOWN_S = 60;

/** The groups the server names in `params.gate` (verified_gate.py). */
export const GATES = ['nda', 'step', 'compute', 'mailing', 'tokens', 'role'] as const;
export type Gate = (typeof GATES)[number];

export interface UnverifiedRefusal { email: string; gate: string }

/** `{email, gate}` when this response is the confirm-your-e-mail refusal, else null. */
export function unverifiedRefusal(status: number, body: unknown): UnverifiedRefusal | null {
  if (status !== 403 && status !== 409) return null;
  const b = (body && typeof body === 'object') ? body as Record<string, unknown> : null;
  if (!b || b.code !== 'email_unverified') return null;
  const p = (b.params && typeof b.params === 'object') ? b.params as Record<string, unknown> : {};
  return { email: typeof p.email === 'string' ? p.email : '', gate: typeof p.gate === 'string' ? p.gate : '' };
}

/** Seconds left before the next resend is offered (0 = now). */
export function resendWait(lastSentMs: number | null, nowMs: number, cooldownS: number = RESEND_COOLDOWN_S): number {
  if (lastSentMs == null) return 0;
  return Math.max(0, Math.ceil((lastSentMs + cooldownS * 1000 - nowMs) / 1000));
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Can the "change address" form be sent: a different, well-formed address and a password. */
export function canChangeAddress(current: string, next: string, password: string): boolean {
  const n = next.trim().toLowerCase();
  return EMAIL_RE.test(n) && n !== current.trim().toLowerCase() && password.length > 0;
}
