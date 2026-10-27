/**
 * StoredPointResults - the THERMAL and MECHANICAL results stored for one computed point, each with its provenance
 * (owner 2026-10-26).  Thermal: the settled winding / magnet / housing temperatures (the coupled loop's answer), the
 * S1 limit current and the S2 time to the limit when stored.  Mechanical: the smallest averaged safety factor, the
 * safety factor per part, the retention verdict, the largest displacement and the proof speed the solve ran at.
 *
 * Read-only, and ONLY real results of THIS point: a block whose point has no record of its own (same duty, solved at the
 * point's speed and current, current solver, physical numbers) is not drawn at all - no header, no empty tiles, no
 * placeholder line.  Provenance (date, solver commit / version, mesh) rides on a tooltip of each block title.
 * Each line says what the record is: the settled coupled point, an S1 continuous-limit search, a time-to-limit run.
 */
import React from 'react';
import { Box, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { MetricTile, PANEL, LABEL, TileRow } from '../common/ResultTiles';
import {
  limitKind, mechTiles, partSafetyFactors, plausibleC, pointBlocks, pointMechanical, pointThermal, provenanceLine, retentionVerdict, thermalSource, thermalTiles,
  worstCaseLabel,
} from '../../modules/development/pointsLimited';
import type { MotorViewDuty } from '../../modules/development/types';

const StoredPointResults: React.FC<{ duty: MotorViewDuty }> = ({ duty }) => {
  const { t } = useTranslation('development');
  const blocks = pointBlocks(duty);
  if (!blocks.thermal && !blocks.mechanical) return null;
  const thermalRecord = pointThermal(duty);
  const mechanical = pointMechanical(duty);
  const thermal = thermalTiles(thermalRecord);
  const mech = mechTiles(mechanical);
  const verdict = retentionVerdict(mechanical);
  const parts = partSafetyFactors(mechanical);
  const source = thermalSource(thermalRecord);
  const thermalProv = provenanceLine(thermalRecord?.duty_temps?.provenance ?? thermalRecord?.provenance);
  const limit = thermalRecord?.limit_search;
  const solvedAt = worstCaseLabel(mechanical);
  const rs = mechanical?.rotor_stress;
  const mechProv = provenanceLine(rs?.provenance);
  const s2 = thermalRecord?.s2;
  const s1Part = thermalRecord?.s1?.limiting_part;
  const lk = limitKind(limit);
  const limitLine = limit && limit.winding_c !== undefined && plausibleC(limit.winding_c)
    ? t(`points.thermal.${lk === 'continuous' ? 'limitLine' : lk === 'time' ? 'timeLine' : lk === 'steady' ? 'steadyLine' : 'searchLine'}`,
      { winding: Math.round(limit.winding_c), day: (limit.provenance?.computed_at ?? '').replace('T', ' ').slice(0, 16) })
    : '';

  return (
    <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap', alignItems: 'flex-start' }} data-testid="stored-point-results">
      {blocks.thermal && <Box sx={{ ...PANEL, p: 1, minWidth: 260, flex: '1 1 320px' }} data-testid="stored-thermal">
        <Tooltip title={thermalProv || t('points.noProvenance')}><Typography sx={LABEL}>{t('points.thermal.title')}{source ? ` · ${t(`points.thermal.source.${source.kind}`)}${source.day ? ` · ${source.day}` : ''}` : ''}</Typography></Tooltip>
        {thermal.length > 0 && <TileRow>
          {thermal.map((tile) => (
            <MetricTile key={tile.id} label={t(`points.thermal.tile.${tile.id}`)} value={tile.value} unit={tile.unit} d={tile.d}
              base={tile.value ?? 0} plain blankTip={t('points.notStored')}
              tip={tile.id === 's1' && s1Part ? t('points.thermal.s1Tip', { part: s1Part }) : tile.id === 's2' && s2?.limiting_part ? t('points.thermal.s2Tip', { part: s2.limiting_part }) : t(`points.thermal.tileTip.${tile.id}`)} />
          ))}
        </TileRow>}
        {limitLine && (
          <Tooltip title={`${t(lk === 'steady' ? 'points.thermal.steadyTip' : 'points.thermal.limitTip')} ${provenanceLine(limit?.provenance)}`}>
            <Typography data-testid="stored-limit-search" sx={{ fontSize: 11.5, color: 'var(--text-3)', mt: 0.5 }}>{limitLine}</Typography>
          </Tooltip>)}
      </Box>}

      {blocks.mechanical && <Box sx={{ ...PANEL, p: 1, minWidth: 260, flex: '1 1 320px' }} data-testid="stored-mechanical">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Tooltip title={`${mechProv || t('points.noProvenance')}${rs?.filed_under ? ` · ${t('points.mech.filedUnder', { duty: rs.filed_under })}` : ''}`}><Typography sx={LABEL}>{t('points.mech.title')}{solvedAt ? ` · ${t('points.mech.atCase', { case: solvedAt })}` : ''}</Typography></Tooltip>
          {verdict !== 'unknown' && (
            <Tooltip title={rs?.retention?.message ?? t('points.mech.retentionTip')}>
              <Box component="span" sx={{ fontSize: 10, px: 0.75, borderRadius: 1, color: verdict === 'pass' ? '#4ade80' : '#f87171', bgcolor: 'var(--panel-3, var(--panel-2))' }}>{t(`points.mech.retention.${verdict}`)}</Box>
            </Tooltip>)}
        </Box>
        <Box data-testid="mech-body">
          <TileRow>
            {mech.map((tile) => (
              <MetricTile key={tile.id} label={t(`points.mech.tile.${tile.id}`)} value={tile.value} unit={tile.unit} d={tile.d}
                base={tile.value ?? 0} plain blankTip={t('points.notStored')} tip={t(`points.mech.tileTip.${tile.id}`)} />
            ))}
          </TileRow>
          {parts.length > 0 && (
            <Box component="table" sx={{ borderCollapse: 'collapse', mt: 0.5 }} data-testid="stored-sf-parts">
              <tbody>
                {parts.map((p) => (
                  <tr key={p.part}>
                    <Box component="td" sx={{ fontSize: 11, color: 'var(--text-2)', pr: 1.5 }}>{p.part}{p.material ? ` · ${p.material}` : ''}</Box>
                    <Box component="td" sx={{ fontSize: 12, fontFamily: 'monospace', textAlign: 'right', color: p.sf < 3 ? '#f87171' : 'var(--text-0)' }}>{p.sf.toFixed(2)}</Box>
                  </tr>
                ))}
              </tbody>
            </Box>)}
        </Box>
      </Box>}
    </Box>
  );
};

export default StoredPointResults;
