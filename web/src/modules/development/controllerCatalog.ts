// The controller field of the specification (2_4) picks its controller from the catalogue of complete commercial
// controllers (GET /api/controllers/catalog) plus "own design / other" free text.  Pure logic, no React.
//
// Rules (owner 2026-10-26):
//  - choosing a catalogue entry fills the brand / model automatically and pre-fills the fields it knows (2_6 phase current
//    limits and peak duration, 2_7 switching frequency, 2_8 position sensor, 2_5 power devices if the entry names them) as
//    EDITABLE values; a value that still equals the catalogue value is shown as "from catalogue <id>";
//  - changing the controller later updates only the fields that still hold the OLD catalogue values;
//  - the power devices are a property of the controller: an entry that does not name them gets ONE warning;
//  - the battery's maximum voltage above the controller's supply maximum is a loud validation error.
import type { SpecDraft } from './wizardState.ts';

export interface CatalogCell { value: string | number; basis?: string; page?: number }
export interface CatalogController {
  id: string; brand: string; model: string; revision?: string | null; display_name: string; manufacturer?: string | null;
  aliases?: string[];
  supply_max_V: number | null;
  supply_voltage_V?: Record<string, { value?: number; page?: number } | null>;
  power_devices_known: boolean;
  power_devices?: Record<string, unknown>;
  prefill: Record<string, CatalogCell>;
  [key: string]: unknown;
}
/** The text the "own design / other" menu entry stores. */
export const OWN_DESIGN = 'Own design';
/** Fields a catalogue entry pre-fills: `<field>.<property>` (a bare field id is the whole value). */
export const CATALOG_PREFILL_PATHS = ['2_5.device_type', '2_5.voltage_class_V', '2_6.continuous_Arms', '2_6.peak_Arms', '2_6.peak_duration_s', '2_7.switching_frequency_Hz', '2_8'];
/** What the supplier answer used to be: a choice of who provides the controller, not a company. */
export const LEGACY_SUPPLIER_CHOICES = ['customer', 'motres', 'third party'];

const record = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
const blank = (v: unknown) => v === undefined || v === null || (typeof v === 'string' && !v.trim()) || (typeof v === 'number' && Number.isNaN(v));
const norm = (text: unknown) => String(text ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '');
const same = (a: unknown, b: unknown) => !blank(a) && !blank(b) && (String(a).trim() === String(b).trim() || (Number(a) === Number(b) && !Number.isNaN(Number(a))));

export function controllerAnswer(draft: SpecDraft): { value: Record<string, unknown>; status?: string } {
  const answer = record((draft.fields as Record<string, unknown> | undefined)?.['2_4']);
  return { value: record(answer.value), status: answer.status as string | undefined };
}
export function catalogEntry(catalog: CatalogController[], id: unknown): CatalogController | undefined {
  return typeof id === 'string' && id ? catalog.find((entry) => entry.id === id) : undefined;
}
/** The entry a free-text controller name means: an exact name (case, spaces and punctuation ignored) of one entry only. */
export function matchCatalog(text: unknown, catalog: CatalogController[]): CatalogController | undefined {
  const wanted = norm(text);
  if (!wanted) return undefined;
  const hits = catalog.filter((entry) => [entry.display_name, `${entry.brand} ${entry.model}`, `${entry.brand} ${entry.model} ${entry.revision ?? ''}`, `${entry.model} ${entry.revision ?? ''}`, ...(entry.aliases ?? [])].map(norm).includes(wanted));
  return hits.length === 1 ? hits[0] : undefined;
}
/** The controller the draft is linked to (stored link first, then an exact free-text name). */
export function linkedController(draft: SpecDraft, catalog: CatalogController[]): CatalogController | undefined {
  const value = controllerAnswer(draft).value;
  return catalogEntry(catalog, value.catalog_id) ?? (value.catalog_id ? undefined : matchCatalog(value.manufacturer_model, catalog));
}

