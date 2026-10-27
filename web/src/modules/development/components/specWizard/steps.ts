/** The seven steps of the specification wizard (owner 2026-10-27) and the map from schema fields to steps.
 *
 *  Pure data and functions, no React: the step layout, the per-step progress and the "still open" list are tested in node.
 *  Field ids and the schema are never changed; a field lands in at most one step (`core` = always shown, `optional` = in the
 *  collapsed "Optional details" group of the step).  A field not listed here is not asked on the page (its stored value is
 *  kept): derived values (2_2 operating voltages, 1_3 mission notes), retired ones (2_3 battery current, 2_5 power
 *  devices), commercial ones (6_3 target budget, target_price - no price talk while designing), legal text (6_5) and the
 *  catalogue-owned load data (load_source, load_characteristic). */
import type { DevelopmentSchema } from '../../types';
import type { SpecificationField } from '../../specificationLayout';
import { fieldApplies, fieldRequired, sectionAnswerCounts, type FieldAnswer, type SpecDraft } from '../../wizardState.ts';

export type WizardStepId = 'application' | 'operating' | 'supply' | 'cooling' | 'mechanical' | 'safety' | 'project';

export interface StepDef { id: WizardStepId; core: string[]; optional: string[]; legacy: string[] }

/** The map of the wizard.  Order inside `core` is the order on the page. */
export const STEP_DEFS: StepDef[] = [
  { id: 'application', core: ['1_1', 'load_type', '1_2'], optional: ['load_inertia_kg_m2', 'startup_mode'], legacy: ['application', 'key'] },
  { id: 'operating', core: ['key_points', '1_6', '1_8', 'mission_profile'], optional: ['1_7'], legacy: ['continuous', 'mission'] },
  { id: 'supply', core: ['2_1', '2_4', '2_6', '2_9'], optional: ['2_7', '2_8', '2_11', '2_12'], legacy: ['electrical'] },
  { id: 'cooling', core: ['3_1', '3_2', '3_3', '3_6'], optional: ['3_4', '3_5'], legacy: ['cooling'] },
  { id: 'mechanical', core: ['4_1', '4_2', '4_3', '4_4', '4_8', '4_6'], optional: ['4_5', '4_7', '4_9'], legacy: ['mechanical', 'construction', 'load'] },
  { id: 'safety', core: ['5_1', '5_4', '5_2', '5_3', '1_10', '1_9'], optional: ['tests', 'certification'], legacy: [] },
  { id: 'project', core: ['6_1', '6_2', 'additional_requirements'], optional: ['6_4', 'packaging_delivery', 'controller_supplier', 'magnets_supplier', 'housing_supplier'], legacy: ['delivery', 'requirements', 'more'] },
];

/** How a step's core fields group into cards (a card with one field has no title of its own: the field's label is its
 *  heading).  The operating step draws its own cards (points, chart, maximum speed) and lists the rest here. */
export interface CardDef { id: string; ids: string[]; /** no title even with several fields (a second field that shows only sometimes) */ untitled?: boolean }
export const STEP_CARDS: Record<WizardStepId, CardDef[]> = {
  application: [{ id: 'application', ids: ['1_1', 'load_type', '1_2'] }],
  operating: [{ id: 'points', ids: ['key_points'] }, { id: 'maxSpeed', ids: ['1_6'] }, { id: 'ripple', ids: ['1_8'] }, { id: 'mission', ids: ['mission_profile'] }],
  supply: [{ id: 'dcBus', ids: ['2_1'] }, { id: 'genOutput', ids: ['2_13'] }, { id: 'controller', ids: ['2_4'] }, { id: 'phases', ids: ['2_6', '2_9'] }],
  cooling: [{ id: 'cooling', ids: ['3_1', '3_2'] }, { id: 'housing', ids: ['3_3', '3_6'], untitled: true }],
  mechanical: [{ id: 'envelope', ids: ['4_1', '4_2'] }, { id: 'interface', ids: ['4_3', '4_4', '4_8'] }, { id: 'loads', ids: ['4_6'] }],
  safety: [{ id: 'standards', ids: ['5_1', '5_4', '5_2', '5_3'] }, { id: 'life', ids: ['1_10', '1_9'] }],
  project: [{ id: 'delivery', ids: ['6_1', '6_2'] }, { id: 'notes', ids: ['additional_requirements'] }],
};

