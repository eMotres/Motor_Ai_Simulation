// What the Gears page tells the help assistant (owner 2026-11-26): the sub-tab, the preset, the kinds and count of stages, whether
// a calculation was run, its status chips, the selected motor and configuration, the last visible error.  Ids, names, modes and
// chips only: no geometry value, no result number, no price.  The ids below are the keys of src/motor_ai_sim/help_docs/registry.json
// (a test checks that every id has an EN and a zh-CN help document).
import { maskNumbers, type HelpContext } from '../../lib/supportContext.ts';
import type { StageKind } from './types';

export const GEARS_HELP_TAB = 'gears';
export const GEARS_HELP_SUBS = ['point', 'spectrum', 'assembly', 'motor'] as const;
export type GearsSub = typeof GEARS_HELP_SUBS[number];

interface RanLite { result?: { state?: unknown; passed?: unknown } | null }

export interface GearsPageHelp {
  sub: GearsSub; preset: string; stageKinds: StageKind[];
  ran: { response: RanLite } | null; stale: boolean;
  errors: Array<{ path: string; message: string }>; failure: string | null;
  recordOpen: boolean;
}

/** The context of the page (every sub-tab). On the With motor sub-tab the result and error belong to the motor panel. */
export function gearsPageHelp(i: GearsPageHelp): HelpContext {
  const motor = i.sub === 'motor';
  const chips: string[] = [];
  const r = i.ran?.response?.result;
  if (!motor && r) {
    if (typeof r.state === 'string') chips.push(`state_${r.state}`);
    if (typeof r.passed === 'boolean' && (i.sub === 'spectrum')) chips.push(r.passed ? 'passed' : 'not_passed');
  }
  if (!motor && i.stale) chips.push('inputs_changed');
  const err = i.errors[0] ? `${i.errors[0].path || '/'}: ${i.errors[0].message}` : i.failure;
  return {
    tab: GEARS_HELP_TAB, sub: i.sub,
    preset: i.preset, stageKinds: i.stageKinds, stageCount: Math.min(99, i.stageKinds.length),
    record: i.recordOpen,
    ...(motor ? {} : { calculated: !!i.ran, stale: i.stale, status: chips, ...(err ? { error: maskNumbers(err) } : {}) }),
  };
}

export interface MotorPanelHelp {
  die: string; configuration: string; source: 'inline' | 'record'; rows: number;
  statuses: string[] | null; errors: Array<{ path: string; message: string }>; failure: string | null;
}

/** The With motor panel's part: the selected motor and configuration, the gearbox source, the row statuses of the last run. */
export function motorPanelHelp(i: MotorPanelHelp): HelpContext {
  const err = i.errors[0] ? `${i.errors[0].path || '/'}: ${i.errors[0].message}` : i.failure;
  return {
    tab: GEARS_HELP_TAB, sub: 'motor',
    die: i.die, configuration: i.configuration, source: i.source, rows: Math.min(99, i.rows),
    calculated: i.statuses !== null, status: [...new Set(i.statuses ?? [])].map((s) => `row_${s}`),
    ...(err ? { error: maskNumbers(err) } : {}),
  };
}
