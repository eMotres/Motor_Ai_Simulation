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

/** Budgets of the tree (Astra on #856): component rows, instance rows under one component, instance rows in the whole tree.  Nothing
 *  is lost above them: the row over the budget switches all its parts at once. */
export const MAX_ROWS = 300;
export const MAX_INSTANCES = 200;
export const MAX_INSTANCE_ROWS = 2000;

/** One row per component name in file order, its instances below it; a file without names has no tree (as before).  Linear in the
 *  number of parts; at most MAX_ROWS + 1 component rows and MAX_INSTANCE_ROWS instance rows. */
export function modelTree(groups: readonly PartGroup[], frameless: boolean, otherLabel = '…'): PartTreeProps['nodes'] | null {
  if (!groups.some((g) => g.label)) return null;
  const rows = new Map<string, PartGroup[]>();
  for (const g of groups) {
    if (frameless && !isFramelessPart(g)) continue;
    const key = componentName(g.label ?? g.name);
    const bucket = rows.get(key);
    if (bucket) bucket.push(g); else rows.set(key, [g]);
  }
  const out: PartTreeProps['nodes'] = [];
  const rest: string[] = [];
  let instanceRows = 0;
  for (const [name, list] of rows) {
    if (out.length >= MAX_ROWS) { for (const g of list) rest.push(g.name); continue; }
    if (list.length === 1) { out.push({ key: `part:${list[0].name}`, label: name, colour: list[0].colour, sceneNames: [list[0].name] }); continue; }
    const label = `${name} (${list.length})`;
    if (list.length > MAX_INSTANCES || instanceRows + list.length > MAX_INSTANCE_ROWS) {
      out.push({ key: `group:${name}`, label, colour: list[0].colour, sceneNames: list.map((g) => g.name) });
      continue;
    }
    instanceRows += list.length;
    out.push({ key: `group:${name}`, label, colour: list[0].colour, sceneNames: [],
      children: list.map((g) => ({ key: `part:${g.name}`, label: g.label ?? g.name, colour: g.colour, sceneNames: [g.name] })) });
  }
  if (rest.length) out.push({ key: 'group:\u0000rest', label: `${otherLabel} (${rest.length})`, sceneNames: rest });
  return out;
}
