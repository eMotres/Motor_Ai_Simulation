import type { DevelopmentSchema, Localized, SchemaField, SchemaProperty } from './types';

export type DiffAtom = string | number | boolean | null;
export interface SpecificationDiffEntry {
  fieldId: string;
  path: string;
  property?: string;
  rowId?: string;
  namespace?: 'fields' | 'production' | 'mission_profile';
  before: DiffAtom;
  after: DiffAtom;
  change: 'added' | 'removed' | 'changed';
}

type Obj = Record<string, unknown>;
const INTERNAL = new Set(['provenance', 'schema_migrations', 'legacy_draft_snapshot', 'source_sha256', 'private_audit']);
const PUBLIC_METADATA = new Set([
  'catalog_id', 'catalog_type', 'vendor', 'model', 'data_quality', 'power_data', 'calculation_model',
  'medium', 'shaft_side', 'conditions', 'requested_conditions', 'valid_domain', 'samples', 'rpm',
  'torque_Nm', 'shaft_power_W', 'thrust_N', 'quality', 'extrapolated', 'pitch_in', 'diameter_in',
  'direct_drive_confirmed', 'temperature_C', 'altitude_m', 'pressure_Pa', 'min_rpm', 'max_rpm',
  'source_snapshot', 'row_id', 'phase', 'duration_s', 'speed_rpm', 'battery_voltage_V', 'torque_Nm',
  'power_W', 'duty_cycle_pct', 'notes', 'source', 'override', 'stale', 'catalog_revision', 'source_revision',
  'content_digest', 'digest', 'sha256', 'source_urls', 'fit_ct', 'fit_cp', 'c_ref', 'rpm_ref', 'k',
  'rpm_min', 'rpm_max', 'rms_rel', 'n_used',
]);
const isObject = (value: unknown): value is Obj => !!value && typeof value === 'object' && !Array.isArray(value);
const isAnswer = (value: unknown): value is Obj => isObject(value) && (typeof value.status === 'string' || 'not_known_yet' in value) && ('value' in value || 'status' in value);
const atom = (value: unknown): value is DiffAtom => value === null || typeof value === 'string' || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value));
const stable = (value: unknown): string => JSON.stringify(value);

function schemaFields(schema?: DevelopmentSchema): Map<string, SchemaField> {
  const out = new Map<string, SchemaField>();
  for (const section of schema?.sections ?? []) for (const field of section.fields ?? []) out.set(field.id, field);
  return out;
}

function publicPath(path: string): boolean {
  const parts = path.split('.');
  return parts.every((part) => !INTERNAL.has(part)) && parts.every((part) => PUBLIC_METADATA.has(part) || /^[A-Za-z0-9_-]+$/.test(part));
}

function emitValue(out: Map<string, DiffAtom>, value: unknown, prefix: string, allowMetadata = false): void {
  if (atom(value)) { out.set(prefix, value); return; }
  if (Array.isArray(value)) {
    value.forEach((item, index) => emitValue(out, item, `${prefix}.${index}`, allowMetadata));
    return;
  }
  if (!isObject(value)) return;
  for (const [key, child] of Object.entries(value)) {
    if (INTERNAL.has(key)) continue;
    if (allowMetadata && !PUBLIC_METADATA.has(key)) continue;
    emitValue(out, child, prefix ? `${prefix}.${key}` : key, allowMetadata);
  }
}

function flattenField(value: unknown, fieldId: string, allowMetadata: boolean): Map<string, DiffAtom> {
  const out = new Map<string, DiffAtom>();
  if (fieldId === 'mission_profile' && isObject(value)) {
    for (const [key, child] of Object.entries(value)) {
      if (!PUBLIC_METADATA.has(key) || key === 'row_id') continue;
      emitValue(out, child, key, true);
    }
    return out;
  }
  if (isAnswer(value)) {
    const status = value.status === 'not_known_yet' || value.not_known_yet === true ? 'not_known_yet' : value.status;
    if (typeof status === 'string') out.set('$status', status);
    if ('value' in value) emitValue(out, value.value, '', allowMetadata);
  } else emitValue(out, value, '', allowMetadata);
  return out;
}

