// THE assembly tree model (owner 2026-10-15: "the tree on the left is ONE for all geometries and for all users").
//
// One tree component draws every part tree in the app: `viewer3d/ComponentTreeView` (the "Motor Assembly" panel - eye toggles,
// colour swatches, expandable nodes, isolate).  This module builds the rows it draws for the viewers that show the server's part tree
// (Geometry Full, Materials Full, the 3-D model tab):
//
//   group     Stator / Winding / Rotor / Magnets / Shaft / Housing & holders / Bearings / Electronics / Fasteners   (part_tree.py)
//     part    Stator core, Winding copper, Stator holder front ...   (ids, EN / zh-CN names, order, kit filtering: the server's tree)
//       node  one instance the model draws: Coil 1..12, Magnet 1 N / Magnet 2 S, Screw 1..6, Bearing front / rear ...
//             (the GLB nodes of the part; nothing is invented that the model does not contain)
//
// Visibility is a set of hidden row keys (group, part or node); the viewer hides the GLB nodes it maps to.  Pure and framework-free
// (type-only imports), so node --test pins it.  Geometry Active keeps its own binding (the global store and the 2-D regions) of the
// same component.

import type { PartGroup } from './model3d.ts';
import { nameOf, partsOfGroup, type PartTreeData, type TreePart } from './partTree.ts';

/** Translator hook: (key, vars, English fallback) -> text.  The tests pass `(k, v, f) => f`. */
export type Fmt = (key: string, vars: Record<string, string | number>, fallback: string) => string;

/** One row of the tree (the shape `ComponentTreeView` draws; `extra` is added by the host). */
export interface AssemblyRow {
  key: string;
  label: string;
  colour: string;
  visible: boolean;
  /** 'EM' | 'not EM' for a part row (a subtle tag); absent on groups and nodes */
  tag?: 'EM' | 'notEM';
  /** the part id (rows of kind part), for the host's extra column */
  partId?: string;
  /** the group / part is expanded when first drawn */
  defaultOpen?: boolean;
  group?: AssemblyRow[];
}

export const groupKey = (id: string): string => `g:${id}`;
export const nodeKey = (name: string): string => `n:${name}`;
export const isNodeKey = (key: string): boolean => key.startsWith('n:');
export const isGroupKey = (key: string): boolean => key.startsWith('g:');

/** Palette of the groups when the model gives no colour (no model built yet). */
export const GROUP_COLOURS: Readonly<Record<string, string>> = {
  stator: '#42526b', winding: '#e0821a', rotor: '#394860', magnets: '#e02718', shaft: '#9aa3b2', housing: '#4f8fcf',
  bearings: '#8b93a1', electronics: '#1f8f5f', fasteners: '#c8ccd4', other: '#94a3b8',
};

export interface Instance { name: string; label: string; colour?: string }

const num = (s: string): number => Number(s);

/** The row label of one drawn node.  Patterns of the CAD exporter (cad/fc_scripts/export_models.py): Coil_01, Magnet_01 (odd = N,
 *  even = S), Screw_1, Bearings_front[_balls|_seals], Ring_front, PhaseBoard_pins_001 ...  Anything else keeps its name, readable. */
export function instanceLabel(name: string, fmt: Fmt): string {
  let m = /^Coil_0*(\d+)$/.exec(name);
  if (m) return fmt('assemblyCoil', { n: num(m[1]) }, `Coil ${num(m[1])}`);
  m = /^Magnet_0*(\d+)$/.exec(name);
  if (m) {
    const n = num(m[1]);
    const pole = n % 2 === 1 ? 'N' : 'S';
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
    const fallback = `${what === 'bearing' ? 'Bearing' : what === 'balls' ? 'Balls' : 'Seals'}, ${side}`;
    return fmt(`assembly_${what}_${side}`, {}, fallback);
  }
  m = /^(Ring|Holder)_(front|back)$/.exec(name);
  if (m) {
    const side = m[2] === 'front' ? 'front' : 'rear';
    return fmt(`assembly_${m[1].toLowerCase()}_${side}`, {}, `${m[1]}, ${side}`);
  }
  const generic = fmt(`assemblyNode_${name}`, {}, name.replace(/_/g, ' ').replace(/\s+0*(\d+)$/, ' $1').trim());
  return generic;
}

const trailingNumber = (name: string): number => {
  const m = /(\d+)$/.exec(name);
  return m ? num(m[1]) : 0;
};

/** The instances of a part: its GLB nodes that hold several drawn objects expand into one row each (Coil 1..12); a part drawn by
 *  several nodes (the bearings, the rings) lists the nodes themselves.  A part drawn by one single object has no instance rows. */
