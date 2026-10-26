/** The review screen's summary cards: for each step, the answered fields as a label and a short value.  Pure functions
 *  (no React), tested in node.  Values are formatted from the stored answer and the schema field (unit, menu labels). */
import { CONTINUOUS } from '../../keyPoints.ts';
import { coolingMedium, coolingPropertyIds, suggestionDisplay } from '../../specChoices.ts';
import type { SchemaProperty, Localized } from '../../types';
import type { SpecificationField } from '../../specificationLayout';
import { visibleProperties } from '../../specGrid.ts';
import { fieldApplies, type FieldAnswer, type SpecDraft } from '../../wizardState.ts';
import { deliveryConflicts } from '../../reviewPresentation.ts';
import { formatNumber, operatingRows, pointRoles, shaftPowerOf, formatPower } from './operatingPoints.ts';
import { answerPresent, type WizardStep } from './steps.ts';
import type { WizardProfile } from './profile.ts';

export type Translate = (key: string, args?: Record<string, unknown>) => string;
export interface ReviewItem { id: string; label: string; value: string; note?: string; unknown?: boolean; wide?: boolean }

export const localized = (v: Localized | unknown, lang: string): string => {
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object') { const o = v as Record<string, unknown>; return String(o[lang === 'zh-CN' ? 'zh-CN' : 'en'] ?? o.en ?? ''); }
  return '';
};
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const blank = (v: unknown) => v === undefined || v === null || v === '' || (typeof v === 'string' && !v.trim());

type Shape = Pick<SchemaProperty, 'type' | 'unit' | 'options' | 'suggestions' | 'allow_range' | 'properties' | 'items' | 'label' | 'short_label'> & { choices?: SchemaProperty['options'] };

/** One stored value as a short readable string; '' when it holds nothing.  A range stored before the one-value rule reads
 *  as its maximum. */
export function formatValue(shape: Shape, value: unknown, lang: string, t: Translate): string {
  if (blank(value)) return '';
  if (typeof value === 'boolean') return t(value ? 'yes' : 'no');
  if (Array.isArray(value)) return value.map((x) => formatValue({ ...(shape.items ?? {}) } as Shape, x, lang, t)).filter(Boolean).join(', ');
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (shape.allow_range && 'maximum' in obj) return formatValue({ ...shape, allow_range: false }, obj.maximum, lang, t);
    const props = visibleProperties(shape.properties as Array<SchemaProperty & { ui_hidden?: boolean }>);
    const parts: Array<{ label: string; text: string; nested: boolean }> = [];
    for (const prop of props) {
      const text = formatValue(prop as Shape, obj[prop.id], lang, t);
      if (text) parts.push({ label: localized(prop.short_label ?? prop.label, lang), text, nested: prop.type !== 'number' });
    }
    // a single filled property reads as its value alone (the field's label already names it); in a list only numbers carry
    // their label (menu and text values read for themselves)
    return parts.map((x) => parts.length > 1 && !x.nested ? `${x.label} ${x.text}` : x.text).join(' · ');

  }
  const options = (shape.options ?? shape.choices) as Array<string | { value: string; label?: Localized }> | undefined;
  const hit = options?.find((o) => (typeof o === 'string' ? o : o.value) === value);
  if (hit && typeof hit !== 'string') return localized(hit.label, lang) || String(value);
  if (shape.suggestions?.length) return suggestionDisplay(shape.suggestions, value, lang);
  const text = typeof value === 'number' ? formatNumber(value) : String(value);
  const unit = shape.unit && shape.unit !== 'date' && shape.unit !== 'units' ? ` ${shape.unit}` : '';
  return shape.type === 'number' || typeof value === 'number' ? `${text}${unit}` : text;
}

const answerOf = (draft: SpecDraft, field: SpecificationField): FieldAnswer => {
  if (field.id === 'mission_profile') return { value: draft.mission_profile ?? [], status: draft.mission_profile_status };
  const store = (field.storage_namespace === 'production' ? draft.production as Record<string, unknown> | undefined : draft.fields) ?? {};
  const raw = store[field.id];
  return raw && typeof raw === 'object' && !Array.isArray(raw) && 'status' in (raw as object) ? raw as FieldAnswer : { value: raw };
};

/** The summary lines of one step.  The key points read one line per point (torque, speed, duration, role); the maximum
 *  speed and the other fields read as their value.  A field marked "answer later" shows as such; a field that is empty is
 *  skipped (it is listed under "still open" when it is required). */
