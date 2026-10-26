import { Box, Chip, Stack, Tooltip, Typography } from '@mui/material';
import { activityChip, activityForMotor, type ActivityRow } from './projectActivity';
import type { ProjectMotor } from './types';

/** "Computing on the server: <what> · <N> min" (pulsing dot) or "Queued: <what>" next to a variant (or, without `motor`, for the
 *  whole project).  Renders nothing when nothing runs; the tooltip lists every run. */
export function ActivityChip({ rows, motor, t }: { rows: ActivityRow[]; motor?: Pick<ProjectMotor, 'die' | 'canonical_die' | 'config'>; t: (key: string, options?: Record<string, unknown>) => string }) {
  const mine = motor ? activityForMotor(rows, motor) : rows;
  const model = activityChip(mine, Date.now(), t);
  if (!model) return null;
  const running = model.state === 'running';
  return (
    <Tooltip title={<Stack spacing={0.3}>{model.lines.map((line, i) => <Typography key={i} sx={{ fontSize: 11 }}>{line}</Typography>)}</Stack>}>
      <Chip size="small" variant="outlined" data-testid="activity-chip" data-state={model.state} label={model.text}
        icon={running
          ? <Box component="span" aria-hidden sx={{ width: 8, height: 8, ml: '8px !important', borderRadius: '50%', bgcolor: '#10b981', animation: 'activityDotPulse 1.4s infinite ease-in-out', '@keyframes activityDotPulse': { '0%': { opacity: 1, transform: 'scale(1)' }, '50%': { opacity: 0.35, transform: 'scale(0.7)' }, '100%': { opacity: 1, transform: 'scale(1)' } }, '@media (prefers-reduced-motion: reduce)': { animation: 'none' } }} />
          : <Box component="span" aria-hidden sx={{ width: 8, height: 8, ml: '8px !important', borderRadius: '50%', border: '1.5px solid var(--text-3)' }} />}
        sx={{ height: 22, fontSize: 11, maxWidth: 360, color: 'var(--text-1)', borderColor: running ? 'rgba(16,185,129,0.55)' : 'var(--line-soft)' }} />
    </Tooltip>
  );
}
