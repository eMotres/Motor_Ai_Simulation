// Admin · Users: how the last authentication mail of an account is described (pure; no imports, so node --test can run it).
// `ok` means the SMTP server ACCEPTED the message; whether it was delivered or bounced is not knowable from here.
export interface LastMail { kind: string; ts: string; ok: boolean; error?: string }

const KIND: Record<string, string> = { verify: 'confirmation link', reset: 'password-reset link' };

export function lastMailText(m: LastMail | null | undefined): string {
  if (!m) return 'No mail is recorded for this account.';
  const what = KIND[m.kind] ?? m.kind;
  const when = m.ts.replace('T', ' ').slice(0, 16);
  return m.ok
    ? `Last mail: ${what} sent ${when} (accepted by the mail server; a later bounce is not tracked).`
    : `Last mail: ${what} FAILED ${when}${m.error ? ` (${m.error})` : ''}.`;
}

/** An address that can be edited by the admin: only an account whose e-mail is not confirmed. */
export const canEditEmail = (u: { email_verified?: boolean } | null | undefined): boolean => !!u && u.email_verified === false;