/** Fields a step asks only for one project profile: the generator's AC output window (2_13) sits in the supply step of a
 *  generator project and nowhere else; no existing answer is touched. */
export const PROFILE_FIELDS: Record<string, Partial<Record<WizardStepId, string[]>>> = { generator: { supply: ['2_13'] } };

/** Fields the schema has that the wizard never asks (see the header). */
export const NOT_ASKED = ['1_3', '2_2', '2_3', '2_5', '2_10', '6_3', '6_5', 'target_price', 'load_source', 'load_characteristic', 'quantities', 'timeline'];

export interface WizardStep {
  id: WizardStepId | 'review';
  /** i18n keys (development namespace) */
  title: string; summary: string;
  fields: SpecificationField[];
  coreIds: string[]; optionalIds: string[];
  /** section comments written against the previous card ids still count here */
  legacy_ids: string[];
  kind?: 'review';
}

/** The supply step of a generator speaks of the controller input window, not of a battery (owner 2026-10-27). */
const variant = (id: string, profile?: string) => id === 'supply' && profile === 'generator' ? 'Generator' : '';
export const stepTitleKey = (id: string, profile?: string) => `wizard.steps.${id}.title${variant(id, profile)}`;
export const stepSummaryKey = (id: string, profile?: string) => `wizard.steps.${id}.summary${variant(id, profile)}`;

/** The steps of one schema (the review step last).  A field the schema does not have is skipped. */
export function buildWizardSteps(schema: DevelopmentSchema, profile?: string): WizardStep[] {
  const byId = new Map<string, SpecificationField>();
  for (const section of schema.sections) {
    const namespace = section.id === 'production' ? 'production' : 'fields';
    for (const field of section.fields ?? []) {
      if (!byId.has(field.id)) byId.set(field.id, { ...field, storage_namespace: namespace, source_section_id: section.id });
    }
  }
  const pick = (ids: string[]) => ids.map((id) => byId.get(id)).filter((f): f is SpecificationField => !!f);
  const steps: WizardStep[] = STEP_DEFS.map((def) => {
    const core = pick([...def.core, ...(PROFILE_FIELDS[profile ?? '']?.[def.id] ?? [])]), optional = pick(def.optional);
    return {
      id: def.id, title: stepTitleKey(def.id, profile), summary: stepSummaryKey(def.id, profile),
      fields: [...core, ...optional], coreIds: core.map((f) => f.id), optionalIds: optional.map((f) => f.id), legacy_ids: def.legacy,
    };
  });
  steps.push({ id: 'review', title: stepTitleKey('review'), summary: stepSummaryKey('review'), fields: [], coreIds: [], optionalIds: [], legacy_ids: ['review'], kind: 'review' });
  return steps;
}

/** The step that holds a field (a submit error or a comment link opens it); -1 when no step asks it. */
export function stepIndexForField(steps: WizardStep[], fieldId: string): number {
  return steps.findIndex((step) => step.fields.some((field) => field.id === fieldId));
}
/** True when the field sits in the collapsed optional group of its step. */
export function isOptionalField(steps: WizardStep[], fieldId: string): boolean {
  return steps.some((step) => step.optionalIds.includes(fieldId));
}
export function stepCommentRefs(step: Pick<WizardStep, 'id' | 'legacy_ids'>): string[] {
  return [...new Set([step.id, ...step.legacy_ids])].map((id) => `section:${id}`);
}

/** A stored answer holds something worth showing (a value or an explicit "not known yet"). */
export function answerPresent(raw: unknown): boolean {
  const answer = raw && typeof raw === 'object' && !Array.isArray(raw) && 'status' in (raw as object) ? raw as FieldAnswer : { value: raw } as FieldAnswer;
  if (answer.status === 'not_known_yet' || answer.not_known_yet === true) return true;
  const blank = (v: unknown) => v === undefined || v === null || (typeof v === 'string' && !v.trim());
  const filled = (v: unknown): boolean => {
    if (Array.isArray(v)) return v.length > 0;
    if (v && typeof v === 'object') return Object.values(v as Record<string, unknown>).some(filled);
    return !blank(v);
  };
  return filled(answer.value);
}