function rows(value: unknown): Map<string, unknown> {
  const out = new Map<string, unknown>();
  if (!Array.isArray(value)) return out;
  value.forEach((row, index) => {
    const id = isObject(row) && typeof row.row_id === 'string' && row.row_id.trim() ? row.row_id : `legacy-${index}`;
    out.set(id, row);
  });
  return out;
}

/** Compares persisted specification semantics and emits only scalar display atoms. */
export function semanticSpecificationDiff(before: unknown, after: unknown, schemas?: { before?: DevelopmentSchema; after?: DevelopmentSchema }): SpecificationDiffEntry[] {
  if (!isObject(before) || !isObject(after)) return [];
  const leftFields = schemaFields(schemas?.before);
  const rightFields = schemaFields(schemas?.after);
  const entries: SpecificationDiffEntry[] = [];
  const addNamespace = (namespace: 'fields' | 'production', a: unknown, b: unknown) => {
    const left = isObject(a) ? a : {};
    const right = isObject(b) ? b : {};
      const ids = new Set([...rightFields.keys(), ...leftFields.keys(), ...Object.keys(left), ...Object.keys(right)]);
    for (const fieldId of ids) {
      if (INTERNAL.has(fieldId)) continue;
      const field = rightFields.get(fieldId) ?? leftFields.get(fieldId);
      const metadata = fieldId === 'load_characteristic' || field?.id === 'load_characteristic';
      const x = flattenField(left[fieldId], fieldId, metadata);
      const y = flattenField(right[fieldId], fieldId, metadata);
      const paths = new Set([...x.keys(), ...y.keys()]);
      for (const propertyPath of paths) {
        const hasBefore = x.has(propertyPath); const hasAfter = y.has(propertyPath);
        const oldValue = x.get(propertyPath); const newValue = y.get(propertyPath);
        if (hasBefore && hasAfter && stable(oldValue) === stable(newValue)) continue;
        const property = propertyPath && propertyPath !== '$status' ? propertyPath : undefined;
        const displayPath = propertyPath === '$status' ? 'status' : propertyPath || fieldId;
        if (!publicPath(displayPath)) continue;
        entries.push({ fieldId, path: displayPath, property, namespace,
          before: hasBefore ? oldValue! : null, after: hasAfter ? newValue! : null,
          change: !hasBefore ? 'added' : !hasAfter ? 'removed' : 'changed' });
      }
    }
  };
  addNamespace('fields', before.fields, after.fields);
  addNamespace('production', before.production, after.production);

  const leftRows = rows(before.mission_profile); const rightRows = rows(after.mission_profile);
  const remapLegacyRows = (target: Map<string, unknown>, counterpart: Map<string, unknown>, migrationDraft: Obj) => {
    const provenance = isObject(migrationDraft.provenance) ? migrationDraft.provenance : {};
    const records = Array.isArray(provenance.schema_migrations) ? provenance.schema_migrations : [];
    for (const migration of records) {
      if (!isObject(migration) || !Array.isArray(migration.mission_row_index_map)) continue;
      for (const mapping of migration.mission_row_index_map) {
        if (!isObject(mapping) || typeof mapping.row_id !== 'string' || typeof mapping.legacy_index !== 'number') continue;
        const legacyId = `legacy-${mapping.legacy_index}`;
        if (target.has(legacyId) && !target.has(mapping.row_id) && counterpart.has(mapping.row_id)) {
          const legacyRow = target.get(legacyId); target.delete(legacyId); target.set(mapping.row_id, legacyRow);
        }
      }
    }
  };
  remapLegacyRows(leftRows, rightRows, after);
  remapLegacyRows(rightRows, leftRows, before);
  const rowIds = new Set([...leftRows.keys(), ...rightRows.keys()]);
  for (const rowId of rowIds) {
    const hasBefore = leftRows.has(rowId); const hasAfter = rightRows.has(rowId);
    const a = leftRows.get(rowId); const b = rightRows.get(rowId);
    const x = flattenField(a, 'mission_profile', true); const y = flattenField(b, 'mission_profile', true);
    const paths = new Set([...x.keys(), ...y.keys()]);
    for (const property of paths) {
      if (property === '$status') continue;
      const oldValue = x.get(property); const newValue = y.get(property);
      if (hasBefore && hasAfter && stable(oldValue) === stable(newValue)) continue;
      if (!publicPath(property)) continue;
      entries.push({ fieldId: 'mission_profile', path: property || 'row', property: property || undefined, rowId, namespace: 'mission_profile',
        before: hasBefore && x.has(property) ? oldValue! : null, after: hasAfter && y.has(property) ? newValue! : null,
        change: !hasBefore ? 'added' : !hasAfter ? 'removed' : 'changed' });
    }
  }
  const statusBefore = before.mission_profile_status;
  const statusAfter = after.mission_profile_status;
  if (atom(statusBefore) && atom(statusAfter) && stable(statusBefore) !== stable(statusAfter)) {
    entries.push({ fieldId: 'mission_profile', path: 'status', property: 'status', namespace: 'mission_profile', before: statusBefore, after: statusAfter, change: 'changed' });
  }
  return entries;
}

