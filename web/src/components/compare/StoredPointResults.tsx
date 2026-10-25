/**
 * StoredPointResults - the THERMAL and MECHANICAL results stored for one computed point, each with its provenance
 * (owner 2026-10-26).  Thermal: the settled winding / magnet / housing temperatures (the coupled loop's answer), the
 * S1 limit current and the S2 time to the limit when stored.  Mechanical: the smallest averaged safety factor, the
 * safety factor per part, the retention verdict, the largest displacement and the proof speed the solve ran at.
 *
 * Read-only.  A kind that was never computed for this point says so (one line); nothing is estimated or filled in.
 * Provenance (date, solver commit / version, mesh) rides on a tooltip of each block title and on one muted line.
 */
import React from 'react';
import { Box, Chip, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { MetricTile, PANEL, LABEL, TileRow } from '../common/ResultTiles';
import {
  mechTiles, partSafetyFactors, provenanceLine, retentionVerdict, thermalTiles,
} from '../../modules/development/pointsLimited';
import type { MotorViewDuty } from '../../modules/development/types';

const StoredPointResults: React.FC<{ duty: MotorViewDuty }> = ({ duty }) => {
  const { t } = useTranslation('development');
  const thermal = thermalTiles(duty.thermal);
  const mech = mechTiles(duty.mechanical);
  const verdict = retentionVerdict(duty.mechanical);
  const parts = partSafetyFactors(duty.mechanical);
  const thermalProv = provenanceLine(duty.thermal?.coupled?.provenance ?? duty.thermal?.provenance);
  const mechProv = provenanceLine(duty.mechanical?.rotor_stress?.provenance);
  const s2 = duty.thermal?.s2;
  const limit = duty.thermal?.s1?.limiting_part;

  return (
    <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap', alignItems: 'flex-start' }} data-testid="stored-point-results">
      <Box sx={{ ...PANEL, p: 1, minWidth: 260, flex: '1 1 320px' }} data-testid="stored-thermal">
        <Tooltip title={thermalProv || t('points.noProvenance')}><Typography sx={LABEL}>{t('points.thermal.title')}</Typography></Tooltip>
        {thermal.length === 0
          ? <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 0.5 }}>{t('points.notComputed')}</Typography>
          : <TileRow>
            {thermal.map((tile) => (
              <MetricTile key={tile.id} label={t(`points.thermal.tile.${tile.id}`)} value={tile.value} unit={tile.unit} d={tile.d}
                base={tile.value ?? 0} plain blankTip={t('points.notStored')}
                tip={tile.id === 's1' && limit ? t('points.thermal.s1Tip', { part: limit }) : tile.id === 's2' && s2?.limiting_part ? t('points.thermal.s2Tip', { part: s2.limiting_part }) : t(`points.thermal.tileTip.${tile.id}`)} />
            ))}
          </TileRow>}
      </Box>

      <Box sx={{ ...PANEL, p: 1, minWidth: 260, flex: '1 1 320px' }} data-testid="stored-mechanical">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Tooltip title={mechProv || t('points.noProvenance')}><Typography sx={LABEL}>{t('points.mech.title')}</Typography></Tooltip>
          {mech.length > 0 && verdict !== 'unknown' && (
            <Tooltip title={duty.mechanical?.rotor_stress?.retention?.message ?? t('points.mech.retentionTip')}>
              <Chip size="small" label={t(`points.mech.retention.${verdict}`)}
                sx={{ height: 18, fontSize: 10, color: verdict === 'pass' ? '#4ade80' : '#f87171', bgcolor: 'var(--panel-3, var(--panel-2))' }} />
            </Tooltip>)}
        </Box>
        {mech.length === 0
          ? <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 0.5 }}>{t('points.notComputed')}</Typography>
          : <>
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
          </>}
      </Box>
    </Box>
  );
};

export default StoredPointResults;
