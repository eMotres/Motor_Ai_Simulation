// What each Simulations page tells the help assistant (owner 2026-11-26; rules of the Astra review of #910): flags, bounded counts,
// status chips and values from EXACT lists only - no name, no typed text, no setting value, no result number, no price.  Pure builders:
// the pages call `useHelpContext(...)` with them.  The tab ids, keys and allowed values are the ones in
// src/motor_ai_sim/help_docs/registry.json (the server drops anything else); a test checks that the lists agree and that every id has
// an EN and a zh-CN document.
import type { HelpContext } from './supportContext.ts';

export const HELP_TABS = ['simulation', 'thermal', 'mechanical'] as const;

const pick = (v: unknown, allowed: readonly string[]): string | undefined => (typeof v === 'string' && allowed.includes(v) ? v : undefined);
const put = (key: string, v: string | undefined): Record<string, string> => (v === undefined ? {} : { [key]: v });

export const SIM_OP_MODES = ['motor', 'generator'] as const;
export const SIM_COUPLED_DRIVES = ['sine', 'inverter'] as const;
export const SIM_SOLVE_TO = ['steady', 'limits', 'continuous'] as const;
export const COOL_MODES = ['air', 'liquid', 'manual', 'none', 'robotics'] as const;
export const BORE_MODES = ['none', 'air', 'liquid', 'still'] as const;
export const OUTER_SOURCES = ['manual', 'propeller'] as const;
export const LOADS = ['centrifugal', 'torque', 'both'] as const;
export const SYMMETRIES = ['full', 'sector'] as const;

// ── Electromagnetic ──────────────────────────────────────────────────────────
export interface SimulationHelp {
  opMode: string; drive: string; targetKind: string; coupled: boolean; coupledDrive: string; solveTo: string;
  running: boolean; stopped: boolean; failed: boolean; ran: boolean; hasNotice: boolean;
}
export function simulationHelp(i: SimulationHelp): HelpContext {
  const input = i.drive === 'current' ? (i.targetKind === 'kw' ? 'target_power' : i.targetKind === 'nm' ? 'target_torque' : 'sine_current') : 'other_drive';
  const status = i.running ? ['running'] : i.stopped ? ['stopped'] : i.failed ? ['error'] : i.ran ? ['done'] : ['not_run'];
  return {
    tab: 'simulation', ...put('opMode', pick(i.opMode, SIM_OP_MODES)), input, coupled: i.coupled,
    ...(i.coupled ? { ...put('coupledDrive', pick(i.coupledDrive, SIM_COUPLED_DRIVES)), ...put('solveTo', pick(i.solveTo, SIM_SOLVE_TO)) } : {}),
    calculated: i.ran, status, ...(i.hasNotice || i.failed ? { error: 'run_failed' } : {}),
  };
}

// ── Thermal ──────────────────────────────────────────────────────────────────
export interface ThermalHelp {
  coolMode: string; boreMode: string; outerSource: string;
  busy: boolean; ran: boolean; stale: boolean; emMissing: boolean; compareRows: number; invalid: boolean;
}
export function thermalHelp(i: ThermalHelp): HelpContext {
  return {
    tab: 'thermal', ...put('coolMode', pick(i.coolMode, COOL_MODES)), ...put('boreMode', pick(i.boreMode, BORE_MODES)),
    ...put('outerSource', pick(i.outerSource, OUTER_SOURCES)),
    calculated: i.ran, stale: i.stale, emMissing: i.emMissing, compareRows: Math.min(99, i.compareRows),
    status: i.busy ? ['solving'] : i.ran ? ['done'] : ['not_solved'], ...(i.invalid ? { error: 'not_valid' } : {}),
  };
}

// ── Mechanical ───────────────────────────────────────────────────────────────
export interface MechanicalHelp { loads: string; symmetry: string; busy: boolean; ran: boolean; stale: boolean; failed: boolean }
export function mechanicalHelp(i: MechanicalHelp): HelpContext {
  return {
    tab: 'mechanical', ...put('loads', pick(i.loads, LOADS)), ...put('symmetry', pick(i.symmetry, SYMMETRIES)),
    calculated: i.ran, stale: i.stale, status: i.busy ? ['solving'] : i.ran ? ['done'] : ['not_solved'], ...(i.failed ? { error: 'run_failed' } : {}),
  };
}
