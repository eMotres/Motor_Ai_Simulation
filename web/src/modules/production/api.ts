// Production module — fetch wrappers for /api/production/* and /api/admin/production/*
// (docs/PRODUCTION_MODULE_DESIGN.md section 5). The session token is added by the global fetch interceptor.
import { errorInfo, safeFileName, unwrapList } from './helpers';
import type {
  Bilingual, BomRow, CostRow, CostsData, DocumentRec, ExtraCost, Member, MemberRole, Part, ProcessSection, Production, Proposal, Revision,
  Thread, TranslationRef, Lang, ProcurementData, ProcurementLine, ScheduleData, ScheduleBaseline, TimelineData,
} from './types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export class ProductionApiError extends Error {
  status: number;
  code: string | null;
  constructor(status: number, code: string | null, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
  /** 403 {code: "nda_required"}: the NDA gate of the 3-D model applies. */
  get ndaRequired(): boolean { return this.status === 403 && this.code === 'nda_required'; }
  get notFound(): boolean { return this.status === 404; }
}

const enc = encodeURIComponent;
const base = (die: string, config: string) => `/api/production/${enc(die)}/${enc(config)}`;
const adminBase = (die: string, config: string) => `/api/admin/production/${enc(die)}/${enc(config)}`;

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
  const r = await fetch(`${API}${path}`, {
    method,
    headers: body === undefined || isForm ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  const j = await r.json().catch(() => null);
  if (!r.ok) {
    const e = errorInfo(r.status, j);
    throw new ProductionApiError(r.status, e.code, e.message);
  }
  return j as T;
}

/** Download a traced production file (needs the session header, so a plain link would 401). */
export async function downloadFile(die: string, config: string, fileId: string, name: string): Promise<void> {
  const r = await fetch(`${API}${base(die, config)}/files/${enc(fileId)}`);
  if (!r.ok) throw new ProductionApiError(r.status, null, `HTTP ${r.status}`);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement('a');
  a.href = url; a.download = safeFileName(name);
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** A production file as an object URL (drawing preview); the caller revokes it. */
export async function fileObjectUrl(die: string, config: string, fileId: string): Promise<string> {
  const r = await fetch(`${API}${base(die, config)}/files/${enc(fileId)}`);
  if (!r.ok) throw new ProductionApiError(r.status, null, `HTTP ${r.status}`);
  return URL.createObjectURL(await r.blob());
}

/** The existing 3-D model download of the same motor (same NDA gate). */
export async function downloadModelStep(die: string, config: string): Promise<void> {
  const r = await fetch(`${API}/api/model3d/${enc(die)}/${enc(config)}/model.step`);
  if (!r.ok) throw new ProductionApiError(r.status, null, `HTTP ${r.status}`);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement('a');
  a.href = url; a.download = safeFileName(`${die}_${config}.step`);
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const productionApi = {
  // -- caller routes ---------------------------------------------------------------------------------------------------
  list: async (): Promise<Production[]> => unwrapList<Production>(await call('GET', '/api/production'), 'productions'),
  get: (die: string, config: string) => call<Production>('GET', base(die, config)),

  parts: async (die: string, config: string) => unwrapList<Part>(await call('GET', `${base(die, config)}/parts`), 'parts'),
  createPart: (die: string, config: string, part: Partial<Part>) => call<Part>('POST', `${base(die, config)}/parts`, part),
  updatePart: (die: string, config: string, id: string, patch: Partial<Part>) =>
    call<Part>('PATCH', `${base(die, config)}/parts/${enc(id)}`, patch),
  deletePart: (die: string, config: string, id: string) => call<unknown>('DELETE', `${base(die, config)}/parts/${enc(id)}`),

  bom: async (die: string, config: string) => unwrapList<BomRow>(await call('GET', `${base(die, config)}/bom`), 'rows'),
  saveBom: (die: string, config: string, rows: BomRow[]) => call<unknown>('PUT', `${base(die, config)}/bom`, { rows }),

  costs: (die: string, config: string) => call<CostsData>('GET', `${base(die, config)}/costs`),
  saveCosts: (die: string, config: string, data: { rows: CostRow[]; extras: ExtraCost[]; margin_pct: number }) =>
    call<CostsData>('PUT', `${base(die, config)}/costs`, data),

  procurement: (die: string, config: string) => call<ProcurementData>('GET', `${base(die, config)}/procurement`),
  saveProcurementLine: (die: string, config: string, partId: string, line: Partial<ProcurementLine>) =>
    call<{ row: ProcurementLine }>('PUT', `${base(die, config)}/procurement/${enc(partId)}`, line),

  schedule: (die: string, config: string) => call<ScheduleData>('GET', `${base(die, config)}/schedule`),
  saveSchedule: (die: string, config: string, data: Partial<ScheduleData>) =>
    call<ScheduleData>('PUT', `${base(die, config)}/schedule`, data),
  freezeBaseline: (die: string, config: string) =>
    call<{ baseline: ScheduleBaseline }>('POST', `${base(die, config)}/schedule/baseline`, {}),

  timeline: (die: string) => call<TimelineData>('GET', `/api/production/${enc(die)}/timeline`),

  process: async (die: string, config: string) => unwrapList<ProcessSection>(await call('GET', `${base(die, config)}/process`), 'sections'),
  saveProcess: (die: string, config: string, sections: ProcessSection[]) =>
    call<unknown>('PUT', `${base(die, config)}/process`, { sections }),

  documents: async (die: string, config: string) => unwrapList<DocumentRec>(await call('GET', `${base(die, config)}/documents`), 'documents'),
  uploadDocument: (die: string, config: string, file: File, partId: string | null) => {
    const f = new FormData();
    f.append('file', file, file.name);
    if (partId) f.append('part_id', partId);
    return call<DocumentRec>('POST', `${base(die, config)}/documents`, f);
  },

  threads: async (die: string, config: string) => unwrapList<Thread>(await call('GET', `${base(die, config)}/threads`), 'threads'),
  createThread: (die: string, config: string, partId: string | null) =>
    call<Thread>('POST', `${base(die, config)}/threads`, { part_id: partId }),
  /** multipart (text JSON + files) when there are attachments, plain JSON otherwise */
  postMessage: (die: string, config: string, threadId: string, text: Bilingual, files: File[]) => {
    const path = `${base(die, config)}/threads/${enc(threadId)}/messages`;
    if (!files.length) return call<Thread>('POST', path, { text, src_lang: text.src_lang });
    const f = new FormData();
    f.append('text', JSON.stringify(text));
    f.append('src_lang', text.src_lang);
    files.forEach((x) => f.append('files', x, x.name));
    return call<Thread>('POST', path, f);
  },

  proposals: async (die: string, config: string) => unwrapList<Proposal>(await call('GET', `${base(die, config)}/proposals`), 'proposals'),
  createProposal: (die: string, config: string, partId: string | null, text: Bilingual) =>
    call<Proposal>('POST', `${base(die, config)}/proposals`, { part_id: partId, text }),
  decide: (die: string, config: string, id: string, action: 'accept' | 'reject' | 'implement', note: Bilingual | null) =>
    call<Proposal>('POST', `${base(die, config)}/proposals/${enc(id)}/${action}`, note ? { note } : {}),

  revisions: async (die: string, config: string) => unwrapList<Revision>(await call('GET', `${base(die, config)}/revisions`), 'revisions'),
  publishRevision: (die: string, config: string, summary: Bilingual, changes: Bilingual[]) =>
    call<Revision>('POST', `${base(die, config)}/revisions`, { summary, changes }),
  ackRevision: (die: string, config: string, rev: string) =>
    call<Revision>('POST', `${base(die, config)}/revisions/${enc(rev)}/ack`, {}),

  retryTranslation: (die: string, config: string, ref: TranslationRef) =>
    call<unknown>('POST', `${base(die, config)}/translate/retry`, ref),
  /** confirm the machine translation as is, or replace it with an edited text (`text` + `lang`) */
  confirmTranslation: (die: string, config: string, ref: TranslationRef, lang: Lang, text?: string) =>
    call<unknown>('POST', `${base(die, config)}/translate/confirm`, text === undefined ? { ...ref, lang } : { ...ref, lang, text }),

  // -- admin routes ----------------------------------------------------------------------------------------------------
  adminList: async (): Promise<Production[]> => unwrapList<Production>(await call('GET', '/api/admin/production'), 'productions'),
  adminCreate: (die: string, config: string, title: Bilingual, orderQuantity: number, members: Member[]) =>
    call<Production>('POST', adminBase(die, config), { title, order_quantity: orderQuantity, members }),
  adminDelete: (die: string, config: string) => call<unknown>('DELETE', adminBase(die, config)),
  adminSetMembers: (die: string, config: string, members: Member[]) =>
    call<unknown>('PUT', `${adminBase(die, config)}/members`, { members }),
  adminAddMember: (die: string, config: string, email: string, role: MemberRole) =>
    call<unknown>('POST', `${adminBase(die, config)}/members`, { email, role }),
  adminRemoveMember: (die: string, config: string, email: string) =>
    call<unknown>('DELETE', `${adminBase(die, config)}/members/${enc(email)}`),
  setShareCosts: (die: string, config: string, share: boolean) =>
    call<{ share_costs_with_oem: boolean }>('PUT', `${adminBase(die, config)}/share_costs`, { share_costs_with_oem: share }),
};
