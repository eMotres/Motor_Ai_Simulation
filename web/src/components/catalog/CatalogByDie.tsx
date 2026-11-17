/** The Motors catalog by die (owner 2026-11-17): family header -> one compact block per die (lamination) -> its motors by
 *  the standard designation, steel / magnet / segmentation / slot insulation BESIDE the name, then (admins) the projects
 *  and the production that use it.  Every action of the list view stays reachable: the motor row's ▶ loads it at its
 *  default mode, the two ⋮ menus carry everything else.  The grouping and the current / old rule: lib/catalogByDie.ts. */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, IconButton, Menu, MenuItem, Popover, Tooltip, Typography } from '@mui/material';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { useTranslation } from 'react-i18next';
import MotorThumbnail from './MotorThumbnail';
import { buildByDie, COL_DIGITS, colValue, COLS, NAME_COL_W, nextSort, segmentationText, sortMotors, type ByDieDie, type ByDieOverview,
  type ColKey, type MotorRow, type Sort } from '../../lib/catalogByDie';
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
}

interface Props {
  dies: Array<ByDieDie & { slots: number; poles: number }>; thumbs: (die: ByDieDie) => string | null | undefined;
  rated: (die: string, cfg: string) => Rated | null; active: { die?: string | null; config?: string | null } | null;
  isAdmin: boolean; canWrite: boolean; standardUser: boolean; busy: boolean; actions: ByDieActions;
  /** for the tests: the Overview answer given instead of fetched */
  overview?: ByDieOverview | null;
  /** the non-numeric cells and the cogging of a motor (what the list view showed and this table keeps), from FamilyCatalog */
  cells?: (die: string, config: string) => RowCells | null;
  dieExtras?: (die: string) => React.ReactNode;
  /** a line added to the name's tooltip (writers: the passport card / older-build line) */
  nameTip?: (die: string, config: string) => string;
}

/** One motor's cells beyond the quoted point: the application column, the marks column, the saved cogging. */
export interface RowCells { application?: React.ReactNode; marks?: React.ReactNode; cogging?: number | null; coggingTip?: string; coggingStale?: boolean }

const COL_TIP: Record<ColKey, string> = { power: 'power', torque: 'torque', speed: 'speed', eff: 'efficiency', ripple: 'ripple', volt: 'battery',
  current: 'current', cogging: 'cogging', mass: 'mass' };
/** the fixed widths: the designation, then one column per characteristic */
const charsGrid = { display: 'grid', gridTemplateColumns: `${NAME_COL_W}px ${COLS.map((c) => `${c.w}px`).join(' ')}`, columnGap: '8px',
  alignItems: 'center', flexShrink: 0 } as const;
