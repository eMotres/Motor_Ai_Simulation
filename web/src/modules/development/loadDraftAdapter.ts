import type { FieldAnswer, SpecDraft } from './wizardState';
import type { LoadDraft } from './missionProfileState';

const answer = (draft: SpecDraft, id: string): FieldAnswer => draft.fields?.[id] ?? {};
const value = (draft: SpecDraft, id: string): unknown => answer(draft, id).value;
const object = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
const num = (v: unknown): number | undefined => typeof v === 'number' && Number.isFinite(v) ? v : undefined;

/** Read the schema's separate answer fields into the convenience model used by load UI. */
export function readLoadDraft(draft: SpecDraft): LoadDraft {
  const characteristic = object(value(draft, 'load_characteristic'));
  const conditions = object(characteristic.conditions);
  const requested = object(characteristic.requested_conditions);
  const samples = Array.isArray(characteristic.samples) ? characteristic.samples.map(object) : [];
  const domain = object(characteristic.valid_domain);
  const loadType = String(answer(draft, 'load_type').value ?? 'unknown') as LoadDraft['load_type'];
  const source = String(answer(draft, 'load_source').value ?? 'unknown') as LoadDraft['source'];
  const c = characteristic;
  const rpm = samples.map((s) => num(s.rpm)).filter((x): x is number => x !== undefined);
  const torque = samples.map((s) => num(s.torque_Nm)).filter((x): x is number => x !== undefined);
  const series = rpm.length && rpm.length === torque.length ? {
    rpm, torque_Nm: torque, shaft_power_W: rpm.map((r, i) => torque[i] * 2 * Math.PI * r / 60),
    extrapolated: samples.map((s) => s.extrapolated === true), quality: String(samples[0]?.quality ?? 'unknown'),
    ambient_C: num(conditions.temperature_C) ?? 25, altitude_m: num(conditions.altitude_m) ?? 0,
    pressure_Pa: num(conditions.pressure_Pa) ?? null,
    tested_domain_rpm: domain.min_rpm != null && domain.max_rpm != null ? [Number(domain.min_rpm), Number(domain.max_rpm)] as [number, number] : null,
    content_digest: typeof c.content_digest === 'string' ? c.content_digest : undefined,
    pitch_in: num(object(c.source_snapshot).pitch_in),
    source_urls: Array.isArray(object(c.source_snapshot).source_urls) ? (object(c.source_snapshot).source_urls as unknown[]).filter((u): u is string => typeof u === 'string') : [],
    fit: { ct: object(c.source_snapshot).fit_ct, cp: object(c.source_snapshot).fit_cp },
  } : undefined;
  const characteristicMatches = source === 'catalog'
    ? c.calculation_model === 'server_propeller_series' && c.catalog_type === loadType
    : source === 'manual' ? c.calculation_model === 'manual' && c.catalog_type === loadType : false;
  return {
    load_type: loadType, source, catalog_id: typeof c.catalog_id === 'string' ? c.catalog_id : undefined,
    catalog_metadata: object(c.source_snapshot),
    vendor: typeof object(c.source_snapshot).vendor === 'string' ? String(object(c.source_snapshot).vendor) : undefined,
    model: typeof object(c.source_snapshot).model === 'string' ? String(object(c.source_snapshot).model) : undefined,
    data_quality: typeof object(c.source_snapshot).data_quality === 'string' ? String(object(c.source_snapshot).data_quality) : undefined,
    power_data: typeof object(c.source_snapshot).power_data === 'string' ? String(object(c.source_snapshot).power_data) : undefined,
    temperature_C: num(requested.temperature_C) ?? num(conditions.temperature_C), altitude_m: num(requested.altitude_m) ?? num(conditions.altitude_m), pressure_Pa: Object.keys(requested).length ? num(requested.pressure_Pa) : num(conditions.pressure_Pa),
    load_inertia_kg_m2: num(answer(draft, 'load_inertia_kg_m2').value) ?? null,
    startup_mode: typeof answer(draft, 'startup_mode').value === 'string' ? String(answer(draft, 'startup_mode').value) : undefined,
    shaft_side: typeof c.shaft_side === 'string' ? c.shaft_side : undefined, series: characteristicMatches ? series : undefined,
    answer_status: Object.fromEntries(['load_type', 'load_source', 'load_inertia_kg_m2', 'startup_mode', 'load_characteristic'].map((id) => [id, answer(draft, id).status ?? 'missing'])) as LoadDraft['answer_status'],
  };
}

