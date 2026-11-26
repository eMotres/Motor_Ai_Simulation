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

// ── Propellers ───────────────────────────────────────────────────────────────
export interface PropellersHelp { view: 'catalog' | 'designer'; rows: number; error?: string | null }
export function propellersHelp(i: PropellersHelp): HelpContext {
  return { tab: 'propellers', sub: i.view, ...(i.view === 'catalog' ? { rows: Math.min(99, i.rows) } : {}), ...(i.view === 'catalog' ? err(i.error) : {}) };
}
export interface DesignerHelp { polar: string; ran: boolean; busy: boolean; runs: number; drafts: number; optimised: boolean; error?: string | null }
export function designerHelp(i: DesignerHelp): HelpContext {
  return {
    tab: 'propellers', sub: 'designer', polar: word(i.polar), calculated: i.ran, runs: Math.min(99, i.runs), drafts: Math.min(99, i.drafts), optimised: i.optimised,
    status: i.busy ? ['running'] : i.ran ? ['done'] : ['not_run'], ...err(i.error),
  };
}

// ── Schemas ──────────────────────────────────────────────────────────────────
export interface SchemasHelp {
  mode: 'view' | 'compose'; graphView: string; showAll: boolean; die?: string | null; configuration?: string | null; duty?: string | null;
  loading: boolean; error?: string | null;
}
export function schemasHelp(i: SchemasHelp): HelpContext {
  return {
    tab: 'schemas', sub: i.mode,
    ...(i.mode === 'view' ? {
      graphView: word(i.graphView), showAll: i.showAll,
      ...(i.die ? { die: String(i.die).slice(0, 80) } : {}), ...(i.configuration ? { configuration: String(i.configuration).slice(0, 80) } : {}),
      ...(i.duty ? { duty: String(i.duty).slice(0, 80) } : {}),
      status: i.loading ? ['loading'] : i.error ? ['load_failed'] : ['shown'], ...err(i.error),
    } : {}),
  };
}
export interface ComposerHelp { graph: string; template: boolean; running: boolean; error?: string | null }
export function composerHelp(i: ComposerHelp): HelpContext {
  return { tab: 'schemas', sub: 'compose', graph: String(i.graph ?? '').slice(0, 80), template: i.template, status: i.running ? ['running'] : ['idle'], ...err(i.error) };
}
