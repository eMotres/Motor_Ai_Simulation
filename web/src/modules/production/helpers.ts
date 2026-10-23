// Pure helpers of the Production module (no React, no I/O): revision numbers, bilingual display, caller standing.
// Imported by __tests__/helpers.test.mjs (Node >= 22.18 strips the types).
import type {
  AssemblyNode, AssemblyStep, AssemblyTree, Bilingual, BomRow, CallerRole, CostRow, CostSummary, CurrencyTotals, ExtraCost, Lang, Member, Part, Production, Revision, TranslationStatus,
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

// -- money ----------------------------------------------------------------------------------------------------------------

/** Currency of a price line that names none. */
export const DEFAULT_CURRENCY = 'CNY';
/** Currencies offered in the editing dropdown (a currency already stored on a line is always offered too). */
export const KNOWN_CURRENCIES: readonly string[] = ['CNY', 'USD', 'EUR'];
/** U+2009 THIN SPACE: the thousands separator in every locale (EN and zh-CN read the same). */
export const THIN_SPACE = ' ';

/** "1234.5" -> "1 234.50" (2 decimals, thin-space thousands, locale independent). Non-finite -> "—". */
export function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const fixed = Math.abs(value).toFixed(2);
  const [int, frac] = fixed.split('.');
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_SPACE);
  const negative = value < 0 && Number(fixed) !== 0;
  return `${negative ? '-' : ''}${grouped}.${frac}`;
}

/** "325.00 CNY"; the amount alone ("—") when it is missing. */
export function formatMoney(value: number | null | undefined, currency?: string | null): string {
  const amount = formatAmount(value);
  if (amount === '—') return amount;
  return `${amount} ${currency || DEFAULT_CURRENCY}`;
}

