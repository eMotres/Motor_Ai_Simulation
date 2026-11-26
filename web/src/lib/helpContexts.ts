// What each Simulations page tells the help assistant (owner 2026-11-26): modes, status chips and flags only - no value of a geometry,
// a setting number or a result.  Pure builders: the pages call `useHelpContext(...)` with them.  The tab ids and keys are the ones in
// src/motor_ai_sim/help_docs/registry.json (the server drops anything else); a test checks every id has an EN and a zh-CN document.
import { maskNumbers, type HelpContext } from './supportContext.ts';

export const HELP_TABS = ['simulation', 'thermal', 'mechanical', 'propellers', 'schemas'] as const;

const word = (v: unknown): string => String(v ?? '').trim().replace(/[^A-Za-z0-9_\- ]/g, '').slice(0, 40);
const motorName = (name?: string | null): Record<string, string> => (name ? { motor: String(name).slice(0, 80) } : {});
const err = (e?: string | null): Record<string, string> => (e && e.trim() ? { error: maskNumbers(e) } : {});

// ── Electromagnetic ──────────────────────────────────────────────────────────
export interface SimulationHelp {
  motorName?: string | null; opMode: string; drive: string; targetKind: string; connection: string;
  coupled: boolean; coupledDrive: string; solveTo: string;
  running: boolean; stopped: boolean; failed: boolean; ran: boolean; notice?: string | null;
}
export function simulationHelp(i: SimulationHelp): HelpContext {
  const input = i.drive === 'current' && i.targetKind !== 'off' ? (i.targetKind === 'kw' ? 'target_power' : 'target_torque') : `${word(i.drive) || 'current'}_drive`;
  const status = i.running ? ['running'] : i.stopped ? ['stopped'] : i.failed ? ['error'] : i.ran ? ['done'] : ['not_run'];
  return {
    tab: 'simulation', ...motorName(i.motorName), opMode: word(i.opMode), input, connection: word(i.connection),
    coupled: i.coupled, ...(i.coupled ? { coupledDrive: word(i.coupledDrive), solveTo: word(i.solveTo) } : {}),
    calculated: i.ran, status, ...err(i.notice),
  };
}

// ── Thermal ──────────────────────────────────────────────────────────────────
export interface ThermalHelp {
  motorName?: string | null; coolMode: string; boreMode: string; outerSource: string;
  busy: boolean; ran: boolean; stale: boolean; emMissing: boolean; compareRows: number; issue?: string | null;
}
export function thermalHelp(i: ThermalHelp): HelpContext {
  return {
    tab: 'thermal', ...motorName(i.motorName), coolMode: word(i.coolMode), boreMode: word(i.boreMode), outerSource: word(i.outerSource),
    calculated: i.ran, stale: i.stale, emMissing: i.emMissing, compareRows: Math.min(99, i.compareRows),
    status: i.busy ? ['solving'] : i.ran ? ['done'] : ['not_solved'], ...err(i.issue),
  };
}

// ── Mechanical ───────────────────────────────────────────────────────────────
export interface MechanicalHelp { motorName?: string | null; loads: string; symmetry: string; busy: boolean; ran: boolean; stale: boolean; error?: string | null }
export function mechanicalHelp(i: MechanicalHelp): HelpContext {
  return {
    tab: 'mechanical', ...motorName(i.motorName), loads: word(i.loads), symmetry: word(i.symmetry),
    calculated: i.ran, stale: i.stale, status: i.busy ? ['solving'] : i.ran ? ['done'] : ['not_solved'], ...err(i.error),
  };
}
