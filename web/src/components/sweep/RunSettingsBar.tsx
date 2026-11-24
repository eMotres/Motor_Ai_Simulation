/**
 * The run settings of a Sweep / Optimization / Descent panel: two preset buttons, the steps and gap rows (editable: an edit is
 * an override and the run reads custom), and the other settings read-only with their source, all read from the server answer
 * (lib/useRunSettings).  Nothing here touches the Simulation or Mesh tab.
 */
import React from 'react';
import { Box, Button, Chip, TextField, Tooltip, Typography } from '@mui/material';
import { nsT } from '../../i18n/nsT';
import type { RunSettingsState } from '../../lib/useRunSettings';

const tx = nsT('simulation');

const SRC: Record<string, string> = { preset: 'runSettingsSrcPreset', machine: 'runSettingsSrcMachine', override: 'runSettingsSrcTyped' };

/** The settings besides steps and gap rows, READ-ONLY, each with where it comes from. */
export function describeSettings(st: Record<string, any>, src: Record<string, string> = {}): string {
  const onOff = (b: boolean) => (b ? tx('runSettingsOn') : tx('runSettingsOff'));
  const tag = (k: string) => (src[k] && SRC[src[k]] ? ` (${tx(SRC[src[k]])})` : '');
  const sect = Number(st.n_sectors) > 1 ? `1/${st.n_sectors}` : tx('runSettingsFullRing');
  const comp = st.component_mesh && Object.keys(st.component_mesh).length
    ? Object.entries(st.component_mesh).map(([k, v]) => `${k} ${String(v)}`).join(', ') : '-';
  return [`${tx('runSettingsMesh')} ${st.mesh_size_mm} / ${st.min_size_mm} mm${tag('mesh_size_mm')}`,
          `${tx('runSettingsSectors')} ${sect}${tag('n_sectors')}`,
          `${tx('runSettingsRotorEddy')} ${onOff(!!st.rotor_eddy)}${tag('rotor_eddy')}`,
          `${tx('runSettingsDemag')} ${onOff(!!st.demag)}${tag('demag')}`,
          `${tx('runSettingsEddy')} ${onOff(!!st.eddy)}${tag('eddy')}`,
          `${tx('runSettingsOuterAir')} ${st.outer_air_factor}${tag('outer_air_factor')}`,
          `${tx('runSettingsComponent')} ${comp}${tag('component_mesh')}`,
          `${tx('runSettingsOrder')} ${st.element_order}${tag('element_order')}`].join(' · ');
}

const RunSettingsBar: React.FC<{ rs: RunSettingsState; dense?: boolean }> = ({ rs, dense = true }) => {
  const r = rs.resolved;
  const st = r?.settings;
  const label = r ? (r.custom ? tx('runSettingsCustom') : `${r.preset_label} ${tx('runSettingsPresetWord')}`) : '';
  const diff = r ? Object.entries(r.differs).map(([k, d]) => `${k}: ${String(d.current)} (${r.preset_label} ${String(d.preset)})`) : [];
  const clamps = r ? r.clamps.map(c => `${c.key}: ${String(c.requested)} → ${String(c.used)} (${c.reason})`) : [];
  const field = (key: 'steps_per_period' | 'gap_layers', name: string, width: number) => (
    <TextField label={name} type="number" size="small" disabled={!r} value={st ? st[key] : ''}
      onChange={e => { const v = Number(e.target.value); if (Number.isFinite(v) && e.target.value !== '') rs.setOverride(key, v); }}
      inputProps={{ step: key === 'gap_layers' ? 1 : 12, style: { fontSize: 12, padding: '4px 6px', width } }}
      InputLabelProps={{ sx: { fontSize: 11 } }} />
  );
  return (
    <Tooltip placement="top" title={<span style={{ whiteSpace: 'pre-line' }}>{[tx('runSettingsHelp'), ...diff, ...clamps].join('\n')}</span>}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap', mb: 1 }}>
        <Typography sx={{ fontSize: dense ? 11 : 12, color: 'var(--text-2)' }}>{tx('runSettingsLabel')}</Typography>
        {(['optimization', 'simulation'] as const).map(n => (
          <Button key={n} size="small" variant={r && !r.custom && r.preset === n ? 'contained' : 'outlined'}
            onClick={() => rs.choose(n)} sx={{ textTransform: 'none', fontSize: 11, py: 0.1 }}>
            {tx(n === 'optimization' ? 'runPresetOptimization' : 'runPresetSimulation')}
          </Button>
        ))}
        {r?.custom && <Chip size="small" color="warning" label={tx('runSettingsCustom')} sx={{ height: 20, fontSize: 10.5 }} />}
        {field('steps_per_period', tx('runSettingsSteps'), 44)}
        {field('gap_layers', tx('runSettingsGap'), 36)}
        {r && st && <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          {label} · {describeSettings(st, r?.sources)}{clamps.length ? ` · ${tx('runSettingsAdjusted')}` : ''}
        </Typography>}
        {rs.error && (
          <Typography sx={{ fontSize: 11, color: '#fca5a5' }} role="alert">
            {tx('sweepPresetError')} {rs.error}{' '}
            <Button size="small" onClick={rs.refresh} sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>{tx('sweepPresetRetry')}</Button>
          </Typography>
        )}
      </Box>
    </Tooltip>
  );
};

export default RunSettingsBar;
