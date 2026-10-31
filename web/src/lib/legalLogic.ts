// Legal pages (/terms, /privacy, /platform-agreement) and the sign-up acceptance: pure helpers (no DOM, no network, no React) so
// node --test can run them directly.  The texts come from GET /api/legal/{doc}?lang= (src/motor_ai_sim/legal_texts.py), verbatim;
// this file only (a) knows the routes, (b) turns the Markdown of a text into blocks the page renders as React elements (never HTML),
// (c) decides when the acceptance box lets a sign-up button go and what the request carries, (d) decides when the existing-user
// dialog is shown.

export type LegalPageKind = 'terms' | 'privacy' | 'platform-agreement';
export type LegalLang = 'en' | 'zh-CN';

/** The server document names behind a page: the agreement page shows the agreement and its Schedules one after the other. */
export const PAGE_DOCS: Record<LegalPageKind, readonly string[]> = {
  terms: ['terms'],
  privacy: ['privacy'],
  'platform-agreement': ['platform-agreement', 'schedules'],
};

/** The version of the texts this build shows and a sign-up accepts.  Must equal legal_texts.TERMS_VERSION on the server (a node test
 *  compares the two): a request with another version is refused with 422 `terms_required`. */
export const TERMS_VERSION = 'v0.4';

export const LEGAL_PAGES: readonly LegalPageKind[] = ['terms', 'privacy', 'platform-agreement'];

const ZH_PREFIX = '/zh-cn';

/** /terms, /privacy, /platform-agreement and the same under /zh-cn/, with or without a trailing slash. */
export function matchLegalPage(pathname: unknown): LegalPageKind | null {
  if (typeof pathname !== 'string') return null;
  const m = /^(?:\/zh-cn)?\/(terms|privacy|platform-agreement)\/?$/i.exec(pathname);
  return m ? (m[1].toLowerCase() as LegalPageKind) : null;
}

export function isLegalPath(pathname: unknown): boolean {
  return matchLegalPage(pathname) !== null;
}

/** The in-app address of a page in a language: `/terms`, `/zh-cn/terms` (the same prefix the landing and /preferences use). */
export function legalPath(kind: LegalPageKind, lang: LegalLang): string {
  return `${lang === 'zh-CN' ? ZH_PREFIX : ''}/${kind}`;
}

/** A build-time override (VITE_TERMS_URL / VITE_PRIVACY_URL) wins over the in-app page; blank = not set. */
export function resolveLegalUrl(envValue: unknown, kind: LegalPageKind, lang: LegalLang): string {
  const s = typeof envValue === 'string' ? envValue.trim() : '';
  return s || legalPath(kind, lang);
}

// ── Markdown → blocks ───────────────────────────────────────────────────────────────────────────────────────────────────

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'strong'; c: Inline[] }
  | { t: 'em'; c: Inline[] }
  | { t: 'link'; href: string; c: Inline[] };

export type Block =
  | { t: 'h'; level: 1 | 2 | 3; c: Inline[]; id: string }
  | { t: 'p'; c: Inline[] }
  | { t: 'ul'; items: Inline[][] }
  | { t: 'table'; head: Inline[][]; rows: Inline[][][] };

/** Only http(s), mailto and same-site paths are links; anything else (javascript:, data:) is shown as plain text. */
export function safeHref(href: string): string | null {
  const h = href.trim();
  return /^(https?:\/\/|mailto:|\/(?!\/))/i.test(h) ? h : null;
}

const INLINE_SRC = /\*\*([\s\S]+?)\*\*|(?<![\w*])\*(?!\s)([\s\S]+?)(?<!\s)\*(?![\w*])|\[([^\]]+)\]\(([^)\s]+)\)/;

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let at = 0;
  const INLINE_RE = new RegExp(INLINE_SRC.source, 'g');       // one per call: the nested parseInline below must not share lastIndex
  for (let m = INLINE_RE.exec(s); m; m = INLINE_RE.exec(s)) {
    if (m.index > at) out.push({ t: 'text', v: s.slice(at, m.index) });
    if (m[1] !== undefined) out.push({ t: 'strong', c: parseInline(m[1]) });
    else if (m[2] !== undefined) out.push({ t: 'em', c: parseInline(m[2]) });
    else {
      const href = safeHref(m[4]);
      if (href) out.push({ t: 'link', href, c: parseInline(m[3]) });
      else out.push({ t: 'text', v: m[0] });
    }
    at = m.index + m[0].length;
  }
  if (at < s.length) out.push({ t: 'text', v: s.slice(at) });
  return out.reduce<Inline[]>((acc, n) => {                  // adjacent plain runs (a rejected link) become one
    const last = acc[acc.length - 1];
    if (n.t === 'text' && last && last.t === 'text') last.v += n.v; else acc.push(n);
    return acc;
  }, []);
}

