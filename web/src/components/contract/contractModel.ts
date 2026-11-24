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
export interface ContractFileRef { id: string; kind: ContractKind; name: string; sha256: string; template?: boolean; version?: string }
export interface ContractTemplate { kind: 'agreement' | 'annex_a' | 'annex_b'; version: string; name: string; size: number; sha256: string; uploaded_at: string }
export interface ContractView {
  status: ContractStatus; revision: number; history: Array<{ status: ContractStatus | 'scan_uploaded' | 'scan_rejected'; at: string; by: string; revision?: number; files?: ContractFileRef[]; reason?: string }>; files: ContractFile[];
  current: Partial<Record<ContractKind, string>>; sample_iteration: 1 | 2; step: ContractStep | null; steps: ContractStep[];
  /** the digest of the terms a signature is about; captured with the revision when a dialog opens */
  terms_digest: string;
  /** the standard documents of every project (owner 2026-11-22): the current version of each, or null */
  templates: Record<'agreement' | 'annex_a' | 'annex_b', ContractTemplate | null> & { /** the store needs repair: nothing is shown or sent */ unavailable?: boolean };
  /** a draft with no terms of its own: the standard agreement is what would be sent */
  template_only: boolean;
  /** what the current revision was sent with (a template is named by its version) */
  sent_files: ContractFileRef[];
  /** a signed scan awaiting the MOTRES check (staff confirm or reject exactly this file) */
  pending_scan: (ContractFileRef & { revision: number }) | null;
  company: { available: boolean; filled: boolean; confirmed_on: string; confirmed_by: string };
  staff: boolean; can_manage: boolean; upload_kinds: ContractKind[]; staff_scheme: SchemeData | null;
  /** the stored customer view (null: the page's built-in one) */
  customer_scheme?: SchemeData | null;
}

/** One scheme: boxes on a 1000 x 420 grid and arrows between them.  Labels are either translation keys (the customer view, in the
 *  repository) or EN / ZH text pairs (the staff view, served from the private data checkout). */
export type SchemeText = string | { en?: string; zh?: string };
/** A box: on the grid by ``col`` / ``row`` (0-based); older files carry ``x`` / ``y`` instead, mapped to the grid.  ``w`` / ``h``
 *  are kept for old files and ignored: every card has the same size. */
export interface SchemeNode { id: string; label: SchemeText; sub?: SchemeText; col?: number; row?: number; x?: number; y?: number; w?: number; h?: number }
/** A relation; the connector is routed automatically (``bend`` of old files is ignored). */
export interface SchemeEdge { from: string; to: string; kind: 'money' | 'goods' | 'documents'; label?: SchemeText; bend?: number }
export interface SchemeData { nodes: SchemeNode[]; edges: SchemeEdge[]; notes?: SchemeText[]; /** whom the file is for */ audience?: 'customer' | 'staff' }

/** The customer view (task 2026-11-22): no factory name, no internal prices, no transfer price; the customer is the importer. */
export const CUSTOMER_SCHEME: SchemeData = {
  nodes: [
    { id: 'customer', label: 'scheme.customer', sub: 'scheme.customerSub', col: 0, row: 1 },
    { id: 'motres', label: 'scheme.motres', sub: 'scheme.motresSub', col: 1, row: 0 },
    { id: 'china', label: 'scheme.china', sub: 'scheme.chinaSub', col: 1, row: 2 },
  ],
  edges: [
    { from: 'customer', to: 'motres', kind: 'money', label: 'scheme.money' },
    { from: 'motres', to: 'customer', kind: 'documents', label: 'scheme.docsMotres' },
    { from: 'china', to: 'customer', kind: 'goods', label: 'scheme.goods' },
    { from: 'china', to: 'customer', kind: 'documents', label: 'scheme.docsChina' },
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
  /** admins: a contract scheme (staff or customer) as JSON, checked by the server (422 names what is wrong, also an
   *  "audience" that is not ``kind``) */
  putScheme: (text: string, kind: 'staff' | 'customer' = 'staff') => fetch(`${API}/api/contract-scheme?kind=${kind}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: text })
    .then((r) => json<{ ok: boolean }>(r)),
  /** the standard document (current, or the version a sent revision was made from) */
  downloadTemplate: async (projectId: string, kind: string, name: string, version?: string, sha256?: string) => {
    // a sent document by its frozen identity (version + hash); the current one by the version the page shows
    const q = [version ? `version=${enc(version)}` : '', sha256 ? `sha256=${enc(sha256)}` : ''].filter(Boolean).join('&');
    const r = await fetch(`${contractBase(projectId)}/template/${enc(kind)}/download${q ? `?${q}` : ''}`);
    if (!r.ok) throw new Error(await failure(r));
    const href = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = href; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 10_000);
  },
  /** admins: a new version of a standard document - every project shows it */
  uploadTemplate: (kind: string, version: string, file: File) => {
    const form = new FormData();
    form.append('kind', kind); form.append('version', version); form.append('file', file, file.name);
    return fetch(`${API}/api/contract-templates`, { method: 'POST', body: form }).then((r) => json<ContractTemplate>(r));
  },
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
