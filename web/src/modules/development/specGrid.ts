import type { Localized, SchemaField, SchemaProperty, SchemaSuggestion } from './types';

/** The ONE grid every specification card uses (owner 2026-10-26, «не выровнено по полям ввода»):
 *   - every group (a field with its inputs) starts its own row, so two groups never sit side by side with headers of
 *     different heights;
 *   - the group header and every field label are ONE line (ellipsis, the full text in the tooltip) of a FIXED height, so
 *     all inputs of a row share one top edge;
 *   - the columns of a card are equal and as wide as the card's longest option (`--spec-col`), so a select never
 *     truncates the chosen value and every group of a card uses the same column edges. */

export const SPEC_LABEL_HEIGHT = 20;
const singleLine = { height: SPEC_LABEL_HEIGHT, lineHeight: `${SPEC_LABEL_HEIGHT}px`, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 } as const;
/** A group header (the field's own label): one line, fixed height. */
export const specGroupHeaderSx = { ...singleLine, fontSize: 12, fontWeight: 700, color: 'var(--text-0)' } as const;
/** A field label inside a group (a property): one line, fixed height, one level below the header. */
export const specFieldLabelSx = { ...singleLine, fontSize: 11, fontWeight: 600, color: 'var(--text-2)' } as const;
/** The grid of a card's groups AND of the inputs inside a group: the same equal columns everywhere. */
export const specGridSx = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, var(--spec-col, 180px)), 1fr))',
  gap: '6px 12px', alignItems: 'start', minWidth: 0,
} as const;

const LANGS = ['en', 'zh-CN'] as const;
const text = (value: Localized | undefined, lang: string): string => typeof value === 'string' ? value : (value?.[lang as 'en' | 'zh-CN'] ?? value?.en ?? '');
/** Printed width of a label in `ch`: a CJK character is about two. */
export function labelChars(label: string): number {
  let n = 0;
  for (const ch of label) n += /[⺀-鿿＀-￯]/.test(ch) ? 2 : 1;
  return n;
}
/** Every label a menu offers, in every language (a language switch must not truncate the chosen value). */
export function optionLabels(options: ReadonlyArray<string | number | { value?: string | number; label?: unknown }> | undefined): string[] {
  const out: string[] = [];
  for (const option of options ?? []) {
    if (typeof option === 'number') continue; // numeric menus are short
    if (typeof option === 'string') { out.push(option); continue; }
    if (!option) continue;
    for (const lang of LANGS) { const label = text(option.label as Localized, lang); if (label) out.push(label); }
    if (typeof option.value === 'string' && option.value) out.push(option.value);
  }
  return Array.from(new Set(out));
}
/** The longest label of a menu, in `ch`. */
export function longestOptionChars(options: ReadonlyArray<string | number | { value?: string | number; label?: unknown }> | undefined): number {
  return optionLabels(options).reduce((n, label) => Math.max(n, labelChars(label)), 0);
}
/** CSS min width of a select / autocomplete: at least as wide as its longest option (plus the arrow and padding); capped. */
export function optionsMinWidth(options: ReadonlyArray<string | number | { value?: string | number; label?: unknown }> | undefined, cap = 46): string | undefined {
  const chars = Math.min(cap, longestOptionChars(options));
  return chars > 0 ? `calc(${chars}ch + 60px)` : undefined;
}
type Shape = Pick<SchemaProperty, 'options' | 'suggestions'> & { choices?: SchemaField['choices']; properties?: SchemaProperty[]; items?: { properties?: SchemaProperty[] } };
/** Every menu label of a field and of its properties (recursively). */
export function fieldOptionLabels(shape: Shape | undefined): string[] {
  if (!shape) return [];
  const own = [...optionLabels(shape.options as never), ...optionLabels(shape.suggestions as SchemaSuggestion[] as never), ...optionLabels(shape.choices as never)];
  const nested = [...(shape.properties ?? []), ...(shape.items?.properties ?? [])].flatMap((prop) => fieldOptionLabels(prop as Shape));
  return [...own, ...nested];
}
/** The `--spec-col` of a card: its longest menu label (all of its fields) or the default column. */
export function cardColumnMin(fields: Shape[], base = 180, cap = 46): string {
  const chars = Math.min(cap, fields.flatMap((f) => fieldOptionLabels(f)).reduce((n, label) => Math.max(n, labelChars(label)), 0));
  return chars > 0 ? `max(${base}px, calc(${chars}ch + 60px))` : `${base}px`;
}
/** The properties a field shows: the schema marks the ones kept in the data but not asked (`ui_hidden`). */
export function visibleProperties<T extends { ui_hidden?: boolean }>(properties: T[] | undefined): T[] {
  return (properties ?? []).filter((p) => !p.ui_hidden);
}