function readPath(draft: SpecDraft, path: string): unknown {
  const [field, prop] = path.split('.');
  const answer = record((draft.fields as Record<string, unknown> | undefined)?.[field]);
  return prop ? record(answer.value)[prop] : answer.value;
}
function writePath(draft: SpecDraft, path: string, value: unknown): SpecDraft {
  const [field, prop] = path.split('.');
  const fields = { ...(draft.fields ?? {}) } as Record<string, unknown>;
  const answer = record(fields[field]);
  if (answer.status === 'not_known_yet') return draft;                    // a field marked "not known yet" is left alone
  if (!prop) { fields[field] = { ...answer, value, status: blank(value) ? 'missing' : 'filled' }; return { ...draft, fields } as SpecDraft; }
  const obj = { ...record(answer.value), [prop]: value };
  const anyValue = Object.values(obj).some((v) => !blank(v));
  fields[field] = { ...answer, value: obj, status: anyValue ? 'filled' : 'missing' };
  return { ...draft, fields } as SpecDraft;
}

export type ControllerChoice = { kind: 'catalog'; entry: CatalogController } | { kind: 'own'; text?: string } | { kind: 'text'; text: string };

/** The draft after the controller is changed.  Only the values that are blank or still hold the OLD catalogue entry's
 *  values are replaced (and cleared when the new entry does not know them); everything the customer typed stays. */
export function applyControllerChoice(draft: SpecDraft, choice: ControllerChoice, catalog: CatalogController[]): SpecDraft {
  if (choice.kind === 'text') {
    const hit = matchCatalog(choice.text, catalog);
    if (hit) return applyControllerChoice(draft, { kind: 'catalog', entry: hit }, catalog);
    // free typing: unlink only, nothing else is touched
    const current = controllerAnswer(draft);
    const value = { ...current.value, manufacturer_model: choice.text };
    delete value.catalog_id;
    return writeAnswer(draft, value);
  }
  const current = controllerAnswer(draft).value;
  const old = catalogEntry(catalog, current.catalog_id);
  const next = choice.kind === 'catalog' ? choice.entry : undefined;
  let out = draft;
  for (const path of CATALOG_PREFILL_PATHS) {
    const had = readPath(out, path);
    const oldValue = old?.prefill?.[path]?.value;
    const holdsOld = blank(had) || (!blank(oldValue) && same(had, oldValue));
    if (!holdsOld) continue;
    const newValue = next?.prefill?.[path]?.value;
    if (blank(newValue) && blank(had)) continue;
    out = writePath(out, path, blank(newValue) ? '' : newValue);
  }
  const value = { ...controllerAnswer(out).value };
  const supplier = value.supplier;
  const supplierIsAutomatic = blank(supplier) || same(supplier, old?.brand) || (typeof supplier === 'string' && LEGACY_SUPPLIER_CHOICES.includes(supplier.trim().toLowerCase()));
  if (next) {
    value.manufacturer_model = next.display_name;
    value.catalog_id = next.id;
    if (supplierIsAutomatic) value.supplier = next.brand;
  } else {
    delete value.catalog_id;
    value.manufacturer_model = choice.kind === 'own' && choice.text ? choice.text : OWN_DESIGN;
    if (same(supplier, old?.brand)) value.supplier = '';
  }
  return writeAnswer(out, value);
}
function writeAnswer(draft: SpecDraft, value: Record<string, unknown>): SpecDraft {
  const fields = { ...(draft.fields ?? {}) } as Record<string, unknown>;
  const answer = record(fields['2_4']);
  const clean = Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined));
  const filled = !blank(clean.manufacturer_model);
  fields['2_4'] = { ...answer, value: clean, status: answer.status === 'not_known_yet' ? 'not_known_yet' : filled ? 'filled' : 'missing' };
  return { ...draft, fields } as SpecDraft;
}

/** Link the draft to the catalogue the way the server does on read (free-text name -> catalogue id; the old supplier CHOICE
 *  shows the brand and is kept as `supplier_legacy`).  Used while the page holds a draft the server has not linked yet. */
