// The project's "Contract" section (owner 2026-11-22): types, server calls and the scheme data of the customer view.
// The server decides who reads the section (/api/projects/{id}/contract: staff, the project owner, members who see prices);
// a 404 means "not for you" and the tab is not shown.
import { useEffect, useState } from 'react';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');
const enc = encodeURIComponent;

export type ContractStatus = 'draft' | 'sent' | 'signed_customer' | 'signed_both';
export type ContractKind = 'agreement' | 'annex_a' | 'annex_b' | 'signed_customer' | 'signed_motres' | 'other';
export type ContractStep = 'specification' | 'simulation_agreement' | 'contract_payment' | 'samples_1' | 'samples_2' | 'series';
export const STATUS_ORDER: readonly ContractStatus[] = ['draft', 'sent', 'signed_customer', 'signed_both'];
export const KIND_ORDER: readonly ContractKind[] = ['agreement', 'annex_a', 'annex_b', 'signed_customer', 'signed_motres', 'other'];

export interface ContractFile { id: string; kind: ContractKind; name: string; note: string; size: number; sha256: string; uploaded_at: string; uploader: string; archived: boolean; archived_at?: string | null; /** the contract revision it was uploaded in */ revision?: number; /** the current revision's signing rests on it: no replacement, no archive */ frozen?: boolean }
export interface ContractFileRef { id: string; kind: ContractKind; name: string; sha256: string }
export interface ContractView {
  status: ContractStatus; revision: number; history: Array<{ status: ContractStatus | 'scan_uploaded' | 'scan_rejected'; at: string; by: string; revision?: number; files?: ContractFileRef[]; reason?: string }>; files: ContractFile[];
  current: Partial<Record<ContractKind, string>>; sample_iteration: 1 | 2; step: ContractStep | null; steps: ContractStep[];
  /** the digest of the terms a signature is about; captured with the revision when a dialog opens */
  terms_digest: string;
  /** a signed scan awaiting the MOTRES check (staff confirm or reject exactly this file) */
  pending_scan: (ContractFileRef & { revision: number }) | null;
  company: { available: boolean; filled: boolean; confirmed_on: string; confirmed_by: string };
  staff: boolean; can_manage: boolean; upload_kinds: ContractKind[]; staff_scheme: SchemeData | null;
}

/** One scheme: boxes on a 1000 x 420 grid and arrows between them.  Labels are either translation keys (the customer view, in the
 *  repository) or EN / ZH text pairs (the staff view, served from the private data checkout). */
export type SchemeText = string | { en?: string; zh?: string };
export interface SchemeNode { id: string; label: SchemeText; sub?: SchemeText; x: number; y: number; w?: number; h?: number }
export interface SchemeEdge { from: string; to: string; kind: 'money' | 'goods' | 'documents'; label?: SchemeText; bend?: number }
export interface SchemeData { nodes: SchemeNode[]; edges: SchemeEdge[]; notes?: SchemeText[] }

/** The customer view (task 2026-11-22): no factory name, no internal prices, no transfer price; the customer is the importer. */
export const CUSTOMER_SCHEME: SchemeData = {
  nodes: [
    { id: 'customer', label: 'scheme.customer', sub: 'scheme.customerSub', x: 30, y: 150, w: 250, h: 110 },
    { id: 'motres', label: 'scheme.motres', sub: 'scheme.motresSub', x: 720, y: 20, w: 250, h: 110 },
    { id: 'china', label: 'scheme.china', sub: 'scheme.chinaSub', x: 720, y: 280, w: 250, h: 110 },
  ],
  edges: [
    { from: 'customer', to: 'motres', kind: 'money', label: 'scheme.money', bend: -40 },
    { from: 'motres', to: 'customer', kind: 'documents', label: 'scheme.docsMotres', bend: -40 },
    { from: 'china', to: 'customer', kind: 'goods', label: 'scheme.goods', bend: 40 },
    { from: 'china', to: 'customer', kind: 'documents', label: 'scheme.docsChina', bend: 95 },
  ],
  notes: ['scheme.noteImporter', 'scheme.noteSupport'],
};

/** What the caller was looking at when it opened a dialog: sent with the request, never refreshed behind its back. */
export interface Seen { revision: number; terms_digest: string }

export function contractBase(projectId: string): string { return `${API}/api/projects/${enc(projectId)}/contract`; }

async function failure(r: Response): Promise<string> {
  try { const b = await r.json(); const d = b?.detail; return typeof d === 'string' ? d : typeof d?.message === 'string' ? d.message : `HTTP ${r.status}`; }
  catch { return `HTTP ${r.status}`; }
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(await failure(r));
  return (await r.json()) as T;
}

const post = <T,>(url: string, body: unknown) =>
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => json<T>(r));

export const contractApi = {
  get: (projectId: string, archived = false) => fetch(`${contractBase(projectId)}${archived ? '?archived=1' : ''}`).then((r) => json<ContractView>(r)),
  /** ``revision`` = the contract revision on screen: the server refuses (409) when it changed meanwhile */
  upload: (projectId: string, kind: ContractKind, file: File, note: string, seen: Seen) => {
    const form = new FormData();
    form.append('kind', kind); form.append('revision', String(seen.revision)); form.append('terms_digest', seen.terms_digest);
    form.append('file', file, file.name);
    if (note) form.append('note', note);
    return fetch(`${contractBase(projectId)}/files`, { method: 'POST', body: form }).then((r) => json<ContractView>(r));
  },
  status: (projectId: string, body: { status?: 'sent'; new_revision?: true; sample_iteration?: 1 | 2; revision?: number; terms_digest?: string }) =>
    fetch(`${contractBase(projectId)}/status`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => json<ContractView>(r)),
  /** staff: confirm / reject EXACTLY the scan awaiting the check, with the revision and terms seen when the dialog opened */
  confirm: (projectId: string, scan: ContractFileRef, seen: Seen) => post<ContractView>(`${contractBase(projectId)}/confirm`,
    { file_id: scan.id, sha256: scan.sha256, ...seen }),
  reject: (projectId: string, scan: ContractFileRef, seen: Seen, reason: string) => post<ContractView>(`${contractBase(projectId)}/reject`,
    { file_id: scan.id, sha256: scan.sha256, ...seen, reason }),
  archive: (projectId: string, fileId: string, archived: boolean) =>
    fetch(`${contractBase(projectId)}/files/${enc(fileId)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ archived }) }).then((r) => json<ContractView>(r)),
  download: async (projectId: string, file: ContractFile) => {
    const r = await fetch(`${contractBase(projectId)}/files/${enc(file.id)}/download`);
    if (!r.ok) throw new Error(await failure(r));
    const href = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = href; a.download = file.name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 10_000);
  },
};

/** Does this caller see the section?  One quiet request per project; any answer but 200 hides the tab.  ``null`` = not known yet
 *  (a remembered "contract" view waits for the answer instead of falling back at once). */
export function useContractVisible(projectId: string | null | undefined): boolean | null {
  const [visible, setVisible] = useState<{ id: string; ok: boolean } | null>(null);
  useEffect(() => {
    if (!projectId) return undefined;
    let live = true;
    fetch(contractBase(projectId)).then((r) => { if (live) setVisible({ id: projectId, ok: r.ok }); }).catch(() => { if (live) setVisible({ id: projectId, ok: false }); });
    return () => { live = false; };
  }, [projectId]);
  if (!projectId) return false;
  return visible?.id === projectId ? visible.ok : null;
}