/** Plain text of inline runs (anchors, the "In short" excerpt, tests). */
export function inlineText(c: Inline[]): string {
  return c.map((n) => (n.t === 'text' ? n.v : inlineText(n.c))).join('');
}

const cells = (line: string): string[] => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
const isRule = (line: string): boolean => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

/** Section anchor of a heading: its leading number ("1", "13") or Schedule label, else a slug; stable across languages. */
export function headingId(text: string): string {
  const num = /^(\d+(?:\.\d+)*)\.?\s/.exec(text);
  if (num) return `s${num[1].replace(/\./g, '-')}`;
  const sch = /^(?:Schedule|附件)\s*(\d+)/i.exec(text);
  if (sch) return `schedule-${sch[1]}`;
  return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 40);
}

/**
 * The text as the page shows it.  The agreement carries the click-through's conditional blocks and company placeholders; on the
 * read-only page both section 13 variants are shown (each labels itself "Variant A / B") and a placeholder is a blank line.
 * Nothing else is touched: this is a display transform, the served text and its hash stay verbatim.
 */
export function displayText(text: string): string {
  return text
    .replace(/\{\{[#/](?:if|unless)[^}]*\}\}\n?/g, '')
    .replace(/\{\{\s*\w+\s*\}\}/g, '________')
    .replace(/\n{3,}/g, '\n\n');
}

/** Markdown (headings, paragraphs, `- ` lists, one-level tables, **bold**, *italic*, [links](url)) → blocks. */
export function parseLegalMarkdown(text: string): Block[] {
  const lines = text.replace(/\r/g, '').split('\n');
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ t: 'p', c: parseInline(para.join(' ')) });
    para = [];
  };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line.trim()) { flush(); continue; }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      flush();
      const c = parseInline(h[2].trim());
      blocks.push({ t: 'h', level: h[1].length as 1 | 2 | 3, c, id: headingId(inlineText(c)) });
      continue;
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && isRule(lines[i + 1])) {
      flush();
      const head = cells(line).map(parseInline);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(cells(lines[i]).map(parseInline)); i += 1; }
      i -= 1;
      blocks.push({ t: 'table', head, rows });
      continue;
    }
    const li = /^\s*[-*]\s+(.*)$/.exec(line);
    if (li) {
      flush();
      const items: Inline[][] = [parseInline(li[1])];
      while (i + 1 < lines.length && /^\s*[-*]\s+/.test(lines[i + 1])) { i += 1; items.push(parseInline(lines[i].replace(/^\s*[-*]\s+/, ''))); }
      blocks.push({ t: 'ul', items });
      continue;
    }
    // a lettered line "(a) ..." directly under a paragraph is its own line, not a continuation of it
    if (para.length && /^\(\w\)\s/.test(line.trim())) flush();
    para.push(line.trim());
  }
  flush();
  return blocks;
}

/** The "In short" paragraph of the Terms of Use (the first paragraph that opens with a bold label ending in a colon), plain text with
 *  its bold label kept as `label`; null when the text has none. */
export function inShort(terms: string): { label: string; body: string } | null {
  for (const b of parseLegalMarkdown(terms)) {
    if (b.t !== 'p') continue;
    const first = b.c[0];
    if (first && first.t === 'strong') {
      const label = inlineText(first.c);
      if (/[:：]\s*$/.test(label)) return { label, body: inlineText(b.c.slice(1)).trim() };
    }
  }
  return null;
}

// ── the acceptance box ──────────────────────────────────────────────────────────────────────────────────────────────────

/** Agreement section 2.3: in which capacity an account accepts.  `business` = a business it is authorised to bind (the name is then
 *  required), `sole_trader` = itself as a registered sole trader / self-employed professional, `other` = neither (for example an
 *  employee or contractor without authority to bind its employer or customer). */
export const CAPACITIES = ['business', 'sole_trader', 'other'] as const;
export type Capacity = (typeof CAPACITIES)[number];

/** The acceptance block of a sign-up / the existing-user dialog: the capacity (REQUIRED radio), the country where the business is
 *  established (ISO code, required), the business name (required for `business`, optional for `sole_trader`, not asked for `other`), the
 *  box (initially UNTICKED) and, in the existing-user dialog only, the optional second box for the data entered before today
 *  (agreement 7.9, initially UNTICKED). */
export interface AcceptanceForm { capacity: Capacity | ''; country: string; business: string; ticked: boolean; earlier: boolean }

export const emptyAcceptance = (): AcceptanceForm => ({ capacity: '', country: '', business: '', ticked: false, earlier: false });

/** Mainland China and the Hong Kong SAR: section 13 variant B (Hong Kong law, HKIAC) of the Platform Agreement; everything else is variant A.
 *  Same rule as the NDA / agreement forms (`isCnHk` in lib/nda.ts, `country_is_cn_hk` in the texts). */
