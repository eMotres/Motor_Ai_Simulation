// THE part tree on the client: the types of the server's tree (motor_ai_sim/part_tree.py) and the pure helpers every surface
// shares (Geometry Full, Materials Full, the 3-D model's parts list).  One shape, one order, one set of names.

export interface PartNames { en: string; zh: string }

export interface TreePart {
  id: string;
  key?: string;
  group: string;
  name: string;
  names: PartNames;
  role?: string | null;
  em: boolean;
  /** GLB node names that draw this part (the viewer highlights them when the part is selected) */
  glb: string[];
  order: number;
  // full form only (not in the 3-D model's brief form)
  material_id?: string | null;
  default_material_id?: string | null;
  user_set?: boolean;
  mass_kg?: number | null;
  counted?: boolean;
  status?: string;
  basis?: string;
}

export interface TreeGroup { id: string; name: PartNames; parts: string[] }

export interface PartTreeData {
  die?: string;
  config?: string;
  available?: boolean;
  reason?: string;
  partial?: boolean;
  /** frameless kit (cad_variant: frameless): the tree holds only the kit parts */
  kit?: boolean;
  assembly?: string;
  scope?: string;
  source?: string;
  total_kg?: number;
  active_kg?: number;
  groups: TreeGroup[];
  parts: TreePart[];
}

/** The name of a part / group in the UI language: zh-CN gets the Chinese name, every other language the English one. */
export function nameOf(n: PartNames | undefined, lang: string | undefined): string {
  if (!n) return '';
  return (lang ?? 'en').toLowerCase().startsWith('zh') ? (n.zh || n.en) : (n.en || n.zh);
}

/** The parts of a group, in tree order. */
export function partsOfGroup(tree: PartTreeData, groupId: string): TreePart[] {
  const by = new Map(tree.parts.map((p) => [p.id, p]));
  const g = tree.groups.find((x) => x.id === groupId);
  return (g?.parts ?? []).map((id) => by.get(id)).filter((p): p is TreePart => !!p);
}

/** Mass of a group (counted parts only); null when no part of it has a known mass. */
export function groupMassKg(tree: PartTreeData, groupId: string): number | null {
  const ps = partsOfGroup(tree, groupId).filter((p) => p.counted && p.mass_kg != null);
  return ps.length ? ps.reduce((t, p) => t + (p.mass_kg as number), 0) : null;
}

/** The ids in display order (group by group) - what "the same order everywhere" is tested against. */
export function orderedIds(tree: PartTreeData): string[] {
  return tree.groups.flatMap((g) => g.parts);
}