export function withCatalogLink(draft: SpecDraft, catalog: CatalogController[]): SpecDraft {
  const value = controllerAnswer(draft).value;
  if (!Object.keys(value).length) return draft;
  const entry = catalogEntry(catalog, value.catalog_id) ?? (value.catalog_id ? undefined : matchCatalog(value.manufacturer_model, catalog));
  if (!entry) return draft;
  const next = { ...value };
  if (!next.catalog_id) next.catalog_id = entry.id;
  const supplier = next.supplier;
  if (typeof supplier === 'string' && LEGACY_SUPPLIER_CHOICES.includes(supplier.trim().toLowerCase())) { if (blank(next.supplier_legacy)) next.supplier_legacy = supplier; next.supplier = entry.brand; }
  else if (blank(supplier)) next.supplier = entry.brand;
  return JSON.stringify(next) === JSON.stringify(value) ? draft : writeAnswer(draft, next);
}

/** `"DTI HV-850 (Rev 4)"` when a field's stored value is still the catalogue value (the tooltip says "from catalogue <id>"). */
export function prefillSource(draft: SpecDraft, path: string, catalog: CatalogController[]): string | null {
  const entry = linkedController(draft, catalog);
  const cell = entry?.prefill?.[path];
  return entry && cell && same(readPath(draft, path), cell.value) ? entry.id : null;
}
/** The provenance of every pre-filled field that still holds the catalogue value: `{ '2_6.peak_Arms': { id, basis, page } }`. */
export function prefillProvenance(draft: SpecDraft, catalog: CatalogController[]): Record<string, { id: string; basis?: string; page?: number }> {
  const entry = linkedController(draft, catalog);
  const out: Record<string, { id: string; basis?: string; page?: number }> = {};
  if (!entry) return out;
  for (const path of CATALOG_PREFILL_PATHS) {
    const cell = entry.prefill?.[path];
    if (cell && same(readPath(draft, path), cell.value)) out[path] = { id: entry.id, basis: cell.basis, page: cell.page };
  }
  return out;
}

/** The battery's maximum voltage: the stated pack maximum, else cells x the cell maximum. */
export function batteryMaxV(draft: SpecDraft): number | null {
  const value = record(record((draft.fields as Record<string, unknown> | undefined)?.['2_1']).value);
  const num = (v: unknown) => typeof v === 'number' && Number.isFinite(v) ? v : (typeof v === 'string' && v.trim() && Number.isFinite(Number(v)) ? Number(v) : null);
  const top = num(value.voltage_max_V);
  if (top !== null) return top;
  const cells = num(value.series_cells), cell = num(value.cell_voltage_max_V);
  return cells && cell ? Math.round(cells * cell * 1000) / 1000 : null;
}
/** A loud validation error when the battery maximum exceeds the linked controller's supply maximum (806 V pack, 800 V controller). */
export function batteryVsController(draft: SpecDraft, catalog: CatalogController[]): { max: number; limit: number; controller: string } | null {
  const entry = linkedController(draft, catalog);
  const top = batteryMaxV(draft);
  const limit = entry?.supply_max_V;
  return entry && typeof limit === 'number' && top !== null && top > limit ? { max: top, limit, controller: entry.display_name } : null;
}
/** ONE warning when the linked controller's power devices are not known (the catalogue never infers them). */
export function powerDevicesUnknown(draft: SpecDraft, catalog: CatalogController[]): boolean {
  const entry = linkedController(draft, catalog);
  return !!entry && !entry.power_devices_known;
}
/** What the customer stated before as power devices (2_5, no longer asked): shown read-only in the controller's tooltip. */
export function statedPowerDevices(draft: SpecDraft): string {
  const answer = record((draft.fields as Record<string, unknown> | undefined)?.['2_5']);
  const value = record(answer.value);
  return [value.device_type, blank(value.voltage_class_V) ? '' : `${value.voltage_class_V} V`].filter((x) => !blank(x)).join(', ');
}
/** The company names a supplier menu offers: the brands of the catalogue. */
export function catalogBrands(catalog: CatalogController[]): string[] {
  return Array.from(new Set(catalog.map((entry) => entry.brand).filter(Boolean)));
}
