/** Upload queue state (owner 2026-11-26: "I pressed upload and see no progress anywhere").
 *  Pure and immutable: the queue of files, bytes sent, the concurrency limit, the duplicate guard, retry / cancel and the
 *  server's error text.  The React hook and the XHR transport only drive these functions (see useUploadQueue.ts). */

export type UploadStatus = 'queued' | 'uploading' | 'done' | 'failed' | 'cancelled';

export interface UploadItem {
  id: number;
  key: string;
  name: string;
  size: number;
  loaded: number;
  status: UploadStatus;
  /** the server's error text (failed rows only) */
  error: string;
}

export interface UploadQueueState {
  items: UploadItem[];
  /** files sent at the same time */
  limit: number;
  seq: number;
}

export const DEFAULT_UPLOAD_LIMIT = 2;

export const emptyQueue = (limit = DEFAULT_UPLOAD_LIMIT): UploadQueueState => ({ items: [], limit: Math.max(1, Math.floor(limit)), seq: 0 });

/** Two files are the same upload when name and size agree. */
export const uploadKey = (name: string, size: number): string => `${size}\u0000${name}`;

const isActive = (s: UploadStatus) => s === 'queued' || s === 'uploading';
const patch = (state: UploadQueueState, id: number, f: (i: UploadItem) => UploadItem): UploadQueueState => {
  let hit = false;
  const items = state.items.map((i) => { if (i.id !== id) return i; hit = true; return f(i); });
  return hit ? { ...state, items } : state;
};

/** Queued or uploading right now (a leave warning and the disabled Upload button read this). */
export const isBusy = (state: UploadQueueState): boolean => state.items.some((i) => isActive(i.status));

/** The file is already queued or uploading: it is not added twice. */
export const isDuplicate = (state: UploadQueueState, name: string, size: number): boolean => {
  const key = uploadKey(name, size);
  return state.items.some((i) => i.key === key && isActive(i.status));
};

export interface EnqueueResult { state: UploadQueueState; accepted: Array<{ id: number; index: number }>; duplicates: string[] }

/** Add files.  A duplicate of an active row is refused and reported by name; an old finished / failed row of the same file is replaced. */
export function enqueue(state: UploadQueueState, files: ReadonlyArray<{ name: string; size: number }>): EnqueueResult {
  let items = state.items;
  let seq = state.seq;
  const accepted: Array<{ id: number; index: number }> = [];
  const duplicates: string[] = [];
  for (let index = 0; index < files.length; index += 1) {
    const f = files[index];
    const key = uploadKey(f.name, f.size);
    if (items.some((i) => i.key === key && isActive(i.status))) { duplicates.push(f.name); continue; }
    seq += 1;
    items = [...items.filter((i) => i.key !== key), { id: seq, key, name: f.name, size: f.size, loaded: 0, status: 'queued', error: '' }];
    accepted.push({ id: seq, index });
  }
  return { state: { ...state, items, seq }, accepted, duplicates };
}

/** Ids to start now: the oldest queued rows, up to the free slots. */
export function startable(state: UploadQueueState): number[] {
  const running = state.items.filter((i) => i.status === 'uploading').length;
  const free = Math.max(0, state.limit - running);
  return state.items.filter((i) => i.status === 'queued').slice(0, free).map((i) => i.id);
}

export const markStarted = (state: UploadQueueState, id: number): UploadQueueState =>
  patch(state, id, (i) => (i.status === 'queued' ? { ...i, status: 'uploading', loaded: 0, error: '' } : i));

/** Bytes sent so far; never goes back, never above the size (headers and form fields count in the transport's total). */
export const setProgress = (state: UploadQueueState, id: number, loaded: number): UploadQueueState =>
  patch(state, id, (i) => {
    if (i.status !== 'uploading') return i;
    const next = Math.min(i.size, Math.max(i.loaded, Number.isFinite(loaded) ? Math.max(0, loaded) : 0));
    return next === i.loaded ? i : { ...i, loaded: next };
  });

export const markDone = (state: UploadQueueState, id: number): UploadQueueState =>
  patch(state, id, (i) => (i.status === 'uploading' ? { ...i, status: 'done', loaded: i.size, error: '' } : i));

export const markFailed = (state: UploadQueueState, id: number, error: string): UploadQueueState =>
  patch(state, id, (i) => (i.status === 'uploading' || i.status === 'queued' ? { ...i, status: 'failed', error } : i));

/** A queued or uploading row is cancelled (the hook aborts the request of an uploading one). */
export const cancel = (state: UploadQueueState, id: number): UploadQueueState =>
  patch(state, id, (i) => (isActive(i.status) ? { ...i, status: 'cancelled', error: '' } : i));

/** A failed or cancelled row goes back to the queue, unless the same file is queued or uploading meanwhile. */
export function retry(state: UploadQueueState, id: number): UploadQueueState {
  const row = state.items.find((i) => i.id === id);
  if (!row || (row.status !== 'failed' && row.status !== 'cancelled')) return state;
  if (state.items.some((i) => i.id !== id && i.key === row.key && isActive(i.status))) return state;
  return patch(state, id, (i) => ({ ...i, status: 'queued', loaded: 0, error: '' }));
}

export const dismiss = (state: UploadQueueState, id: number): UploadQueueState => {
  const row = state.items.find((i) => i.id === id);
  return !row || isActive(row.status) ? state : { ...state, items: state.items.filter((i) => i.id !== id) };
};

export const clearFinished = (state: UploadQueueState): UploadQueueState =>
  ({ ...state, items: state.items.filter((i) => i.status !== 'done') });

/** 0..100, whole percent; an empty file counts as done only when it is. */
export const percent = (i: Pick<UploadItem, 'loaded' | 'size' | 'status'>): number =>
  i.status === 'done' ? 100 : i.size > 0 ? Math.min(100, Math.floor((i.loaded / i.size) * 100)) : 0;

/** "12.4 MB" - the same units as the Documents table (formatBytes of the Development module). */
export function formatUploadBytes(bytes: number, language = 'en'): string {
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = Number.isFinite(bytes) && bytes > 0 ? bytes : 0;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  const digits = i === 0 || value >= 100 ? 0 : 1;
  return `${value.toLocaleString(language, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${units[i]}`;
}

/** "4.2 MB / 11.3 MB" */
export const progressLabel = (i: Pick<UploadItem, 'loaded' | 'size'>, language = 'en'): string =>
  `${formatUploadBytes(i.loaded, language)} / ${formatUploadBytes(i.size, language)}`;

/** The text of a refused request, as the Development API layer shows it: `detail` as a string, `detail.message`, else `HTTP <status>`. */
export function serverErrorText(status: number, body: unknown): string {
  const detail = (body && typeof body === 'object' ? (body as { detail?: unknown }).detail : undefined);
  if (typeof detail === 'string') return detail;
  const m = detail && typeof detail === 'object' ? (detail as { message?: unknown }).message : undefined;
  return typeof m === 'string' ? m : `HTTP ${status}`;
}

/** Summary for the row list: how many wait / run / failed. */
export function counts(state: UploadQueueState): Record<UploadStatus, number> {
  const c: Record<UploadStatus, number> = { queued: 0, uploading: 0, done: 0, failed: 0, cancelled: 0 };
  for (const i of state.items) c[i.status] += 1;
  return c;
}
