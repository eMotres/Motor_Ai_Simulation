/**
 * A RELEASED die context is never a dead end (2026-09-20).
 *
 * 12:47:08: the owner had optimised the live machine, a single-key geometry
 * edit (poles/segment 7 → 8) made it a different lamination from the active
 * die, the backend's identity guard released the context — and the header
 * strip offered only "press ▶ in Motors to load one", which would have
 * overwritten the optimised geometry.  Second time ("опять та же самая
 * проблема — я всё оптимизировал, а сохранить не могу").
 *
 * This module holds the PURE half of the fix — what `/api/family/context`
 * says about a released context, turned into the OFFERS the strip draws:
 *
 *   • "Save as NEW die"                — always, when the released die exists
 *     (a copy of it with the new lamination; backend POST /save_as_new_die);
 *   • "Save as new configuration of X" — only when the live machine IS that
 *     lamination again (`live_is_die`), so a configuration of the same die is
 *     the honest place for it;
 *   • "Discard live changes and reload ▶ X / cfg / duty" — when the record
 *     names (or the backend could fill in) the configuration and duty.
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

/** The name the "Save as new die" prompt opens with: the released die plus
 *  the live topology ("CIANO14 50 edited 12s16p"), so the catalog tells the
 *  two laminations apart at a glance.  Never returns the released name
 *  itself — the backend refuses a taken name (409). */
export function suggestedDieName(ctx: ReleasedCtxLike): string {
  const rel = String(ctx.released_from ?? '').trim() || 'new die';
  const t = ctx.live_topology ?? null;
  const topo = t && t.slots && t.poles ? `${t.slots}s${t.poles}p` : '';
  const base = rel.replace(/\s+\d+s\d+p$/, '');
  return topo ? `${base} ${topo}` : `${rel} new`;
}

export interface ReleasedOffers {
  /** the one-line strip text */
  line: string;
  /** the tooltip behind it */
  tip: string;
  /** "Save as NEW die (copy of X with the new lamination: …)" */
  saveNewDie: { label: string; initialName: string; hint: string } | null;
  /** "Save as new configuration of X" — only when the live machine IS the die */
  saveNewConfig: { label: string; die: string; duty: string } | null;
  /** "Discard live changes and reload ▶ X / cfg / duty" */
  reload: { label: string; die: string; config: string; duty: string } | null;
  /** "↩ Re-attach to X / cfg / duty (keep the machine on screen)" — only when
   *  the live machine IS the die's lamination again; activates the context
   *  without loading anything (backend POST /reattach). */
  reattach: { label: string; die: string; config: string; duty: string | null } | null;
}

/**
 * PURE.  The strip's offers for a released context, or null when the strip
 * has nothing to draw (no released die, or a reader who cannot write).
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

  const line = changed
    ? `⚠ no die active — the machine on screen is not '${rel}' any more (${changed}); your work is NOT lost — save it as a new die`
    : liveIsDie
      ? `⚠ no die active — '${rel}' was released (${ctx.reason ?? 'a whole-machine load'}); the machine on screen is that lamination again`
      : `⚠ no die active — the loaded machine is not a catalog duty (released from '${rel}')`;
  const tip = `Context released from '${rel}'${cfg ? ` / ${cfg}` : ''}${duty ? ` / ${duty}` : ''}`
    + `${ctx.at ? ` at ${String(ctx.at).replace('T', ' ')}` : ''}: `
    + `${ctx.reason ?? 'a whole-machine load outside the catalog'}. `
    + (changed
        ? `A die keeps its diameter and slot/pole topology for life, so a machine with ${changed} is a NEW lamination. `
          + 'Nothing is synced into the catalog until you save it as a new die (the live geometry, its build and the operating point go into it), '
          + 'or discard the live changes and reload the released duty.'
        : 'The machine on screen belongs to no die, so nothing is synced into the catalog. Re-attach it to the released die as it is, '
          + 'save it as a new configuration or a new die, or reload the released duty with ▶.');

  return {
    line, tip,
    saveNewDie: dieExists ? {
      label: changed
        ? `＋ Save as NEW die (copy of ${rel} with the new lamination: ${changed})`
        : `＋ Save as NEW die (copy of ${rel})`,
      initialName: suggestedDieName(ctx),
      hint: 'A new unlocked die from the geometry on screen, one configuration from its build (stack, wire, winding, materials) '
        + 'and one duty at the Simulation tab\'s point; the last run\'s results are recorded into it. The released die is untouched.',
    } : null,
    saveNewConfig: (liveIsDie && dieExists) ? {
      label: `＋ Save as new configuration of ${rel}`,
      die: rel, duty: duty || 'rated',
    } : null,
    reload: (dieExists && cfg && duty) ? {
      label: `↺ Discard live changes and reload ▶ ${rel} / ${cfg} / ${duty}`,
      die: rel, config: cfg, duty,
    } : null,
    reattach: (liveIsDie && dieExists && cfg) ? {
      label: `↩ Re-attach to ${rel} / ${cfg}${duty ? ` / ${duty}` : ''} (keep the machine on screen)`,
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
