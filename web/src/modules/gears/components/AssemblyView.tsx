// 3-D assembly in the application's own viewer.  The rotation is computed here from the assembly's own speed ratios and orbit data
// (pose.ts, the core's pose formula), so playing makes no server call.  Opens in the isometric view; every stage can be hidden.
import { useEffect, useMemo, useState } from 'react';
import { Box, Button, Slider, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { UnifiedViewer } from '../../viewer3d';
import { buildParts } from '../mesh';
import { poseAt } from '../pose';
import type { AssemblyResult } from '../types';
import { Lbl } from './parts';

const MAX_ANGLE = 720;

export default function AssemblyView({ assembly, sourceKey, busy }: { assembly: AssemblyResult | null; sourceKey: string; busy: boolean }) {
  const { t } = useTranslation('gears');
  const [angle, setAngle] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [hidden, setHidden] = useState<ReadonlySet<number>>(new Set());

  useEffect(() => {
    if (!playing) return undefined;
    const h = window.setInterval(() => setAngle((cur) => (cur + 6) % MAX_ANGLE), 60);
    return () => window.clearInterval(h);
  }, [playing]);

  const stages = useMemo(() => [...new Set((assembly?.members ?? []).map((m) => m.stage_index))].sort((a, b) => a - b), [assembly]);
  const pose = useMemo(() => (assembly ? poseAt(assembly, angle) : null), [assembly, angle]);
  const parts = useMemo(() => (assembly ? buildParts(assembly, pose, hidden) : null), [assembly, pose, hidden]);
  const shownStages = stages.filter((s) => !hidden.has(s));
  return (
    <Box data-testid="gears-assembly" sx={{ display: 'flex', flexDirection: 'column', gap: 1, minHeight: 0 }}>
      <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 13 }}><Lbl k="inputAngle" /></Typography>
        <Slider size="small" min={0} max={MAX_ANGLE} step={1} value={angle} disabled={!assembly} onChange={(_, v) => setAngle(v as number)}
          sx={{ width: 260 }} aria-label={t('l.inputAngle')} data-testid="gears-angle" />
        <Typography sx={{ fontSize: 13, width: 56 }}>{angle}&deg;</Typography>
        <Button size="small" variant="outlined" disabled={!assembly} onClick={() => setPlaying((p) => !p)} data-testid="gears-play">{playing ? t('l.pause') : t('l.play')}</Button>
        {busy && <Typography sx={{ fontSize: 12 }}>{t('calculating')}</Typography>}
      </Box>
      {stages.length > 1 && (
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }} data-testid="gears-stage-toggles">
          <Typography sx={{ fontSize: 13 }}><Lbl k="showStages" /></Typography>
          <ToggleButtonGroup size="small" value={shownStages} aria-label={t('l.showStages')}
            onChange={(_, next: number[]) => setHidden(new Set(stages.filter((s) => !next.includes(s))))}>
            {stages.map((s) => (
              <Tooltip key={s} title={t('tip.stageToggle')}><ToggleButton value={s} sx={{ textTransform: 'none', py: 0.3, px: 1 }} data-testid={`gears-stage-toggle-${s}`}>{s + 1}</ToggleButton></Tooltip>
            ))}
          </ToggleButtonGroup>
        </Box>
      )}
      <Box sx={{ height: 440, border: '1px solid var(--line)', borderRadius: 1, position: 'relative' }}>
        <UnifiedViewer sourceKey={sourceKey} resetKey={sourceKey} activeParts={parts} toolbarSpec="simplified" initialView="iso"
          contextLostText={t('viewerLost')} noViewText={t('noAssembly')} />
      </Box>
    </Box>
  );
}
