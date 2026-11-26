/** The Motors catalog by die (owner 2026-11-17): family header -> one compact block per die (lamination) -> its motors by
 *  the standard designation, steel / magnet / segmentation / slot insulation BESIDE the name, then (admins) the projects
 *  and the production that use it.  Every action of the list view stays reachable: the motor row's ▶ loads it at its
 *  default mode, the two ⋮ menus carry everything else.  The grouping and the current / old rule: lib/catalogByDie.ts. */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Chip, Divider, IconButton, Menu, MenuItem, Popover, Tooltip, Typography } from '@mui/material';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { useTranslation } from 'react-i18next';
import MotorThumbnail from './MotorThumbnail';
import { activeView, applyFilters, buildByDie, oldInActive, restrictTo, stageKey, COL_DIGITS, colValue, COLS, jobChip, motorCount, motorStage, NAME_COL_W, nextSort,
  projectOptions, segmentationText, sortMotors, stageOptions, stateCounts, type ByDieDie, type ByDieOverview, type ColKey, type JobsAnswer, type MotorRow, type RunningJob, type ServerView, type Sort,
  type StateFilter } from '../../lib/catalogByDie';
import { useCatalogFilterStore } from '../../stores/catalogFilterStore';
import { displayStateApi } from '../../lib/displayStateApi';
import { appLinkPath } from '../../lib/appLink';
import { ownerQueueApi } from '../../modules/overview/decisions/ownerQueueApi';
import { parseDesignation, SEGMENTS } from '../../lib/motorDesignation';
import { DesignationDecoder } from '../common/MotorDesignation';
import { NARROW } from '../../lib/narrowLayout';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export interface Rated { duty: string | null; power_kw: number | null; torque_nm: number | null; mass_kg: number | null }
/** What the list view can do, handed in so that no action is lost (FamilyCatalog owns them all). */
export interface ByDieActions {
  load: (die: string, config: string) => void;
  renameDie: (die: string) => void; toggleDieLock: (die: string) => void; dieHistory: (die: string) => void;
  duplicateDie: (die: string) => void; addConfig: (die: string) => void; deleteDie: (die: string) => void;
  retireOrArchive: (die: string) => void;
  renameCfg: (die: string, cfg: string) => void; toggleCfgLock: (die: string, cfg: string) => void;
  cfgHistory: (die: string, cfg: string) => void; duplicateCfg: (die: string, cfg: string) => void;
  moveCfg: (die: string, cfg: string, mode: 'move' | 'copy') => void;
  datasheet: (die: string, cfg: string) => void; report: (die: string, cfg: string, pdf: boolean) => void;
  toMyMotors: (die: string, cfg: string) => void; battery: (die: string, cfg: string) => void;
  deleteCfg: (die: string, cfg: string) => void;
  /** admins: archive ONE configuration (owner 2026-11-22) */
  archiveCfg?: (die: string, cfg: string) => void;
  /** only a configuration of a SHARED-catalog die is archived */
  archivable?: (die: string) => boolean;
}

interface Props {
  dies: Array<ByDieDie & { slots: number; poles: number }>; thumbs: (die: ByDieDie) => string | null | undefined;
  rated: (die: string, cfg: string) => Rated | null; active: { die?: string | null; config?: string | null } | null;
  isAdmin: boolean; canWrite: boolean; standardUser: boolean; busy: boolean; actions: ByDieActions;
  /** for the tests: the Overview answer given instead of fetched */
  overview?: ByDieOverview | null;
  /** for the tests: the running work given instead of fetched (`/api/catalog/jobs`, admins) */
  jobs?: JobsAnswer | null;
  /** the non-numeric cells and the cogging of a motor (what the list view showed and this table keeps), from FamilyCatalog */
  cells?: (die: string, config: string) => RowCells | null;
  dieExtras?: (die: string) => React.ReactNode;
  /** every die of the catalog (before the application filter): the old / current marks are computed over these */
  allDies?: Array<ByDieDie & { slots: number; poles: number }>;
  /** a line added to the name's tooltip (writers: the passport card / older-build line) */
  nameTip?: (die: string, config: string) => string;
  /** the project the tab highlights links this motor (ProjectHighlightSelect): the "only its motors" filter of every role */
  highlighted?: (die: string, config: string) => boolean;
}

/** One motor's cells beyond the quoted point: the application column, the marks column, the saved cogging. */
export interface RowCells { application?: React.ReactNode; marks?: React.ReactNode; cogging?: number | null; coggingTip?: string; coggingStale?: boolean }

const COL_TIP: Record<ColKey, string> = { power: 'power', torque: 'torque', speed: 'speed', eff: 'efficiency', ripple: 'ripple', volt: 'battery',
  current: 'current', cogging: 'cogging', mass: 'mass' };
/** the fixed widths: the designation, then one column per characteristic */
const charsGrid = { display: 'grid', gridTemplateColumns: `${NAME_COL_W}px ${COLS.map((c) => `${c.w}px`).join(' ')}`, columnGap: '8px',
  alignItems: 'center', flexShrink: 0 } as const;