const storedAnswer = (draft: SpecDraft, field: SpecificationField): FieldAnswer => {
  if (field.id === 'mission_profile') return { value: draft.mission_profile ?? [], status: draft.mission_profile_status };
  const store = (field.storage_namespace === 'production' ? draft.production as Record<string, unknown> | undefined : draft.fields) ?? {};
  const raw = store[field.id];
  return raw && typeof raw === 'object' && !Array.isArray(raw) && 'status' in (raw as object) ? raw as FieldAnswer : { value: raw };
};
const isUnknown = (a: FieldAnswer) => a.status === 'not_known_yet' || a.not_known_yet === true;

export type FieldState = 'answered' | 'unknown' | 'missing' | 'invalid' | 'optional' | 'optional-answered';
/** One field as the page counts it: a required field is answered / unknown / missing / invalid (the same rule as the
 *  completeness), an optional one is unknown (marked), answered (has a value) or plain optional. */
export function fieldState(field: SpecificationField, draft: SpecDraft): FieldState {
  if (fieldRequired(field, draft) && !field.handled_by) {
    const c = sectionAnswerCounts({ id: 'one', title: '', fields: [field] }, draft);
    return c.answered ? 'answered' : c.unknown ? 'unknown' : c.invalid ? 'invalid' : 'missing';
  }
  const answer = storedAnswer(draft, field);
  if (isUnknown(answer)) return 'unknown';
  return answerPresent(answer) ? 'optional-answered' : 'optional';
}

export type StepState = 'done' | 'partial' | 'empty' | 'optional';
export interface StepProgress { state: StepState; required: number; answered: number; unknown: number; missing: number; invalid: number; optionalAnswered: number }

/** done = every required field answered; empty = none touched; partial = in between (a field marked "answer later" is
 *  not done).  A step without required fields reads "optional" until something is entered, then done. */
export function stepProgress(step: Pick<WizardStep, 'fields'>, draft: SpecDraft): StepProgress {
  const c = sectionAnswerCounts({ id: 'step', title: '', fields: step.fields }, draft);
  const optionalAnswered = step.fields.filter((f) => fieldState(f, draft) === 'optional-answered').length;
  let state: StepState;
  if (c.required === 0) state = optionalAnswered > 0 ? 'done' : 'optional';
  else if (c.answered === c.required) state = 'done';
  else if (c.answered + c.unknown + c.invalid === 0) state = optionalAnswered > 0 ? 'partial' : 'empty';
  else state = 'partial';
  return { state, required: c.required, answered: c.answered, unknown: c.unknown, missing: c.missing, invalid: c.invalid, optionalAnswered };
}

export interface OpenItem { stepId: WizardStep['id']; fieldId: string; state: 'unknown' | 'missing' | 'invalid' }
/** What is still open, step by step: required fields that are missing, invalid or marked "answer later", and optional
 *  fields marked "answer later". */
export function stillOpen(steps: WizardStep[], draft: SpecDraft): OpenItem[] {
  const out: OpenItem[] = [];
  for (const step of steps) for (const field of step.fields) {
    if (!fieldApplies(field, draft)) continue;
    const state = fieldState(field, draft);
    if (state === 'unknown' || state === 'missing' || state === 'invalid') out.push({ stepId: step.id, fieldId: field.id, state });
  }
  return out;
}

/** Header progress: required fields answered of all required, as a whole percentage. */
export function overallProgress(c: { answered?: number; total_required?: number; required?: number } | undefined): { answered: number; total: number; pct: number } {
  const total = c?.total_required ?? c?.required ?? 0, answered = Math.min(c?.answered ?? 0, total);
  return { answered, total, pct: total ? Math.round(answered / total * 100) : 0 };
}