const REST_W = { application: 84, marks: 130, production: 96 } as const;

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
  cells: cellsOf, dieExtras, nameTip }) => {
  const { t } = useTranslation('motors');
  const [overview, setOverview] = useState<ByDieOverview | null>(given ?? null);
  const [dieMenu, setDieMenu] = useState<{ el: HTMLElement; die: ByDieDie } | null>(null);
  const [cfgMenu, setCfgMenu] = useState<{ el: HTMLElement; m: MotorRow } | null>(null);
  const [decode, setDecode] = useState<{ el: HTMLElement; m: MotorRow } | null>(null);
  const [sort, setSort] = useState<Sort>(null);
  const cells = cellsOf;
  useEffect(() => {
    if (given !== undefined || !isAdmin) return;           // the Overview is admin-only (route-gated): nobody else asks
    let alive = true;
    fetch(`${API}/api/overview`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null))
      .then((a) => { if (alive) setOverview(a as ByDieOverview | null); }).catch(() => { /* rows without links */ });
    return () => { alive = false; };
  }, [isAdmin, given]);
  const groups = useMemo(() => buildByDie(dies, isAdmin ? overview : null, { staff: isAdmin }), [dies, overview, isAdmin]);

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
    const muted = (key === 'ripple' && !!m.values?.ripple_low) || (key === 'cogging' && !!cells?.(m.die, m.config)?.coggingStale);
    return (
      <Tooltip key={key} title={cellTip(key, m)}>
        <Box component="span" data-testid={`by-die-v-${key}`}
          sx={{ textAlign: 'right', color: muted ? 'var(--text-4)' : 'var(--text-2)', cursor: 'help', whiteSpace: 'nowrap' }}>
          {x === null ? '—' : x.toFixed(COL_DIGITS[key])}
        </Box>
      </Tooltip>
    );
  };
  const run = (fn: () => void) => () => { setDieMenu(null); setCfgMenu(null); fn(); };

  const motorRow = (m: MotorRow) => {
    const r = rated(m.die, m.config);                       // the default mode: what ▶ loads
    const loaded = active?.die === m.die && active?.config === m.config;
    const nameTipBase = !m.named ? (isAdmin ? t('byDie.noNameTip') : '')
      : !m.complete ? t('byDie.incompleteTip', { missing: m.unknown.join(', ') }) : '';   // a named motor: no old name anywhere
    const tipText = [nameTipBase, nameTip?.(m.die, m.config) ?? ''].filter(Boolean).join(' · ');
    const extra = cells?.(m.die, m.config);
    const projects = m.projects.map((p) => p.title).join(', ');
    return (
      <Box key={`${m.die}/${m.config}`} data-testid="by-die-motor" data-old={m.old ? 'true' : undefined}
        sx={{ display: 'flex', alignItems: 'center', columnGap: 1, flexWrap: 'wrap', py: 0.2, minHeight: 26,
              borderTop: '1px solid var(--panel)', opacity: m.old ? 0.5 : 1 }}>
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
        {/* 3: everything else, fixed columns too; at a narrow width this part wraps under the row first */}
        <Box data-testid="by-die-rest" sx={{ display: 'flex', alignItems: 'center', columnGap: 1, flex: '1 1 320px', minWidth: 0, ...small }}>
          <Box sx={{ width: REST_W.application, flexShrink: 0, overflow: 'hidden' }}>{extra?.application}</Box>
          <Box data-testid="by-die-marks" sx={{ width: REST_W.marks, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 0.5, overflow: 'hidden' }}>
            {extra?.marks}
            {m.old && <Tooltip title={t('byDie.oldTip')}><Typography component="span" sx={{ ...small, fontStyle: 'italic' }}>{t('byDie.old')}</Typography></Tooltip>}
            {m.newestLocked && <Tooltip title={t('byDie.newestLockedTip')}><Typography component="span" data-testid="by-die-newest-locked" sx={{ ...small, fontStyle: 'italic' }}>{t('byDie.newestLocked')}</Typography></Tooltip>}
          </Box>
          {isAdmin && (
            <Tooltip title={projects}>
              <Typography component="span" noWrap data-testid="by-die-projects" sx={{ ...small, color: 'var(--text-2)', flex: '1 1 120px', minWidth: 0 }}>
                {projects || (m.computed && !m.productions.length ? t('byDie.computed') : '')}
              </Typography>
            </Tooltip>
          )}
          {isAdmin && (
            <Typography component="span" noWrap data-testid="by-die-production" sx={{ ...small, width: REST_W.production, flexShrink: 0 }}>
              {m.productions.map((p) => t('byDie.production', { rev: p.rev || '—' })).join(', ')}
            </Typography>
          )}
          {!isAdmin && <Box sx={{ flex: 1 }} />}
          {/* 4: the row actions, last */}
          <Box sx={{ display: 'flex', alignItems: 'center', flexShrink: 0, ml: 'auto' }}>
            <Tooltip title={t('byDie.loadTip')}>
              <span>
                <IconButton size="small" disabled={busy || !r?.duty} aria-label={t('byDie.loadTip')} data-testid="by-die-load"
                  onClick={() => actions.load(m.die, m.config)} sx={{ p: 0.25, color: '#60a5fa' }}>
                  <PlayArrowIcon sx={{ fontSize: 16 }} />
                </IconButton>
              </span>
            </Tooltip>
            <IconButton size="small" aria-label={t('byDie.motorMenu')} data-testid="by-die-motor-menu" onClick={(e) => setCfgMenu({ el: e.currentTarget, m })}
              sx={{ p: 0.25, color: 'var(--text-4)' }}><MoreVertIcon sx={{ fontSize: 15 }} /></IconButton>
          </Box>
        </Box>
      </Box>
    );
  };
  const sortBlock = (motors: MotorRow[]) => (sort ? sortMotors(motors, sort.key, sort.dir, (m) => cells?.(m.die, m.config)?.cogging ?? null) : motors);
  const header = (
    <Box data-testid="by-die-header" sx={{ position: 'sticky', top: 0, zIndex: 2, bgcolor: 'var(--panel-2)', display: 'flex', alignItems: 'flex-end',
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
      <Box sx={{ display: 'flex', columnGap: 1, flex: '1 1 320px', minWidth: 0 }}>
        <Box component="span" sx={{ width: REST_W.application, flexShrink: 0 }}>{t('byDie.col.application')}</Box>
        <Box component="span" sx={{ width: REST_W.marks, flexShrink: 0 }}>{t('byDie.col.marks')}</Box>
        {isAdmin && <Box component="span" sx={{ flex: '1 1 120px' }}>{t('byDie.col.projects')}</Box>}
        {isAdmin && <Box component="span" sx={{ width: REST_W.production, flexShrink: 0 }}>{t('byDie.col.production')}</Box>}
      </Box>
    </Box>
  );

  return (
    <Box data-testid="catalog-by-die" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, overflowX: 'auto' }}>
      {header}
      {groups.map((g) => (
        <Box key={g.family || '-'} data-testid="by-die-family" sx={{ border: '1px solid var(--line-soft)', borderRadius: 1, overflow: 'hidden', ...(g.named ? {} : { opacity: 0.6 }) }}>
          <Box sx={{ px: 1, py: 0.4, bgcolor: 'var(--panel)', display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 800, color: g.named ? 'var(--text-0)' : 'var(--text-3)' }}>{g.named ? g.family : isAdmin ? t('byDie.noName') : t('byDie.otherMotors')}</Typography>
            <Typography sx={small}>{t('byDie.motors', { n: g.blocks.reduce((n, b) => n + b.motors.length, 0) })}</Typography>
          </Box>
          {g.blocks.map((b) => (
            <Box key={b.die.name} data-testid="by-die-block" sx={{ px: 1, py: 0.4, borderTop: '1px solid var(--line-soft)' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', [NARROW]: { gap: 0.5 } }}>
                <Box sx={{ lineHeight: 0, flexShrink: 0 }}>
                  <MotorThumbnail slots={b.die.slots ?? 0} poles={b.die.poles ?? 0} size={28} thumbSvg={thumbs(b.die) ?? undefined} />
                </Box>
                <Typography sx={{ ...small, color: 'var(--text-2)' }}>
                  Ø{fmt(b.die.stator_diameter, 0)} · {b.die.slots}s{b.die.poles}p
                </Typography>
                {dieExtras?.(b.die.name)}
                {canWrite && (
                  <Tooltip title={b.die.locked ? t('byDie.lockedTip') : t('byDie.unlockedTip')}>
                    <span>
                      <IconButton size="small" disabled={busy} onClick={() => actions.toggleDieLock(b.die.name)}
                        sx={{ fontSize: 11, p: 0.2, color: b.die.locked ? '#fbbf24' : 'var(--text-4)' }}>{b.die.locked ? '🔒' : '🔓'}</IconButton>
                    </span>
                  </Tooltip>
                )}
                <Box sx={{ flex: 1 }} />
                {canWrite && (
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
      <Menu open={!!dieMenu} anchorEl={dieMenu?.el ?? null} onClose={() => setDieMenu(null)}>
        {dieMenu && dieMenuItems(dieMenu.die, { canWrite, isAdmin, standardUser }, actions, t).map((it) => (
          <MenuItem key={it.key} dense sx={it.danger ? { color: '#f87171' } : undefined} onClick={run(it.run)}>{it.label}</MenuItem>
        ))}
      </Menu>
      <Menu open={!!cfgMenu} anchorEl={cfgMenu?.el ?? null} onClose={() => setCfgMenu(null)}>
        {cfgMenu && motorMenuItems(cfgMenu.m, { canWrite, isAdmin, standardUser }, actions, t).map((it) => (
          <MenuItem key={it.key} dense sx={it.danger ? { color: '#f87171' } : undefined} onClick={run(it.run)}>{it.label}</MenuItem>
        ))}
      </Menu>
    </Box>
  );
};

export default CatalogByDie;