/** Map the load editor model to schema v2 answer wrappers; leave unrelated answers intact. */
export function writeLoadDraft(draft: SpecDraft, load: LoadDraft): SpecDraft {
  const fields = { ...(draft.fields ?? {}) };
  const set = (id: string, v: unknown) => {
    if (load.answer_status?.[id] === 'not_known_yet') { fields[id] = { ...fields[id], status: 'not_known_yet' }; return; }
    if (v === 'unknown' && (id === 'load_type' || id === 'load_source')) { fields[id] = { ...fields[id], status: 'missing' }; return; }
    if (fields[id] && Object.is(fields[id].value, v) && fields[id].status !== 'not_known_yet') return;
    if (v === undefined && fields[id]?.value === undefined) return;
    fields[id] = { ...fields[id], value: v, status: v === undefined || v === null || v === '' ? 'missing' : 'filled' };
  };
  set('load_type', load.load_type); set('load_source', load.source);
  set('load_inertia_kg_m2', load.load_inertia_kg_m2 ?? undefined);
  set('startup_mode', load.startup_mode ?? undefined);
  const currentCharacteristic = object(fields.load_characteristic?.value);
  const inactiveCharacteristic = load.answer_status?.load_type === 'not_known_yet' || load.answer_status?.load_source === 'not_known_yet' || load.load_type === 'unknown' || load.source === 'unknown';
  const requestedConditions = {
    ...(load.temperature_C != null ? { temperature_C: load.temperature_C } : {}),
    ...(load.altitude_m != null ? { altitude_m: load.altitude_m } : {}),
    ...(load.pressure_Pa != null ? { pressure_Pa: load.pressure_Pa } : {}),
  };
  const oldRequested = object(currentCharacteristic.requested_conditions);
  const frozenConditions = object(currentCharacteristic.conditions);
  const hasRequestedConditions = Object.keys(oldRequested).length > 0;
  const conditionsChanged = Object.entries(requestedConditions).some(([key, v]) => typeof v === 'number' && v !== frozenConditions[key]);
  const requestedConditionsToSave = hasRequestedConditions || conditionsChanged ? requestedConditions : {};
  if (load.answer_status?.load_characteristic === 'not_known_yet') {
    fields.load_characteristic = { ...fields.load_characteristic, value: { ...currentCharacteristic, ...(Object.keys(requestedConditionsToSave).length ? { requested_conditions: requestedConditionsToSave } : {}) }, status: 'not_known_yet' };
  } else if (inactiveCharacteristic) {
    fields.load_characteristic = { ...fields.load_characteristic, status: 'missing' };
  } else if (load.series && (load.source === 'catalog' || load.source === 'manual')) {
    const s = load.series;
    const characteristic = {
      ...(load.source === 'catalog' ? { catalog_id: load.catalog_id, catalog_type: 'air_propeller', medium: 'air', content_digest: s.content_digest, calculation_model: 'server_propeller_series' } : { catalog_type: load.load_type, medium: load.load_type === 'marine_propeller' || load.load_type === 'water_jet' ? 'water' : undefined, calculation_model: 'manual' }),
      conditions: { temperature_C: s.ambient_C, altitude_m: s.altitude_m, ...(s.pressure_Pa != null ? { pressure_Pa: s.pressure_Pa } : {}) }, ...(Object.keys(requestedConditions).length ? { requested_conditions: requestedConditions } : {}),
      units: 'rpm,N·m,W', shaft_side: load.shaft_side ?? 'load',
      valid_domain: s.tested_domain_rpm ? { min_rpm: s.tested_domain_rpm[0], max_rpm: s.tested_domain_rpm[1] } : undefined,
      samples: s.rpm.map((rpm, i) => ({ rpm, torque_Nm: s.torque_Nm[i], shaft_power_W: s.torque_Nm[i] * 2 * Math.PI * rpm / 60, quality: s.quality, extrapolated: s.extrapolated[i] === true })).filter((sample) => load.source !== 'catalog' || sample.rpm > 0),
      ...(load.source === 'catalog' ? { source_snapshot: { catalog_id: load.catalog_id, vendor: load.vendor, model: load.model, data_quality: load.data_quality, power_data: load.power_data, ...(s.pitch_in != null ? { pitch_in: s.pitch_in } : {}), ...(s.source_urls?.length ? { source_urls: s.source_urls } : {}), ...(object(s.fit).ct ? { fit_ct: object(s.fit).ct } : {}), ...(object(s.fit).cp ? { fit_cp: object(s.fit).cp } : {}) } } : {}),
    };
    set('load_characteristic', characteristic);
  } else if ((load.source === 'catalog' && (currentCharacteristic.calculation_model !== 'server_propeller_series' || currentCharacteristic.catalog_type !== load.load_type)) || (load.source === 'manual' && (currentCharacteristic.calculation_model !== 'manual' || currentCharacteristic.catalog_type !== load.load_type)) || load.source === 'unknown' || load.load_type === 'unknown') {
    fields.load_characteristic = { ...fields.load_characteristic, status: 'missing' };
  } else if (Object.keys(requestedConditionsToSave).length) {
    const updated = { ...currentCharacteristic, requested_conditions: requestedConditionsToSave };
    fields.load_characteristic = { ...fields.load_characteristic, value: updated, status: fields.load_characteristic?.status ?? 'filled' };
  }
  return { ...draft, fields };
}
