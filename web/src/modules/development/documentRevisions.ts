/** Groups the Documents list by revision (first-appearance order, server order kept inside a group). */
export interface RevisionGroup<T> { key: string; docs: T[] }

/** key is the trimmed revision; '' is the "no revision" group. */
export function groupByRevision<T extends { revision?: string | null }>(docs: T[]): RevisionGroup<T>[] {
  const groups: RevisionGroup<T>[] = [];
  const index = new Map<string, RevisionGroup<T>>();
  for (const d of docs) {
    const key = (d.revision ?? '').trim();
    let g = index.get(key);
    if (!g) { g = { key, docs: [] }; index.set(key, g); groups.push(g); }
    g.docs.push(d);
  }
  return groups;
}

/** Default state: the first group is open, the others closed; `toggled` holds the user's explicit choices. */
export function isGroupOpen(groups: { key: string }[], key: string, toggled: Record<string, boolean>): boolean {
  if (key in toggled) return toggled[key];
  return groups.length > 0 && groups[0].key === key;
}
