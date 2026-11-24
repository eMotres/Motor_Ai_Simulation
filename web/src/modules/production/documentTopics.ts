/** The Documents tab by topic, and the links between documents and assembly steps (owner 2026-11-24).
 *
 *  Pure (no React, no window): `node --test` imports it.  Three rules live here and nowhere else:
 *   - ONE superseded rule (`isSupersededDoc`): status `superseded` OR a name that starts with `[superseded`.  Server mirror:
 *     `models.is_superseded`.
 *   - ONE derivation of a document's topic (`topicOf`): the stored `topic` of the document when it has one, else what is already
 *     known (a step picture, a tooling / packaging part, the step that names it, the file type, the name).  `group` stays what it
 *     was: the certification label.
 *   - ONE list of the documents a step shows (`stepDocsOf`), and its inverse (`stepsOfDocuments`), so a chip in Documents and a
 *     link in the step panel can never disagree.
 *  Nothing here changes stored data; the factory and project viewers get the same list as before (the server filters). */
import type { AssemblyStep, Bilingual, DocumentRec } from './types';

/** The topics of the Documents tab, in the order they are shown.  Server mirror: `models.DOCUMENT_TOPICS`. */
export const DOCUMENT_TOPICS = ['process', 'drawings', 'tooling', 'tests', 'packaging', 'photos', 'general'] as const;
export type DocumentTopic = typeof DOCUMENT_TOPICS[number];
/** The groups on screen: the topics, then ONE group of superseded files at the bottom (never stored as a topic). */
export const TOPIC_GROUPS = [...DOCUMENT_TOPICS, 'superseded'] as const;
export type TopicGroup = typeof TOPIC_GROUPS[number];

const IMAGE = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff'];
const CAD = ['step', 'stp', 'iges', 'igs', 'stl', 'dxf', 'dwg', 'svg', 'glb', 'x_t', 'sldprt', 'f3d'];
const extOfName = (name: string): string => (name.split('.').pop() || '').toLowerCase();
// whole-word starts: "latest" is not a test, "x_label_v2" is a label
const PACKAGING = /(?<![a-z])(packag|label|carton|sticker|pallet|barcode)|(?<![a-z])box(?![a-z])|包装|标签|贴纸|纸箱/i;
const TESTS = /(?<![a-z])(test|measure|inspection|calibrat|protocol|hipot)|测试|检验|测量|试验/i;
const PROCESS = /(?<![a-z])(process|procedure|instruction|sop|sequence)|工艺|装配流程|作业指导/i;

/** THE superseded rule (server mirror: `models.is_superseded`).  A file is superseded when
 *   1. its `status` field says `superseded` (the field is authoritative: it wins over whatever the name looks like), or
 *   2. its name carries the legacy hand-archive marker: the text `[superseded` in any letter case at the very start of the name,
 *      followed by `]` or a space (`[superseded] a.pdf`, `[SUPERSEDED 2026-10] a.pdf`).  `[supersededness] a.pdf`, a marker in the
 *      middle of a name or after a leading space do not match. */
const LEGACY_MARKER = /^\[superseded[\] ]/i;
export function isSupersededDoc(d: Pick<DocumentRec, 'status' | 'name'>): boolean {
  if (d.status === 'superseded') return true;
  return LEGACY_MARKER.test(String(d.name ?? ''));
}

/** The server keeps at most this many document ids in one step (`process_steps.py`); the step dialog refuses more instead of dropping. */
export const STEP_DOCS_MAX = 40;

export const isDocumentTopic = (v: unknown): v is DocumentTopic => typeof v === 'string' && (DOCUMENT_TOPICS as readonly string[]).includes(v);

/** The documents a step shows: its own (`documents`) and those of its parts, never a step picture and never a superseded file;
 *  one drawing linked to two parts of the step is shown once (owner 2026-10-18). */
export function stepDocsOf(step: Pick<AssemblyStep, 'documents' | 'parts_resolved'>, documents: readonly DocumentRec[]): DocumentRec[] {
  const ids = new Set((step.parts_resolved ?? []).map((p) => p.id));
  const own = new Set(step.documents ?? []);
  const seen = new Set<string>();
  return documents.filter((d) => !d.step_id && !isSupersededDoc(d) && (own.has(d.id) || (!!d.part_id && ids.has(d.part_id))))
    .filter((d) => (seen.has(d.name) ? false : (seen.add(d.name), true)));
}

/** document id -> the steps that show it, in step order (the inverse of `stepDocsOf`). */
export function stepsOfDocuments(steps: readonly AssemblyStep[], documents: readonly DocumentRec[]): Map<string, AssemblyStep[]> {
  const out = new Map<string, AssemblyStep[]>();
  const ordered = [...steps].sort((a, b) => (a.n || 0) - (b.n || 0));
  for (const s of ordered) for (const d of stepDocsOf(s, documents)) { const l = out.get(d.id); if (l) l.push(s); else out.set(d.id, [s]); }
  return out;
}

