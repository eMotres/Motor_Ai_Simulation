// Propellers > Mission: the calls to /api/propellers/mission (auth headers are attached by the global fetch wrapper).
import { formatApiErrorDetail } from './apiErrorDetail';
import type { MissionResult, MotorEntry, Objective } from './propellerMission';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API.replace(/\/$/, '')}/api/propellers/mission`;

export interface BatteryPreset { label: string; v_nom: number; v_min: number; v_max: number; wh_per_kg: number; r_cell_ohm_ah: number }
export interface Strip { thickness_mm: number; width_mm: number; stock_kg: number; check: boolean }
export interface MissionOptions {
  motors: MotorEntry[]; batteries: Record<string, BatteryPreset>; objectives: Objective[]; strips: Strip[]; note: string; version: string; estimate_label: string;
  defaults: Record<string, Record<string, unknown>>;
}
export interface MotorInfo { kind: 's1' | 'estimate'; name: string; die?: string | null; config?: string | null; card_id?: string | null; label?: string; flags?: string[]; model_sha256?: string }
export interface MissionRun {
  run_id: string; status: 'queued' | 'running' | 'done' | 'stopped' | 'failed' | 'interrupted'; position?: number; created_utc: string; motor: MotorInfo; inputs: Record<string, unknown>;
  version: string; done?: number; total?: number; line?: string; error?: string; elapsed_s?: number; result?: MissionResult;
}
export interface RunSummary {
  run_id: string; created_utc: string; status: string; objective: Objective | null; motor: Partial<MotorInfo>; done?: number | null; total?: number | null;
  best: { name: string; value: number | null; turns: number | null } | null; refined: { value: number | null; gain_pct: number | null } | null; error?: string | null;
}

export class MissionApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

async function call<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method, signal, cache: 'no-store',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let j: unknown = null;
  try { j = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) throw new MissionApiError(r.status, formatApiErrorDetail((j as { detail?: unknown } | null)?.detail) || `HTTP ${r.status}`);
  return j as T;
}

export const getOptions = (signal?: AbortSignal) => call<MissionOptions>('GET', '/options', undefined, signal);
export const startRun = (body: Record<string, unknown>) => call<{ run_id: string; status: string; total: number }>('POST', '/run', body);
export const listRuns = () => call<{ runs: RunSummary[]; note: string }>('GET', '/runs');
export const getRun = (id: string, signal?: AbortSignal) => call<MissionRun>('GET', `/runs/${encodeURIComponent(id)}`, undefined, signal);
export const deleteRun = (id: string) => call<{ deleted: boolean }>('DELETE', `/runs/${encodeURIComponent(id)}`);
export const stopRun = (id: string) => call<{ stopping: boolean }>('POST', `/runs/${encodeURIComponent(id)}/stop`);
