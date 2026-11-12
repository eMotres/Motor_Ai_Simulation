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

/** Default state: the first group (and the first named one, when unlabelled files lead the list) is open, the others closed; `toggled` holds the user's explicit choices. */
export function isGroupOpen(groups: { key: string }[], key: string, toggled: Record<string, boolean>): boolean {
  if (key in toggled) return toggled[key];
  if (!groups.length) return false;
  if (groups[0].key === key) return true;
  // loose files without a revision sit on top after a plain upload: the newest named revision stays open too
  return groups[0].key === '' && groups.length > 1 && groups[1].key === key;
}
