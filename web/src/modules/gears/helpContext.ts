// What the Gears page tells the help assistant (owner 2026-11-26): the sub-tab, the preset, the kinds and count of stages, whether
// a calculation was run, its status chips, the selected gearbox source, a validation CODE.  Flags, bounded counts and values from
// exact lists only: no name, no typed text, no geometry value, no result number, no price (D3; Astra review of #910).  The server
// validates again against help_docs/registry.json; a test checks that every id here has an EN and a zh-CN help document.
import type { HelpContext } from '../../lib/supportContext.ts';
import type { StageKind } from './types.ts';

export const GEARS_HELP_TAB = 'gears';
export const GEARS_HELP_SUBS = ['point', 'spectrum', 'assembly', 'motor'] as const;
export type GearsSub = typeof GEARS_HELP_SUBS[number];

const PRESETS = ['humanoid_robot_joint', 'standard'];
const STATES = ['ok', 'estimated', 'unsupported', 'failed', 'unknown', 'valid'];
const ROW_STATUSES = ['ok', 'missing', 'gearbox_failed'];
const KINDS: StageKind[] = ['planetary', 'compound_planetary', 'pair'];

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
    if (typeof r.state === 'string' && STATES.includes(r.state)) chips.push(`state_${r.state}`);
    if (typeof r.passed === 'boolean' && i.sub === 'spectrum') chips.push(r.passed ? 'passed' : 'not_passed');
  }
  if (!motor && i.stale) chips.push('inputs_changed');
  const kinds = i.stageKinds.filter((k) => KINDS.includes(k));
  return {
    tab: GEARS_HELP_TAB, sub: i.sub,
    ...(PRESETS.includes(i.preset) ? { preset: i.preset } : {}), stageKinds: kinds, stageCount: Math.min(99, i.stageKinds.length),
    record: i.recordOpen,
    ...(motor ? {} : {
      calculated: !!i.ran, stale: i.stale, status: chips,
      ...(i.errors.length ? { error: 'invalid_field' } : i.failure ? { error: 'request_failed' } : {}),
    }),
  };
}

export interface MotorPanelHelp {
  source: 'inline' | 'record'; rows: number;
  statuses: string[] | null; errors: Array<{ path: string; message: string }>; failure: string | null;
}

/** The With motor panel's part: the gearbox source, the row count, the row statuses of the last run (no motor, configuration or other name). */
export function motorPanelHelp(i: MotorPanelHelp): HelpContext {
  return {
    tab: GEARS_HELP_TAB, sub: 'motor', source: i.source, rows: Math.min(99, i.rows),
    calculated: i.statuses !== null,
    status: [...new Set(i.statuses ?? [])].filter((s) => ROW_STATUSES.includes(s)).map((s) => `row_${s}`),
    ...(i.errors.length ? { error: 'invalid_field' } : i.failure ? { error: 'request_failed' } : {}),
  };
}
