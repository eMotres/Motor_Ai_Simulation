// Agent drafts + the account's job queue (MCP Stage 3, docs/MCP_2026-09-28.md).
// Drafts live in the user's workspace; opening one in Configure is client-side
// only — the server's open machine is never replaced by it.
import { getStoredToken } from './localAuth';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

export interface DraftParams {
  stack_mm: number; turns_factor: number; parallel_paths: number | null;
  connection: string | null; current_a_rms: number; speed_rpm: number;
  gamma_deg: number; mode: string; star_delta: string;
}

export interface AgentDraft {
  design_id: string; name: string; status: string; created_at: string; updated_at: string;
  created_by: { kind: string; client_name: string; credential_kind: string };
  requirements: Record<string, unknown>;
  starting_point: { die: string; config: string; duty: string; outer_diameter_mm: number | null;
    base_active_length_mm: number | null };
  why: string[]; warnings: string[];
  params: DraftParams; initial_params: DraftParams;
  estimate: Record<string, unknown>;
  results: Record<string, Record<string, unknown>>;
  runs: Array<{ job_id: string; what: string; queued_at: string }>;
  edited_by_user: boolean;
  reference_motor_id: string | null;
  build?: { conductors_per_slot: number | null; base_conductors_per_slot: number | null };
  open_in_configure: string;
}

export interface JobRow {
  run_id: string; kind: string; state: string; position: number; owner: string;
  /** "platform" or "node:<id>" (a user-owned compute node, docs/BYO_COMPUTE.md). */
  where?: string;
  queued_at: number; started_at: number; finished_at: number; error: string;
  elapsed_s: number; body?: { agent?: { client_name?: string }; design_id?: string; what?: string };
  progress?: { step?: number; total?: number; eta_s?: number };
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredToken();
  const r = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = ((await r.json()) as { detail?: string }).detail ?? msg; } catch { /* keep */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const listDrafts = () => call<{ designs: AgentDraft[] }>('/api/agent_designs').then((j) => j.designs);
export const getDraft = (id: string) => call<AgentDraft>(`/api/agent_designs/${encodeURIComponent(id)}`);
export const patchDraft = (id: string, changes: Partial<DraftParams>) =>
  call<AgentDraft>(`/api/agent_designs/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(changes) });
export const revertDraft = (id: string) =>
  call<AgentDraft>(`/api/agent_designs/${encodeURIComponent(id)}/revert`, { method: 'POST' });
export const deleteDraft = (id: string) =>
  call<{ deleted: string }>(`/api/agent_designs/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const listJobs = () => call<{ jobs: JobRow[] }>('/api/jobs?limit=30').then((j) => j.jobs);
export const cancelJob = (id: string) =>
  call<Record<string, unknown>>(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });

/** Ask the Configure tab to show a draft (it switches tab; the draft is
 *  applied to the tuner only when the engineer clicks Open there). */
export function showDraftInConfigure(id: string): void {
  try {
    const u = new URL(window.location.href);
    u.searchParams.set('tab', 'configure');
    u.searchParams.set('design', id);
    window.history.replaceState(null, '', u.toString());
  } catch { /* ignore */ }
  window.dispatchEvent(new CustomEvent('agent-draft', { detail: { id } }));
}

/** The draft id the page was opened with (…/?tab=configure&design=d-…). */
export function draftIdFromUrl(): string | null {
  try {
    const id = new URLSearchParams(window.location.search).get('design');
    return id && /^d-[0-9a-f]{12}$/.test(id) ? id : null;
  } catch { return null; }
}