/** Orders currency codes: the known ones first (CNY, USD, EUR), then the rest alphabetically. */
export function compareCurrencies(a: string, b: string): number {
  const ia = KNOWN_CURRENCIES.indexOf(a);
  const ib = KNOWN_CURRENCIES.indexOf(b);
  if (ia >= 0 || ib >= 0) return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * One amount per currency: "325.00 CNY" when only one currency is in use,
 * "CNY 1 234.00 · EUR 56.00" when they are mixed (never one summed number).
 */
export function formatCurrencyAmounts(items: ReadonlyArray<{ currency: string; amount: number }>): string {
  if (items.length === 0) return '—';
  if (items.length === 1) return formatMoney(items[0].amount, items[0].currency);
  return items.map((i) => `${i.currency} ${formatAmount(i.amount)}`).join(' · ');
}

/** Options for a currency dropdown: the known ones plus the line's own currency when it is something else. */
export function currencyOptions(current?: string | null): string[] {
  const list = [...KNOWN_CURRENCIES];
  if (current && !list.includes(current)) list.push(current);
  return list;
}

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

  // Amounts are summed per currency only: CNY and EUR are never added into one number.
  const parts = new Map<string, number>();
  const extra = new Map<string, number>();
  let missingPricesCount = 0;

  for (const br of bomRows) {
    const cr = costMap.get(br.part_id);
    const p = cr ? cr.unit_price : br.unit_price;
    if (p !== null && p !== undefined && Number.isFinite(p)) {
      const cur = (cr?.currency || br.currency || DEFAULT_CURRENCY);
      parts.set(cur, (parts.get(cur) ?? 0) + p);
    } else {
      missingPricesCount += 1;
    }
  }

  for (const e of extras) {
    const cur = e.currency || DEFAULT_CURRENCY;
    extra.set(cur, (extra.get(cur) ?? 0) + (Number.isFinite(e.amount) ? Math.max(0, e.amount) : 0));
  }

  const currencies = [...new Set([...parts.keys(), ...extra.keys()])].sort(compareCurrencies);
  if (currencies.length === 0) currencies.push(DEFAULT_CURRENCY);

  const priceFactor = 1 - margin / 100;
  const round2 = (v: number) => Math.round(v * 100) / 100;
  const by_currency: CurrencyTotals[] = currencies.map((currency) => {
    const partsSubtotal = parts.get(currency) ?? 0;
    const extraSubtotal = extra.get(currency) ?? 0;
    const costPerMotor = partsSubtotal + extraSubtotal;
    const pricePerMotor = priceFactor > 0 ? costPerMotor / priceFactor : costPerMotor;
    return {
      currency,
      parts_subtotal: round2(partsSubtotal),
      extra_subtotal: round2(extraSubtotal),
      cost_per_motor: round2(costPerMotor),
      price_per_motor: round2(pricePerMotor),
      order_parts_total: round2(partsSubtotal * qty),
      order_extra_total: round2(extraSubtotal * qty),
      order_cost_total: round2(costPerMotor * qty),
      order_price_total: round2(pricePerMotor * qty),
    };
  });

  const warnings: string[] = [];
  if (missingPricesCount > 0) {
    warnings.push(`${missingPricesCount} BOM item(s) have missing prices; parts subtotal is incomplete.`);
  }

  return {
    margin_pct: round2(margin),
    order_quantity: qty,
    by_currency,
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

/** Only a known production member or administrator can start the bundle request. */
export const canDownloadProductionDocuments = (role: CallerRole): boolean => role !== null;

/** Relative route shared by the production ZIP client and its contract tests. */
export const productionDocumentsZipPath = (die: string, config: string, lang: Lang): string =>
  `/api/production/${encodeURIComponent(die)}/${encodeURIComponent(config)}/documents.zip?lang=${lang}`;

/** Prefer the UTF-8 RFC 5987 name while retaining the ASCII filename fallback if decoding fails. */
export function productionZipFilename(disposition: string, fallbackName: string): string {
  const fallback = disposition.match(/filename\s*=\s*"?([^";]+)"?/i)?.[1] || fallbackName;
  const encoded = disposition.match(/filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i);
  if (encoded && encoded[1].toLowerCase() === 'utf-8') {
    try { return safeFileName(decodeURIComponent(encoded[2].trim())); } catch { /* keep ASCII fallback */ }
  }
  return safeFileName(fallback);
}

export const productionZipErrorKey = (status: number | null): 'tooLarge' | 'access' | 'unavailable' | 'failed' =>
  status === 413 ? 'tooLarge' : status === 403 ? 'access' : status === 503 ? 'unavailable' : 'failed';

/** Process preview stays HTML; the download action asks the server for its PDF form. */
export const productionProcessDocumentPath = (die: string, config: string, lang: Lang, mode: 'view' | 'download'): string =>
  `/api/production/${encodeURIComponent(die)}/${encodeURIComponent(config)}/process_document?lang=${lang}${mode === 'download' ? '&download=true' : ''}`;

export const productionProcessDocumentFilename = (die: string, config: string, lang: Lang): string =>
  safeFileName(`${die}_${config}_process_${lang}.pdf`);

export const processDocumentErrorKey = (mode: 'view' | 'download', status: number | null): 'pdfTooLarge' | 'pdfUnavailable' | null =>
  mode !== 'download' ? null : status === 413 ? 'pdfTooLarge' : status === 503 ? 'pdfUnavailable' : null;

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

// -- Assembly Tree & Roll-up ------------------------------------------------------------------------------------------------

export const DEFAULT_SUBASSEMBLY_PARTS: readonly Part[] = [
  {
    id: 'sub_stator',
    name: {
      en: 'Stator assembly',
      zh: '定子总成',
      src_lang: 'en',
      status: { en: 'confirmed', zh: 'confirmed' },
    },
    kind: 'assembly',
    qty_per_motor: 1,
    material: '',
    spec: '',
    description: {
      en: 'Stator core, coils, insulation, rings and phase board',
      zh: '定子铁芯、线圈、绝缘、固定环与相板',
      src_lang: 'en',
      status: { en: 'confirmed', zh: 'confirmed' },
    },
    drawing_files: [],
    optional_step: null,
    revision: '1.0',
    parent_id: 'motor',
  },
  {
    id: 'sub_rotor',
    name: {
      en: 'Rotor assembly',
      zh: '转子总成',
      src_lang: 'en',
      status: { en: 'confirmed', zh: 'confirmed' },
    },
    kind: 'assembly',
    qty_per_motor: 1,
    material: '',
    spec: '',
    description: {
      en: 'Rotor core, magnets and shaft',
      zh: '转子铁芯、永磁体与电机轴',
      src_lang: 'en',
      status: { en: 'confirmed', zh: 'confirmed' },
    },
    drawing_files: [],
    optional_step: null,
    revision: '1.0',
    parent_id: 'motor',
  },
  {
    id: 'sub_housing',
    name: {
      en: 'Housing',
      zh: '机壳与结构件',
      src_lang: 'en',
      status: { en: 'confirmed', zh: 'confirmed' },
    },
    kind: 'assembly',
    qty_per_motor: 1,
    material: '',
    spec: '',
    description: {
      en: 'Holders, bearings, screws and propeller fix',
      zh: '端盖支架、轴承、螺钉与螺旋桨固定片',
      src_lang: 'en',
      status: { en: 'confirmed', zh: 'confirmed' },
    },
    drawing_files: [],
    optional_step: null,
    revision: '1.0',
    parent_id: 'motor',
  },
];

export function matchDefaultParent(nameEn?: string, nameZh?: string): string {
  const en = (nameEn ?? '').trim().toLowerCase();
  const zh = (nameZh ?? '').trim();

  // Check housing first (to avoid "轴" matching "轴承")
  if (
    ['holder', 'bearing', 'screw', 'bolt', 'prop_fix', 'prop fix', 'housing'].some((k) => en.includes(k)) ||
    ['支架', '端盖', '轴承', '螺钉', '螺栓', '固定片', '机壳'].some((k) => zh.includes(k))
  ) {
    return 'sub_housing';
  }

  // Check rotor
  if (
    ['rotor core', 'magnet', 'shaft'].some((k) => en.includes(k)) ||
    ['转子', '磁钢', '磁体'].some((k) => zh.includes(k)) ||
    (zh.includes('轴') && !zh.includes('轴承'))
  ) {
    if (!en.includes('stator') && !zh.includes('定子')) {
      return 'sub_rotor';
    }
  }

  // Check stator
  if (
    ['stator core', 'coil', 'insulation', 'phase board', 'lead wire', 'varnish', 'epoxy'].some((k) => en.includes(k)) ||
    ['定子', '线圈', '绝缘', '相板', '引线', '浸漆', '环氧'].some((k) => zh.includes(k))
  ) {
    return 'sub_stator';
  }

  return 'sub_housing';
}

export function ensureDefaultAssemblyParts(parts: readonly Part[]): Part[] {
  if (!parts || parts.length === 0) return [];
  const hasAssemblies = parts.some((p) => p.kind === 'assembly');
  const hasParentIds = parts.some((p) => Boolean(p.parent_id));
  if (hasAssemblies || hasParentIds) return [...parts];

  const subMap = new Map(DEFAULT_SUBASSEMBLY_PARTS.map((s) => [s.id, { ...s }]));
  const updated = parts.map((p) => {
    const parent = matchDefaultParent(p.name?.en, p.name?.zh);
    return { ...p, parent_id: parent };
  });

  return [...subMap.values(), ...updated];
}

export function calcAssemblyRollup(
  parts: readonly Part[],
  costRows: readonly CostRow[],
): Map<string, number> {
  const costMap = new Map(costRows.map((r) => [r.part_id, Number(r.unit_price) || 0]));
  const rollup = new Map<string, number>();

  // Leaf costs
  for (const p of parts) {
    if (p.kind !== 'assembly') {
      if (costMap.has(p.id)) {
        rollup.set(p.id, costMap.get(p.id)!);
      }
    }
  }

  // Assembly nodes (parent -> children)
  const childrenMap = new Map<string, Part[]>();
  for (const p of parts) {
    if (p.parent_id) {
      const list = childrenMap.get(p.parent_id) ?? [];
      list.push(p);
      childrenMap.set(p.parent_id, list);
    }
  }

  for (let iter = 0; iter < 5; iter++) {
    let changed = false;
    for (const p of parts) {
      if (p.kind === 'assembly') {
        const children = childrenMap.get(p.id) ?? [];
        const sum = Math.round(children.reduce((acc, c) => acc + (rollup.get(c.id) ?? 0), 0) * 100) / 100;
        if (rollup.get(p.id) !== sum) {
          rollup.set(p.id, sum);
          changed = true;
        }
      }
    }
    if (!changed) break;
  }

  // Motor total
  const motorChildren = parts.filter((p) => p.parent_id === 'motor' || p.kind === 'assembly');
  const motorSum = Math.round(motorChildren.reduce((acc, c) => acc + (rollup.get(c.id) ?? 0), 0) * 100) / 100;
  rollup.set('motor', motorSum);

  return rollup;
}

export function buildAssemblyTree(
  parts: readonly Part[],
  costRows: readonly CostRow[] = [],
  orderQuantity = 1,
): AssemblyTree {
  const structuredParts = ensureDefaultAssemblyParts(parts);
  const rollups = calcAssemblyRollup(structuredParts, costRows);

  const byParent = new Map<string, Part[]>();
  for (const p of structuredParts) {
    const pid = p.parent_id || 'motor';
    const list = byParent.get(pid) ?? [];
    list.push(p);
    byParent.set(pid, list);
  }

  const toAssemblyNode = (p: Part): AssemblyNode => {
    const unitPrice = rollups.has(p.id) ? rollups.get(p.id)! : null;
    const lineTotal = unitPrice !== null ? Math.round(unitPrice * orderQuantity * 100) / 100 : null;
    return {
      id: p.id,
      name: p.name,
      kind: p.kind,
      qty_per_motor: p.qty_per_motor,
      parent_id: p.parent_id,
      unit_price: unitPrice,
      line_total: lineTotal,
    };
  };

  const subAssemblies: Array<{ node: AssemblyNode; children: AssemblyNode[] }> = [];
  const directSubs = byParent.get('motor') ?? [];

  for (const sub of directSubs) {
    const subNode = toAssemblyNode(sub);
    const childrenParts = byParent.get(sub.id) ?? [];
    const childrenNodes = childrenParts.map(toAssemblyNode);
    subAssemblies.push({
      node: subNode,
      children: childrenNodes,
    });
  }

  return {
    id: 'motor',
    name: {
      en: 'Motor',
      zh: '电机整机',
      src_lang: 'en',
      status: { en: 'confirmed', zh: 'confirmed' },
    },
    sub_assemblies: subAssemblies,
  };
}

export function formatReceiptTime(val: string | null | undefined): string {
  if (!val) return '—';
  if (val.includes('T')) {
    const [d, t] = val.split('T');
    const timePart = t.slice(0, 5);
    return `${d} ${timePart}`;
  }
  return val;
}

// -- 3-D model of an assembly step ---------------------------------------------------------------------------------------

/** The CAD groups of the motor model (cad/fc_scripts/glb_writer.py families) that make one BOM part, by its canonical
 *  English name; a part without 3-D geometry (lead wire, insulation, packaging) has none. */
const PART_FAMILIES: Array<[string, string[]]> = [
  ['Stator core', ['Core']], ['Rotor core', ['Rotor']], ['Magnet', ['Magnets']], ['Coil-fixing ring', ['Ring_front', 'Ring_back']],
  ['Coil', ['Coils']], ['Shaft', ['Shaft']], ['Front holder', ['Holder_front']], ['Back holder', ['Holder_back']],
  ['Prop_fix', ['Prop_fix']], ['Bearing', ['Bearings_front', 'Bearings_back']], ['Socket head screw', ['Screws']],
  ['Phase board', ['PhaseBoard']],
];

export function partFamilies(nameEn: string): string[] {
  return PART_FAMILIES.find(([prefix]) => nameEn.startsWith(prefix))?.[1] ?? [];
}

/** The model groups a step shows (owner 2026-10-18): `own` = the families of the parts this step assembles, `context` =
 *  the families of the parts of every earlier step in the graph (its `after` ancestors) that this step does not add. */
export function stepModelFamilies(steps: readonly AssemblyStep[], stepId: string): { own: string[]; context: string[] } {
  const byId = new Map(steps.map((s) => [s.id, s] as const));
  const famsOf = (s: AssemblyStep): string[] => (s.parts_resolved ?? []).flatMap((p) => partFamilies(p.name.en || ''));
  const own = [...new Set(byId.has(stepId) ? famsOf(byId.get(stepId)!) : [])];
  const seen = new Set<string>([stepId]);
  const todo = [...(byId.get(stepId)?.after ?? [])];
  const earlier = new Set<string>();
  while (todo.length) {                                    // transitive `after`, cycle-safe
    const id = todo.pop()!;
    if (seen.has(id)) continue;
    seen.add(id);
    const s = byId.get(id);
    if (!s) continue;
    famsOf(s).forEach((f) => earlier.add(f));
    todo.push(...s.after);
  }
  return { own, context: [...earlier].filter((f) => !own.includes(f)) };
}
