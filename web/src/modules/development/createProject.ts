// Pure helpers of the "New project" dialog (owner 2026-10-30: any signed-in account may open its own project).

/** What the create dialog posts: staff name the customer organisation and the id; a customer gives a title and (optionally) its
 *  company - the server generates the id, makes the account the owner and ignores everything else. */
export function createPayload(staff: boolean, v: { title: string; org: string; id: string }):
  { id?: string; title: string; customer_org: string } {
  const title = v.title.trim(); const customer_org = v.org.trim();
  return staff ? { id: v.id.trim(), title, customer_org } : { title, customer_org };
}

/** May the dialog's Create button be pressed? */
export function createReady(staff: boolean, v: { title: string; org: string; id: string }): boolean {
  return !!v.title.trim() && (!staff || (!!v.org.trim() && !!v.id.trim()));
}

/** The text of a refused create: the account's open-project limit (HTTP 429, detail.code "project_limit") is shown from the
 *  localised string; every other refusal is the server's own message. */
export function createErrorText(e: unknown, t: (key: string, args?: Record<string, unknown>) => string): string {
  const status = (e as { status?: unknown } | null)?.status;
  const detail = ((e as { body?: unknown } | null)?.body as { detail?: { code?: string; limit?: number } } | null | undefined)?.detail;
  if (status === 429 && detail && typeof detail === 'object' && detail.code === 'project_limit') {
    return t('projectLimit', { count: typeof detail.limit === 'number' ? detail.limit : 5 });
  }
  return e instanceof Error ? e.message : String(e);
}
