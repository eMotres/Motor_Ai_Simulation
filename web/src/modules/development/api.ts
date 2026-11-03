import type { CatalogController } from './controllerCatalog';
import { parseActivity } from './stageBar';
import { finalComparison } from '../../lib/resultsPanel';
import type { ProjectProductionEntry } from './projectProductions';
import type { CostEstimate, CostEstimateAnswer, CostSnapshot, CostSuggestions } from './costEstimate';
import type { ProjectQuote, SharedQuoteMark } from './quote';
import type { AdminTimeline, ProjectTimeline, TimelineDoc } from './timeline';
import type { ProjectDocument, ProjectDocumentList, DevelopmentSchema, FieldKind, MotorPointsAnswer, MotorView, StoredField, ProjectSummary, ProjectThreadMessage, ProjectMotor, ProjectMotorPickerItem, ProjectSnapshot, SpecState, SpecSnapshot } from './types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const enc = encodeURIComponent;

export class DevelopmentApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) { super(message); this.status = status; this.body = body; }
}
async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
  const r = await fetch(`${API}${path}`, { method, headers: body === undefined || isForm ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body) });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : `HTTP ${r.status}`;
    throw new DevelopmentApiError(r.status, message, data);
  }
  return data as T;
}
const p = (id: string) => `/api/projects/${enc(id)}`;
const ce = (id: string) => `/api/admin/projects/${enc(id)}/cost-estimate`;
const tl = (id: string) => `/api/admin/projects/${enc(id)}/timeline`;
/** `?display_currency=` - the UI language's currency, for the cost estimate's converted totals. */
const dq = (display?: string) => (display ? `?display_currency=${enc(display)}` : '');

/** The steps feed carries a light panel per computed point; its FINAL numbers are merged into the point's comparison row, so the
 *  steps table and chart read what the information panel shows (the panel itself is not kept). */
const withFinalNumbers = (answer: MotorPointsAnswer): MotorPointsAnswer => ({
  ...answer,
  variants: (answer.variants ?? []).map((v) => ({
    ...v,
    points: (v.points ?? []).map((pt) => {
      if (!pt.panel) return pt;
      const { panel, ...rest } = pt;
      return { ...rest, comparison: finalComparison(panel, pt.comparison) };
    }),
  })),
});

