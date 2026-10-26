// Calls of the specification assistant (routes in development/spec_assistant_routes.py).  Same fetch convention as
// `api.ts`: the app's global fetch layer adds the sign-in.
import type { ChatMessage, Proposal } from './specAssistant';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const enc = encodeURIComponent;

export class SpecAssistantError extends Error {
  status: number; detail: unknown;
  constructor(status: number, message: string, detail: unknown) { super(message); this.status = status; this.detail = detail; }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, { method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : `HTTP ${r.status}`;
    throw new SpecAssistantError(r.status, message, detail);
  }
  return data as T;
}

const p = (id: string) => `/api/projects/${enc(id)}/spec`;

export interface AssistantStatus { allowed: boolean; can_apply: boolean; configured: boolean }
export interface AssistantTurn { reply: string; proposals: Proposal[]; source: string; model?: string }
export interface ProposalList { proposals: Proposal[]; revision: number; can_apply: boolean }

export const specAssistantApi = {
  status: (id: string) => call<AssistantStatus>('GET', `${p(id)}/assistant`),
  turn: (id: string, messages: ChatMessage[], lang: string) => call<AssistantTurn>('POST', `${p(id)}/assistant`, { messages, lang }),
  proposals: (id: string) => call<ProposalList>('GET', `${p(id)}/proposals`),
  apply: (id: string, proposalId: string, revision: number) => call<{ proposal: Proposal; revision: number }>('POST', `${p(id)}/proposals/${enc(proposalId)}/apply`, { revision }),
  dismiss: (id: string, proposalId: string) => call<{ proposal: Proposal }>('POST', `${p(id)}/proposals/${enc(proposalId)}/dismiss`),
};
