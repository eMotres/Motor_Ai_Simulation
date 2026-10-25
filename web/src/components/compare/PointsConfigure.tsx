/**
 * PointsConfigure - the Configure card for a PROJECT motor that has stored duty results but no full
 * passport yet (owner 2026-10-26).  It shows only numbers the project stored:
 *
 *   - a step selector (the project's linked motors in their order, with the engineers' short note),
 *   - a mode switch (one button per stored duty point, one click each; works with a single point),
 *   - the same result tiles Configure shows, wherever the point stores the value ("-" otherwise),
 *   - a steps table of the chosen mode across all steps (opt1 -> opt2 -> opt3),
 *   - live controls that need no passport maps: step, mode, tile colouring against another step,
 *     and a DC-bus check against the bus the point was computed to need,
 *   - the continuous operating knobs (speed, current, angle) shown disabled with one line + tooltip.
 *
 * Nothing is interpolated or invented; a missing value stays "-".
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Button, Chip, MenuItem, Select, Slider, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { MetricTile, PANEL, LABEL, TileRow, fmt } from '../common/ResultTiles';
import { developmentApi } from '../../modules/development/api';
import type { MotorPoint, MotorPointsAnswer, MotorPointVariant } from '../../modules/development/types';
import {
  POINT_TILES, TILE_GROUPS, baseVariantPoint, busHeadroom, dutyAfterVariantSwitch, findVariantIndex,
  pointOperating, tileValue, visibleTiles, type StepBaseMode,
} from '../../modules/development/pointsOnly';
import { usePointsConfigureStore } from '../../stores/pointsConfigureStore';
import { oneOf } from '../../lib/viewMemory';
import { useRememberedView } from '../../lib/useRememberedView';
import { useUIStore } from '../../stores/uiStore';

const TH = { px: 1, py: 0.5, fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textAlign: 'right', whiteSpace: 'nowrap', borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)' } as const;
const TD = { px: 1, py: 0.4, fontSize: 12, textAlign: 'right', whiteSpace: 'nowrap', borderBottom: '1px solid var(--app-bg)', fontFamily: 'monospace', color: 'var(--text-1)' } as const;

const PointsConfigure: React.FC = () => {
  const { t } = useTranslation('development');
  const target = usePointsConfigureStore((s) => s.target);
  const close = usePointsConfigureStore((s) => s.close);
  const remember = usePointsConfigureStore((s) => s.remember);
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const [answer, setAnswer] = useState<MotorPointsAnswer | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [vIndex, setVIndex] = useState(0);
  const [duty, setDuty] = useState<string | null>(null);
  const [baseMode, setBaseMode] = useRememberedView<StepBaseMode>('configure.points.base', 'none', oneOf(['none', 'previous', 'first'] as const));
  const [busText, setBusText] = useState('');

  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setLoading(true); setError(''); setAnswer(null);
    developmentApi.motorPoints(target.projectId, { showAll: target.showAll })
      .then((value) => {
        if (cancelled) return;
        const found = findVariantIndex(value.variants, target.die, target.config);
        const index = found >= 0 ? found : 0;
        setAnswer(value); setVIndex(index);
        setDuty(dutyAfterVariantSwitch(value.variants[index], target.duty));
      })
      .catch((e) => {
        if (cancelled) return;
        // a card restored after a reload whose project, motor or access is gone just returns to the ordinary Configure
        if (usePointsConfigureStore.getState().restored) { close(); return; }
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [target]);

  const variants: MotorPointVariant[] = answer?.variants ?? [];
  const variant: MotorPointVariant | null = variants[vIndex] ?? null;
  const point: MotorPoint | null = variant?.points.find((p) => p.duty === duty) ?? null;
  const peers = useMemo(() => variants.map((v) => v.points.find((p) => p.duty === duty) ?? null), [variants, duty]);
  const basePoint = baseVariantPoint(variants, vIndex, duty ?? '', baseMode);
  const baseStep = baseMode === 'first' ? 1 : vIndex;
  const tiles = visibleTiles(point, peers);
  const operating = pointOperating(point);
  const busV = busText.trim() === '' ? null : Number(busText.replace(',', '.'));
  const busValid = busV !== null && Number.isFinite(busV) && busV > 0;
  const headroom = busValid ? busHeadroom(point, busV) : null;

  const pickVariant = (index: number) => {
    const next = dutyAfterVariantSwitch(variants[index], duty);
    setVIndex(index);
    setDuty(next);
    if (variants[index]) remember({ die: variants[index].die, config: variants[index].config, duty: next });
  };
  const leave = () => { close(); setActiveTab('development' as never); };

  if (!target) return null;
  const knobs: Array<{ id: string; value: number | null; unit: string }> = [
    { id: 'speed', value: operating.rpm, unit: 'rpm' },
    { id: 'current', value: operating.currentA, unit: 'A' },
    { id: 'gamma', value: operating.gammaDeg, unit: '°' },
  ];

  return (
    <Box sx={{ height: '100%', overflow: 'auto', p: 1.5, display: 'flex', flexDirection: 'column', gap: 1.25 }} data-testid="points-configure">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>{t('points.title')}</Typography>
        <HelpTip title={t('points.pendingTip')} />
        <Chip size="small" label={t('points.pending')} sx={{ color: 'var(--text-3)', bgcolor: 'var(--panel-2)' }} />
        <Box sx={{ flex: 1 }} />
        <Button size="small" onClick={leave}>{t('points.backToProject')}</Button>
        <Button size="small" onClick={close}>{t('points.close')}</Button>
      </Box>

      {loading && <Typography role="status" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.loading')}</Typography>}
      {error && <Typography role="alert" sx={{ fontSize: 12, color: 'error.main' }}>{t('points.loadFailed')}: {error}</Typography>}
      {!loading && !error && answer && variants.length === 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.noVariants')}</Typography>}

      {variants.length > 0 && <>
        <Box sx={{ ...PANEL, p: 1, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography sx={LABEL}>{t('points.step')}</Typography>
            <ToggleButtonGroup exclusive size="small" value={vIndex} aria-label={t('points.step')}
              onChange={(_, value) => { if (typeof value === 'number') pickVariant(value); }}>
              {variants.map((v, index) => (
                <ToggleButton key={`${v.die}/${v.config}`} value={index} sx={{ textTransform: 'none', px: 1.2, py: 0.3, fontSize: 12 }}>
                  <Tooltip title={`${v.canonical_die} / ${v.config}`}><span>{v.step} · {v.config}</span></Tooltip>
                  {v.status === 'trial' && <Chip size="small" label={t('motorStatus.trial')} sx={{ ml: 0.75, height: 16, fontSize: 9 }} />}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>
          {variant?.note && <Typography sx={{ fontSize: 12, color: 'var(--text-1)' }} data-testid="points-note">{variant.note}</Typography>}

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography sx={LABEL}>{t('points.mode')}</Typography>
            {variant && variant.points.length > 0
              ? <ToggleButtonGroup exclusive size="small" value={duty} aria-label={t('points.mode')}
                  onChange={(_, value) => { if (typeof value === 'string') { setDuty(value); remember({ duty: value }); } }}>
                  {variant.points.map((p) => (
                    <ToggleButton key={p.duty} value={p.duty} sx={{ textTransform: 'none', px: 1.2, py: 0.3, fontSize: 12 }}>{p.duty}</ToggleButton>
                  ))}
                </ToggleButtonGroup>
              : <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.noPoints')}</Typography>}
            <HelpTip title={t('points.modeTip')} />
          </Box>
        </Box>

        {point && <>
          <Box sx={{ ...PANEL, p: 1, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
              <Typography sx={LABEL}>{t('points.compareWith')}</Typography>
              <Select size="small" value={baseMode} onChange={(e) => setBaseMode(e.target.value as StepBaseMode)} sx={{ minWidth: 150, '& .MuiSelect-select': { py: 0.35, fontSize: 12 } }} aria-label={t('points.compareWith')}>
                <MenuItem value="none">{t('points.base.none')}</MenuItem>
                <MenuItem value="previous" disabled={vIndex === 0}>{t('points.base.previous')}</MenuItem>
                <MenuItem value="first" disabled={vIndex === 0}>{t('points.base.first')}</MenuItem>
              </Select>
              <HelpTip title={baseMode !== 'none' && !basePoint ? `${t('points.compareTip')} ${t('points.noBasePoint')}` : t('points.compareTip')} />
            </Box>

            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {knobs.map((k) => (
                <Tooltip key={k.id} title={`${t('points.knobTip')} ${t('points.knobsDisabledTip')}`}>
                  <Box sx={{ ...PANEL, px: 1, py: 0.5, width: 190, boxSizing: 'border-box', opacity: 0.75 }}>
                    <Typography sx={{ ...LABEL, fontSize: 10.5 }}>{t(`points.knob.${k.id}`)}
                      <Box component="span" sx={{ ml: 0.75, fontFamily: 'monospace', color: 'var(--text-0)' }}>{k.value === null ? '—' : `${fmt(k.value, k.id === 'gamma' ? 1 : 0)} ${k.unit}`}</Box>
                    </Typography>
                    <Slider disabled size="small" min={0} max={Math.max(1, (k.value ?? 0) * 1.5)} value={k.value ?? 0} aria-label={t(`points.knob.${k.id}`)} sx={{ py: 0.75 }} />
                  </Box>
                </Tooltip>
              ))}
            </Box>
          </Box>

          {TILE_GROUPS.map((group) => {
            const groupTiles = tiles.filter((spec) => spec.group === group);
            if (groupTiles.length === 0) return null;
            return (
              <Box key={group}>
                <TileRow>
                  {groupTiles.map((spec) => {
                    const value = tileValue(point, spec);
                    const base = tileValue(basePoint, spec);
                    return <MetricTile key={spec.id} label={t(`points.tile.${spec.id}`)} value={value} unit={spec.unit} d={spec.d}
                      base={base ?? value ?? 0} goodHi={base === null ? undefined : spec.goodHi}
                      plain={base === null} blankTip={t('points.notStored')}
                      tip={base !== null && value !== null ? t('points.vsStep', { step: baseStep, value: fmt(base, spec.d), unit: spec.unit }) : t(`points.tileTip.${spec.id}`)} />;
                  })}
                </TileRow>
              </Box>
            );
          })}

          <Box sx={{ ...PANEL, p: 1, display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography sx={LABEL}>{t('points.bus')}</Typography>
            <TextField size="small" value={busText} onChange={(e) => setBusText(e.target.value)} placeholder="V"
              inputProps={{ inputMode: 'decimal', 'aria-label': t('points.bus') }} sx={{ width: 96, '& input': { py: 0.45, fontSize: 12 } }}
              error={busText.trim() !== '' && !busValid} />
            <HelpTip title={busValid && headroom === null ? `${t('points.busTip')} ${t('points.busNoRequirement')}` : t('points.busTip')} />
            {busText.trim() !== '' && !busValid && <Typography role="alert" sx={{ fontSize: 11, color: 'error.main' }}>{t('points.busInvalid')}</Typography>}
            {headroom && <Typography role="status" sx={{ fontSize: 12, fontWeight: 700, color: headroom.ok ? '#4ade80' : '#f87171' }}>
              {headroom.ok ? t('points.busOk', { required: fmt(headroom.requiredV, 1), margin: fmt(headroom.headroomPct, 1) }) : t('points.busShort', { required: fmt(headroom.requiredV, 1), margin: fmt(-headroom.headroomPct, 1) })}
            </Typography>}
          </Box>
        </>}

        {duty && <Box sx={{ ...PANEL, overflowX: 'auto' }}>
          <Typography sx={{ ...LABEL, p: 1, pb: 0.25 }}>{t('points.stepsTable', { duty })}</Typography>
          <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead><tr>
              <Box component="th" sx={{ ...TH, textAlign: 'left' }}>{t('points.step')}</Box>
              <Box component="th" sx={{ ...TH, textAlign: 'left' }}>{t('points.noteHead')}</Box>
              {POINT_TILES.filter((spec) => spec.core || peers.some((peer) => tileValue(peer, spec) !== null)).map((spec) => (
                <Box component="th" key={spec.id} sx={TH}>{t(`points.tile.${spec.id}`)} <span style={{ fontWeight: 400 }}>{spec.unit}</span></Box>
              ))}
            </tr></thead>
            <tbody>
              {variants.map((v, index) => {
                const p = peers[index];
                return (
                  <Box component="tr" key={`${v.die}/${v.config}`} onClick={() => pickVariant(index)}
                    sx={{ cursor: 'pointer', bgcolor: index === vIndex ? 'var(--panel-3, rgba(255,255,255,0.06))' : 'transparent', '&:hover': { bgcolor: 'rgba(255,255,255,0.04)' } }}>
                    <Box component="td" sx={{ ...TD, textAlign: 'left' }}>{v.step} · {v.config}</Box>
                    <Box component="td" sx={{ ...TD, textAlign: 'left', fontFamily: 'inherit', whiteSpace: 'normal', maxWidth: 260 }}>{v.note || '—'}</Box>
                    {POINT_TILES.filter((spec) => spec.core || peers.some((peer) => tileValue(peer, spec) !== null)).map((spec) => {
                      const value = tileValue(p, spec);
                      return <Box component="td" key={spec.id} sx={TD}>{value === null ? '—' : fmt(value, spec.d)}</Box>;
                    })}
                  </Box>
                );
              })}
            </tbody>
          </Box>
        </Box>}
      </>}
    </Box>
  );
};

export default PointsConfigure;