export const developmentApi = {
  // a 200 that is not the project list (no server answer reached the page, e.g. the app fell back to Local Mode)
  // is an error to show and retry, not a null that crashes the page (2026-10-25: "Cannot read properties of null")
  list: async () => {
    const data = await call<{ projects?: ProjectSummary[] } | null>('GET', '/api/projects');
    if (!data || !Array.isArray(data.projects)) throw new DevelopmentApiError(0, 'No answer from the AeroStator server - reconnecting', data);
    return data.projects;
  },
  /** The catalogue of complete commercial controllers (read-only reference data). */
  controllerCatalog: async () => {
    const data = await call<{ controllers?: CatalogController[] } | null>('GET', '/api/controllers/catalog');
    return Array.isArray(data?.controllers) ? data!.controllers! : [];
  },
  schema: (version?: number) => call<DevelopmentSchema>('GET', `/api/projects/schema${version ? `?version=${version}` : ''}`),
  create: (v: { id?: string; title: string; customer_org: string; owner_engineer?: string }) => call<ProjectSummary>('POST', '/api/projects', v),
  get: (id: string) => call<ProjectSummary>('GET', p(id)),
  update: (id: string, v: Partial<Pick<ProjectSummary, 'title' | 'customer_org' | 'owner_engineer' | 'status' | 'notes'>>) => call<ProjectSummary>('PATCH', p(id), v),
  spec: (id: string) => call<SpecState>('GET', `${p(id)}/spec`),
  migrationPreview: (id: string) => call<{ preview: { draft: Record<string, unknown>; source_sha256: string; source_revision: number; requires_acknowledgement: boolean; issues: Array<{ code: string; message?: string }> }; revision: number }>('POST', `${p(id)}/spec/migration/preview`, {}),
  migrationApply: (id: string, body: { revision: number; source_sha256: string; acknowledge_point: boolean }) => call<{ draft: Record<string, unknown>; revision: number; schema_version: number; completeness: SpecState['completeness'] }>('POST', `${p(id)}/spec/migration/apply`, body),
  saveDraft: (id: string, draft: Record<string, unknown>, revision: number) => call<SpecState>('PUT', `${p(id)}/spec`, { draft, revision, schema_version: Number(draft.schema_version ?? 2) }),
  submitSpec: (id: string, revision: number) => call<{ version: number; revision: number; spec: Record<string, unknown>; completeness: SpecState['completeness'] }>('POST', `${p(id)}/spec/submit`, { revision }),
  versions: (id: string) => call<{ versions: SpecState['versions'] }>('GET', `${p(id)}/spec/versions`),
  version: (id: string, n: number) => call<SpecSnapshot>('GET', `${p(id)}/spec/versions/${n}`),
  thread: async (id: string) => (await call<{ messages: ProjectThreadMessage[] }>('GET', `${p(id)}/thread`)).messages,
  /** The caller has seen the thread up to message `upTo` (the server never moves the mark back). */
  markThreadRead: (id: string, upTo: string) => call<{ last_read_id: string; unread: number }>('POST', `${p(id)}/thread/read`, { up_to: upTo }),
  postMessage: (id: string, text: string, field_ref?: string, scope: { version?: number; revision?: number; row_id?: string } = {}) => call<ProjectThreadMessage>('POST', `${p(id)}/thread`, { text, ...(field_ref ? { field_ref } : {}), ...scope }),
  resolveMessage: (id: string, messageId: string, resolved: boolean) => call<ProjectThreadMessage>('POST', `${p(id)}/thread/${enc(messageId)}/resolve`, { resolved }),
  /** Documents of a project (production package files): KiCad, fabrication, BOM, 3-D models, drawings. */
  documents: (id: string, archived = false) => call<ProjectDocumentList>('GET', `${p(id)}/documents${archived ? '?archived=true' : ''}`),
  uploadDocument: (id: string, file: File, o: { folder?: string; revision?: string; internal?: boolean } = {}) => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (o.folder) form.append('folder', o.folder);
    if (o.revision) form.append('revision', o.revision);
    if (o.internal) form.append('internal', 'true');
    return call<ProjectDocument>('POST', `${p(id)}/documents`, form);
  },
  patchDocument: (id: string, docId: string, v: Partial<Pick<ProjectDocument, 'internal' | 'folder' | 'revision'>> | { archived: false }) =>
    call<ProjectDocument>('PATCH', `${p(id)}/documents/${enc(docId)}`, v),
  archiveDocument: (id: string, docId: string) => call<ProjectDocument>('DELETE', `${p(id)}/documents/${enc(docId)}`),
  /** Make an image of the project's documents the cover (the server makes the thumbnail) / remove the cover. */
  setCover: (id: string, documentId: string) => call<{ document_id: string; cover_version: string }>('PUT', `${p(id)}/cover`, { document_id: documentId }),
  clearCover: (id: string) => call<{ cover_version: null }>('DELETE', `${p(id)}/cover`),
  invite: (id: string, email: string, role: string) => call<unknown>('POST', `${p(id)}/members`, { email, role }),
  motors: async (id: string, showAll = false) => (await call<{ motors: ProjectMotor[] }>('GET', `${p(id)}/motors${showAll ? '?show_all=true' : ''}`)).motors,
  /** Live runs on the project's motors (one request per project; polled every 30 s while the page is visible). */
  activity: async (id: string, since?: string | null) => parseActivity(await call<unknown>('GET', `${p(id)}/activity${since ? `?since=${encodeURIComponent(since)}` : ''}`)),
  motorPoints: (id: string, o: { die?: string; config?: string; showAll?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (o.die) q.set('die', o.die);
    if (o.config) q.set('config', o.config);
    if (o.showAll) q.set('show_all', 'true');
    const qs = q.toString();
    return call<MotorPointsAnswer>('GET', `${p(id)}/motors/points${qs ? `?${qs}` : ''}`).then(withFinalNumbers);
  },
  /** Everything stored for one project motor (same gates as the motor list); the Motors-tab twin is lib/motorViewApi. */
  motorView: (id: string, die: string, config: string, showAll = false) =>
    call<MotorView>('GET', `${p(id)}/motors/view?die=${enc(die)}&config=${enc(config)}${showAll ? '&show_all=true' : ''}`),
  motorField: (id: string, die: string, config: string, duty: string, kind: FieldKind, showAll = false) =>
    call<StoredField>('GET', `${p(id)}/motors/field?die=${enc(die)}&config=${enc(config)}&duty=${enc(duty)}&kind=${kind}${showAll ? '&show_all=true' : ''}`),
  /** Staff only: mark / unmark the variant's report as checked (members may download it only then). */
  setReportReviewed: (id: string, die: string, config: string, value: boolean) => call<unknown>('PATCH', `${p(id)}/motors/report_reviewed`, { die, config, value }),
  /** Staff only: THE recommended variant of the project (setting it clears the previous one). */
  setRecommended: (id: string, die: string, config: string, value: boolean) => call<unknown>('PATCH', `${p(id)}/motors/recommended`, { die, config, value }),
  setMotorNote: (id: string, die: string, config: string, note: string) => call<{ die: string; config: string; note: string }>('PATCH', `${p(id)}/motors/note`, { die, config, note }),
  setMotorStatus: (id: string, die: string, config: string, status: 'current' | 'trial') => call<unknown>('PATCH', `${p(id)}/motors/status`, { die, config, status }),
  motorPicker: (id: string) => call<{ this_project: ProjectMotorPickerItem[]; catalog: ProjectMotorPickerItem[] }>('GET', `${p(id)}/motors/picker`),
  linkMotor: (id: string, die: string, config: string) => call<unknown>('POST', `${p(id)}/motors`, { die, config }),
  unlinkMotor: (id: string, die: string, config: string) => call<unknown>('DELETE', `${p(id)}/motors?die=${enc(die)}&config=${enc(config)}`),
  snapshots: async (id: string) => (await call<{ snapshots: ProjectSnapshot[] }>('GET', `${p(id)}/snapshots`)).snapshots,
  /** The productions of the project's linked motors, as the caller may see them (owner 2026-11-02; projectProductions.ts). */
  productions: async (id: string) => (await call<{ productions: ProjectProductionEntry[] }>('GET', `${p(id)}/productions`)).productions ?? [],
  /** ADMIN ONLY (owner 2026-11-02): the project cost estimate (costEstimate.ts).  403 for anybody else. */
  costEstimate: (id: string, display?: string) => call<CostEstimateAnswer>('GET', ce(id) + dq(display)),
  saveCostEstimate: (id: string, estimate: CostEstimate, display?: string) => call<CostEstimateAnswer>('PUT', ce(id) + dq(display), estimate),
  suggestCostEstimate: (id: string, revision: number, refresh = false, display?: string) => call<CostEstimateAnswer>('POST', `${ce(id)}/suggest${dq(display)}`, { revision, refresh }),
  /** What Suggest WOULD put in the empty cells of the estimate on screen (the table's grey placeholders); nothing is saved. */
  costEstimateSuggestions: (id: string, estimate: CostEstimate) => call<CostSuggestions>('POST', `${ce(id)}/suggestions`, estimate),
  snapshotCostEstimate: (id: string, revision: number, note: string, display?: string) => call<{ snapshot: CostSnapshot }>('POST', `${ce(id)}/snapshot`, { revision, note, display_currency: display }),
  costEstimateSnapshots: async (id: string) => (await call<{ snapshots: CostSnapshot[] }>('GET', `${ce(id)}/snapshots`)).snapshots ?? [],
  /** ADMIN ONLY (owner 2026-11-03): freeze the current revision and share it with the project's members as their quote. */
  shareCostEstimate: (id: string, revision: number) => call<{ shared: SharedQuoteMark | null; snapshot: CostSnapshot }>('POST', `${ce(id)}/share`, { revision }),
  unshareCostEstimate: (id: string) => call<{ shared: null }>('DELETE', `${ce(id)}/share`),
  /** The shared quote of a project: its members and admins only (404 for anybody else, and while nothing is shared). */
  projectQuote: (id: string) => call<ProjectQuote>('GET', `${p(id)}/quote`),
  /** The project timeline (owner 2026-11-03): its members and admins only (404 for anybody else, and while there is none). */
  projectTimeline: (id: string) => call<ProjectTimeline>('GET', `${p(id)}/timeline`),
  /** ADMIN ONLY: the stored timeline, the computed lanes and the customer view; save / create from the cost estimate / freeze. */
  adminTimeline: (id: string) => call<AdminTimeline>('GET', tl(id)),
  saveTimeline: (id: string, body: Pick<TimelineDoc, 'revision' | 'production' | 'iterations'>) => call<AdminTimeline>('PUT', tl(id), body),
  seedTimeline: (id: string, revision: number) => call<AdminTimeline>('POST', `${tl(id)}/seed`, { revision }),
  timelineBaseline: (id: string, revision: number) => call<AdminTimeline>('POST', `${tl(id)}/baseline`, { revision }),
};

