// 3-D assembly in the application's own viewer; the rotation is driven by the pose operation (angle at the chain input).
import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, Slider, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { UnifiedViewer } from '../../viewer3d';
import { buildParts } from '../mesh';
import type { AssemblyResult, PoseMember } from '../types';
import { Lbl } from './parts';

export interface AssemblyViewProps {
  assembly: AssemblyResult | null; sourceKey: string; loadPose: (angleDeg: number) => Promise<Record<string, PoseMember>>; busy: boolean;
}

const MAX_ANGLE = 720;

export default function AssemblyView({ assembly, sourceKey, loadPose, busy }: AssemblyViewProps) {
  const { t } = useTranslation('gears');
  const [angle, setAngle] = useState(0);
  const [pose, setPose] = useState<Record<string, PoseMember> | null>(null);
  const [playing, setPlaying] = useState(false);
  const inflight = useRef(false);
  const wanted = useRef(0);
  const [error, setError] = useState<string | null>(null);

  // One pose request at a time; the newest wanted angle is asked as soon as the previous answer is in.
  const pump = async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      let asked = Number.NaN;
      while (asked !== wanted.current) {
        asked = wanted.current;
        const p = await loadPose(asked);
        setPose(p); setError(null);
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setPlaying(false); }
    finally { inflight.current = false; }
  };
  useEffect(() => { setPose(null); if (assembly) { wanted.current = angle; void pump(); } }, [sourceKey]);  // eslint-disable-line react-hooks/exhaustive-deps
  const move = (a: number) => { setAngle(a); wanted.current = a; if (assembly) void pump(); };
  useEffect(() => {
    if (!playing) return undefined;
    const h = window.setInterval(() => move((wanted.current + 15) % MAX_ANGLE), 220);
    return () => window.clearInterval(h);
  }, [playing, assembly]);  // eslint-disable-line react-hooks/exhaustive-deps

  const parts = useMemo(() => (assembly ? buildParts(assembly, pose) : null), [assembly, pose]);
  return (
    <Box data-testid="gears-assembly" sx={{ display: 'flex', flexDirection: 'column', gap: 1, minHeight: 0 }}>
      <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 13 }}><Lbl k="inputAngle" /></Typography>
        <Slider size="small" min={0} max={MAX_ANGLE} step={1} value={angle} disabled={!assembly} onChange={(_, v) => move(v as number)}
          sx={{ width: 260 }} aria-label={t('l.inputAngle')} data-testid="gears-angle" />
        <Typography sx={{ fontSize: 13, width: 56 }}>{angle}&deg;</Typography>
        <Button size="small" variant="outlined" disabled={!assembly} onClick={() => setPlaying((p) => !p)} data-testid="gears-play">{playing ? t('l.pause') : t('l.play')}</Button>
        {busy && <Typography sx={{ fontSize: 12 }}>{t('calculating')}</Typography>}
      </Box>
      {error && <Typography role="alert" sx={{ color: 'error.main', fontSize: 13 }}>{error}</Typography>}
      <Box sx={{ height: 440, border: '1px solid var(--line)', borderRadius: 1, position: 'relative' }}>
        <UnifiedViewer sourceKey={sourceKey} resetKey={sourceKey} activeParts={parts} toolbarSpec="simplified"
          contextLostText={t('viewerLost')} noViewText={t('noAssembly')} />
      </Box>
    </Box>
  );
}