/** the row actions (▶ load, ⋮ menu), right after the characteristics; the header keeps the same gap */
const ACTIONS_W = 48;
// the die header: ONE line per stamped sheet (owner 2026-11-23: "merge these two lines into one") in fixed columns, so the
// thumbnail, the name, the size, the motor count and the markers of every die line up
const HEAD_W = { name: 190, size: 110, count: 84 } as const;
/** what inside a motor row has its own click (not a load) */
const ROW_CONTROLS = 'button, a, input, textarea, select, label, [role="button"], [role="combobox"], .MuiChip-root, [data-row-click="own"]';
type ClickNode = { closest(sel: string): ClickNode | null; contains(n: unknown): boolean };
/** Whether a click on `target` inside the motor `row` is a load: inside the row's DOM (not a portal) and not on one of the
 *  row's own controls.  Pure over `closest` / `contains`, exported for the test (no DOM library in the repository). */
export function rowClickLoads(target: ClickNode | null, row: ClickNode): boolean {
  if (!target || !row.contains(target)) return false;
  const own = target.closest(ROW_CONTROLS);
  return !own || own === row || !row.contains(own);
}
const REST_W = { application: 84, marks: 130, projects: 200, stage: 96, jobs: 170, production: 96 } as const;
const markChip = { height: 18, fontSize: 10.5, maxWidth: 120, color: 'var(--text-3)', '& .MuiChip-label': { px: 0.75 } } as const;
/** The tab's scroller (App.tsx, the Motors tab) has `p: 3` = 24 px: the sticky header sits at -24 px so that it is flush with the
 *  scroller's top edge, right under the Motors title line; at 0 it stopped 24 px lower and rows showed through above it. */
const SCROLLER_PAD_PX = 24;
const JOBS_POLL_MS = 20000;

export interface MenuEntry { key: string; label: string; run: () => void; danger?: boolean }
type Who = { canWrite: boolean; isAdmin: boolean; standardUser: boolean };
type T = (k: string, o?: Record<string, unknown>) => string;

/** The die menu: every die action of the list view (pure, so the test checks each one is wired). */
export function dieMenuItems(die: ByDieDie, who: Who, a: ByDieActions, t: T): MenuEntry[] {
  if (!who.canWrite) return [];
  const n = die.name;
  return [
    { key: 'add', label: t('byDie.addConfig'), run: () => a.addConfig(n) },
    { key: 'ren', label: t('byDie.renameDie'), run: () => a.renameDie(n) },
    { key: 'his', label: t('byDie.history'), run: () => a.dieHistory(n) },
    { key: 'dup', label: t('byDie.duplicate'), run: () => a.duplicateDie(n) },
    ...(who.isAdmin ? [{ key: 'arc', label: die.layer === 'shared' ? t('retireDieConfirm') : t('archiveDieConfirm'), run: () => a.retireOrArchive(n) }] : []),
    { key: 'del', label: t('byDie.deleteDie'), run: () => a.deleteDie(n), danger: true },
  ];
}

/** The project items of the motor menu (admins; owner 2026-11-25: "there are no buttons there"): park / archive / set
 *  active each project the row shows, the same action as the project chip's menu. */
export function projectStateItems(m: Pick<MotorRow, 'projects'>, t: T, setState: (id: string, state: 'active' | 'parked' | 'archived') => void): MenuEntry[] {
  return m.projects.flatMap((p) => (['active', 'parked', 'archived'] as const).filter((st) => st !== p.state)
    .map((st) => ({ key: `prj-${p.id}-${st}`, label: t('byDie.projectMenu.item', { project: p.title, action: t(`byDie.projectMenu.${st}`) }),
      run: () => setState(p.id, st) })));
}

/** The motor menu: every configuration action of the list view (the lock of the row, the load is ▶). */
export function motorMenuItems(m: Pick<MotorRow, 'die' | 'config'>, who: Who, a: ByDieActions, t: T): MenuEntry[] {
  const { die, config } = m;
  const out: MenuEntry[] = [];
  if (who.canWrite) {
    out.push({ key: 'ren', label: t('byDie.renameMotor'), run: () => a.renameCfg(die, config) },
      { key: 'lock', label: t('byDie.lockMotor'), run: () => a.toggleCfgLock(die, config) },
      { key: 'his', label: t('byDie.history'), run: () => a.cfgHistory(die, config) },
      { key: 'dup', label: t('byDie.duplicate'), run: () => a.duplicateCfg(die, config) },
      { key: 'bat', label: t('byDie.battery'), run: () => a.battery(die, config) });
    if (who.isAdmin) out.push({ key: 'mov', label: t('byDie.move'), run: () => a.moveCfg(die, config, 'move') },
      { key: 'cpy', label: t('byDie.copy'), run: () => a.moveCfg(die, config, 'copy') });
    if (who.isAdmin && a.archiveCfg && (a.archivable?.(die) ?? true)) out.push({ key: 'arch', label: t('byDie.archiveMotor'), run: () => a.archiveCfg?.(die, config) });
  }
  out.push({ key: 'ds', label: t('byDie.datasheet'), run: () => a.datasheet(die, config) },
    { key: 'rep', label: t('byDie.report'), run: () => a.report(die, config, false) },
    { key: 'pdf', label: t('byDie.reportPdf'), run: () => a.report(die, config, true) });
  if (!who.canWrite && !who.standardUser) out.push({ key: 'mine', label: t('byDie.toMyMotors'), run: () => a.toMyMotors(die, config) });
  if (who.canWrite) out.push({ key: 'del', label: t('byDie.deleteMotor'), run: () => a.deleteCfg(die, config), danger: true });
  return out;
}