/** Save a response body as a file (the session header is added by the global fetch, so a plain link would 401). */
async function saveResponse(r: Response, fallbackName: string): Promise<void> {
  if (!r.ok) {
    const data = await r.json().catch(() => null);
    const detail = data?.detail;
    throw new DevelopmentApiError(r.status, typeof detail === 'string' ? detail : `HTTP ${r.status}`, data);
  }
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement('a');
  a.href = url; a.download = Array.from(fallbackName).map((c) => (c.charCodeAt(0) < 32 || '\\/:*?"<>|'.includes(c) ? '_' : c)).join('') || 'document';
  document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const downloadProjectDocument = async (id: string, doc: Pick<ProjectDocument, 'id' | 'name'>): Promise<void> =>
  saveResponse(await fetch(`${API}${p(id)}/documents/${enc(doc.id)}/download`), doc.name);

export const downloadProjectDocumentsZip = async (id: string): Promise<void> =>
  saveResponse(await fetch(`${API}${p(id)}/documents.zip`), `${id}_documents.zip`);

const coverCache = new Map<string, Promise<string>>();
/** The cover thumbnail as an object URL (the session header is added by the global fetch, so a plain <img src> would 401).
 *  One request per project and cover version for the whole page. */
export function loadProjectCover(id: string, version: string): Promise<string> {
  const key = `${id}:${version}`;
  let hit = coverCache.get(key);
  if (!hit) {
    hit = fetch(`${API}${p(id)}/cover`).then(async (r) => {
      if (!r.ok) throw new DevelopmentApiError(r.status, `HTTP ${r.status}`, null);
      return URL.createObjectURL(await r.blob());
    });
    hit.catch(() => coverCache.delete(key));
    coverCache.set(key, hit);
  }
  return hit;
}
