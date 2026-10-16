import type { PartTreeProps } from './PartTree';
type Nodes = PartTreeProps['nodes'];
export const hiddenKeysByDefault = (rows: Nodes): Set<string> => new Set(rows.flatMap((r) => [
  ...(r.defaultVisible === false ? [r.key] : []), ...(r.children ? [...hiddenKeysByDefault(r.children)] : []),
]));
export function hiddenSceneNames(rows: Nodes, hidden: ReadonlySet<string>, hiddenAncestor = false): Set<string> {
  const out = new Set<string>();
  for (const row of rows) {
    const concealed = hiddenAncestor || hidden.has(row.key);
    if (concealed) for (const name of row.sceneNames ?? []) out.add(name);
    if (row.children) for (const name of hiddenSceneNames(row.children, hidden, concealed)) out.add(name);
  }
  return out;
}
function everySceneName(rows: Nodes): Set<string> {
  return new Set(rows.flatMap((r) => [...(r.sceneNames ?? []), ...(r.children ? [...everySceneName(r.children)] : [])]));
}
export function selectedSceneNames(rows: Nodes, key: string | null): Set<string> {
  if (!key) return new Set();
  const visit = (nodes: Nodes): Set<string> | null => {
    for (const row of nodes) {
      const nested = row.children ? visit(row.children) : null;
      if (nested) return nested;
      if (row.key === key) return new Set([...(row.sceneNames ?? []), ...(row.children ? [...everySceneName(row.children)] : [])]);
    }
    return null;
  };
  return visit(rows) ?? new Set();
}
export function selectedKeyForScene(rows: Nodes, sceneName: string): string | null {
  for (const row of rows) {
    const nested = row.children ? selectedKeyForScene(row.children, sceneName) : null;
    if (nested) return nested;
    if (row.sceneNames?.includes(sceneName)) return row.key;
  }
  return null;
}
