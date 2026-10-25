import type { DevelopmentSchema, SchemaField } from './types';

export type SpecificationCardId = 'key' | 'requirements' | 'mission' | 'more' | 'review';
export interface SpecificationField extends SchemaField { storage_namespace?: 'fields' | 'production'; source_section_id?: string }
export interface SpecificationCard { id: SpecificationCardId; title: string; fields: SpecificationField[]; kind?: string; legacy_ids: string[] }

/** The compact one-page specification (owner 2026-10-24): what closes a motor project first,
 *  everything else in one collapsed "More details" card.  Field ids are never renamed. */
export const KEY_FIELDS = ['1_1', '1_4', '1_5', '2_1', '3_1', '3_2'];
export const REQUIREMENT_FIELDS = ['1_2', '1_6', '2_4', '2_6', '2_9', '2_3', '3_3', '4_1', '4_2', '4_6', '1_8', '6_1', '6_2'];
/** Shown in the Requirements card under a divider. */
export const SECOND_TIER_FIELDS = ['4_4', '2_5'];
export const MORE_FIRST_FIELDS = ['additional_requirements', '1_3', '1_7', '2_7'];
/** Rendered together by LoadSection inside "More details". */
export const LOAD_FIELDS = ['load_type', 'load_source', 'load_inertia_kg_m2', 'startup_mode', 'load_characteristic'];

const CARDS: Array<{ id: SpecificationCardId; title: string; legacy_ids: string[] }> = [
  { id: 'key', title: 'wizard.keyData', legacy_ids: ['application', 'continuous', 'electrical', 'cooling'] },
  { id: 'requirements', title: 'wizard.requirements', legacy_ids: ['mechanical'] },
  { id: 'mission', title: 'wizard.mission', legacy_ids: [] },
  { id: 'more', title: 'wizard.moreDetails', legacy_ids: ['load', 'delivery'] },
  { id: 'review', title: 'wizard.review', legacy_ids: [] },
];

function cardFor(field: SchemaField, namespace: 'fields' | 'production'): SpecificationCardId {
  if (namespace === 'production') return 'more';
  if (field.id === 'mission_profile') return 'mission';
  if (KEY_FIELDS.includes(field.id)) return 'key';
  if (REQUIREMENT_FIELDS.includes(field.id) || SECOND_TIER_FIELDS.includes(field.id)) return 'requirements';
  return 'more';
}

const ORDER = [...KEY_FIELDS, ...REQUIREMENT_FIELDS, ...SECOND_TIER_FIELDS, 'mission_profile', ...MORE_FIRST_FIELDS, ...LOAD_FIELDS];
const rank = (field: SpecificationField, schemaIndex: number) => {
  if (field.storage_namespace === 'production') return 10_000 + schemaIndex;
  const listed = ORDER.indexOf(field.id);
  return listed >= 0 ? listed : 1_000 + schemaIndex;
};

/** Groups schema fields into the specification cards while retaining their backend storage
 *  namespace and source section.  Every schema field lands in exactly one card: anything not
 *  listed above goes to "More details", in schema order. */
export function buildSpecificationCards(schema: DevelopmentSchema): SpecificationCard[] {
  const cards: SpecificationCard[] = CARDS.map((c) => ({ ...c, fields: [], kind: c.id === 'review' ? 'review' : undefined }));
  const byId = new Map(cards.map((card) => [card.id, card]));
  const order = new Map<SpecificationField, number>();
  let index = 0;
  for (const section of schema.sections) for (const field of section.fields ?? []) {
    const namespace = section.id === 'production' ? 'production' : 'fields';
    const laidOut: SpecificationField = { ...field, storage_namespace: namespace, source_section_id: section.id };
    order.set(laidOut, rank(laidOut, index++));
    byId.get(cardFor(field, namespace))!.fields.push(laidOut);
  }
  for (const card of cards) card.fields.sort((a, b) => order.get(a)! - order.get(b)!);
  return cards;
}

/** The card that holds a field (for the sidebar, a submit error or a comment link). */
export function cardIndexForField(cards: SpecificationCard[], fieldId: string): number {
  return cards.findIndex((card) => card.fields.some((field) => field.id === fieldId));
}

/** Section comments written against the previous card ids still count on the card that now holds those fields. */
export function sectionCommentRefs(card: SpecificationCard): string[] {
  return [card.id, ...card.legacy_ids].map((id) => `section:${id}`);
}