export interface TopicContext {
  /** ids of the parts of the tooling order list */
  toolingPartIds: ReadonlySet<string>;
  /** part id -> name (derivation by the part's name, and the search by part) */
  partNames: ReadonlyMap<string, Bilingual>;
  /** document id -> kinds of the steps that NAME it in their own `documents` (a link through a part does not decide a topic:
   *  every drawing of a part would become "process") */
  namedBy: ReadonlyMap<string, ReadonlySet<AssemblyStep['kind']>>;
}

export function topicContext(parts: readonly { id: string; name: Bilingual }[], toolingPartIds: Iterable<string>, steps: readonly AssemblyStep[]): TopicContext {
  const namedBy = new Map<string, Set<AssemblyStep['kind']>>();
  for (const s of steps) for (const id of s.documents ?? []) { const k = namedBy.get(id); if (k) k.add(s.kind); else namedBy.set(id, new Set([s.kind])); }
  return { toolingPartIds: new Set(toolingPartIds), partNames: new Map(parts.map((p) => [p.id, p.name])), namedBy };
}

const partText = (ctx: TopicContext, d: DocumentRec): string => {
  const n = d.part_id ? ctx.partNames.get(d.part_id) : undefined;
  return n ? `${n.en ?? ''} ${n.zh ?? ''}` : '';
};

/** The topic a document is filed under, WITHOUT the superseded group (see `groupOf`): the stored `topic` first, else derived from what
 *  is known, in this order: a step picture -> Photos; a part of the tooling list -> Tooling; packaging / label by the part's or the
 *  file's name -> Packaging; named by a check step, or a test / measurement name -> Tests; named by an operation step -> Process;
 *  a CAD / drawing file type -> Drawings; a picture -> Drawings when it belongs to a part, else Photos; a PDF of a part -> Drawings;
 *  a process-like name -> Process; else General. */
export function topicOf(d: DocumentRec, ctx: TopicContext): DocumentTopic {
  const stored = (d as { topic?: unknown }).topic;
  if (isDocumentTopic(stored)) return stored;
  const ext = extOfName(d.name);
  if (d.step_id) return 'photos';
  if (d.part_id && ctx.toolingPartIds.has(d.part_id)) return 'tooling';
  if (PACKAGING.test(partText(ctx, d)) || PACKAGING.test(d.name)) return 'packaging';
  const kinds = ctx.namedBy.get(d.id);
  if (kinds?.has('check') || TESTS.test(d.name)) return 'tests';
  if (kinds?.has('operation')) return 'process';
  if (CAD.includes(ext)) return 'drawings';
  if (IMAGE.includes(ext)) return d.part_id ? 'drawings' : 'photos';
  if (ext === 'pdf' && d.part_id) return 'drawings';
  if (PROCESS.test(d.name)) return 'process';
  return 'general';
}

/** The group a document is shown in: superseded files go to the ONE superseded group, the rest by topic. */
export function groupOf(d: DocumentRec, ctx: TopicContext): TopicGroup {
  return isSupersededDoc(d) ? 'superseded' : topicOf(d, ctx);
}

/** Does a document match the search box?  Name and part name, case-insensitive, every word must be found. */
export function matchesQuery(d: DocumentRec, ctx: TopicContext, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = `${d.name} ${partText(ctx, d)}`.toLowerCase();
  return words.every((w) => hay.includes(w));
}

export interface DocumentGroup { group: TopicGroup; docs: DocumentRec[] }

const byName = (a: DocumentRec, b: DocumentRec) => a.name.localeCompare(b.name, 'en', { numeric: true, sensitivity: 'base' }) || a.uploaded_at.localeCompare(b.uploaded_at);

/** The non-empty groups in screen order (topics, superseded last); inside a group by file name (numbers in order). */
export function groupDocuments(docs: readonly DocumentRec[], ctx: TopicContext, query = ''): DocumentGroup[] {
  const buckets = new Map<TopicGroup, DocumentRec[]>();
  for (const d of docs) {
    if (!matchesQuery(d, ctx, query)) continue;
    const g = groupOf(d, ctx);
    const l = buckets.get(g); if (l) l.push(d); else buckets.set(g, [d]);
  }
  return TOPIC_GROUPS.filter((g) => buckets.has(g)).map((g) => ({ group: g, docs: buckets.get(g)!.sort(byName) }));
}

/** The groups open by default: all, but the superseded one. */
export const DEFAULT_CLOSED: readonly TopicGroup[] = ['superseded'];

/** A stored list of closed groups (view memory): only known group ids survive. */
export function validClosedGroups(raw: unknown): TopicGroup[] | null {
  if (!Array.isArray(raw)) return null;
  return raw.filter((x): x is TopicGroup => typeof x === 'string' && (TOPIC_GROUPS as readonly string[]).includes(x));
}

/** The label of a step chip: "5. Insert the magnets". */
export function stepChipLabel(step: Pick<AssemblyStep, 'n' | 'title'>, lang: 'en' | 'zh'): string {
  const t = (lang === 'zh' ? step.title.zh || step.title.en : step.title.en || step.title.zh) || '';
  return `${step.n}. ${t}`.trim();
}
