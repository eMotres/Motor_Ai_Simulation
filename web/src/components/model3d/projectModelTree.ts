// The component tree of an uploaded project model and its Frameless filter (owner 2026-11-24: one button in the 3-D view that takes
// everything a frameless customer does not get - shafts, holders, bearings, screws - out of the tree and the view).
import type { PartGroup, PartTreeProps } from '../../modules/viewer3d';

/** The component name without its instance number: "Magnet:43", "Coil_07", "Ring 2" -> "Magnet", "Coil", "Ring". */
export const componentName = (label: string): string => label.replace(/[\s:_-]*\d+$/, '').replace(/[_\s]+/g, ' ').trim() || label;

/** What a frameless motor consists of, by component name.  Exact names, so "Stator holder" or "Sealing ring" is not one of them. */
const FRAMELESS = new Set(['stator', 'stator core', 'core', 'lamination', 'stator lamination', 'rotor lamination', 'winding', 'coil',
  'coil end', 'lead', 'rotor', 'rotor core', 'magnet', 'ring', 'coil ring', 'sleeve', 'rotor sleeve']);

export const isFramelessPart = (g: PartGroup): boolean => !!g.label && FRAMELESS.has(componentName(g.label).toLowerCase());

/** The button is offered only when it changes something: the model has frameless parts AND something else. */
export function hasFrameless(groups: readonly PartGroup[]): boolean {
  return groups.some(isFramelessPart) && groups.some((g) => !isFramelessPart(g));
}

/** Scene nodes the Frameless view hides. */
export const notFrameless = (groups: readonly PartGroup[]): Set<string> => new Set(groups.filter((g) => !isFramelessPart(g)).map((g) => g.name));

/** One row per component name in file order, its instances below it; a file without names has no tree (as before). */
export function modelTree(groups: readonly PartGroup[], frameless: boolean): PartTreeProps['nodes'] | null {
  if (!groups.some((g) => g.label)) return null;
  const rows = new Map<string, PartGroup[]>();
  for (const g of groups) {
    if (frameless && !isFramelessPart(g)) continue;
    const key = componentName(g.label ?? g.name);
    rows.set(key, [...(rows.get(key) ?? []), g]);
  }
  return [...rows].map(([name, list]) => (list.length === 1
    ? { key: `part:${list[0].name}`, label: name, colour: list[0].colour, sceneNames: [list[0].name] }
    : { key: `group:${name}`, label: `${name} (${list.length})`, colour: list[0].colour, sceneNames: [],
        children: list.map((g) => ({ key: `part:${g.name}`, label: g.label ?? g.name, colour: g.colour, sceneNames: [g.name] })) }));
}
