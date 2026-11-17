/** The Motors catalog by die (owner 2026-11-17): family header -> one compact block per die (lamination) -> its motors by
 *  the standard designation, steel / magnet / segmentation / slot insulation BESIDE the name, then (admins) the projects
 *  and the production that use it.  Every action of the list view stays reachable: the motor row's ▶ loads it at its
 *  default mode, the two ⋮ menus carry everything else.  The grouping and the current / old rule: lib/catalogByDie.ts. */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Chip, IconButton, Menu, MenuItem, Tooltip, Typography } from '@mui/material';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { useTranslation } from 'react-i18next';
import MotorThumbnail from './MotorThumbnail';
import { buildByDie, segmentationText, type ByDieDie, type ByDieOverview, type MotorRow } from '../../lib/catalogByDie';
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
}

const fmt = (v: number | null | undefined, d = 1) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : v.toFixed(d));
const small = { fontSize: 11.5, color: 'var(--text-3)' };

const CatalogByDie: React.FC<Props> = ({ dies, thumbs, rated, active, isAdmin, canWrite, standardUser, busy, actions, overview: given }) => {
  const { t } = useTranslation('motors');
  const [overview, setOverview] = useState<ByDieOverview | null>(given ?? null);
  const [dieMenu, setDieMenu] = useState<{ el: HTMLElement; die: ByDieDie } | null>(null);
  const [cfgMenu, setCfgMenu] = useState<{ el: HTMLElement; m: MotorRow } | null>(null);
  useEffect(() => {
    if (given !== undefined || !isAdmin) return;           // the Overview is admin-only (route-gated): nobody else asks
    let alive = true;
    fetch(`${API}/api/overview`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null))
      .then((a) => { if (alive) setOverview(a as ByDieOverview | null); }).catch(() => { /* rows without links */ });
    return () => { alive = false; };
  }, [isAdmin, given]);
  const groups = useMemo(() => buildByDie(dies, isAdmin ? overview : null), [dies, overview, isAdmin]);

  const beside = (m: MotorRow) => [m.beside.steel, m.beside.magnet,
    segmentationText(m.beside.magnet_lamination_mm, { solid: t('byDie.solid'), slice: (mm) => t('byDie.slice', { mm }) }),
    m.beside.slot_insulation].filter(Boolean).join(' · ');
  const run = (fn: () => void) => () => { setDieMenu(null); setCfgMenu(null); fn(); };

  const motorRow = (m: MotorRow) => {
    const r = rated(m.die, m.config);
    const loaded = active?.die === m.die && active?.config === m.config;
    const nameTip = !m.named ? t('byDie.noNameTip')
      : !m.complete ? t('byDie.incompleteTip', { missing: m.unknown.join(', ') }) : '';   // a named motor: no old name anywhere
    return (
      <Box key={`${m.die}/${m.config}`} data-testid="by-die-motor" data-old={m.old ? 'true' : undefined}
        sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', pl: 1, py: 0.2, minHeight: 26,
              borderTop: '1px solid var(--panel)', opacity: m.old ? 0.5 : 1 }}>
        <Tooltip title={canWrite || isAdmin ? nameTip : ''}>
          <Typography component="span" sx={{ fontFamily: 'monospace', fontSize: 12.5, fontWeight: 700, minWidth: 190,
            color: loaded ? '#60a5fa' : m.named && m.complete ? 'var(--text-1)' : 'var(--text-4)' }}>
            {m.label}{loaded ? ' ●' : ''}
          </Typography>
        </Tooltip>
        {m.old && <Tooltip title={t('byDie.oldTip')}><Typography component="span" sx={{ ...small, fontStyle: 'italic' }}>{t('byDie.old')}</Typography></Tooltip>}
        <Typography component="span" sx={{ ...small, flex: '1 1 220px' }}>{beside(m)}</Typography>
        {r && (
          <Tooltip title={t('defaultModeNumbersTip', { duty: r.duty ?? '—' })}>
            <Typography component="span" sx={{ ...small, color: 'var(--text-2)', fontVariantNumeric: 'tabular-nums' }}>
              {fmt(r.power_kw, 2)} kW · {fmt(r.torque_nm, 2)} Nm · {fmt(r.mass_kg, 2)} kg
            </Typography>
          </Tooltip>
        )}
        {isAdmin && m.projects.map((p) => (
          <Chip key={p.id} size="small" variant="outlined" label={p.title}
            sx={{ height: 18, fontSize: 10.5, ...(p.trial ? { borderStyle: 'dashed' } : {}) }} />
        ))}
        {isAdmin && m.productions.map((p, i) => (
          <Chip key={i} size="small" variant="outlined" label={t('byDie.production', { rev: p.rev || '—' })} sx={{ height: 18, fontSize: 10.5 }} />
        ))}
        {isAdmin && !m.projects.length && !m.productions.length && m.computed && (
          <Typography component="span" sx={small}>{t('byDie.computed')}</Typography>
        )}
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
    );
  };

  return (
    <Box data-testid="catalog-by-die" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
      {groups.map((g) => (
        <Box key={g.family} data-testid="by-die-family" sx={{ border: '1px solid var(--line-soft)', borderRadius: 1, overflow: 'hidden' }}>
          <Box sx={{ px: 1, py: 0.4, bgcolor: 'var(--panel)', display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 800, color: g.named ? 'var(--text-0)' : 'var(--text-3)' }}>{g.family}</Typography>
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
              {b.motors.map(motorRow)}
            </Box>
          ))}
        </Box>
      ))}
      <Menu open={!!dieMenu} anchorEl={dieMenu?.el ?? null} onClose={() => setDieMenu(null)}>
        {dieMenu && [
          <MenuItem key="add" dense onClick={run(() => actions.addConfig(dieMenu.die.name))}>{t('byDie.addConfig')}</MenuItem>,
          <MenuItem key="ren" dense onClick={run(() => actions.renameDie(dieMenu.die.name))}>{t('byDie.renameDie')}</MenuItem>,
          <MenuItem key="his" dense onClick={run(() => actions.dieHistory(dieMenu.die.name))}>{t('byDie.history')}</MenuItem>,
          <MenuItem key="dup" dense onClick={run(() => actions.duplicateDie(dieMenu.die.name))}>{t('byDie.duplicate')}</MenuItem>,
          ...(isAdmin ? [<MenuItem key="arc" dense onClick={run(() => actions.retireOrArchive(dieMenu.die.name))}>
            {dieMenu.die.layer === 'shared' ? t('retireDieConfirm') : t('archiveDieConfirm')}</MenuItem>] : []),
          <MenuItem key="del" dense sx={{ color: '#f87171' }} onClick={run(() => actions.deleteDie(dieMenu.die.name))}>{t('byDie.deleteDie')}</MenuItem>,
        ]}
      </Menu>
      <Menu open={!!cfgMenu} anchorEl={cfgMenu?.el ?? null} onClose={() => setCfgMenu(null)}>
        {cfgMenu && (() => {
          const { die, config } = cfgMenu.m;
          const items: Array<[string, string, () => void]> = [];
          if (canWrite) {
            items.push(['ren', t('byDie.renameMotor'), () => actions.renameCfg(die, config)],
              ['lock', t('byDie.lockMotor'), () => actions.toggleCfgLock(die, config)],
              ['his', t('byDie.history'), () => actions.cfgHistory(die, config)],
              ['dup', t('byDie.duplicate'), () => actions.duplicateCfg(die, config)],
              ['bat', t('byDie.battery'), () => actions.battery(die, config)]);
            if (isAdmin) items.push(['mov', t('byDie.move'), () => actions.moveCfg(die, config, 'move')], ['cpy', t('byDie.copy'), () => actions.moveCfg(die, config, 'copy')]);
          }
          items.push(['ds', t('byDie.datasheet'), () => actions.datasheet(die, config)], ['rep', t('byDie.report'), () => actions.report(die, config, false)],
            ['pdf', t('byDie.reportPdf'), () => actions.report(die, config, true)]);
          if (!canWrite && !standardUser) items.push(['mine', t('byDie.toMyMotors'), () => actions.toMyMotors(die, config)]);
          if (canWrite) items.push(['del', t('byDie.deleteMotor'), () => actions.deleteCfg(die, config)]);
          return items.map(([k, label, fn]) => (
            <MenuItem key={k} dense sx={k === 'del' ? { color: '#f87171' } : undefined} onClick={run(fn)}>{label}</MenuItem>
          ));
        })()}
      </Menu>
    </Box>
  );
};

export default CatalogByDie;
