import type { DevelopmentSchema, SchemaField, SchemaSection } from './types';

export type SpecificationCardId = 'application' | 'continuous' | 'load' | 'mission' | 'electrical' | 'cooling' | 'mechanical' | 'delivery' | 'review';
export interface SpecificationField extends SchemaField { storage_namespace?: 'fields' | 'production'; source_section_id?: string }
export interface SpecificationCard extends SchemaSection { id: SpecificationCardId; title: string; fields: SpecificationField[] }

const CARDS: Array<{ id: SpecificationCardId; title: string }> = [
  { id: 'application', title: 'wizard.application' }, { id: 'continuous', title: 'wizard.continuous' },
  { id: 'load', title: 'wizard.load' }, { id: 'mission', title: 'wizard.mission' },
  { id: 'electrical', title: 'wizard.electrical' }, { id: 'cooling', title: 'wizard.cooling' },
  { id: 'mechanical', title: 'wizard.mechanical' }, { id: 'delivery', title: 'wizard.delivery' },
  { id: 'review', title: 'wizard.review' },
];

function groupFor(field: SchemaField, sourceSection: SchemaSection): SpecificationCardId {
  const group = (field as SchemaField & { presentation_group?: string }).presentation_group;
  if (field.id === 'mission_profile' || group === 'mission_profile') return 'mission';
  if (group === 'continuous' || ['1_4', '1_5', '1_6', '1_7', '1_8', '1_9'].includes(field.id)) return 'continuous';
  if (group === 'load' || ['4_6', 'load_type', 'load_source', 'load_characteristic', 'load_inertia_kg_m2', 'startup_mode'].includes(field.id)) return 'load';
  if (group === '2') return 'electrical';
  if (group === '3') return 'cooling';
  if (group === '4') return 'mechanical';
  if (group === '5' || group === '6' || group === 'production' || sourceSection.id === 'production' || field.id === '1_10') return 'delivery';
  if (group === '1' || sourceSection.id === '1') return 'application';
  return sourceSection.id === '2' ? 'electrical' : sourceSection.id === '3' ? 'cooling' : sourceSection.id === '4' ? 'mechanical' : 'delivery';
}

/** Groups schema fields for display while retaining their backend storage namespace and source section. */
export function buildSpecificationCards(schema: DevelopmentSchema): SpecificationCard[] {
  const cards = CARDS.map((c) => ({ ...c, fields: [] as SpecificationField[], kind: c.id === 'review' ? 'review' : undefined }));
  const byId = new Map(cards.map((card) => [card.id, card]));
  for (const section of schema.sections) for (const field of section.fields ?? []) {
    const card = byId.get(groupFor(field, section))!;
    card.fields.push({ ...field, storage_namespace: section.id === 'production' ? 'production' : 'fields', source_section_id: section.id });
  }
  return cards;
}
