import type { PartGroup } from './model3d.ts';
import { nameOf, partsOfGroup, type PartTreeData, type TreePart } from './partTree.ts';

export type Fmt = (key: string, vars: Record<string, string | number>, fallback: string) => string;
export interface AssemblyRow {
  key: string;
  label: string;
  colour: string;
  sceneNames: string[];
  present?: boolean;
  defaultExpanded?: boolean;
  children?: AssemblyRow[];
}
export const groupKey = (id: string): string => `g:${id}`;
export const nodeKey = (name: string): string => `n:${name}`;
export const GROUP_COLOURS: Readonly<Record<string, string>> = {
  stator: '#42526b', winding: '#e0821a', rotor: '#394860', magnets: '#e02718', shaft: '#9aa3b2', housing: '#4f8fcf',
  bearings: '#8b93a1', electronics: '#1f8f5f', fasteners: '#c8ccd4', other: '#94a3b8',
};
export interface Instance { name: string; label: string; colour?: string }
const num = (s: string): number => Number(s);
export function instanceLabel(name: string, fmt: Fmt): string {
  let m = /^Coil_0*(\d+)$/.exec(name);
  if (m) return fmt('assemblyCoil', { n: num(m[1]) }, `Coil ${num(m[1])}`);
  m = /^Magnet_0*(\d+)$/.exec(name);
  if (m) {
    const n = num(m[1]);
    const pole = n % 2 ? 'N' : 'S';
    return fmt('assemblyMagnet', { n, pole }, `Magnet ${n} ${pole}`);
  }
  m = /^Screw_0*(\d+)$/.exec(name);
  if (m) return fmt('assemblyScrew', { n: num(m[1]) }, `Screw ${num(m[1])}`);
  m = /^PhaseBoard_pins_0*(\d+)$/.exec(name);
  if (m) return fmt('assemblyPin', { n: num(m[1]) }, `Pin ${num(m[1])}`);
  m = /^Bearings_(front|back)(?:_(balls|seals))?$/.exec(name);
  if (m) {
    const side = m[1] === 'front' ? 'front' : 'rear';
    const what = m[2] ?? 'bearing';
    const label = `${what === 'bearing' ? 'Bearing' : what === 'balls' ? 'Balls' : 'Seals'}, ${side}`;
    return fmt(`assembly_${what}_${side}`, {}, label);
  }
  m = /^(Holder)_(front|back)$/.exec(name);
  if (m) {
    const side = m[2] === 'front' ? 'front' : 'rear';
    return fmt(`assembly_${m[1].toLowerCase()}_${side}`, {}, `${m[1]}, ${side}`);
  }
  return fmt(`assemblyNode_${name}`, {}, name.replace(/_/g, ' ').replace(/\s+0*(\d+)$/, ' $1').trim());
}
const trailingNumber = (name: string): number => Number(/(\d+)$/.exec(name)?.[1] ?? 0);
export function instancesOf(part: Pick<TreePart, 'glb'>, nodes: readonly PartGroup[], fmt: Fmt): Instance[] {
  const present = part.glb.map((name) => nodes.find((group) => group.name === name))
    .filter((group): group is PartGroup => !!group);
  const multi = present.filter((group) => (group.children?.length ?? 0) > 1);
  let out: Instance[] = [];
  if (multi.length) {
    for (const group of present) {
      const instances = (group.children?.length ?? 0) > 1
        ? group.children!.map((name) => ({ name, label: instanceLabel(name, fmt), colour: group.colour }))
        : [{ name: group.name, label: instanceLabel(group.name, fmt), colour: group.colour }];
      out.push(...instances);
    }
    out.sort((a, b) => trailingNumber(a.name) - trailingNumber(b.name) || a.name.localeCompare(b.name));
  } else if (present.length > 1) {
    out = present.map((group) => ({ name: group.name, label: instanceLabel(group.name, fmt), colour: group.colour }));
  }
  return out;
}
export interface BuildOptions { lang: string; fmt: Fmt }
/** Builds neutral display rows and maps each row to the GLB scene names it represents. */
export function buildAssemblyRows(tree: PartTreeData, nodes: readonly PartGroup[], options: BuildOptions): AssemblyRow[] {
  return tree.groups.map((group) => {
    const parts = partsOfGroup(tree, group.id);
    const children = parts.map((part) => {
      const instances = instancesOf(part, nodes, options.fmt);
      const colour = nodes.find((node) => part.glb.includes(node.name) && node.colour)?.colour
        ?? GROUP_COLOURS[group.id] ?? GROUP_COLOURS.other;
      return {
        key: part.id,
        label: nameOf(part.names, options.lang),
        colour,
        sceneNames: [...part.glb],
        present: instances.length > 0 || part.glb.some((name) => nodes.some((node) => node.name === name)),
        ...(instances.length ? {
          children: instances.map((instance) => ({
            key: nodeKey(instance.name), label: instance.label, colour: instance.colour ?? colour, sceneNames: [instance.name],
          })),
        } : {}),
      };
    });
    return {
      key: groupKey(group.id),
      label: nameOf(group.name, options.lang),
      colour: children[0]?.colour ?? GROUP_COLOURS[group.id] ?? GROUP_COLOURS.other,
      sceneNames: parts.flatMap((part) => part.glb),
      present: children.some((part) => part.present !== false),
      defaultExpanded: true,
      children,
    };
  });
}
