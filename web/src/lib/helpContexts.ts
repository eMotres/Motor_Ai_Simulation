// What each Simulations page tells the help assistant (owner 2026-11-26; rules of the Astra review of #910): flags, bounded counts,
// status chips and values from EXACT lists only - no name, no typed text, no setting value, no result number, no price.  Pure builders:
// the pages call `useHelpContext(...)` with them.  The tab ids, keys and allowed values are the ones in
// src/motor_ai_sim/help_docs/registry.json (the server drops anything else); a test checks that the lists agree and that every id has
// an EN and a zh-CN document.
import type { HelpContext } from './supportContext.ts';

export const HELP_TABS = ['simulation', 'thermal', 'mechanical', 'propellers', 'schemas'] as const;

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

// ── Propellers ───────────────────────────────────────────────────────────────
export const POLARS = ['flatplate_model', 'camber_model', 'mfoil_table'] as const;
export const GRAPH_VIEWS = ['modules', 'propeller_s1'] as const;

export interface PropellersHelp { view: 'catalog' | 'designer' | 'mission'; rows: number; failed: boolean }
export function propellersHelp(i: PropellersHelp): HelpContext {
  return { tab: 'propellers', sub: i.view, ...(i.view === 'catalog' ? { rows: Math.min(99, i.rows), ...(i.failed ? { error: 'load_failed' } : {}) } : {}) };
}
export interface DesignerHelp { polar: string; ran: boolean; busy: boolean; runs: number; drafts: number; optimised: boolean; failed: boolean }
export function designerHelp(i: DesignerHelp): HelpContext {
  return {
    tab: 'propellers', sub: 'designer', ...put('polar', pick(i.polar, POLARS)), calculated: i.ran, runs: Math.min(99, i.runs), drafts: Math.min(99, i.drafts),
    optimised: i.optimised, status: i.busy ? ['running'] : i.ran ? ['done'] : ['not_run'], ...(i.failed ? { error: 'request_failed' } : {}),
  };
}

// ── Schemas ──────────────────────────────────────────────────────────────────
export interface SchemasHelp { mode: 'view' | 'compose'; graphView: string; showAll: boolean; loading: boolean; failed: boolean }
export function schemasHelp(i: SchemasHelp): HelpContext {
  return {
    tab: 'schemas', sub: i.mode,
    ...(i.mode === 'view' ? { ...put('graphView', pick(i.graphView, GRAPH_VIEWS)), showAll: i.showAll,
      status: i.loading ? ['loading'] : i.failed ? ['load_failed'] : ['shown'], ...(i.failed ? { error: 'load_failed' } : {}) } : {}),
  };
}
export interface ComposerHelp { template: boolean; running: boolean; failed: boolean }
export function composerHelp(i: ComposerHelp): HelpContext {
  return { tab: 'schemas', sub: 'compose', template: i.template, status: i.running ? ['running'] : ['idle'], ...(i.failed ? { error: 'run_failed' } : {}) };
}
