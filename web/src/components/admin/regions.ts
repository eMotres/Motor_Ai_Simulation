// Admin · two regions (docs/regions/DESIGN.md): pure helpers for the region column / filter and the Servers -> Regions
// card.  No runtime imports, so node --test can run it.

export type Region = 'eu' | 'us';
export const REGIONS: Region[] = ['eu', 'us'];
export type RegionFilter = 'all' | Region;

/** "eu" / " US " -> "eu" / "us"; anything else -> the default (an account without a region is EU). */
export const normRegion = (v: unknown, dflt: Region = 'eu'): Region => {
  const s = String(v ?? '').trim().toLowerCase();
  return s === 'eu' || s === 'us' ? s : dflt;
};

export const regionLabel = (r: unknown): string => normRegion(r).toUpperCase();

export const matchesRegion = (region: unknown, filter: RegionFilter): boolean =>
  filter === 'all' || normRegion(region) === filter;

/** The region of a row owned by an e-mail: the accounts map first, else EU. */
export const regionOf = (email: string | null | undefined, map: Record<string, string> | null | undefined): Region =>
  normRegion(map?.[String(email ?? '').trim().toLowerCase()]);

/** The region of a project = its owner's (the first owner when there are several). */
export const projectRegion = (
  members: Array<{ email?: string; role?: string }> | null | undefined, map: Record<string, string> | null | undefined,
): Region => {
  const owner = (members ?? []).find((m) => m?.role === 'owner');
  return regionOf(owner?.email, map);
};

export interface ServerStatus {
  region?: string; ok?: boolean; reachable?: boolean; role?: string; version?: string; commit?: string;
  load?: { cores?: number; load1?: number | null; load_pct?: number | null };
  queue?: { running?: number | null; queued?: number | null };
  disk?: { free_gb?: number | null; used_pct?: number | null };
}
export interface RegionsView { region: string; mode: 'single' | 'multi'; local: ServerStatus; peer: ServerStatus | null }

export interface ServerRow {
  region: string; self: boolean; state: 'ok' | 'down'; version: string; load: string; queue: string; disk: string;
}

const dash = '—';
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** One table row per region; single-region mode = the local server only. */
export const serverRows = (view: RegionsView | null | undefined): ServerRow[] => {
  if (!view?.local) return [];
  const row = (s: ServerStatus, self: boolean): ServerRow => {
    const up = self || (s.ok !== false && s.reachable !== false);
    const lp = num(s.load?.load_pct);
    const run = num(s.queue?.running);
    const q = num(s.queue?.queued);
    const free = num(s.disk?.free_gb);
    const used = num(s.disk?.used_pct);
    const commit = String(s.commit ?? '').slice(0, 8);
    return {
      region: regionLabel(s.region),
      self,
      state: up ? 'ok' : 'down',
      version: up ? [s.version, commit].filter(Boolean).join(' · ') || dash : dash,
      load: up && lp != null ? `${Math.round(lp)} %` : dash,
      queue: up && run != null && q != null ? `${run} / ${q}` : dash,
      disk: up && free != null ? `${Math.round(free)} GB${used != null ? ` (${Math.round(used)} %)` : ''}` : dash,
    };
  };
  const rows = [row(view.local, true)];
  if (view.peer) rows.push(row(view.peer, false));
  return rows;
};
