// Pure helpers of the Production module (no React, no I/O): revision numbers, bilingual display, caller standing.
// Imported by __tests__/helpers.test.mjs (Node >= 22.18 strips the types).
import type {
  Bilingual, BomRow, CallerRole, Lang, Member, Production, Revision, TranslationStatus,
} from './types';

// -- revisions --------------------------------------------------------------------------------------------------------

/** "1.10" -> [1, 10]; null when it is not "<major>.<minor>". */
export function parseRevision(rev: string | null | undefined): [number, number] | null {
  const m = /^\s*(\d+)\.(\d+)\s*$/.exec(rev ?? '');
  return m ? [Number(m[1]), Number(m[2])] : null;
}

/** Numeric order: "1.10" > "1.9". Unparseable numbers sort first. */
export function compareRevisions(a: string, b: string): number {
  const pa = parseRevision(a) ?? [-1, -1];
  const pb = parseRevision(b) ?? [-1, -1];
  return pa[0] - pb[0] || pa[1] - pb[1];
}

/** The number the next published revision gets: "1.1" -> "1.2"; nothing yet -> "1.0". */
export function nextRevision(current: string | null | undefined): string {
  const p = parseRevision(current);
  return p ? `${p[0]}.${p[1] + 1}` : '1.0';
}

/** Newest first. */
export const sortRevisions = (revs: readonly Revision[]): Revision[] =>
  [...revs].sort((a, b) => compareRevisions(b.number, a.number));

export const hasAcked = (rev: Revision, email: string): boolean =>
  rev.acknowledgements.some((a) => a.email.toLowerCase() === email.toLowerCase());

/** Revisions this account still has to acknowledge, oldest first. */
export const unackedRevisions = (revs: readonly Revision[], email: string): Revision[] =>
  sortRevisions(revs).reverse().filter((r) => !hasAcked(r, email));

// -- bilingual text -----------------------------------------------------------------------------------------------------

/** The interface language (i18next "zh-CN", "en", ...) as a content language. */
export const contentLang = (ui: string | null | undefined): Lang => ((ui ?? '').toLowerCase().startsWith('zh') ? 'zh' : 'en');

export const otherLang = (l: Lang): Lang => (l === 'en' ? 'zh' : 'en');

/** What a writer sends: only his own language filled; the server translates the other side. */
export function makeBilingual(text: string, srcLang: Lang): Bilingual {
  const status: Record<Lang, TranslationStatus> = { en: 'pending', zh: 'pending' };
  status[srcLang] = 'confirmed';
  return { en: srcLang === 'en' ? text : '', zh: srcLang === 'zh' ? text : '', src_lang: srcLang, status };
}

export const emptyBilingual = (lang: Lang = 'en'): Bilingual => makeBilingual('', lang);

export interface ShownText {
  text: string;
  lang: Lang;
  status: TranslationStatus;
  /** the shown side is a machine translation nobody has confirmed */
  machine: boolean;
  /** the translation of the reader's side has not arrived (provider failed / no key) */
  pending: boolean;
  /** the shown side is the author's original */
  isOriginal: boolean;
}

/** The text to show for the reader's language, or the author's original when `original` is set.
 *  A side that is still empty falls back to the original so a reader never sees a blank. */
export function displayText(b: Bilingual | null | undefined, ui: Lang, original = false): ShownText {
  if (!b) return { text: '', lang: ui, status: 'confirmed', machine: false, pending: false, isOriginal: true };
  const want: Lang = original ? b.src_lang : ui;
  const empty = want !== b.src_lang && !(b[want] ?? '').trim();
  const lang: Lang = empty ? b.src_lang : want;
  const status: TranslationStatus = lang === b.src_lang ? 'confirmed' : (b.status?.[lang] ?? 'pending');
  return {
    text: b[lang] ?? '', lang, status,
    machine: status === 'machine',
    pending: empty || status === 'pending',
    isOriginal: lang === b.src_lang,
  };
}

/** Does this bilingual object still hold a side nobody confirmed? */
export const needsConfirmation = (b: Bilingual | null | undefined): boolean =>
  !!b && (b.status.en !== 'confirmed' || b.status.zh !== 'confirmed');

// -- caller standing ------------------------------------------------------------------------------------------------------

export function callerRole(members: readonly Member[], email: string, isAdmin: boolean): CallerRole {
  const m = members.find((x) => x.email.toLowerCase() === email.toLowerCase());
  return m ? m.role : isAdmin ? 'admin' : null;
}

/** owner / engineer / admin edit parts, BOM, process, documents and decide proposals. */
export const canEditRole = (r: CallerRole): boolean => r === 'owner' || r === 'engineer' || r === 'admin';
/** oem and the editors may raise proposals and acknowledge revisions. */
export const canProposeRole = (r: CallerRole): boolean => r === 'oem' || canEditRole(r);
export const canAckRole = canProposeRole;

export const memberLabel = (m: Member): string => `${m.email} (${m.role})`;

export const productionKey = (p: { die: string; config: string }): string => `${p.die} / ${p.config}`;

/** OEM members without a signed NDA cannot open the production (the server answers 403 nda_required). */
export const oemWithoutNda = (p: Pick<Production, 'members' | 'members_without_nda'>): string[] =>
  (p.members_without_nda ?? []).filter((e) => p.members.some((m) => m.role === 'oem' && m.email.toLowerCase() === e.toLowerCase()));

// -- BOM ------------------------------------------------------------------------------------------------------------------

export const orderTotal = (qtyPerMotor: number, orderQuantity: number): number => {
  const q = Number.isFinite(qtyPerMotor) ? qtyPerMotor : 0;
  const n = Number.isFinite(orderQuantity) ? orderQuantity : 0;
  return Math.round(q * n * 1000) / 1000;
};

/** Change a row's per-motor quantity and keep its order total in step with the production's order quantity. */
export const withQty = (rows: readonly BomRow[], partId: string, qty: number, orderQuantity: number): BomRow[] =>
  rows.map((r) => (r.part_id === partId ? { ...r, qty_per_motor: qty, order_total: orderTotal(qty, orderQuantity) } : r));

/** Parts in BOM order first, then the rest. */
export function sortByBom<T extends { id: string }>(parts: readonly T[], rows: readonly BomRow[]): T[] {
  const idx = new Map(rows.map((r, i) => [r.part_id, i] as const));
  return [...parts].sort((a, b) => (idx.get(a.id) ?? 1e9) - (idx.get(b.id) ?? 1e9));
}

// -- API replies ------------------------------------------------------------------------------------------------------------

/** A list reply is either the array itself or an object that holds it under `key`. */
export function unwrapList<T>(j: unknown, key: string): T[] {
  if (Array.isArray(j)) return j as T[];
  const v = j && typeof j === 'object' ? (j as Record<string, unknown>)[key] : undefined;
  return Array.isArray(v) ? (v as T[]) : [];
}

/** The server's refusal as {code, message}: `detail` is a string or {code, message}. */
export function errorInfo(status: number, body: unknown): { code: string | null; message: string } {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const d = b.detail ?? b;
  if (typeof d === 'string') return { code: typeof b.code === 'string' ? b.code : null, message: d };
  const o = (d && typeof d === 'object' ? d : {}) as Record<string, unknown>;
  return {
    code: typeof o.code === 'string' ? o.code : null,
    message: typeof o.message === 'string' ? o.message : `HTTP ${status}`,
  };
}

/** Safe file name for a download. */
export const safeFileName = (name: string): string => name.replace(/[\\/:*?"<>|]+/g, '_').trim() || 'file';