export function reviewItems(step: WizardStep, draft: SpecDraft, o: { lang: string; t: Translate; profile?: WizardProfile }): ReviewItem[] {
  const { lang, t } = o, profile = o.profile ?? 'industrial';
  const nameOf = (key: string) => t(`wizard.op.default.${key}`);
  const out: ReviewItem[] = [];
  for (const field of step.fields) {
    if (!fieldApplies(field, draft)) continue;            // a field the editor hides (ambient under liquid cooling, depth above water) is not an active requirement
    const label = localized(field.short_label ?? field.label, lang);
    const answer = answerOf(draft, field);
    const unknown = answer.status === 'not_known_yet' || answer.not_known_yet === true;
    if (unknown) { out.push({ id: field.id, label, value: t('wizard.review.later'), unknown: true }); continue; }
    if (field.id === 'key_points') {
      const rows = operatingRows(draft, { legacy: { continuous: t('keyPoints.s1'), peak: t('keyPoints.peak') }, profile, nameOf });
      const roles = pointRoles(rows);
      for (const row of rows) {
        if (!isNum(row.torque_Nm) || !isNum(row.speed_rpm)) continue;
        const duration = row.duration_s === CONTINUOUS ? t('keyPoints.continuous') : isNum(row.duration_s) ? `${formatNumber(row.duration_s)} s` : '';
        const power = shaftPowerOf(row.torque_Nm, row.speed_rpm);
        const role = roles.get(row.row_id);
        out.push({ id: `${field.id}:${row.row_id}`, label: row.name || t('keyPoints.name'), value: `${formatNumber(row.torque_Nm)} N·m`,
          note: [`${t('wizard.review.at')} ${formatNumber(row.speed_rpm)} rpm`, duration, power === null ? '' : formatPower(power), role ? t(`wizard.op.role.${role}`) : ''].filter(Boolean).join(' · ') });
      }
      continue;
    }
    if (field.id === 'mission_profile') {
      const count = (draft.mission_profile ?? []).length;
      if (count) out.push({ id: field.id, label, value: t('wizard.review.phases', { count }) });
      continue;
    }
    if (!answerPresent(answer)) continue;
    if (field.id === '3_1') {
      const text = formatValue(activeCoolingShape(field, answer.value, draft), answer.value, lang, t);
      if (text) out.push({ id: field.id, label, value: text, wide: text.length > 26 });
      continue;
    }
    const text = formatValue(field as Shape, answer.value, lang, t);
    if (text) out.push({ id: field.id, label, value: text, wide: text.length > 26 });
  }
  return out;
}


/** The cooling field as the editor shows it: only the medium and the conditions of the ACTIVE medium (liquid: coolant, inlet
 *  temperature ...; air: airflow; underwater: water temperature and speed); the other media's values stay stored, unshown. */
function activeCoolingShape(field: SpecificationField, value: unknown, draft: SpecDraft): Shape {
  const { medium } = coolingMedium(value);
  const loadAnswer = draft.fields?.load_type as { value?: unknown } | undefined;
  const loadType = typeof loadAnswer?.value === 'string' ? loadAnswer.value : undefined;
  const ids = medium ? ['cooling_medium', ...coolingPropertyIds(medium, loadType)] : ['cooling_medium', 'cooling_type'];
  return { ...(field as Shape), properties: (field.properties ?? []).filter((p) => ids.includes(p.id)) } as Shape;
}

/** Commercial requirements (6_1 / 6_2) and the production plan (quantities / timeline) that disagree: one line per differing
 *  value naming both sources.  Neither is chosen and neither namespace is changed (review F06). */
export function deliveryConflictLines(steps: WizardStep[], draft: SpecDraft, lang: string, t: Translate): string[] {
  const out: string[] = [];
  const production = (draft.production ?? {}) as Record<string, { value?: unknown }>;
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  for (const plan of deliveryConflicts(draft)) {
    const fieldId = plan === 'quantities' ? '6_1' : '6_2';
    const field = steps.flatMap((x) => x.fields).find((f) => f.id === fieldId);
    const requirement = (draft.fields?.[fieldId] as { value?: Record<string, unknown> } | undefined)?.value ?? {};
    const launch = (production[plan]?.value ?? {}) as Record<string, unknown>;
    for (const prop of field?.properties ?? []) {
      const a = requirement[prop.id], b = launch[prop.id];
      if (a == null || b == null || same(a, b)) continue;
      out.push(t('wizard.conflict.line', { label: localized(prop.short_label ?? prop.label, lang), requirement: formatValue(prop as Shape, a, lang, t), plan: formatValue(prop as Shape, b, lang, t) }));
    }
  }
  return out;
}
