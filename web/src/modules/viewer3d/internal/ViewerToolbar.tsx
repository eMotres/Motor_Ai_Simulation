import React from 'react';
import { Box, Button, FormControlLabel, Slider, Switch, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import type { Axis, StandardView } from './model3d';
import type { ViewerControl } from './toolbarSpec';

export interface UnifiedViewerToolbarProps {
  full: boolean;
  sceneReady: boolean;
  controls: ReadonlySet<ViewerControl>;
  section: { on: boolean; axis: Axis; position: number };
  sectionRange: { min: number; max: number };
  explode: number;
  measure: boolean;
  projection: 'perspective' | 'orthographic';
  t: (key: string) => string;
  onView: (v: StandardView) => void;
  onFit: () => void;
  onHome: () => void;
  onProjection: (v: 'perspective' | 'orthographic') => void;
  onSection: (v: { on: boolean; axis: Axis; position: number }) => void;
  onExplode: (v: number) => void;
  onMeasure: (v: boolean) => void;
}

const UnifiedViewerToolbar: React.FC<UnifiedViewerToolbarProps> = (p) => {
  const line = { display: 'flex', alignItems: 'center', gap: 0.5 } as const;
  const label = { fontSize: 10.5, whiteSpace: 'nowrap', color: 'var(--text-3)' } as const;
  return <Box data-testid="unified-viewer-toolbar" sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1, px: 1, py: 0.5,
    flexShrink: 0, borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel)' }}>
    <Box sx={line}>
      <Button size="small" disabled={!p.sceneReady} onClick={p.onFit} sx={{ textTransform: 'none', fontSize: 11 }}>{p.t('fit')}</Button>
      <Button size="small" disabled={!p.sceneReady} onClick={p.onHome} sx={{ textTransform: 'none', fontSize: 11 }}>{p.t('home')}</Button>
      <ToggleButtonGroup size="small" exclusive value={p.projection} onChange={(_, v) => v && p.onProjection(v)}>
        <ToggleButton value="orthographic" aria-label={p.t('orthographic')} sx={{ px: 0.75, py: 0, fontSize: 10 }}>{p.t('orthographic')}</ToggleButton>
        <ToggleButton value="perspective" aria-label={p.t('perspective')} sx={{ px: 0.75, py: 0, fontSize: 10 }}>{p.t('perspective')}</ToggleButton>
      </ToggleButtonGroup>
    </Box>
    {p.full && <>
      {p.controls.has('explode') && <Box sx={line} data-testid="tool-explode"><Typography sx={label}>{p.t('explode')}</Typography>
        <Slider disabled={!p.sceneReady} aria-label={p.t('explode')} size="small" min={0} max={1} step={0.01} value={p.explode} onChange={(_, v) => p.onExplode(v as number)} sx={{ width: 84 }} />
      </Box>}
      {p.controls.has('section') && <Box sx={line} data-testid="tool-section">
        <FormControlLabel sx={{ m: 0, '& .MuiFormControlLabel-label': label }} labelPlacement="start" label={p.t('section')}
          control={<Switch size="small" disabled={!p.sceneReady} checked={p.section.on} onChange={(_, on) => p.onSection({ ...p.section, on })} />} />
        {p.section.on && <>
          <ToggleButtonGroup size="small" exclusive disabled={!p.sceneReady} value={p.section.axis} onChange={(_, a) => a && p.onSection({ ...p.section, axis: a, position: (p.sectionRange.min + p.sectionRange.max) / 2 })}>
            {(['x', 'y', 'z'] as const).map((a) => <ToggleButton key={a} value={a} sx={{ px: 0.65, py: 0, fontSize: 10 }}>{a.toUpperCase()}</ToggleButton>)}
          </ToggleButtonGroup>
          <Slider disabled={!p.sceneReady} aria-label={p.t('sectionOffset')} size="small" min={p.sectionRange.min} max={p.sectionRange.max} step={(p.sectionRange.max - p.sectionRange.min) / 400 || 0.01}
            value={p.section.position} onChange={(_, v) => p.onSection({ ...p.section, position: v as number })} sx={{ width: 84 }} />
        </>}
      </Box>}
      {p.controls.has('measure') && <FormControlLabel sx={{ m: 0, '& .MuiFormControlLabel-label': label }} labelPlacement="start" label={p.t('measure')}
        control={<Switch size="small" disabled={!p.sceneReady} checked={p.measure} onChange={(_, v) => p.onMeasure(v)} />} />
      }
    </>}
  </Box>;
};

export default UnifiedViewerToolbar;
