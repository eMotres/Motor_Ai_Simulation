// Online NDA click-through: the pure helpers behind the signing dialog (no React, no fetch) so node --test can pin them.
//
// renderNda() is the client twin of motor_ai_sim/nda.py render(): the blocks {{#if country_is_cn_hk}}..{{/if}} and
// {{#unless country_is_cn_hk}}..{{/unless}} (one level, not nested), then the {{placeholders}}; runs of blank lines collapse.
// The server renders again and hashes ITS text; for a complete form both are the same text, which the dialog shows.

export const NDA_FIELDS = ['company', 'reg_no', 'country', 'address', 'signatory', 'title'] as const;
export type NdaField = (typeof NDA_FIELDS)[number];
export type NdaForm = Record<NdaField, string>;

export const emptyForm = (): NdaForm => ({ company: '', reg_no: '', country: '', address: '', signatory: '', title: '' });

/** Same normalisation as the server: whitespace runs become one space, ends trimmed. */
export const norm = (s: string): string => s.replace(/\s+/g, ' ').trim();

export const isCnHk = (country: string): boolean => ['CN', 'HK'].includes(country.trim().toUpperCase());

export const utcToday = (now: Date = new Date()): string => now.toISOString().slice(0, 10);

export interface NdaValues extends NdaForm { email: string; date: string; version: string; country_is_cn_hk: boolean }

export function values(form: NdaForm, email: string, date: string, version: string, blank = ''): NdaValues {
  const v = {} as NdaValues;
  for (const k of NDA_FIELDS) v[k] = norm(form[k]) || blank;
  v.country = v.country === blank ? blank : v.country.toUpperCase();
  v.email = email; v.date = date; v.version = version;
  v.country_is_cn_hk = isCnHk(form.country);
  return v;
}

/** `countryName` turns an ISO code into the name shown in the text (the server puts the code; the dialog may show a name). */
export function renderNda(text: string, v: NdaValues): string {
  const flags = v as unknown as Record<string, unknown>;
  const t = text.replace(/\{\{#(if|unless)\s+(\w+)\s*\}\}([\s\S]*?)\{\{\/\1\s*\}\}/g, (_m, kind: string, name: string, body: string) => {
    const truth = Boolean(flags[name]);
    return (kind === 'if' ? truth : !truth) ? body : '';
  }).replace(/\{\{\s*(\w+)\s*\}\}/g, (m, name: string) => (name in flags ? String(flags[name]) : m));
  return t.replace(/\n{3,}/g, '\n\n').replace(/^\n+|\n+$/g, '') + '\n';
}

export type NdaError = 'required' | 'country' | 'name' | 'accept';

/** Field errors of the dialog (keys are field names plus `typed_name` / `accept`); empty = may press Sign. */
export function formErrors(form: NdaForm, countries: readonly string[], typed: string, accepted: boolean): Record<string, NdaError> {
  const e: Record<string, NdaError> = {};
  for (const k of NDA_FIELDS) if (!norm(form[k])) e[k] = 'required';
  if (norm(form.country) && !countries.includes(form.country.trim().toUpperCase())) e.country = 'country';
  if (!norm(typed)) e.typed_name = 'required';
  else if (norm(form.signatory) && norm(typed).toLowerCase() !== norm(form.signatory).toLowerCase()) e.typed_name = 'name';
  if (!accepted) e.accept = 'accept';
  return e;
}

export interface MdRun { text: string; bold?: boolean; italic?: boolean }
export interface MdBlock { kind: 'h1' | 'h2' | 'p'; runs: MdRun[] }

function runsOf(s: string): MdRun[] {
  const out: MdRun[] = [];
  const re = /\*\*([\s\S]+?)\*\*|(?<![\w*])\*(?!\s)([\s\S]+?)(?<!\s)\*(?![\w*])/g;
  let at = 0;
  for (let m = re.exec(s); m; m = re.exec(s)) {
    if (m.index > at) out.push({ text: s.slice(at, m.index) });
    out.push(m[1] !== undefined ? { text: m[1], bold: true } : { text: m[2], italic: true });
    at = m.index + m[0].length;
  }
  if (at < s.length) out.push({ text: s.slice(at) });
  return out;
}

/** The agreement as blocks (headings, paragraphs with bold / italic runs) - rendered by React as text, never as HTML. */
export function mdBlocks(text: string): MdBlock[] {
  return text.trim().split(/\n\s*\n/).map((c) => c.trim()).filter(Boolean).map((c): MdBlock => {
    const m = /^(#{1,3})\s+([\s\S]*)$/.exec(c);
    return m ? { kind: m[1].length === 1 ? 'h1' : 'h2', runs: runsOf(m[2].trim()) } : { kind: 'p', runs: runsOf(c) };
  });
}
