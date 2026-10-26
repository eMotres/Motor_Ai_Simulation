import { useEffect, useRef, useState } from 'react';
import { Box, Button, Checkbox, Chip, CircularProgress, FormControlLabel, IconButton, Stack, Tooltip } from '@mui/material';
import StarIcon from '@mui/icons-material/Star';
import StarBorderIcon from '@mui/icons-material/StarBorder';
import { downloadExport } from '../../../lib/exportDownload';
import { IDLE as RING_IDLE, newRunId, ringDone, ringFail, ringPoll, ringStart, ringTip, type ReportProgressInfo, type ReportRing } from '../../../components/catalog/reportProgress';
import { motorRevisionsApi } from '../../../lib/motorRevisionsApi';
import { revisionAction } from '../../../lib/motorRevisions';
import { useMotorRevision, useMotorRevisionsStore } from '../../../stores/motorRevisionsStore';
import { NewRevisionDialog } from './NewRevisionDialog';
import { developmentApi } from '../api';
import { flagWho, isRecommended, reportAccess } from '../projectFlags';
import type { ProjectMotor } from '../types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

/** What a project variant offers next to its name (owner 2026-10-26): the "Recommended" star (staff mark it, everybody sees it),
 *  the report buttons (members only after our engineers checked the report; staff always) and, for staff, the "Report
 *  reviewed" toggle.  Owner 2026-10-27: staff also get «Assign Rev A» on a motor with no product revision and «New revision» on the
 *  working revision of a lineage (an older revision is read-only and offers neither).  One line, labels + tooltips only. */
export function VariantControls({ projectId, motor, staff, busy = false, t, onChanged, showAll = false }: {
  projectId: string; motor: ProjectMotor; staff: boolean; busy?: boolean; t: (key: string, o?: Record<string, unknown>) => string;
  onChanged: () => void | Promise<void>; showAll?: boolean;
}) {
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const die = motor.canonical_die ?? motor.die;
  const revision = useMotorRevision(motor);
  const revisionButton = revisionAction(revision, staff);
  const refreshRevisions = () => useMotorRevisionsStore.getState().ensure(true);
  const access = reportAccess(motor, staff);
  const recommended = isRecommended(motor);
  const run = async (action: () => Promise<unknown>) => {
    setWorking(true); setError('');
    try { await action(); await onChanged(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setWorking(false); }
  };
  // the same progress ring as the catalogue's report buttons (owner 2026-10-27): the click mints a run id, the build publishes
  // its stages under it, a 1 s poll of GET /api/family/report/progress turns the spin into a percentage
  const [ring, setRing] = useState<ReportRing>(RING_IDLE);
  const pollRef = useRef(0);
  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);
  const download = async (format: 'docx' | 'pdf') => {
    setWorking(true); setError('');
    const key = `report:${format}:${motor.die}/${motor.config}`;
    const runId = newRunId();
    const q = `die=${encodeURIComponent(motor.die)}&config=${encodeURIComponent(motor.config)}&format=${format}${showAll ? '&show_all=true' : ''}&run_id=${encodeURIComponent(runId)}`;
    const t0 = Date.now();
    setRing(ringStart(key, runId));
    const poll = pollRef.current = window.setInterval(() => {
      void (async () => {
        let info: ReportProgressInfo | null = null;
        try {
          const r = await fetch(`${API}/api/family/report/progress?run_id=${encodeURIComponent(runId)}`);
          if (r.ok) info = await r.json();
        } catch { /* a lost poll is not a failed build */ }
        setRing((prev) => (prev.runId === runId ? ringPoll(prev, info, (Date.now() - t0) / 1000) : prev));
      })();
    }, 1000);
    const problem = await downloadExport(`${API}/api/projects/${encodeURIComponent(projectId)}/motors/report?${q}`, `${motor.die} ${motor.config} report.${format}`);
    window.clearInterval(poll); if (pollRef.current === poll) pollRef.current = 0;
    setRing((prev) => (prev.runId === runId ? (problem ? ringFail(prev, problem) : ringDone(prev)) : prev));
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
      {ring.runId && !ring.notice && (
        <Tooltip title={ringTip(ring)}>
          <Box component="span" data-testid="report-ring" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.45, fontSize: 11, color: 'var(--text-2)' }}>
            <CircularProgress variant={ring.pct == null ? 'indeterminate' : 'determinate'} value={ring.pct ?? 0} size={15} thickness={6} sx={{ color: 'inherit' }} />
            {ring.pct != null && <span>{ring.pct} %</span>}
          </Box>
        </Tooltip>
      )}
      {staff && (
        <Tooltip title={motor.report_reviewed ? `${t('report.reviewedTip')} ${flagWho(motor.report_reviewed)}` : t('report.reviewedTip')}>
          <FormControlLabel sx={{ m: 0 }} control={<Checkbox size="small" checked={!!motor.report_reviewed} disabled={busy || working}
            onChange={(e) => void run(() => developmentApi.setReportReviewed(projectId, die, motor.config, e.target.checked))} />}
            label={<span style={{ fontSize: 11 }}>{t('report.reviewed')}</span>} />
        </Tooltip>
      )}
      {revisionButton === 'assign' && (
        <Tooltip title={t('revisions.assignTip')}>
          <span><Button size="small" disabled={busy || working} data-testid="assign-revision"
            onClick={() => void run(async () => { await motorRevisionsApi.assign({ die, config: motor.config, project_id: projectId, agreed_with_client: true }); await refreshRevisions(); })}>{t('revisions.assign')}</Button></span>
        </Tooltip>
      )}
      {revisionButton === 'new' && revision && (
        <>
          <Tooltip title={t('revisions.newTip')}>
            <span><Button size="small" disabled={busy || working} data-testid="new-revision" onClick={() => setRevisionOpen(true)}>{t('revisions.new')}</Button></span>
          </Tooltip>
          <NewRevisionDialog revision={revision} open={revisionOpen} t={t} onClose={() => setRevisionOpen(false)}
            onCreated={async () => { await refreshRevisions(); await onChanged(); }} />
        </>
      )}
      {error && <Tooltip title={error}><Chip size="small" color="error" label="!" /></Tooltip>}
    </Stack>
  );
}