export function instancesOf(part: Pick<TreePart, 'glb'>, nodes: readonly PartGroup[], fmt: Fmt): Instance[] {
  const present = part.glb.map((n) => nodes.find((g) => g.name === n)).filter((g): g is PartGroup => !!g);
  const multi = present.filter((g) => (g.children?.length ?? 0) > 1);
  let out: Instance[] = [];
  if (multi.length) {
    for (const g of present) {
      if ((g.children?.length ?? 0) > 1) out.push(...g.children!.map((c) => ({ name: c, label: instanceLabel(c, fmt), colour: g.colour })));
      else out.push({ name: g.name, label: instanceLabel(g.name, fmt), colour: g.colour });
    }
    out.sort((a, b) => trailingNumber(a.name) - trailingNumber(b.name) || a.name.localeCompare(b.name));
  } else if (present.length > 1) {
    out = present.map((g) => ({ name: g.name, label: instanceLabel(g.name, fmt), colour: g.colour }));
  }
  return out;
}

export interface BuildOptions {
  lang: string;
  fmt: Fmt;
  hidden: ReadonlySet<string>;
}

/** Rows of THE tree: groups (open) > parts (with an EM tag) > instances.  Parts and groups take the colour the model draws them with. */
export function buildAssemblyRows(tree: PartTreeData, nodes: readonly PartGroup[], o: BuildOptions): AssemblyRow[] {
  return tree.groups.map((g): AssemblyRow => {
    const parts = partsOfGroup(tree, g.id);
    const partRows = parts.map((p): AssemblyRow => {
      const inst = instancesOf(p, nodes, o.fmt);
      const colour = nodes.find((n) => p.glb.includes(n.name) && n.colour)?.colour ?? GROUP_COLOURS[g.id] ?? GROUP_COLOURS.other;
      return {
        key: p.id, partId: p.id, label: nameOf(p.names, o.lang), colour, visible: !o.hidden.has(p.id), tag: p.em ? 'EM' : 'notEM',
        ...(inst.length ? { group: inst.map((i) => ({ key: nodeKey(i.name), label: i.label, colour: i.colour ?? colour, visible: !o.hidden.has(nodeKey(i.name)) })) } : {}),
      };
    });
    return { key: groupKey(g.id), label: nameOf(g.name, o.lang), colour: partRows[0]?.colour ?? GROUP_COLOURS[g.id] ?? GROUP_COLOURS.other,
      visible: !o.hidden.has(groupKey(g.id)), defaultOpen: true, group: partRows };
  });
}

/** Every key of the tree (groups, parts, instances), in drawing order. */
export function allKeys(rows: readonly AssemblyRow[]): string[] {
  return rows.flatMap((r) => [r.key, ...(r.group ? allKeys(r.group) : [])]);
}

/** Toggle one key in the hidden set (a new set). */
export function toggled(hidden: ReadonlySet<string>, key: string): Set<string> {
  const n = new Set(hidden);
  if (n.has(key)) n.delete(key); else n.add(key);
  return n;
}

/** The branch of a key: itself, its descendants and its ancestors (what "isolate" keeps visible). */
export function branchOf(rows: readonly AssemblyRow[], key: string): Set<string> {
  const keep = new Set<string>();
  const walk = (rs: readonly AssemblyRow[], path: string[]): boolean => {
    let found = false;
    for (const r of rs) {
      if (r.key === key) {
        found = true;
        [...path, r.key, ...(r.group ? allKeys(r.group) : [])].forEach((k) => keep.add(k));
      } else if (r.group && walk(r.group, [...path, r.key])) {
        found = true;
      }
    }
    return found;
  };
  walk(rows, []);
  return keep;
}

/** Isolate: hide every key outside the branch.  Isolating the only visible branch again shows everything. */
export function isolated(rows: readonly AssemblyRow[], hidden: ReadonlySet<string>, key: string): Set<string> {
  const keep = branchOf(rows, key);
  const others = allKeys(rows).filter((k) => !keep.has(k));
  const already = others.every((k) => hidden.has(k)) && [...keep].every((k) => !hidden.has(k));
  return already ? new Set() : new Set(others);
}

/** The GLB nodes a key stands for: a group = the nodes of all its parts, a part = its nodes, a node = itself. */
export function nodesOfKey(tree: PartTreeData | null | undefined, key: string | null | undefined): string[] {
  if (!tree || !key) return [];
  if (isNodeKey(key)) return [key.slice(2)];
  if (isGroupKey(key)) return partsOfGroup(tree, key.slice(2)).flatMap((p) => p.glb);
  return [...(tree.parts.find((p) => p.id === key)?.glb ?? [])];
}

/** The node names the viewer must hide for a hidden set (a hidden group or part hides its nodes, a hidden node itself). */
export function hiddenNodeNames(tree: PartTreeData | null | undefined, hidden: ReadonlySet<string>): Set<string> {
  const out = new Set<string>();
  if (!tree) return out;
  for (const k of hidden) for (const n of nodesOfKey(tree, k)) out.add(n);
  return out;
}

/** The node names to highlight for a selected key. */
export function highlightedNodeNames(tree: PartTreeData | null | undefined, selected: string | null | undefined): Set<string> {
  return new Set(nodesOfKey(tree, selected));
}