function localized(label: Localized | undefined, locale: string): string | undefined {
  if (typeof label === 'string') return label;
  return label?.[locale as 'en' | 'zh-CN'] ?? label?.en ?? label?.['zh-CN'];
}

/** Translate schema choices and UI states, while retaining customer-entered text verbatim. */
export function formatDiffValue(value: DiffAtom, entry: SpecificationDiffEntry, schema: DevelopmentSchema | undefined, locale: string, t: (key: string) => string): string {
  if (value == null) return t('versionMissing');
  if (typeof value === 'number') return value.toLocaleString(locale);
  if (typeof value === 'boolean') return t(value ? 'yes' : 'no');
  if (entry.path === 'status') return t(value === 'not_known_yet' ? 'unknown' : `answerStatus.${value}`);
  const field = schemaFields(schema).get(entry.fieldId);
  let shape: SchemaField | SchemaProperty | undefined = field;
  for (const part of entry.property?.split('.') ?? []) shape = shape?.properties?.find(property => property.id === part);
  const choice = shape?.options?.find(option => typeof option === 'string' ? option === value : option.value === value);
  if (choice && typeof choice !== 'string') return localized(choice.label, locale) ?? value;
  const key = entry.property?.split('.').at(-1);
  if (key === 'source' && ['manual', 'catalog'].includes(value)) return t(`mission.source.${value}`);
  if (key === 'catalog_type' && ['air_propeller', 'marine_propeller', 'water_jet', 'wheel', 'pump', 'engine_driven_generator'].includes(value)) return t(`load.types.${value}`);
  if (key === 'shaft_side' && ['motor', 'load'].includes(value)) return t(`load.${value === 'motor' ? 'motorSide' : 'loadSide'}`);
  if (['quality', 'data_quality', 'power_data'].includes(key ?? '') && ['manual', 'measured', 'estimated', 'reference', 'unknown', 'torque_measured', 'measured_torque', 'estimated_power', 'none'].includes(value)) return t(`catalog.quality.${value}`);
  return value;
}

/** Formats a semantic path using schema labels and units, with a stable fallback for unknown fields. */
export function formatDiffLabel(entry: SpecificationDiffEntry, schema: DevelopmentSchema | undefined, locale: string, translate?: (key: string) => string): string {
  const fields = schemaFields(schema);
  const field = fields.get(entry.fieldId);
  const base = localized(field?.short_label ?? field?.label, locale) ?? entry.fieldId;
  const fallback = (id: string) => {
    const value = translate?.(`versionDiff.property.${id}`);
    return value && value !== `versionDiff.property.${id}` ? value : id;
  };
  if (!entry.property || entry.property === 'status') return entry.property === 'status' ? `${base} · ${fallback('status')}` : base;
  const parts = entry.property.split('.');
  let label = base;
  let properties: SchemaProperty[] | undefined = field?.properties ?? field?.items?.properties ?? field?.columns;
  let last: SchemaProperty | undefined;
  for (const part of parts) {
    last = properties?.find((property) => property.id === part);
    label += ` · ${localized(last?.short_label ?? last?.label, locale) ?? fallback(part)}`;
    properties = last?.properties;
  }
  const unit = last?.unit ?? (parts.length === 1 ? (field?.properties ?? field?.items?.properties ?? field?.columns)?.find((property) => property.id === parts[0])?.unit : undefined);
  return unit ? `${label} (${unit})` : label;
}
