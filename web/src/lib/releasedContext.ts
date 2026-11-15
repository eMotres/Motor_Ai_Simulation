/**
 * A RELEASED die context is never a dead end (2026-09-20).
 *
 * 12:47:08: the owner had optimised the live machine, a single-key geometry
 * edit (poles/segment 7 → 8) made it a different lamination from the active
 * die, the backend's identity guard released the context — and the header
 * strip offered only "press ▶ in Motors to load one", which would have
 * overwritten the optimised geometry.  Second time ("the same problem
 * again — I optimised everything and can't save it").
 *
 * This module holds the PURE half of the fix — what `/api/family/context`
 * says about a released context, turned into the OFFERS the strip draws.
 * Since 2026-11-16 the strip is ONE short line plus only the buttons that
 * apply (see `releasedOffers`): an identical screen offers just "Reload".
 *
 * Superseded the same day, for the identity-guard case specifically: a
 * die-defining edit on the ACTIVE die no longer releases the context at all
 * — it AUTO-TRANSITIONS the active configuration/duty to the die the new
 * lamination is (new or reused), so `/api/family/context` comes back ACTIVE
 * again and none of the offers below ever render for it.  `released_from`
 * (and everything this module computes from it) now fires only for a
 * whole-machine load OUTSIDE the catalog — Compare apply, My motors, a
 * preset — which is exactly what bf246ff/f46913c also covered; this module
 * is kept, unmodified in shape, as that fallback.
 *
 * Kept free of React and fetch so `node --test` pins the rules
 * (lib/__tests__/releasedContext.test.mjs — copied verbatim, the repo's
 * convention).  The same file also holds `dieDefiningSelected`, which still
 * flags die-defining keys in the sweep/optimizer pickers — now purely as
 * INFORMATION (amber), since the backend gate they used to require
 * `allow_new_lamination` for no longer refuses anything.
 */

import { familyBase } from './dieNameRule';

/** One identity key on which the live machine differs from the released die,
 *  as the backend reports it (`die_identity_diffs`). */
export interface DieDiff { key: string; label?: string; die: number; live: number }

/** `/api/family/context` for a released context — only the fields read here. */
export interface ReleasedCtxLike {
  active?: boolean;
  can_write?: boolean;
  released_from?: string | null;
  released_config?: string | null;
  released_duty?: string | null;
  reason?: string | null;
  at?: string | null;
  die_exists?: boolean;
  die_diffs?: DieDiff[] | null;
  live_is_die?: boolean | null;
  /** every stored geometry value of the released configuration equals the screen's (null = unknown) */
  live_matches_config?: boolean | null;
  /** who released the context (account), when recorded */
  by?: string | null;
  live_topology?: { stator_diameter?: number | null; slots?: number | null;
                    poles?: number | null; motor_length?: number | null } | null;
}

/** How an identity key reads on screen (mirrors the backend's labels). */
export const DIE_KEY_LABELS: Record<string, string> = {
  stator_diameter: 'stator Ø',
  num_seg: 'segments',
  num_slots_per_segment: 'slots/segment',
  num_poles_per_segment: 'poles/segment',
};

export function dieKeyLabel(key: string): string {
  return DIE_KEY_LABELS[key] ?? key;
}

/** "poles/segment 7 → 8; stator Ø 40 → 50" — the guard's own verdict, in
 *  words.  Empty when nothing differs. */
export function diffText(diffs: DieDiff[] | null | undefined): string {
  return (diffs ?? [])
    .map(d => `${d.label ?? dieKeyLabel(d.key)} ${d.die} → ${d.live}`)
    .join('; ');
}

/** The OFFLINE fallback of the "Save as new die" prompt's name: the released die's
 *  family base ("CIANO14 50 edited" -> "CIANO14 50").  The prompt really opens with
 *  the server's "<family base> <your suffix> <n>" (lib/dieNames.suggestDieName;
 *  owner 2026-10-26: no diameter / topology / free words in new names).  The
 *  backend refuses a taken name (409). */
export function suggestedDieName(ctx: ReleasedCtxLike): string {
  return familyBase(String(ctx.released_from ?? '')) || 'new die';
}

/** What the released strip says, in one word (locale keys `releasedStrip.*.<kind>`):
 *  same    — the screen is the stored configuration again, nothing to save;
 *  edited  — same lamination, but values differ from the stored configuration;
 *  differs — another lamination (a die-identity key differs);
 *  foreign — the loaded machine is not a catalog duty. */
export type ReleasedKind = 'same' | 'edited' | 'differs' | 'foreign';

export interface ReleasedOffers {
  kind: ReleasedKind;
  /** values for the locale strings */
  vars: { die: string; config: string; changed: string; by: string; when: string };
  /** the one-line strip text (English source; the strip renders the locale string of `kind`) */
  line: string;
  /** the tooltip behind it */
  tip: string;
  /** "Save as NEW die" — the primary button only when it is the one way to keep the work */
  saveNewDie: { label: string; initialName: string; hint: string; primary: boolean } | null;
  /** "Save as new configuration of X" — only when the live machine IS the die and differs from the configuration */
  saveNewConfig: { label: string; die: string; duty: string } | null;
  /** "Reload X / cfg" */
  reload: { label: string; die: string; config: string; duty: string } | null;
  /** "Re-attach" — only when the screen is the die but no reload is possible (no duty stored);
   *  activates the context without loading anything (backend POST /reattach). */
  reattach: { label: string; die: string; config: string; duty: string | null } | null;
}