export function forumVariant(country: string): 'A' | 'B' {
  return ['CN', 'HK'].includes(country.trim().toUpperCase()) ? 'B' : 'A';
}

/** The business name is asked for `business` (required) and `sole_trader` (optional); `other` has none. */
export function asksBusinessName(capacity: Capacity | ''): boolean {
  return capacity === 'business' || capacity === 'sole_trader';
}

/** The block is complete: a capacity is chosen, a country is chosen, a `business` capacity names its business AND the box is ticked. */
export function acceptanceComplete(f: AcceptanceForm): boolean {
  if (f.ticked !== true || f.country.trim() === '') return false;
  if (!(CAPACITIES as readonly string[]).includes(f.capacity)) return false;
  return f.capacity !== 'business' || f.business.trim() !== '';
}

/** The two labels as the person saw them (agreement 15.3 keeps them with the record). */
export interface AcceptLabels { accept?: string; election?: string }

export interface AcceptancePayload {
  accept_terms: boolean; terms_version: string; country: string; business_name: string; capacity: string;
  accept_label?: string; historical_opt_in?: boolean; election_label?: string;
}

/** What a sign-up request (e-mail form, Google, the existing-user dialog) carries.  The server refuses a new account without a ticked
 *  box for THIS version (422 `terms_required`), without a country (`country_required`), a capacity (`capacity_required`) or, for
 *  `business`, a name (`business_name_required`), so an incomplete block sends nothing it could mistake for consent.  The earlier-data
 *  election goes only with `existing: true` (a new account has no earlier data). */
export function acceptancePayload(f: AcceptanceForm, version: string, opts: { existing?: boolean; labels?: AcceptLabels } = {}): AcceptancePayload {
  const ticked = f.ticked === true;
  const out: AcceptancePayload = {
    accept_terms: ticked, terms_version: ticked ? version : '', country: f.country.trim().toUpperCase(),
    business_name: asksBusinessName(f.capacity) ? f.business.trim() : '', capacity: f.capacity,
  };
  if (opts.labels?.accept) out.accept_label = opts.labels.accept;
  if (opts.existing) {
    out.historical_opt_in = f.earlier === true;
    if (opts.labels?.election) out.election_label = opts.labels.election;
  }
  return out;
}

/** The pieces of the box sentence in common.json `legal.accept`: the capacity-specific `lead` / `mid` and the shared link texts. */
export interface AcceptStrings {
  lead: Record<Capacity, string>; mid: Record<Capacity, string>; terms: string; sep1: string; agreement: string; privacy: string; tail: string;
}

/** The sentence beside the box as plain text, for the capacity chosen (what the record keeps as the label shown). */
export function acceptLabelText(a: AcceptStrings, capacity: Capacity): string {
  return [a.lead[capacity], a.terms, a.sep1, a.agreement, a.mid[capacity], a.privacy, a.tail].join('');
}

/** May the sign-up button (or the Google button) be used?  Only a CREATING flow needs the block; signing in to an existing account
 *  never does.  `busy` keeps its own say. */
export function signupAllowed(opts: { creating: boolean; form: AcceptanceForm; busy?: boolean }): boolean {
  if (opts.busy) return false;
  return !opts.creating || acceptanceComplete(opts.form);
}

/** The short hint beside a disabled button: shown while a creating flow has an incomplete block. */
export function showTermsHint(opts: { creating: boolean; form: AcceptanceForm }): boolean {
  return opts.creating && !acceptanceComplete(opts.form);
}

/** The server answered 422 `terms_required` / `country_required` / `capacity_required` / `business_name_required` (a Google sign-in of a
 *  brand-new account that was started without the block): the dialog switches to "create account" and shows it. */
export function isTermsRequired(err: unknown): boolean {
  const code = !!err && typeof err === 'object' ? (err as { code?: unknown }).code : undefined;
  return code === 'terms_required' || code === 'country_required' || code === 'capacity_required' || code === 'business_name_required';
}

// ── existing accounts ───────────────────────────────────────────────────────────────────────────────────────────────────

export interface LegalStatus { version: string; required: boolean; accepted_version?: string | null; required_from?: string | null }

/** Show the "Updated terms" dialog?  Once per sign-in: the server says it is needed and the person did not press "Later" in this
 *  session (`laterFor` is the id of the session they dismissed it for). */
export function shouldAskTerms(status: LegalStatus | null | undefined, sessionId: string | null, laterFor: string | null): boolean {
  if (!status || status.required !== true) return false;
  return !(sessionId && laterFor === sessionId);
}

/** The owner's deadline ("Later" stops working): `YYYY-MM-DD` when the server reports one, else null.  The dialog shows it. */
export function termsDeadline(status: { required_from?: string | null } | null | undefined): string | null {
  const d = status?.required_from;
  return typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
}
