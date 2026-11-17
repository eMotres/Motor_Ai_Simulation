/**
 * The mesh preset selector (owner 2026-11-17): Optimization / Simulation / custom.  One short label, a tooltip with the
 * numbers resolved for the open machine.  Choosing a preset fills the stored Mesh-tab and step-count fields (lib/meshPresets);
 * every manual control stays; a manual change reads "custom".
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { MenuItem, Select, Tooltip, Box, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import {
  applyPreset, fetchMeshPresets, matchPreset, readCurrentSettings,
  type PresetChoice, type PresetSet, type ResolvedPreset,
} from '../../lib/meshPresets';

const tx = nsT('simulation');
const EVENTS = ['sim-settings-restored', 'mesh-preset-applied', 'sim-design-applied', 'family-changed', 'descent-eval-params', 'storage'];

function numbersOf(p: ResolvedPreset): string {
  const v = p.values;
  if (!v) return p.error ?? '';
  const sec = v.n_sectors > 1 ? `1/${v.n_sectors}` : tx('meshPresetFullRing');
  return [
    `${v.n_steps_per_period} ${tx('meshPresetStepsPerPeriod')}`,
    `${v.gap_layers} ${tx('meshPresetGapRows')}`,
    `${tx('meshPresetMesh')} ${v.mesh_size_mm} / ${v.min_size_mm} mm`,
    `${tx('meshPresetOuterAir')} ${v.outer_air_factor}`,
    `${tx('meshPresetSector')} ${sec}`,
    `${tx('meshPresetWireCell')} ${v.component_mesh.coil_rel ?? 1}h`,
  ].join(' · ');
}

export const MeshPresetSelect: React.FC<{ dense?: boolean }> = ({ dense = false }) => {
  useTranslation('simulation');                       // re-render on language change; lazy-loads the namespace
  const [set, setSet] = useState<PresetSet | null>(null);
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);
  const load = useCallback(() => {
    fetchMeshPresets().then(s => { setSet(s); setFailed(false); }).catch(() => setFailed(true));
  }, []);
  useEffect(() => {
    load();
    const bump = () => setTick(t => t + 1);
    const machine = () => { load(); bump(); };
    for (const e of EVENTS) window.addEventListener(e, e === 'family-changed' || e === 'sim-design-applied' ? machine : bump);
    return () => { for (const e of EVENTS) {
      window.removeEventListener(e, e === 'family-changed' || e === 'sim-design-applied' ? machine : bump);
    } };
  }, [load]);
  // the match is re-read from the stored fields whenever any panel changes them
  const choice: PresetChoice = useMemo(() => matchPreset(readCurrentSettings(), set), [set, tick]);   // eslint-disable-line react-hooks/exhaustive-deps
  const tip = set
    ? set.presets.map(p => `${p.label}: ${numbersOf(p)}`).join('\n') + `\n${tx('meshPresetVersion')} ${set.version}`
    : (failed ? tx('meshPresetUnavailable') : '');
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
      <Typography sx={{ fontSize: dense ? 11 : 12, color: 'var(--text-2)' }}>{tx('meshPresetLabel')}</Typography>
      <Tooltip title={<span style={{ whiteSpace: 'pre-line' }}>{tip || tx('meshPresetHelp')}</span>} placement="right">
        <Select
          size="small" value={choice} disabled={!set}
          onChange={e => {
            const p = set?.presets.find(x => x.name === e.target.value);
            if (p) void applyPreset(p);
          }}
          sx={{ minWidth: 132, fontSize: dense ? 12 : 13, '& .MuiSelect-select': { py: 0.35 } }}
          inputProps={{ 'aria-label': tx('meshPresetLabel') }}
        >
          {(set?.presets ?? []).map(p => (
            <MenuItem key={p.name} value={p.name} disabled={!p.values}>{p.label}</MenuItem>
          ))}
          <MenuItem value="custom" disabled>{tx('meshPresetCustom')}</MenuItem>
        </Select>
      </Tooltip>
    </Box>
  );
};

export default MeshPresetSelect;