/**
 * PURE.  The strip's offers for a released context, or null when the strip
 * has nothing to draw (no released die, or a reader who cannot write).
 *
 * Owner 2026-11-16: one short line, and no choices that do not apply.  A
 * screen identical to the stored configuration offers only "Reload" (nothing
 * creates a copy); the save buttons appear only for real differences.
 */
export function releasedOffers(ctx: ReleasedCtxLike | null | undefined): ReleasedOffers | null {
  if (!ctx || ctx.active === true) return null;
  const rel = String(ctx.released_from ?? '').trim();
  if (!rel || ctx.can_write !== true) return null;
  const diffs = ctx.die_diffs ?? [];
  const changed = diffText(diffs);
  const liveIsDie = ctx.live_is_die === true;
  const dieExists = ctx.die_exists !== false;
  const cfg = String(ctx.released_config ?? '').trim();
  const duty = String(ctx.released_duty ?? '').trim();
  const by = String(ctx.by ?? '').trim();
  const when = ctx.at ? String(ctx.at).replace('T', ' ').slice(0, 16) : '';

  const kind: ReleasedKind = changed ? 'differs'
    : liveIsDie ? (ctx.live_matches_config === true ? 'same' : 'edited')
    : 'foreign';
  const tail = `${by ? ` by ${by}` : ''}${when ? ` at ${when}` : ''}`;
  const vars = { die: rel, config: cfg, changed, by: by ? ` by ${by}` : '', when: when ? ` at ${when}` : '' };

  const line = kind === 'same'
    ? `No die attached: ${rel} was detached${tail}. Nothing to save.`
    : kind === 'edited'
      ? `No die attached: the screen differs from ${rel}${cfg ? ` / ${cfg}` : ''}.`
      : kind === 'differs'
        ? `No die attached: the screen is a new lamination (${changed}).`
        : 'No die attached: the loaded machine is not a catalog duty.';
  const tip = `Context released from '${rel}'${cfg ? ` / ${cfg}` : ''}${duty ? ` / ${duty}` : ''}${tail}: `
    + `${ctx.reason ?? 'a whole-machine load outside the catalog'}. `
    + (kind === 'same'
        ? 'The machine on screen is that configuration unchanged, so reloading it loses nothing.'
        : 'Nothing is synced into the catalog until the machine on screen is saved or the stored duty is reloaded.');

  const canReload = dieExists && !!cfg && !!duty;
  const reload = canReload ? {
    label: kind === 'same' ? `Reload ${cfg}` : `Discard changes and reload ${cfg}`,
    die: rel, config: cfg, duty,
  } : null;

  return {
    kind, vars, line, tip,
    saveNewDie: dieExists && (kind === 'differs' || kind === 'foreign') ? {
      label: 'Save as new die',
      initialName: suggestedDieName(ctx),
      primary: true,
      hint: 'A new unlocked die from the geometry on screen, one configuration from its build (stack, wire, winding, materials) '
        + 'and one duty at the Simulation tab\'s point; the last run\'s results are recorded into it. The released die is untouched.',
    } : null,
    saveNewConfig: (kind === 'edited' && dieExists) ? {
      label: `Save as new configuration of ${rel}`,
      die: rel, duty: duty || 'rated',
    } : null,
    reload,
    reattach: ((kind === 'edited' || (kind === 'same' && !reload)) && dieExists && cfg) ? {
      label: `Re-attach to ${rel} / ${cfg}${duty ? ` / ${duty}` : ''}`,
      die: rel, config: cfg, duty: duty || null,
    } : null,
  };
}

// ── "allow new lamination" — vestigial (2026-09-20) ──────────────────────────
// Used to be consent a sweep/optimizer request needed before the backend
// would even RUN a campaign that varies a die-defining key; the backend gate
// (`refuse_die_defining_variables`) is now a no-op, so this value is read and
// sent by the request builders below but no longer changes anything either
// side of the wire.  Left in place rather than ripped out: the field is still
// accepted (`OptRequest.allow_new_lamination` et al.), and removing the
// plumbing buys nothing over just not gating on it.

export const ALLOW_NEW_LAMINATION_KEY = 'opt.allowNewLamination';

export function readAllowNewLamination(): boolean {
  try { return localStorage.getItem(ALLOW_NEW_LAMINATION_KEY) === '1'; }
  catch { return false; }
}

export function writeAllowNewLamination(v: boolean): void {
  try { localStorage.setItem(ALLOW_NEW_LAMINATION_KEY, v ? '1' : '0'); }
  catch { /* quota — the checkbox state is a convenience */ }
}

/** PURE.  Which of the selected variables are die-defining, given the key
 *  list the backend serves (`/api/family/context` → `die_keys`). */
export function dieDefiningSelected(names: Iterable<string>,
                                    dieKeys: Iterable<string> | null | undefined): string[] {
  const keys = new Set(dieKeys ?? []);
  const out: string[] = [];
  for (const n of names) if (keys.has(n)) out.push(n);
  return out;
}
