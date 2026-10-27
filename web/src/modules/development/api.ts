import type { CatalogController } from './controllerCatalog';
import { parseActivity } from './stageBar';
import type { DevelopmentSchema, FieldKind, MotorPointsAnswer, MotorView, StoredField, ProjectSummary, ProjectThreadMessage, ProjectMotor, ProjectMotorPickerItem, ProjectSnapshot, SpecState, SpecSnapshot } from './types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const enc = encodeURIComponent;

export class DevelopmentApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) { super(message); this.status = status; this.body = body; }
}
async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, { method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : `HTTP ${r.status}`;
    throw new DevelopmentApiError(r.status, message, data);
  }
  return data as T;
}
const p = (id: string) => `/api/projects/${enc(id)}`;

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
  create: (v: { id: string; title: string; customer_org: string; owner_engineer?: string }) => call<ProjectSummary>('POST', '/api/projects', v),
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
  postMessage: (id: string, text: string, field_ref?: string, scope: { version?: number; revision?: number; row_id?: string } = {}) => call<ProjectThreadMessage>('POST', `${p(id)}/thread`, { text, ...(field_ref ? { field_ref } : {}), ...scope }),
  resolveMessage: (id: string, messageId: string, resolved: boolean) => call<ProjectThreadMessage>('POST', `${p(id)}/thread/${enc(messageId)}/resolve`, { resolved }),
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
    return call<MotorPointsAnswer>('GET', `${p(id)}/motors/points${qs ? `?${qs}` : ''}`);
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
};
