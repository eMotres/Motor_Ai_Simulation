import { useState } from 'react';
import { Button, Checkbox, Chip, FormControlLabel, IconButton, Stack, Tooltip } from '@mui/material';
import StarIcon from '@mui/icons-material/Star';
import StarBorderIcon from '@mui/icons-material/StarBorder';
import { downloadExport } from '../../../lib/exportDownload';
import { developmentApi } from '../api';
import { flagWho, isRecommended, reportAccess } from '../projectFlags';
import type { ProjectMotor } from '../types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

/** What a project variant offers next to its name (owner 2026-10-26): the "Recommended" star (staff mark it, everybody sees it),
 *  the report buttons (members only after our engineers checked the report; staff always) and, for staff, the "Report
 *  reviewed" toggle.  One line, labels + tooltips only. */
export function VariantControls({ projectId, motor, staff, busy = false, t, onChanged, showAll = false }: {
  projectId: string; motor: ProjectMotor; staff: boolean; busy?: boolean; t: (key: string, o?: Record<string, unknown>) => string;
  onChanged: () => void | Promise<void>; showAll?: boolean;
}) {
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const die = motor.canonical_die ?? motor.die;
  const access = reportAccess(motor, staff);
  const recommended = isRecommended(motor);
  const run = async (action: () => Promise<unknown>) => {
    setWorking(true); setError('');
    try { await action(); await onChanged(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setWorking(false); }
  };
  const download = async (format: 'docx' | 'pdf') => {
    setWorking(true); setError('');
    const q = `die=${encodeURIComponent(motor.die)}&config=${encodeURIComponent(motor.config)}&format=${format}${showAll ? '&show_all=true' : ''}`;
    const problem = await downloadExport(`${API}/api/projects/${encodeURIComponent(projectId)}/motors/report?${q}`, `${motor.die} ${motor.config} report.${format}`);
    if (problem) setError(problem);
    setWorking(false);
  };
  const disabledTip = access.enabled ? '' : t('report.notReviewedTip');
  return (
    <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap data-testid="variant-controls">
      {staff
        ? <Tooltip title={recommended ? `${t('recommended.tip')} ${flagWho(motor.recommended)}` : t('recommended.setTip')}>
          <span><IconButton size="small" aria-label={t('recommended.label')} aria-pressed={recommended} disabled={busy || working}
            onClick={() => void run(() => developmentApi.setRecommended(projectId, die, motor.config, !recommended))}
            sx={{ color: recommended ? '#f59e0b' : 'var(--text-4)' }}>{recommended ? <StarIcon sx={{ fontSize: 18 }} /> : <StarBorderIcon sx={{ fontSize: 18 }} />}</IconButton></span>
        </Tooltip>
        : recommended && <Tooltip title={`${t('recommended.tip')} ${flagWho(motor.recommended)}`}>
          <Chip size="small" icon={<StarIcon sx={{ fontSize: 14, color: '#f59e0b !important' }} />} label={t('recommended.label')} data-testid="recommended-chip" sx={{ height: 20, fontSize: 11 }} />
        </Tooltip>}
      {staff && recommended && <Chip size="small" label={t('recommended.label')} data-testid="recommended-chip" sx={{ height: 20, fontSize: 11, color: '#b45309', bgcolor: 'rgba(245,158,11,0.15)' }} />}
      <Tooltip title={disabledTip}>
        <span>
          <Button size="small" disabled={!access.enabled || busy || working} onClick={() => void download('docx')}>{t('report.word')}</Button>
          <Button size="small" disabled={!access.enabled || busy || working} onClick={() => void download('pdf')}>{t('report.pdf')}</Button>
        </span>
      </Tooltip>
      {staff && (
        <Tooltip title={motor.report_reviewed ? `${t('report.reviewedTip')} ${flagWho(motor.report_reviewed)}` : t('report.reviewedTip')}>
          <FormControlLabel sx={{ m: 0 }} control={<Checkbox size="small" checked={!!motor.report_reviewed} disabled={busy || working}
            onChange={(e) => void run(() => developmentApi.setReportReviewed(projectId, die, motor.config, e.target.checked))} />}
            label={<span style={{ fontSize: 11 }}>{t('report.reviewed')}</span>} />
        </Tooltip>
      )}
      {error && <Tooltip title={error}><Chip size="small" color="error" label="!" /></Tooltip>}
    </Stack>
  );
}