const fmt = (v: number | null | undefined, d = 1) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : v.toFixed(d));
const small = { fontSize: 11.5, color: 'var(--text-3)' };

const CatalogByDie: React.FC<Props> = ({ dies, thumbs, rated, active, isAdmin, canWrite, standardUser, busy, actions, overview: given,
  cells: cellsOf, dieExtras, nameTip, jobs: givenJobs, highlighted, allDies }) => {
  const { t } = useTranslation(['motors', 'common', 'development']);
  // per render: does this row's die block mix two shaft-power conventions (filled by sortBlock, read by cell)
  const convMixed = new Map<MotorRow, boolean>();
  const [overview, setOverview] = useState<ByDieOverview | null>(given ?? null);
  const [dieMenu, setDieMenu] = useState<{ el: HTMLElement; die: ByDieDie } | null>(null);
  const [cfgMenu, setCfgMenu] = useState<{ el: HTMLElement; m: MotorRow } | null>(null);
  const [decode, setDecode] = useState<{ el: HTMLElement; m: MotorRow } | null>(null);
  const [sort, setSort] = useState<Sort>(null);
  // Active / Parked / Archived of the linked projects (owner 2026-11-17: "how do I remove old projects from the catalog")
  // the filters live in the Motors title line (components/catalog/CatalogFilters); this table applies them
  const filter = useCatalogFilterStore((s) => s.filter);
  const setFilterOptions = useCatalogFilterStore((s) => s.setOptions);
  const stateFilter: StateFilter = filter.state;
  const [projMenu, setProjMenu] = useState<{ el: HTMLElement; id: string; title: string } | null>(null);
  // the "old" chip of a row opens its one action, Archive (owner 2026-11-25: the archive must be findable on the row)
  const [oldMenu, setOldMenu] = useState<{ el: HTMLElement; m: MotorRow } | null>(null);
  const [overviewGen, setOverviewGen] = useState(0);
  // the running work per motor and server (admins only; the route is admin-gated), refreshed while the page is open
  const [jobs, setJobs] = useState<JobsAnswer | null>(givenJobs ?? null);
  useEffect(() => {
    if (givenJobs !== undefined || !isAdmin) return;
    let alive = true;
    const pull = () => fetch(`${API}/api/catalog/jobs`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null))
      .then((a) => { if (alive) setJobs(a as JobsAnswer | null); }).catch(() => { /* the strip says "no status" */ });
    void pull();
    const id = window.setInterval(() => { if (typeof document === 'undefined' || document.visibilityState === 'visible') void pull(); }, JOBS_POLL_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [isAdmin, givenJobs]);
  const cells = cellsOf;
  useEffect(() => {
    if (given !== undefined || !isAdmin) return;           // the Overview is admin-only (route-gated): nobody else asks
    let alive = true;
    fetch(`${API}/api/overview`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null))
      .then((a) => { if (alive) setOverview(a as ByDieOverview | null); }).catch(() => { /* rows without links */ });
    return () => { alive = false; };
  }, [isAdmin, given, overviewGen]);
  // marks over the WHOLE catalog, then only the shown dies (a mark never depends on the application filter)
  const allGroups = useMemo(() => restrictTo(buildByDie(allDies ?? dies, isAdmin ? overview : null, { staff: isAdmin }), dies),
    [allDies, dies, overview, isAdmin]);
  // the state filter needs the Overview's links: admins only (a customer's view is the whole catalog, as before)
  const showState = isAdmin && !!overview;
  // Active leaves out the motors marked old unless "Show old" is on (owner 2026-11-25); the tab counts say what is shown
  const stateGroups = useMemo(() => (showState ? activeView(allGroups, stateFilter, filter.showOld) : allGroups),
    [allGroups, showState, stateFilter, filter.showOld]);
  const counts = useMemo(() => stateCounts(allGroups, filter.showOld), [allGroups, filter.showOld]);
  const oldCount = useMemo(() => (showState ? oldInActive(allGroups) : 0), [allGroups, showState]);
  // the stage / project / running filters are the admin's (they read the Overview and the jobs); "only the highlighted
  // project's motors" is everybody's (their own project, through the project selector of the tab)
  const effFilter = isAdmin ? filter : { ...filter, stages: [], projects: [], running: false };
  const groups = useMemo(() => applyFilters(stateGroups, effFilter,
    (d, c) => !!jobs?.motors?.[`${d}/${c}`]?.length, highlighted),
    [stateGroups, effFilter.stages.join('|'), effFilter.projects.join('|'), effFilter.running, effFilter.onlyHighlighted, jobs, highlighted]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setFilterOptions({ stages: isAdmin ? stageOptions(allGroups) : [], projects: isAdmin ? projectOptions(allGroups) : [],
      counts, shown: motorCount(groups), total: motorCount(allGroups), old: oldCount });
  }, [allGroups, groups, counts, oldCount, isAdmin, setFilterOptions]);
  const setProjectState = async (id: string, state: 'active' | 'parked' | 'archived') => {
    try { await displayStateApi.set({ kind: 'project', project_id: id }, state); } catch { /* the chip stays as it was */ }
    setOverviewGen((n) => n + 1);                               // re-read the Overview: the chip moves to its state
  };

  // owner queue (2026-11-25): the project's priority, written through the queue's own route (admin only), then the Overview is re-read
  const setProjectPriority = async (id: string, p: 'urgent' | 'normal' | 'low') => {
    try { await ownerQueueApi.setPriority(id, p); } catch { /* not an admin, or the store needs attention: nothing changes */ }
    setOverviewGen((n) => n + 1);
  };

  // the four per-motor parameters left the row (owner 2026-11-17); they are in the "?" popover, under the name's segments
  const materials = (m: MotorRow) => [
    { label: t('byDie.m.steel'), value: m.beside.steel || '—' }, { label: t('byDie.m.magnet'), value: m.beside.magnet || '—' },
    { label: t('byDie.m.segmentation'), value: segmentationText(m.beside.magnet_lamination_mm, { solid: t('byDie.solid'), slice: (mm) => t('byDie.slice', { mm }) }) || '—' },
    { label: t('byDie.m.insulation'), value: m.beside.slot_insulation || '—' }];
  const segmentsOf = (m: MotorRow) => {
    const f = m.named ? parseDesignation(m.label) : null;
    return f ? Object.fromEntries(SEGMENTS.filter((s) => s !== 'rev' || f.rev).map((s) => [s, { value: f[s], source: 'name' }])) : {};
  };
  // ── the motor rows as a TABLE (owner 2026-11-17): the characteristics first, one fixed column each, then the rest ──
  const cellTip = (key: ColKey, m: MotorRow): string => {
    const v = m.values;
    if (key === 'ripple' && v?.ripple_low) return t('byDie.v.rippleLow', { steps: v.ripple_low.steps ?? '—', rows: v.ripple_low.gap_layers ?? '—' });
    if (key === 'volt') {
      return v?.voltage?.kind === 'battery'
        ? t('byDie.v.battery', { min: v.voltage.min ?? '—', max: v.voltage.max ?? '—', cells: v.voltage.cells ?? '—' }) : t('byDie.v.vllPeak');
    }
    if (key === 'cogging') return cells?.(m.die, m.config)?.coggingTip ?? t('byDie.v.cogging');
    if (key === 'power') return t('byDie.v.power', { duty: m.rated?.name ?? '' });
    return t(`byDie.v.${COL_TIP[key]}`);
  };
  const cell = (key: ColKey, m: MotorRow) => {
    const x = colValue(key, m, cells?.(m.die, m.config)?.cogging ?? null);
    const oldConv = convMixed.get(m) === true && m.values?.shaft_conv !== 1 && (key === 'power' || key === 'torque' || key === 'eff');
    const muted = (key === 'ripple' && !!m.values?.ripple_low) || (key === 'cogging' && !!cells?.(m.die, m.config)?.coggingStale);
    return (
      <Tooltip key={key} title={oldConv ? `${t('common:shaftConvOld')} ${cellTip(key, m)}` : cellTip(key, m)}>
        <Box component="span" data-testid={`by-die-v-${key}`}
          sx={{ textAlign: 'right', color: muted ? 'var(--text-4)' : 'var(--text-2)', cursor: 'help', whiteSpace: 'nowrap' }}>
          {x === null ? '—' : `${x.toFixed(COL_DIGITS[key])}${oldConv ? '†' : ''}`}
        </Box>
      </Tooltip>
    );
  };
  const run = (fn: () => void) => () => { setDieMenu(null); setCfgMenu(null); setProjMenu(null); setOldMenu(null); fn(); };
  const canArchive = (die: string) => isAdmin && canWrite && !!actions.archiveCfg && (actions.archivable?.(die) ?? true);
  const stageLabel = (s: string | null) => (!s ? '' : t(stageKey(s), { defaultValue: s }));
  const jobTip = (j: RunningJob) => t('byDie.job.tip', { tag: j.tag || '—', session: j.session || '—', since: j.started_at || '—',
    cpus: j.cpus_reserved ?? '—', used: j.cpu_used ?? '—', mem: j.mem_reserved_gb ?? '—', now: j.mem_now_gb ?? '—' });
  const jobChipEl = (j: RunningJob) => (
    <Tooltip key={`${j.server}/${j.tag}`} title={jobTip(j)}>
      <Chip size="small" data-testid="by-die-job" label={jobChip(j)}
        sx={{ height: 18, fontSize: 10.5, color: j.state === 'running' ? '#34d399' : 'var(--text-3)', borderColor: 'currentColor' }} variant="outlined" />
    </Tooltip>
  );
  const serverText = (s: ServerView) => {
    const name = (s.server || '?').toUpperCase();
    if (s.state === 'missing' || s.state === 'no_status') return t('byDie.server.noStatus', { name });
    const tt = s.totals ?? {}, b = s.budget ?? {};
    const load = t('byDie.server.load', { name, cpus: tt.cpus_reserved ?? '—', used: tt.cpus_used ?? '—', budget: b.cpus ?? '—',
      mem: tt.mem_reserved_gb ?? '—', memUsed: tt.mem_used_gb ?? '—', memBudget: b.mem_gb ?? '—', queued: tt.queued ?? 0 });
    return s.state === 'stale' ? `${load} · ${t('byDie.server.stale', { min: Math.round((s.age_s ?? 0) / 60) })}` : load;
  };
  const topLine = isAdmin && jobs ? (
    <Box data-testid="by-die-topline" sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', ...small }}>
      {jobs?.servers?.map((s) => (
        <Typography key={s.server} component="span" data-testid="by-die-server"
          sx={{ ...small, color: s.state === 'live' ? 'var(--text-2)' : '#f59e0b' }}>{serverText(s)}</Typography>
      ))}
      {!!jobs?.unnamed?.length && <Typography component="span" sx={small}>{t('byDie.server.unnamed', { n: jobs.unnamed.length })}</Typography>}
      {!!jobs?.unknown?.length && (
        <Tooltip title={jobs.unknown.map((j) => `${j.tag}: ${j.die}`).join(', ')}>
          <Typography component="span" sx={{ ...small, color: '#f59e0b' }}>{t('byDie.server.unknownDie', { n: jobs.unknown.length })}</Typography>
        </Tooltip>
      )}
    </Box>
  ) : null;

  /** A click on the row (the name included) loads the motor at its default mode, like ▶; the row's own controls (the "?"
   *  decoder, the application editor, a chip, the menus) keep their clicks, and a click inside a dialog or menu opened from
   *  the row (a portal: React bubbles it here, the DOM does not contain it) is not a load. */
  const rowClick = (m: MotorRow) => (e: React.MouseEvent<HTMLElement>) => {
    if (rowClickLoads(e.target as Element | null, e.currentTarget)) actions.load(m.die, m.config);
  };

  const motorRow = (m: MotorRow) => {
    const r = rated(m.die, m.config);                       // the default mode: what ▶ loads
    const loaded = active?.die === m.die && active?.config === m.config;
    const nameTipBase = !m.named ? (isAdmin ? t('byDie.noNameTip') : '')
      : !m.complete ? t('byDie.incompleteTip', { missing: m.unknown.join(', ') }) : '';   // a named motor: no old name anywhere
    const tipText = [nameTipBase, nameTip?.(m.die, m.config) ?? ''].filter(Boolean).join(' · ');
    const extra = cells?.(m.die, m.config);
    const projects = m.projects.map((p) => p.title).join(', ');
    const canLoad = !busy && !!r?.duty;
    return (
      <Box key={`${m.die}/${m.config}`} data-testid="by-die-motor" data-old={m.old ? 'true' : undefined}
        data-loadable={canLoad ? 'true' : undefined} onClick={canLoad ? rowClick(m) : undefined}
        sx={{ display: 'flex', alignItems: 'center', columnGap: 1, flexWrap: 'wrap', py: 0.2, minHeight: 26,
              borderTop: '1px solid var(--panel)', opacity: m.old ? 0.5 : 1,
              ...(canLoad ? { cursor: 'pointer', '&:hover': { bgcolor: 'var(--panel)' } } : {}) }}>
        {/* 1-2: designation and the characteristics, fixed columns: they line up across every family and never reorder */}
        <Box data-testid="by-die-chars" sx={{ ...charsGrid, ...small, fontVariantNumeric: 'tabular-nums' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', minWidth: 0 }}>
            <Tooltip title={tipText}>
              <Typography component="span" noWrap sx={{ fontFamily: 'monospace', fontSize: 12.5, fontWeight: 700,
                color: loaded ? '#60a5fa' : m.named && m.complete ? 'var(--text-1)' : 'var(--text-4)' }}>
                {m.label || t('byDie.namePending')}{loaded ? ' ●' : ''}
              </Typography>
            </Tooltip>
            {m.named && (
              <IconButton size="small" aria-label={t('byDie.decodeTip')} data-testid="by-die-decode" onClick={(e) => setDecode({ el: e.currentTarget, m })}
                sx={{ p: 0.15, color: 'var(--text-4)' }}><HelpOutlineIcon sx={{ fontSize: 13 }} /></IconButton>
            )}
          </Box>
          {COLS.map((c) => cell(c.key, m))}
        </Box>
        {/* 3: the row actions, right after the characteristics (owner 2026-11-17: "they are not clickable"; at the far right
            end of a 1974 px row the ▶ went unnoticed or off-screen): always on the first line, at 1280 px too */}
        <Box data-testid="by-die-actions" sx={{ width: ACTIONS_W, display: 'flex', alignItems: 'center', flexShrink: 0 }}>
          <Tooltip title={t('byDie.loadTip')}>
            <span>
              <IconButton size="small" disabled={!canLoad} aria-label={t('byDie.loadTip')} data-testid="by-die-load"
                onClick={() => actions.load(m.die, m.config)} sx={{ p: 0.25, color: '#60a5fa' }}>
                <PlayArrowIcon sx={{ fontSize: 16 }} />
              </IconButton>
            </span>
          </Tooltip>
          <IconButton size="small" aria-label={t('byDie.motorMenu')} data-testid="by-die-motor-menu" onClick={(e) => setCfgMenu({ el: e.currentTarget, m })}
            sx={{ p: 0.25, color: 'var(--text-4)' }}><MoreVertIcon sx={{ fontSize: 15 }} /></IconButton>
        </Box>
        {/* 4: everything else, fixed columns too; at a narrow width this part wraps under the row first */}
        <Box data-testid="by-die-rest" sx={{ display: 'flex', alignItems: 'center', columnGap: 1, flexWrap: 'wrap', flex: '1 1 320px', minWidth: 0, ...small }}>
          <Box sx={{ width: REST_W.application, flexShrink: 0, overflow: 'hidden' }}>{extra?.application}</Box>
          <Box data-testid="by-die-marks" sx={{ width: REST_W.marks, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 0.5, overflow: 'hidden' }}>
            {extra?.marks}
            {/* one short chip each, the reason in the tooltip: a sentence wrapped the Marks column on three lines (browser finding) */}
            {m.old && (
              <Tooltip title={canArchive(m.die) ? `${t('byDie.oldTip')} · ${t('byDie.oldArchiveTip')}` : t('byDie.oldTip')}>
                <Chip size="small" variant="outlined" label={t('byDie.old')} data-testid="by-die-old"
                  {...(canArchive(m.die) ? { onClick: (e: React.MouseEvent<HTMLElement>) => setOldMenu({ el: e.currentTarget, m }),
                    onDelete: (e: React.SyntheticEvent) => setOldMenu({ el: (e.currentTarget as HTMLElement).closest('.MuiChip-root') as HTMLElement, m }),
                    deleteIcon: <ArrowDropDownIcon data-testid="by-die-old-open" /> } : {})} sx={markChip} />
              </Tooltip>
            )}
            {m.newestLocked && (
              <Tooltip title={`${t('byDie.newestLocked')}: ${t('byDie.newestLockedTip')}`}>
                <Chip size="small" variant="outlined" label={t('byDie.newestShort')} data-testid="by-die-newest-locked" sx={markChip} />
              </Tooltip>
            )}
          </Box>
          {/* the projects BEFORE stage / running (owner 2026-11-25: at the far right the chips were cut off) */}
          {isAdmin && (
            <Tooltip title={projects}>
              <Box data-testid="by-die-projects" sx={{ ...small, width: REST_W.projects, flexShrink: 0, display: 'flex', gap: 0.5, overflow: 'hidden', whiteSpace: 'nowrap' }}>
                {m.projects.map((p) => (
                  <Chip key={p.id} size="small" variant="outlined" label={p.title} data-testid="by-die-project-chip"
                    onClick={(e) => setProjMenu({ el: e.currentTarget, id: p.id, title: p.title })}
                    sx={{ height: 18, fontSize: 10.5, maxWidth: 160, ...(p.trial ? { borderStyle: 'dashed' } : {}), ...(p.state !== 'active' ? { opacity: 0.6 } : {}) }} />
                ))}
                {!m.projects.length && m.computed && !m.productions.length ? <span>{t('byDie.computed')}</span> : null}
              </Box>
            </Tooltip>
          )}
          {isAdmin && (
            <Typography component="span" noWrap data-testid="by-die-stage" sx={{ ...small, width: REST_W.stage, flexShrink: 0, color: 'var(--text-2)' }}>
              {stageLabel(motorStage(m))}
            </Typography>
          )}
          {isAdmin && (
            <Box data-testid="by-die-jobs" sx={{ width: REST_W.jobs, flexShrink: 0, display: 'flex', gap: 0.5, overflow: 'hidden' }}>
              {(jobs?.motors?.[`${m.die}/${m.config}`] ?? []).map(jobChipEl)}
            </Box>
          )}
          {isAdmin && (
            <Typography component="span" noWrap data-testid="by-die-production" sx={{ ...small, width: REST_W.production, flexShrink: 0 }}>
              {m.productions.map((p) => t('byDie.production', { rev: p.rev || '—' })).join(', ')}
            </Typography>
          )}
        </Box>
      </Box>
    );
  };
  // a block whose rows carry two shaft-power conventions marks its earlier rows' power, torque and efficiency "†"
  // (owner 2026-11-25: never two conventions read as one; the sort keeps them apart, the mark says why)
  const mixedConvBlock = (motors: MotorRow[]) => new Set(motors.map((m) => (m.values?.shaft_conv === 1 ? 1 : 0))).size > 1;
  const sortBlock = (motors: MotorRow[]) => {
    const mixed = mixedConvBlock(motors);
    for (const m of motors) convMixed.set(m, mixed);
    return sort ? sortMotors(motors, sort.key, sort.dir, (m) => cells?.(m.die, m.config)?.cogging ?? null) : motors;
  };
  const header = (
    <Box data-testid="by-die-header" sx={{ position: 'sticky', top: -SCROLLER_PAD_PX, zIndex: 2, bgcolor: 'var(--panel-2)', display: 'flex', alignItems: 'flex-end',
      columnGap: 1, flexWrap: 'wrap', px: 1, py: 0.3, borderBottom: '1px solid var(--line-soft)', fontSize: 10, color: 'var(--text-3)' }}>
      <Box sx={{ ...charsGrid }}>
        <Box component="span">{t('byDie.col.motor')}</Box>
        {COLS.map((c) => (
          <Tooltip key={c.key} title={t('byDie.col.sortTip')}>
            <Box component="span" role="button" data-testid={`by-die-sort-${c.key}`} onClick={() => setSort(nextSort(sort, c.key))}
              sx={{ textAlign: 'right', cursor: 'pointer', userSelect: 'none', lineHeight: 1.15, color: sort?.key === c.key ? 'var(--text-1)' : undefined }}>
              {t(`byDie.col.${c.key}`)}<br />{c.unit}{sort?.key === c.key ? (sort.dir === 'desc' ? ' ▾' : ' ▴') : ''}
            </Box>
          </Tooltip>
        ))}
      </Box>
      <Box component="span" sx={{ width: ACTIONS_W, flexShrink: 0 }} />
      <Box sx={{ display: 'flex', columnGap: 1, flexWrap: 'wrap', flex: '1 1 320px', minWidth: 0 }}>
        <Box component="span" sx={{ width: REST_W.application, flexShrink: 0 }}>{t('byDie.col.application')}</Box>
        <Box component="span" sx={{ width: REST_W.marks, flexShrink: 0 }}>{t('byDie.col.marks')}</Box>
        {isAdmin && <Box component="span" sx={{ width: REST_W.projects, flexShrink: 0 }}>{t('byDie.col.projects')}</Box>}
        {isAdmin && <Box component="span" sx={{ width: REST_W.stage, flexShrink: 0 }}>{t('byDie.col.stage')}</Box>}
        {isAdmin && <Box component="span" sx={{ width: REST_W.jobs, flexShrink: 0 }}>{t('byDie.col.jobs')}</Box>}
        {isAdmin && <Box component="span" sx={{ width: REST_W.production, flexShrink: 0 }}>{t('byDie.col.production')}</Box>}
      </Box>
    </Box>
  );

  return (
    // NO overflow on the catalog itself on a desktop: an overflow box would become the sticky header's scroll container
    // and the header would scroll away with the list (browser finding on #738); only a phone scrolls the table sideways
    <Box data-testid="catalog-by-die" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, [NARROW]: { overflowX: 'auto' } }}>
      {topLine}
      {header}
      {groups.map((g) => (
        <Box key={g.family || '-'} data-testid="by-die-family" sx={{ border: '1px solid var(--line-soft)', borderRadius: 1, overflow: 'hidden', ...(g.named ? {} : { opacity: 0.6 }) }}>
          {g.blocks.map((b, bi) => (
            <Box key={b.sheet} data-testid="by-die-block" data-members={b.members.length}
              sx={{ px: 1, py: 0.4, ...(bi ? { borderTop: '1px solid var(--line-soft)' } : {}) }}>
              <Box data-testid="by-die-head" sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', bgcolor: 'var(--panel)', mx: -1, my: -0.4, px: 1, py: 0.4, [NARROW]: { gap: 0.5 } }}>
                <Box sx={{ lineHeight: 0, flexShrink: 0 }}>
                  <MotorThumbnail slots={b.die.slots ?? 0} poles={b.die.poles ?? 0} size={28} thumbSvg={thumbs(b.die) ?? undefined} />
                </Box>
                <Tooltip title={g.named ? g.family : ''}>
                  <Typography noWrap sx={{ width: HEAD_W.name, flexShrink: 0, fontSize: 13, fontWeight: 800, color: g.named ? 'var(--text-0)' : 'var(--text-3)' }}>
                    {g.named ? g.family : isAdmin ? t('byDie.noName') : t('byDie.otherMotors')}
                  </Typography>
                </Tooltip>
                <Typography noWrap sx={{ ...small, width: HEAD_W.size, flexShrink: 0, color: 'var(--text-2)' }}>
                  Ø{fmt(b.die.stator_diameter, 0)} · {b.die.slots}s{b.die.poles}p
                </Typography>
                {/* the motor count wraps LAST on a narrow screen (after the markers), never the name */}
                <Typography noWrap data-testid="by-die-count" sx={{ ...small, width: HEAD_W.count, flexShrink: 0, [NARROW]: { order: 9, width: 'auto' } }}>
                  {t('byDie.motors', { n: b.motors.length })}
                </Typography>
                {/* one header per stamped sheet (owner 2026-11-17); every stored record of it keeps its own lock, extras,
                    running work and menu as one compact mark, never as a header of its own */}
                {b.members.map((d) => (
                  <Box key={d.name} data-testid="by-die-record" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.25,
                    ...(b.members.length > 1 ? { border: '1px solid var(--line-soft)', borderRadius: 1, px: 0.25 } : {}) }}>
                    {b.members.length > 1 && (
                      <Tooltip title={isAdmin ? t('byDie.recordTip', { name: d.name, n: b.motors.filter((m) => m.die === d.name).length }) : ''}>
                        <Typography component="span" sx={{ ...small, color: 'var(--text-3)' }}>{b.motors.filter((m) => m.die === d.name).length}</Typography>
                      </Tooltip>
                    )}
                    {dieExtras?.(d.name)}
                    {isAdmin && (jobs?.dies?.[d.name] ?? []).map(jobChipEl)}
                    {canWrite && (
                      <Tooltip title={d.locked ? t('byDie.lockedTip') : t('byDie.unlockedTip')}>
                        <span>
                          <IconButton size="small" disabled={busy} onClick={() => actions.toggleDieLock(d.name)} data-testid="by-die-lock"
                            sx={{ fontSize: 11, p: 0.2, color: d.locked ? '#fbbf24' : 'var(--text-4)' }}>{d.locked ? '🔒' : '🔓'}</IconButton>
                        </span>
                      </Tooltip>
                    )}
                    {canWrite && b.members.length > 1 && (
                      <IconButton size="small" aria-label={t('byDie.dieMenu')} data-testid="by-die-die-menu" onClick={(e) => setDieMenu({ el: e.currentTarget, die: d })}
                        sx={{ p: 0.15, color: 'var(--text-4)' }}><MoreVertIcon sx={{ fontSize: 14 }} /></IconButton>
                    )}
                  </Box>
                ))}
                <Box sx={{ flex: 1 }} />
                {canWrite && b.members.length === 1 && (
                  <IconButton size="small" aria-label={t('byDie.dieMenu')} data-testid="by-die-die-menu" onClick={(e) => setDieMenu({ el: e.currentTarget, die: b.die })}
                    sx={{ p: 0.25, color: 'var(--text-4)' }}><MoreVertIcon sx={{ fontSize: 15 }} /></IconButton>
                )}
              </Box>
              {sortBlock(b.motors).map(motorRow)}
            </Box>
          ))}
        </Box>
      ))}
      <Popover open={!!decode} anchorEl={decode?.el ?? null} onClose={() => setDecode(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
        {decode && <DesignationDecoder segments={segmentsOf(decode.m) as never} materials={materials(decode.m)} />}
      </Popover>
      <Menu open={!!projMenu} anchorEl={projMenu?.el ?? null} onClose={() => setProjMenu(null)}>
        {projMenu && [
          <MenuItem key="open" dense component="a" href={appLinkPath({ kind: 'project', id: projMenu.id }) ?? undefined}>{t('byDie.projectMenu.open')}</MenuItem>,
          ...(['active', 'parked', 'archived'] as const).map((st) => (
            <MenuItem key={st} dense onClick={run(() => void setProjectState(projMenu.id, st))}>{t(`byDie.projectMenu.${st}`)}</MenuItem>
          )),
          ...(isAdmin ? (['urgent', 'normal', 'low'] as const).map((p) => (
            <MenuItem key={`prio-${p}`} dense data-testid={`project-priority-${p}`} onClick={run(() => void setProjectPriority(projMenu.id, p))}>
              {t('common:ownerQueue.priorityMenu', { priority: t(`common:ownerQueue.priority.${p}`) })}</MenuItem>
          )) : []),
        ]}
      </Menu>
      <Menu open={!!dieMenu} anchorEl={dieMenu?.el ?? null} onClose={() => setDieMenu(null)}>
        {dieMenu && dieMenuItems(dieMenu.die, { canWrite, isAdmin, standardUser }, actions, t).map((it) => (
          <MenuItem key={it.key} dense sx={it.danger ? { color: '#f87171' } : undefined} onClick={run(it.run)}>{it.label}</MenuItem>
        ))}
      </Menu>
      <Menu open={!!cfgMenu} anchorEl={cfgMenu?.el ?? null} onClose={() => setCfgMenu(null)}>
        {cfgMenu && motorMenuItems(cfgMenu.m, { canWrite, isAdmin, standardUser }, actions, t).map((it) => (
          <MenuItem key={it.key} dense sx={it.danger ? { color: '#f87171' } : undefined} onClick={run(it.run)}>{it.label}</MenuItem>
        ))}
        {cfgMenu && isAdmin && cfgMenu.m.projects.length > 0 && <Divider />}
        {cfgMenu && isAdmin && projectStateItems(cfgMenu.m, t, (id, st) => void setProjectState(id, st)).map((it) => (
          <MenuItem key={it.key} dense data-testid="by-die-motor-project-state" onClick={run(it.run)}>{it.label}</MenuItem>
        ))}
      </Menu>
      <Menu open={!!oldMenu} anchorEl={oldMenu?.el ?? null} onClose={() => setOldMenu(null)}>
        {oldMenu && (
          <MenuItem dense data-testid="by-die-old-archive" onClick={run(() => actions.archiveCfg?.(oldMenu.m.die, oldMenu.m.config))}>
            {t('byDie.archiveMotor')}
          </MenuItem>
        )}
      </Menu>
    </Box>
  );
};

export default CatalogByDie;
