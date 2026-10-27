import { Box, Stack, Tooltip, Typography } from '@mui/material';
import { STAGE_ORDER, projectPercent, segmentFill, stageAria, stageTooltip, stagesForMotor, summaryLine, summaryTooltip, variantEndText, variantPercent, type ActivityData, type StageInfo } from './stageBar';
import type { ProjectMotor } from './types';

type Tr = (key: string, options?: Record<string, unknown>) => string;

const TRACK = 'var(--line-soft)';
const HATCH = 'repeating-linear-gradient(135deg, var(--line) 0 2px, transparent 2px 6px)';

function Tip({ lines }: { lines: string[] }) {
  return <Stack spacing={0.3}>{lines.map((line, i) => <Typography key={i} sx={{ fontSize: 11 }}>{line}</Typography>)}</Stack>;
}

/** One segment: grey track, brand-blue fill to its own completion, a subtle hatch while queued, a thin red outline when failed. */
function Segment({ stage, t, nowMs }: { stage: StageInfo; t: Tr; nowMs: number }) {
  const fill = segmentFill(stage);
  return (
    <Tooltip title={<Tip lines={stageTooltip(stage, nowMs, t)} />} placement="top">
      <Box data-testid="stage-segment" data-stage={stage.id} data-state={stage.state} sx={{ flex: '1 1 0', minWidth: 0 }}>
        <Box role="progressbar" aria-label={stageAria(stage, t)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={fill.pct}
          sx={{ position: 'relative', height: 6, borderRadius: '3px', overflow: 'hidden', bgcolor: TRACK,
            outline: fill.outline ? '1px solid #ef4444' : 'none', outlineOffset: 0, backgroundImage: fill.hatch ? HATCH : 'none' }}>
          <Box sx={{ position: 'absolute', inset: 0, width: `${fill.pct}%`, bgcolor: 'var(--brand)', borderRadius: '3px',
            transition: 'width 600ms ease',
            ...(fill.active ? { animation: 'stagePulse 1.6s infinite ease-in-out', '@keyframes stagePulse': { '0%': { opacity: 1 }, '50%': { opacity: 0.6 }, '100%': { opacity: 1 } }, '@media (prefers-reduced-motion: reduce)': { animation: 'none' } } : {}) }} />
        </Box>
        <Typography noWrap sx={{ mt: 0.3, fontSize: 10, lineHeight: 1.2, color: stage.state === 'not_started' ? 'var(--text-3)' : 'var(--text-2)' }}>{t(`stages.names.${stage.id}`)}</Typography>
      </Box>
    </Tooltip>
  );
}

/** The five-segment bar of one variant, with the overall percent and "results ~15:05" at the right end.  Null when the server
 *  sent no stages for this variant (an older server, or a variant the caller cannot open). */
export function VariantStageBar({ data, motor, t }: { data: ActivityData; motor: Pick<ProjectMotor, 'die' | 'canonical_die' | 'config'>; t: Tr }) {
  const v = stagesForMotor(data.stages, motor);
  if (!v) return null;
  const nowMs = Date.now();
  const byId = new Map(v.stages.map((s) => [s.id, s]));
  const ordered = STAGE_ORDER.map((id) => byId.get(id)).filter((s): s is StageInfo => !!s);
  const pct = variantPercent(v);
  return (
    <Stack direction="row" alignItems="flex-start" spacing={1} data-testid="stage-bar" role="group" aria-label={t('stages.label')} sx={{ width: '100%', maxWidth: 560 }}>
      <Stack direction="row" spacing={0.5} sx={{ flex: '1 1 auto', minWidth: 0 }}>
        {ordered.map((s) => <Segment key={s.id} stage={s} t={t} nowMs={nowMs} />)}
      </Stack>
      <Typography data-testid="stage-bar-end" aria-label={`${pct} %`} sx={{ flex: '0 0 auto', fontSize: 11, lineHeight: '8px', color: 'var(--text-1)', whiteSpace: 'nowrap', maxWidth: { xs: 120, sm: 220 }, overflow: 'hidden', textOverflow: 'ellipsis' }}>{variantEndText(v, nowMs, t)}</Typography>
    </Stack>
  );
}

/** The slim project bar and its one line ("Simulation: computing 2 runs · results expected ~15:05" / "Results ready").  Nothing when idle. */
export function ProjectStatusBar({ data, t }: { data: ActivityData; t: Tr }) {
  const nowMs = Date.now();
  const text = summaryLine(data.summary, data.rows, data.stages, nowMs, t);
  if (!text) return null;
  const ready = data.summary?.state === 'results_ready';
  const pct = ready ? 100 : projectPercent(data.stages);
  const tip = summaryTooltip(data.summary, t);
  const body = (
    <Stack spacing={0.4} data-testid="project-status" data-state={data.summary?.state} sx={{ mb: 1, maxWidth: 560 }}>
      <Typography sx={{ fontSize: 12, color: 'var(--text-1)' }}>{text}</Typography>
      <Box role="progressbar" aria-label={text} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
        sx={{ position: 'relative', height: 4, borderRadius: '2px', overflow: 'hidden', bgcolor: TRACK }}>
        <Box sx={{ position: 'absolute', inset: 0, width: `${pct}%`, bgcolor: 'var(--brand)', borderRadius: '2px', transition: 'width 600ms ease' }} />
      </Box>
    </Stack>
  );
  return tip.length ? <Tooltip title={<Tip lines={tip} />} placement="bottom-start">{body}</Tooltip> : body;
}
