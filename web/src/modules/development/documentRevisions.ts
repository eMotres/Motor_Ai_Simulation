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

/** One release group as the server lists it (GET /documents `releases`, owner 2026-11-24): the current release first, then the
 *  unlabelled "general documents", then the superseded groups newest first.  Computed by the server from what THIS caller sees. */
export interface ReleaseInfo { revision: string; current: boolean; superseded: boolean; count: number; latest_upload: string }

export interface ReleaseGroup<T> extends RevisionGroup<T> { current: boolean; superseded: boolean }

/** The revision groups in the server's release order, each marked current / superseded.  A group the server did not list (an
 *  archived-only group shown with "show archived") keeps its place after the listed ones and is neither current nor superseded. */
export function orderByRelease<T extends { revision?: string | null }>(groups: RevisionGroup<T>[], releases: ReleaseInfo[] | undefined): ReleaseGroup<T>[] {
  const info = new Map((releases ?? []).map((r, i) => [r.revision, { ...r, i }]));
  const rank = (key: string) => info.get(key)?.i ?? Number.MAX_SAFE_INTEGER;
  return groups
    .map((g, i) => ({ g, i }))
    .sort((a, b) => rank(a.g.key) - rank(b.g.key) || a.i - b.i)
    .map(({ g }) => ({ ...g, current: !!info.get(g.key)?.current, superseded: !!info.get(g.key)?.superseded }));
}

/** Release view default: the current release and the general documents are open, a superseded group is closed; `toggled` wins. */
export function isReleaseGroupOpen(group: { key: string; current: boolean; superseded: boolean }, toggled: Record<string, boolean>): boolean {
  if (group.key in toggled) return toggled[group.key];
  return !group.superseded;
}
