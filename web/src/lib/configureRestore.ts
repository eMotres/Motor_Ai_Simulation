// Configure RELOAD = one CONSISTENT state (owner 2026-10-15: after a plain reload the tab showed
// the L20 stack with L12's 6S pack, 13000 rpm above that pack's 10183 rpm full-pack maximum and
// no preset selected).  The rules, pure so node --test can pin them:
//
//   * the BASE (the configuration + mode the state is measured against) is derived from the
//     build the knobs actually hold - never from a guess made before the loaded machine arrived;
//   * the remembered knobs belong to ONE machine: another machine's (or an unstamped legacy)
//     record is not restored;
//   * a restored state that does not fit its pack (line voltage above the nominal, speed above
//     the full-pack maximum) is not shown silently - the caller falls back to the reference.
//
// Pure: imports only the two pure preset libraries.
// web/src/lib/__tests__/configureRestore.test.mjs imports this file.
import { presetOfBuild } from './configuratorPresets.ts';
import type { Preset } from './configuratorPresets.ts';
import { buildModePresets, modeOfPoint } from './configuratorModes.ts';
import type { ModePreset } from './configuratorModes.ts';

export interface BaseKnobs { N: number; L_mm: number; wireH_mm: number; nP: number; I_A: number; rpm: number; }
export interface ActiveDutyLike { die: string; config: string; duty: string; }

/** The configuration (and mode) whose build the knobs hold, or null when none is that build.
 *  Mode: the saved point the knobs sit on, else the duty the user loaded, else unknown (null). */
export function seedBase(presets: readonly Preset[], k: BaseKnobs,
                         active?: ActiveDutyLike | null): { config: string; mode: string | null } | null {
  const pr = presetOfBuild(presets as Preset[], k);
  if (!pr) return null;
  const mps = buildModePresets(presets);
  const exact = modeOfPoint(mps, pr.config, k.I_A, k.rpm);
  if (exact) return { config: pr.config, mode: exact };
  const loaded = active && active.die === pr.die && active.config === pr.config
    && mps.some((m) => m.config === pr.config && m.duty === active.duty) ? active.duty : null;
  return { config: pr.config, mode: loaded };
}

// ── the remembered knobs belong to one machine ─────────────────────────────────

/** The text to store: the knobs, stamped with the machine they belong to. */
export function stampKnobs(knobs: object, refId: string): string {
  return JSON.stringify({ ...knobs, _ref: refId });
}

/** The remembered knobs for THIS machine, else null (another machine's record, or one written
 *  before the stamp existed, is not trusted: it may belong to any machine). */
export function readStampedKnobs<K extends object>(raw: string | null, refId: string): K | null {
  if (!raw || !refId) return null;
  try {
    const k = JSON.parse(raw);
    if (!k || typeof k !== 'object' || k._ref !== refId || typeof k.N !== 'number') return null;
    const { _ref, ...rest } = k;
    void _ref;
    return rest as K;
  } catch { return null; }
}

// ── a restored state must fit its pack ─────────────────────────────────────────

export type Misfit = 'voltage' | 'speed';

/** What is wrong with a restored state: the winding's line-peak voltage above the pack NOMINAL,
 *  or the speed above the full-pack maximum.  Unknown limits (no pack) never misfit. */
export function restoreMisfit(a: {
  lineVpeak: number; packNomV: number | null; rpm: number; speedMaxRpm: number | null;
}): Misfit[] {
  const out: Misfit[] = [];
  if (a.packNomV && a.packNomV > 0 && a.lineVpeak > a.packNomV) out.push('voltage');
  if (a.speedMaxRpm != null && Number.isFinite(a.speedMaxRpm) && a.rpm > a.speedMaxRpm * 1.0005 + 1e-9) out.push('speed');
  return out;
}

// ── the loaded configuration IS the reference ──────────────────────────────────

export interface LoadedSelection { die: string; config: string; duty: string | null; }

/** The mode button of the configuration the account has loaded: its saved duty, else its first mode. */
export function selectionPreset(modePresets: readonly ModePreset[], sel: LoadedSelection): ModePreset | null {
  const mine = modePresets.filter((m) => m.config === sel.config && (!m.die || m.die === sel.die));
  return mine.find((m) => sel.duty != null && m.duty === sel.duty) ?? mine[0] ?? null;
}

const same = (a: number, b: number) => Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(b));
const sameBuild = (k: BaseKnobs, pr: ModePreset) => {
  const p = pr.knobs;
  const eq = (a: number, b: number | null) => b == null || same(a, b);
  return eq(k.L_mm, p.L_mm) && eq(k.N, p.N) && eq(k.wireH_mm, p.wireH_mm) && eq(k.nP, p.nP);
};

/** On reload / motor load: what makes the Configure state consistent with the loaded configuration.
 *  `apply` = the knobs (or the reference knobs) hold another build than the loaded configuration's
 *  - restore the whole preset (its pack, point, drive); `keep` = the build is the loaded one, only the
 *  base (configuration + mode) is named from the selection.  `null` = the selection names no preset. */
export function reconcilePlan(modePresets: readonly ModePreset[], sel: LoadedSelection,
                              knobs: BaseKnobs, refKnobs: BaseKnobs | null):
  { action: 'apply' | 'keep'; preset: ModePreset } | null {
  const preset = selectionPreset(modePresets, sel);
  if (!preset) return null;
  const consistent = !!refKnobs && sameBuild(knobs, preset) && sameBuild(refKnobs, preset);
  return { action: consistent ? 'keep' : 'apply', preset };
}
