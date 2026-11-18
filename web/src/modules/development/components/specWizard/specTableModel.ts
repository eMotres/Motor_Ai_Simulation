/** The Specification's TABLE view (owner 2026-11-18: "when you enter the specification, by default it must show a table with
 *  all the specification values, to understand it quickly"): every step of the wizard as a section of one compact table, one line
 *  per value.  Pure functions (no React), tested in node.  The values come from the review step's own summary (reviewItems), so
 *  the table and the review never disagree; this file only adds the rows that are still open, the unit column and the points. */
import { CONTINUOUS } from '../../keyPoints.ts';
import type { ProjectThreadMessage } from '../../types';
import { fieldApplies, type SpecDraft } from '../../wizardState.ts';
import { formatNumber, operatingRows, pointRoles, shaftPowerOf, formatPower } from './operatingPoints.ts';
import { localized, reviewItems, type Translate } from './reviewSummary.ts';
import { answerPresent, fieldState, type WizardStep } from './steps.ts';
import type { WizardProfile } from './profile.ts';

export type SpecViewMode = 'table' | 'wizard';
export const SPEC_VIEW_MODES: readonly SpecViewMode[] = ['table', 'wizard'];
export const parseSpecViewMode = (raw: unknown): SpecViewMode | null => (raw === 'table' || raw === 'wizard' ? raw : null);

/** True when the specification holds any answer: a value, an "answer later" mark, a mission phase or a production plan. */
export function specHasAnswers(draft: SpecDraft | null | undefined): boolean {
  if (!draft) return false;
  const stores = [draft.fields, draft.production].filter(Boolean) as Array<Record<string, unknown>>;
  if (stores.some((s) => Object.values(s).some(answerPresent))) return true;
  return (draft.mission_profile ?? []).length > 0;
}

/** The view a Specification opens on: a brand-new empty specification opens in the Wizard (a customer starting a project is not
 *  shown an empty table); any other opens on the viewer's remembered choice, by default the Table. */
export function initialSpecView(remembered: SpecViewMode | null, draft: SpecDraft | null | undefined): SpecViewMode {
  if (!specHasAnswers(draft)) return 'wizard';
  return remembered ?? 'table';
}

export type RowState = 'answered' | 'unknown' | 'missing' | 'invalid';
export interface TableRow {
  id: string;
  /** the field the wizard opens for this row */
  fieldId: string;
  label: string; value: string; unit: string;
  state: RowState;
  /** shown as the value's tooltip (the other stored bounds of a worst-case value) */
  title?: string;
  /** open comments written against this field */
  comments: number;
}
export interface TablePoint { id: string; name: string; torque: string; speed: string; duration: string; power: string; role: string }
export interface TableSection { stepId: string; index: number; title: string; rows: TableRow[]; points: TablePoint[] }

/** Split a trailing unit off a formatted value ("48 V" -> "48" / "V") when the field's own unit ends it; everything else keeps
 *  its text whole (menu labels, composite one-line values). */
export function splitUnit(value: string, unit: string | undefined): { value: string; unit: string } {
  const u = (unit ?? '').trim();
  if (!u || u === 'date' || u === 'units' || !value.endsWith(` ${u}`)) return { value, unit: '' };
  const head = value.slice(0, -(u.length + 1));
  return /^[\d\s.,+\-−]+$/.test(head) ? { value: head, unit: u } : { value, unit: '' };
}

/** The table: one section per field step (the review step is not a section), in the wizard's own order.  A field that is
 *  answered reads its review value; an "answer later" field reads as such; a REQUIRED field that is empty or invalid gets its
 *  row with the state marked; an empty optional field is left out (one line per value, no wall of blanks). */
export function specTable(steps: WizardStep[], draft: SpecDraft, o: { lang: string; t: Translate; profile?: WizardProfile; messages?: ProjectThreadMessage[] }): TableSection[] {
  const { lang, t } = o, profile = o.profile ?? 'industrial';
  const open = (o.messages ?? []).filter((m) => !m.resolved && !!m.field_ref);
  const commentsOf = (fieldId: string) => open.filter((m) => m.field_ref === fieldId).length;
  const out: TableSection[] = [];
  steps.forEach((step, index) => {
    if (step.kind === 'review') return;
    const items = reviewItems(step, draft, { lang, t, profile });
    const rows: TableRow[] = [];
    const points: TablePoint[] = [];
    for (const field of step.fields) {
      if (!fieldApplies(field, draft)) continue;
      const label = localized(field.short_label ?? field.label, lang);
      const state = fieldState(field, draft);
      if (field.id === 'key_points' && state !== 'unknown') {
        const list = operatingRows(draft, { legacy: { continuous: t('keyPoints.s1'), peak: t('keyPoints.peak') }, profile, nameOf: (key) => t(`wizard.op.default.${key}`) });
        const roles = pointRoles(list);
        for (const row of list) {
          if (typeof row.torque_Nm !== 'number' || typeof row.speed_rpm !== 'number') continue;
          const power = shaftPowerOf(row.torque_Nm, row.speed_rpm);
          const role = roles.get(row.row_id);
          points.push({ id: row.row_id, name: row.name || t('keyPoints.name'), torque: formatNumber(row.torque_Nm), speed: formatNumber(row.speed_rpm),
            duration: row.duration_s === CONTINUOUS ? t('keyPoints.continuous') : typeof row.duration_s === 'number' ? `${formatNumber(row.duration_s)} s` : '',
            power: power === null ? '' : formatPower(power), role: role ? t(`wizard.op.role.${role}`) : '' });
        }
        // no point with a value: the field is still a row, so a missing required list is seen and can be opened
        if (points.length === 0 && (state === 'missing' || state === 'invalid')) rows.push({ id: field.id, fieldId: field.id, label, value: '', unit: '', state, comments: commentsOf(field.id) });
        continue;
      }
      const item = items.find((i) => i.id === field.id);
      if (item) {
        const split = item.unknown ? { value: item.value, unit: '' } : splitUnit(item.value, field.unit);
        rows.push({ id: field.id, fieldId: field.id, label: item.label || label, value: split.value, unit: split.unit,
          state: item.unknown ? 'unknown' : state === 'invalid' ? 'invalid' : 'answered', title: item.title, comments: commentsOf(field.id) });
      } else if (state === 'missing' || state === 'invalid' || state === 'unknown') {
        rows.push({ id: field.id, fieldId: field.id, label, value: state === 'unknown' ? t('wizard.review.later') : '', unit: '', state, comments: commentsOf(field.id) });
      }
    }
    if (rows.length || points.length) out.push({ stepId: step.id, index, title: step.title, rows, points });
  });
  return out;
}
