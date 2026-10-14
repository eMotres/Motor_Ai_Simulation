// What a STANDARD (role "user") account sees and may do in the web.
//
// Owner 2026-10-05: a standard user has only two menus - Motors and Configure; 2026-10-13 adds "3D model".
// Both read the catalogue (tree, payload, references, configure_context); neither
// needs a write to the shared or the personal config, so the server gate is NOT
// widened.  Hiding tabs is UI only: every gated route stays gated server-side
// (RoleGateMiddleware), and a refused call says why (refusalReasonKey below).
//
// Pure, no runtime imports: web/src/lib/__tests__/accessUi.test.mjs imports it.

/** The tab ids a standard user gets ('compare' is the Configure tab's id; 'model3d': everybody looks at the model, tools and STEP download come with the NDA). */
export const STANDARD_USER_TABS: readonly string[] = ['motors', 'compare', 'model3d'];

/** A signed-in account on a backend that enforces auth whose role is "user" (Configure only).  An "engineer" (the whole
 *  simulation UI, owner 2026-10-14) is NOT a standard user. */
export const isStandardUser = (enforced: boolean, isAdmin: boolean, role: string): boolean =>
  enforced && !isAdmin && role !== 'anon' && role !== 'engineer';

/** Does this session get the simulation UI (Geometry, Materials, Mesh, Electromagnetic, Thermal, Mechanical, Controller, Compare)?
 *  Admins, engineers and an unenforced (local) backend. */
export const hasSimulationUI = (enforced: boolean, isAdmin: boolean, role: string): boolean =>
  !enforced || isAdmin || role === 'engineer';

/** The account roles (the server's users.ROLES) and what the Admin role select calls them (owner 2026-10-14). */
export const ROLE_VALUES = ['user', 'engineer', 'admin'] as const;
export type AccountRole = typeof ROLE_VALUES[number];
export const ROLE_LABELS: Readonly<Record<AccountRole, string>> = { user: 'Configure', engineer: 'Simulation', admin: 'Admin' };
export const roleLabel = (role: string): string => ROLE_LABELS[role as AccountRole] ?? role;

/** Who may see a tab: 'always' everyone, 'signedIn' any signed-in session, 'fullUI' the simulation UI (admin, engineer, local
 *  dev), 'admin' admins only (and the local dev backend, which is admin). */
export type TabGate = 'always' | 'signedIn' | 'fullUI' | 'admin';
export const TAB_GATES: Readonly<Record<string, TabGate>> = {
  motors: 'always', geometry: 'fullUI', materials: 'fullUI', mesh: 'fullUI', simulation: 'fullUI',
  static3d: 'admin', mechanical: 'fullUI', thermal: 'fullUI', controller: 'fullUI', sweep: 'admin',
  comparePoints: 'fullUI', cost: 'admin', compare: 'signedIn', model3d: 'signedIn', admin: 'admin',
};
export interface GateCtx { signedIn: boolean; fullUI: boolean; isAdmin: boolean }
export const gateOpen = (gate: TabGate, c: GateCtx): boolean =>
  gate === 'always' || (gate === 'signedIn' && c.signedIn) || (gate === 'fullUI' && c.fullUI) || (gate === 'admin' && c.isAdmin);
export const tabVisible = (id: string, c: GateCtx): boolean => gateOpen(TAB_GATES[id] ?? 'admin', c);

/** One line for a non-admin account whose catalogue is empty (no motor granted yet). */
export const emptyCatalogHintApplies = (enforced: boolean, isAdmin: boolean): boolean => enforced && !isAdmin;

/** Which one line an EMPTY Motors catalog shows.  The developer hint ("freeze the current geometry") is for a session that may
 *  write the shared catalog (admin / local dev) and only once the tree has answered; a role-user account never reads it - it
 *  gets the backend's own note or a plain line.  Until the first answer (or after a failed one) nothing claims "no motors". */
export type EmptyCatalogLine = 'loading' | 'unreachable' | 'note' | 'noMotors' | 'vendorHint';
export function emptyCatalogLine(s: { loaded: boolean; failed: boolean; note: string | null; canWrite: boolean }): EmptyCatalogLine {
  if (s.failed) return 'unreachable';
  if (!s.loaded) return 'loading';
  if (s.note) return 'note';
  return s.canWrite ? 'vendorHint' : 'noMotors';
}

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

/** "My compute nodes" (docs/BYO_COMPUTE.md) lives in the account menu: every SIGNED-IN account reaches it - user, engineer and
 *  admin alike (the server routes /api/nodes are not role-gated, they are scoped to the caller). */
export const computeNodesReachable = (signedIn: boolean): boolean => signedIn;

/** The simulation tabs that start a (heavy) FEM run; they carry the "connect your own server" line. */
export const COMPUTE_HINT_TABS: readonly string[] = ['simulation', 'mesh', 'thermal', 'mechanical', 'controller', 'comparePoints'];

/** The one-line "FEM runs are heavy; connect your own server" hint: for the user and engineer roles on an enforcing backend,
 *  on a simulation tab.  Admins (they own the platform) and the unenforced local dev backend never see it. */
export const computeHintApplies = (tabId: string, enforced: boolean, isAdmin: boolean, role: string): boolean =>
  enforced && !isAdmin && (role === 'user' || role === 'engineer') && COMPUTE_HINT_TABS.includes(tabId);

/** The window event the hint fires and the account menu answers by opening "My compute nodes". */
export const OPEN_COMPUTE_NODES_EVENT = 'motres:open-compute-nodes';

/** The worker install docs (section 9 of the design doc). */
export const COMPUTE_WORKER_DOCS_URL = 'https://github.com/eMotres/Motor_Ai_Simulation/blob/pre-migration-freeze-2026-09-15/docs/BYO_COMPUTE.md#9-install';
