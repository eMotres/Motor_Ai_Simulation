// Pure helpers of the Production module (no React, no I/O): revision numbers, bilingual display, caller standing.
// Imported by __tests__/helpers.test.mjs (Node >= 22.18 strips the types).
import type {
  Bilingual, BomRow, CallerRole, CostRow, CostSummary, ExtraCost, Lang, Member, Production, Revision, TranslationStatus,
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

// -- Costs ----------------------------------------------------------------------------------------------------------------

export const canViewCosts = (role: CallerRole, shareWithOem = false): boolean =>
  role === 'owner' || role === 'engineer' || role === 'admin' || (role === 'oem' && shareWithOem);

export const canEditCosts = (role: CallerRole): boolean =>
  role === 'owner' || role === 'engineer' || role === 'admin';

export function calcCostSummary(
  bomRows: readonly BomRow[],
  costRows: readonly CostRow[],
  extras: readonly ExtraCost[],
  marginPct = 0,
  orderQuantity = 0,
): CostSummary {
  const qty = Math.max(0, Number.isFinite(orderQuantity) ? orderQuantity : 0);
  const margin = Math.max(0, Math.min(99.99, Number.isFinite(marginPct) ? marginPct : 0));
  const costMap = new Map(costRows.map((r) => [r.part_id, r]));

  let partsSubtotal = 0;
  let missingPricesCount = 0;

  for (const br of bomRows) {
    const cr = costMap.get(br.part_id);
    const p = cr ? cr.unit_price : br.unit_price;
    if (p !== null && p !== undefined && Number.isFinite(p)) {
      partsSubtotal += p;
    } else {
      missingPricesCount += 1;
    }
  }

  const extraSubtotal = extras.reduce((sum, e) => sum + (Number.isFinite(e.amount) ? Math.max(0, e.amount) : 0), 0);
  const costPerMotor = partsSubtotal + extraSubtotal;
  const priceFactor = 1 - margin / 100;
  const pricePerMotor = priceFactor > 0 ? costPerMotor / priceFactor : costPerMotor;

  const round2 = (v: number) => Math.round(v * 100) / 100;
  const warnings: string[] = [];
  if (missingPricesCount > 0) {
    warnings.push(`${missingPricesCount} BOM item(s) have missing prices; parts subtotal is incomplete.`);
  }

  return {
    parts_subtotal: round2(partsSubtotal),
    extra_subtotal: round2(extraSubtotal),
    cost_per_motor: round2(costPerMotor),
    margin_pct: round2(margin),
    price_per_motor: round2(pricePerMotor),
    order_quantity: qty,
    order_parts_total: round2(partsSubtotal * qty),
    order_extra_total: round2(extraSubtotal * qty),
    order_cost_total: round2(costPerMotor * qty),
    order_price_total: round2(pricePerMotor * qty),
    missing_prices_count: missingPricesCount,
    warnings,
  };
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

// -- Procurement & Timeline ------------------------------------------------------------------------------------------------

export const canEditProcurement = (role: CallerRole): boolean =>
  role === 'owner' || role === 'engineer' || role === 'admin' || role === 'oem';

export const canEditSchedule = (role: CallerRole): boolean =>
  role === 'owner' || role === 'engineer' || role === 'admin';

export const canEditScheduleOem = (role: CallerRole): boolean =>
  role === 'oem' || canEditSchedule(role);

export function getStatusColor(status: string | undefined): { bg: string; text: string; border: string } {
  switch (status) {
    case 'received':
      return { bg: 'rgba(16, 185, 129, 0.15)', text: '#10b981', border: 'rgba(16, 185, 129, 0.4)' };
    case 'partial':
      return { bg: 'rgba(59, 130, 246, 0.15)', text: '#3b82f6', border: 'rgba(59, 130, 246, 0.4)' };
    case 'late':
      return { bg: 'rgba(239, 68, 68, 0.15)', text: '#ef4444', border: 'rgba(239, 68, 68, 0.4)' };
    case 'at_risk':
      return { bg: 'rgba(245, 158, 11, 0.15)', text: '#f59e0b', border: 'rgba(245, 158, 11, 0.4)' };
    case 'confirmed':
      return { bg: 'rgba(139, 92, 246, 0.15)', text: '#8b5cf6', border: 'rgba(139, 92, 246, 0.4)' };
    case 'ordered':
      return { bg: 'rgba(6, 182, 212, 0.15)', text: '#06b6d4', border: 'rgba(6, 182, 212, 0.4)' };
    case 'to_order':
      return { bg: 'rgba(249, 115, 22, 0.15)', text: '#f97316', border: 'rgba(249, 115, 22, 0.4)' };
    case 'not_ordered':
    default:
      return { bg: 'rgba(107, 114, 128, 0.15)', text: '#9ca3af', border: 'rgba(107, 114, 128, 0.4)' };
  }
}

/** Number of days between two ISO dates (YYYY-MM-DD). */
export function daysBetween(d1: string, d2: string): number {
  if (!d1 || !d2) return 0;
  const t1 = new Date(d1).getTime();
  const t2 = new Date(d2).getTime();
  if (isNaN(t1) || isNaN(t2)) return 0;
  return Math.round((t2 - t1) / (1000 * 60 * 60 * 24));
}

/** Offset in px of a date relative to minDate. */
export function dateToPx(dateStr: string, minDate: string, pxPerDay: number): number {
  const d = daysBetween(minDate, dateStr);
  return Math.max(0, d * pxPerDay);
}

/** Width in px for a date span [start, end]. */
export function dateSpanToPx(startDate: string, endDate: string, pxPerDay: number): number {
  const d = Math.max(1, daysBetween(startDate, endDate) + 1);
  return d * pxPerDay;
}

/** Format YYYY-MM-DD to short display e.g. "Oct 12" / "10-12". */
export function formatShortDate(d: string | null | undefined): string {
  if (!d) return '—';
  const parts = d.split('-');
  if (parts.length === 3) {
    return `${parts[1]}-${parts[2]}`;
  }
  return d;
}

/** Compute procurement line status client-side matching the ERP priority order. */
export function computeClientProcurementStatus(line: {
  qty_ordered?: number | null;
  qty_needed?: number;
  qty_per_motor?: number;
  receipts?: Array<{ accepted: number; rejected: number; date: string }>;
  received_qty?: number;
  confirmed_on?: string | null;
  requested_on?: string | null;
  ordered_on?: string | null;
  need_by?: string | null;
  due_on?: string | null;
  lead_time_days?: number;
  today?: string;
}, orderQuantity = 1): string {
  const today = line.today ?? new Date().toISOString().slice(0, 10);
  const qtyNeeded = line.qty_ordered && line.qty_ordered > 0
    ? line.qty_ordered
    : (line.qty_needed ?? (line.qty_per_motor ?? 1) * orderQuantity);

  const receipts = line.receipts ?? [];
  const acceptedSum = receipts.reduce((sum, r) => sum + (Number(r.accepted) || 0), 0);
  const receivedQty = receipts.length > 0 ? acceptedSum : (Number(line.received_qty) || 0);

  const needBy = line.need_by || line.due_on;
  const leadTime = Number(line.lead_time_days) || 0;

  let orderBy: string | null = null;
  if (needBy) {
    const d = new Date(needBy);
    if (!isNaN(d.getTime())) {
      d.setDate(d.getDate() - leadTime);
      orderBy = d.toISOString().slice(0, 10);
    }
  }

  let expectedArrival: string | null = null;
  if (line.confirmed_on) {
    expectedArrival = line.confirmed_on;
  } else if (line.requested_on) {
    expectedArrival = line.requested_on;
  } else if (line.ordered_on && leadTime >= 0) {
    const d = new Date(line.ordered_on);
    if (!isNaN(d.getTime())) {
      d.setDate(d.getDate() + leadTime);
      expectedArrival = d.toISOString().slice(0, 10);
    }
  } else if (line.due_on || line.need_by) {
    expectedArrival = line.due_on || line.need_by || null;
  }

  const isFullyReceived = (qtyNeeded > 0 && receivedQty >= qtyNeeded) || (qtyNeeded === 0 && receivedQty > 0);
  const isPartiallyReceived = 0 < receivedQty && receivedQty < qtyNeeded;
  const isLate = Boolean(expectedArrival && expectedArrival < today && !isFullyReceived);
  const isAtRisk = Boolean(expectedArrival && needBy && expectedArrival > needBy && !isFullyReceived);
  const isToOrder = Boolean(!line.ordered_on && orderBy && today >= orderBy);

  if (isFullyReceived) return 'received';
  if (isPartiallyReceived) return 'partial';
  if (isLate) return 'late';
  if (isAtRisk) return 'at_risk';
  if (line.confirmed_on) return 'confirmed';
  if (line.ordered_on) return 'ordered';
  if (isToOrder) return 'to_order';
  return 'not_ordered';
}
