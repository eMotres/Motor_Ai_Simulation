// What a STANDARD (role "user") account sees and may do in the web.
//
// Owner 2026-10-05: a standard user has only two menus - Motors and Configure.
// Both read the catalogue (tree, payload, references, configure_context); neither
// needs a write to the shared or the personal config, so the server gate is NOT
// widened.  Hiding tabs is UI only: every gated route stays gated server-side
// (RoleGateMiddleware), and a refused call says why (refusalReasonKey below).
//
// Pure, no runtime imports: web/src/lib/__tests__/accessUi.test.mjs imports it.

/** The tab ids a standard user gets ('compare' is the Configure tab's id). */
export const STANDARD_USER_TABS: readonly string[] = ['motors', 'compare'];

/** A signed-in, non-admin account on a backend that enforces auth. */
export const isStandardUser = (enforced: boolean, isAdmin: boolean, role: string): boolean =>
  enforced && !isAdmin && role !== 'anon';

/** May this tab be shown?  Admins and an unenforced (local) backend see everything. */
export const tabAllowed = (id: string, standardUser: boolean): boolean =>
  !standardUser || STANDARD_USER_TABS.includes(id);

/** The tab a standard user lands on: Configure when something is selected there
 *  (his own last choice, or the default motor an admin set), else Motors. */
export const landingTabForUser = (hasConfigureChoice: boolean): 'compare' | 'motors' =>
  hasConfigureChoice ? 'compare' : 'motors';

/** The server's `can_write` is "may write a copy into my own workspace" under the
 *  per-user workspaces - true for EVERY registered account.  The UI offers editing
 *  and the owner's load-into-the-shared-config flow only to a session that may
 *  write the SHARED config (admin / unenforced local dev). */
export const uiCanWrite = (serverCanWrite: boolean | undefined, canWriteShared: boolean): boolean =>
  serverCanWrite === true && canWriteShared;

/** A shared-context follower must wait until the role is known, then run only
 *  for an admin (or an explicitly unenforced local backend). */
export const mayFollowSharedContext = (resolved: boolean, enforced: boolean, isAdmin: boolean): boolean =>
  resolved && (!enforced || isAdmin);

/** Which i18n key (common:refused.*) explains a refused call, or null when the
 *  response is not a gate refusal.  `body` is the parsed JSON (the gate answers
 *  `{detail, required_role, your_role}`). */
export function refusalReasonKey(status: number, body: unknown): string | null {
  const b = (body && typeof body === 'object') ? body as Record<string, unknown> : null;
  if (!b || typeof b.required_role !== 'string') return null;
  if (status === 401) return 'refused.signIn';
  if (status === 403) return b.required_role === 'admin' ? 'refused.admin' : 'refused.forbidden';
  return null;
}

export interface DefaultMotor { die: string; config: string }

/** The catalogue reference (Configure's `cat:<id>`) that IS the default motor's
 *  machine: the one whose full-card record names it, else the one named
 *  "<die> <config>", else a card named by the die alone.  null = not in the list
 *  (not granted, or no characterised card yet). */
export function referenceOfDefault<R extends { id: string; name: string;
    card?: { die?: string; config?: string } | null }>(
  refs: readonly R[], dm: DefaultMotor | null | undefined): R | null {
  if (!dm?.die || !dm?.config) return null;
  const die = dm.die.trim().toLowerCase();
  const cfg = dm.config.trim().toLowerCase();
  const byCard = refs.find((r) => r.card?.die?.toLowerCase() === die && r.card?.config?.toLowerCase() === cfg);
  if (byCard) return byCard;
  const full = `${die} ${cfg}`;
  const exact = refs.find((r) => r.name.trim().toLowerCase() === full);
  if (exact) return exact;
  const byDie = refs.filter((r) => r.name.trim().toLowerCase() === die);
  return byDie.length === 1 ? byDie[0] : null;
}

/** "2/3 configs" summary of a die: `cards` full cards over `total` configurations. */
export const cardSummary = (cards: number, total: number): { text: string; state: 'none' | 'partial' | 'all' } =>
  ({ text: `${cards}/${total}`, state: cards <= 0 ? 'none' : cards >= total ? 'all' : 'partial' });
