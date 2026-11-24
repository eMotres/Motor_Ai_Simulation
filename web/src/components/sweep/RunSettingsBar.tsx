/**
 * The run settings of a Sweep / Optimization / Descent panel: ONE read-only line (owner 2026-11-24, one place for the settings).
 * The values are the ones the Mesh and Simulation tabs hold; the server labels them (Optimization / Simulation preset, or custom)
 * and the run uses exactly these.  Nothing can be typed here: "Change in Mesh" opens the Mesh tab.  Not the Optimization preset
 * = warning colour AND the words.  A value the tabs do not hold = "unknown" and Run is disabled.
 */
import React from 'react';
import { Box, Button, Chip, Tooltip, Typography } from '@mui/material';
import { nsT } from '../../i18n/nsT';
import { useUIStore } from '../../stores/uiStore';
import { stepsReason } from '../../lib/eddySteps';
import { lineText, notOptimization } from '../../lib/runSettings';
import type { RunSettingsState } from '../../lib/useRunSettings';

const tx = nsT('simulation');

const SRC: Record<string, string> = { preset: 'runSettingsSrcPreset', machine: 'runSettingsSrcMachine', tab: 'runSettingsSrcTab', fixed: 'runSettingsSrcFixed' };

/** The settings besides steps, gap rows and the wire cell, READ-ONLY, each with where it comes from. */
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
  const goToTab = useUIStore(st => st.setActiveTab);
  const r = rs.resolved;
  const st = r?.settings;
  const clamps = r ? r.clamps.map(c => `${c.key}: ${String(c.requested)} → ${String(c.used)} (${c.reason})`) : [];
  const diff = r ? Object.entries(r.differs).map(([k, d]) => `${k}: ${String(d.current)} (${r.preset_label} ${String(d.preset)})`) : [];
  const fin = r?.finalist_note ? [tx('runSettingsFinalists', { n: r.finalist_note.min_steps, run: r.finalist_note.run_steps })] : [];
  const why = r && st ? stepsReason(Number(st.steps_per_period), rs.stepsSource, Number(r.geometry?.num_slots), Number(r.geometry?.num_poles)) : null;
  const reason = why ? (why.kind === 'ripple_grade'
    ? tx('runSettingsReasonRipple', { cycles: why.cycles, samples: why.samples }) : tx('runSettingsReasonEddy')) : null;
  const warn = !!r && notOptimization(r);
  const text = r ? lineText(r, {
    preset: (label, version) => tx('runSettingsPresetLine', { label, version }), custom: tx('runSettingsCustom'),
    steps: n => tx('runSettingsStepsN', { n }), gap: n => tx(n === 1 ? 'runSettingsGapOne' : 'runSettingsGapMany', { n }),
    wire: n => tx('runSettingsWireN', { n }),
  }) : '';
  const unknown = !rs.request && rs.missing.length > 0;
  return (
    <Tooltip placement="top" title={<span style={{ whiteSpace: 'pre-line' }}>
      {[tx('runSettingsHelp'), ...(st ? [describeSettings(st, r?.sources)] : []), ...diff, ...clamps, ...fin].join('\n')}</span>}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap', mb: 1 }}>
        {r && st && (
          <Typography data-testid="run-settings-line" sx={{ fontSize: dense ? 11.5 : 12.5, color: warn ? '#fbbf24' : 'var(--text-2)' }}>
            {text}{reason ? ` (${reason})` : ''}{clamps.length ? ` · ${tx('runSettingsAdjusted')}` : ''} · {tx('runSettingsSetInMesh')}
          </Typography>
        )}
        {warn && <Chip size="small" color="warning" label={tx('runSettingsNotOptimization')} sx={{ height: 20, fontSize: 10.5 }} />}
        {unknown && (
          <Typography sx={{ fontSize: 11.5, color: '#fbbf24' }} role="alert">
            {tx('runSettingsUnknown', { what: rs.missing.join(', ') })}
          </Typography>
        )}
        <Button size="small" onClick={() => goToTab('mesh')} sx={{ textTransform: 'none', fontSize: 11, minWidth: 0, py: 0.1 }}>
          {tx('runSettingsChangeInMesh')}
        </Button>
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
