// The project's "Contract" section (owner 2026-11-22): "How it works" on top, then the status with its history, the standard terms
// (read-only), the files (agreement, Annex A, Annex B, the two signed scans) with every version kept, and the customer company state
// for Annex B.  Signing, stage 1: download, sign, upload the scan.  Nothing here sends mail.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, MenuItem,
  Paper, Select, Switch, Table, TableBody, TableCell, TableRow, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import UploadIcon from '@mui/icons-material/Upload';
import ArchiveOutlinedIcon from '@mui/icons-material/ArchiveOutlined';
import UnarchiveOutlinedIcon from '@mui/icons-material/UnarchiveOutlined';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import ContractScheme from './ContractScheme';
import {
  CUSTOMER_SCHEME, KIND_ORDER, STATUS_ORDER, contractApi,
  type ContractFile, type ContractFileRef, type ContractKind, type ContractView, type Seen,
} from './contractModel';

const TERMS = ['advance', 'samples', 'series', 'dispatch', 'supply', 'law'] as const;

const size = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} kB`);

const UploadDialog: React.FC<{ kind: ContractKind | null; busy: boolean; onClose: () => void; onSubmit: (f: File, note: string) => void }> = ({ kind, busy, onClose, onSubmit }) => {
  const scan = kind === 'signed_customer' || kind === 'signed_motres';
  const { t } = useTranslation('contract');
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState('');
  useEffect(() => { setFile(null); setNote(''); }, [kind]);
  return (
    <Dialog open={kind !== null} onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: 16 }}>{kind ? t(`kind.${kind}`) : ''}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: '8px !important' }}>
        <Button variant="outlined" component="label" size="small" sx={{ textTransform: 'none', justifyContent: 'flex-start' }}>
          {file ? `${file.name} (${size(file.size)})` : t('pickFile')}
          <input hidden type="file" accept={scan ? '.pdf,.png,.jpg,.jpeg' : '.pdf,.docx,.png,.jpg,.jpeg'} data-testid="contract-file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Button>
        <TextField size="small" label={t('note')} value={note} onChange={(e) => setNote(e.target.value)} inputProps={{ maxLength: 300 }} />
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose} disabled={busy}>{t('cancel')}</Button>
        <Button size="small" variant="contained" disabled={busy || !file} startIcon={busy ? <CircularProgress size={14} /> : undefined}
          onClick={() => file && onSubmit(file, note.trim())} data-testid="contract-upload-submit">{t('upload')}</Button>
      </DialogActions>
    </Dialog>
  );
};

const ProjectContract: React.FC<{ projectId: string }> = ({ projectId }) => {
  const { t, i18n, ready } = useTranslation('contract');
  const [view, setView] = useState<ContractView | null>(null);
  const [archived, setArchived] = useState(false);
  const [scheme, setScheme] = useState<'customer' | 'staff'>('customer');
  // the upload dialog: its kind AND what was on screen when it opened (revision + terms), sent as they were; a refusal closes it
  const [upload, setUpload] = useState<{ kind: ContractKind; seen: Seen } | null>(null);
  const uploadKind = upload?.kind ?? null;
  const [rejecting, setRejecting] = useState<{ scan: ContractFileRef; seen: Seen } | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setView(await contractApi.get(projectId, archived)); } catch (e: any) { setErr(e?.message ?? String(e)); }
  }, [projectId, archived]);
  useEffect(() => { void load(); }, [load]);

  const run = async (fn: () => Promise<ContractView | void>) => {
    setBusy(true); setErr(null);
    try { const v = await fn(); if (v) { if (archived) await load(); else setView(v); } } catch (e: any) { setErr(e?.message ?? String(e)); void load(); } finally { setBusy(false); }   // a refusal reloads: the page shows what the server has
  };
  const date = (iso: string) => { try { return new Date(iso).toLocaleDateString(i18n.language || undefined); } catch { return iso.slice(0, 10); } };

  if (!ready || !view) return err ? <Alert severity="error">{err}</Alert> : <CircularProgress size={18} />;
  const byKind = (k: ContractKind) => view.files.filter((f) => f.kind === k);
  const head = { fontSize: 14, fontWeight: 800, color: 'var(--text-0)' } as const;
  const btn = { textTransform: 'none', fontSize: 12.5 } as const;
  const fileLine = (f: ContractFile, current: boolean) => (
    <Box key={f.id} sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', opacity: f.archived ? 0.55 : 1, pl: current ? 0 : 2 }}>
      <Tooltip title={`sha256 ${f.sha256}`}><Typography sx={{ fontSize: 13, color: current ? 'var(--text-0)' : 'var(--text-2)' }}>{f.name}</Typography></Tooltip>
      <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('revisionShort', { n: f.revision ?? 1 })} · {date(f.uploaded_at)}{f.uploader ? ` · ${f.uploader}` : ''} · {size(f.size)}{f.note ? ` · ${f.note}` : ''}{f.archived ? ` · ${t('archived')}` : ''}</Typography>
      {f.frozen && <Tooltip title={t('frozenTip')}><LockOutlinedIcon data-testid="contract-frozen" sx={{ fontSize: 15, color: 'var(--text-2)' }} /></Tooltip>}
      <Tooltip title={t('download')}><IconButton size="small" aria-label={t('download')} disabled={busy} onClick={() => void run(() => contractApi.download(projectId, f))}><DownloadIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
      {view.can_manage && !f.frozen && (
        <Tooltip title={t(f.archived ? 'restore' : 'archive')}>
          <IconButton size="small" aria-label={t(f.archived ? 'restore' : 'archive')} disabled={busy} onClick={() => void run(() => contractApi.archive(projectId, f.id, !f.archived))}>
            {f.archived ? <UnarchiveOutlinedIcon sx={{ fontSize: 17 }} /> : <ArchiveOutlinedIcon sx={{ fontSize: 17 }} />}
          </IconButton>
        </Tooltip>
      )}
    </Box>
  );

  return (
    <Box data-testid="project-contract" sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {err && <Alert severity="error" onClose={() => setErr(null)}>{err}</Alert>}

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={head}>{t('scheme.title')}</Typography>
        <HelpTip title={t('scheme.tip')} />
        <Box sx={{ flex: 1 }} />
        {view.staff && (
          <ToggleButtonGroup size="small" exclusive value={scheme} onChange={(_e, v) => v && setScheme(v)} aria-label={t('scheme.view')}>
            <ToggleButton value="customer" sx={btn}>{t('scheme.customerView')}</ToggleButton>
            <ToggleButton value="staff" sx={btn} data-testid="contract-staff-view">{t('scheme.staffView')}</ToggleButton>
          </ToggleButtonGroup>
        )}
      </Box>
      {scheme === 'staff' && view.staff
        ? (view.staff_scheme
          ? <ContractScheme data={view.staff_scheme} step={view.step} steps={view.steps} translate={false} />
          : <Alert severity="info">{t('scheme.staffMissing')}</Alert>)
        : null}
      {scheme === 'staff' && view.staff && (
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button size="small" component="label" sx={btn} startIcon={<UploadIcon sx={{ fontSize: 16 }} />} disabled={busy} data-testid="contract-scheme-upload">
            {t('scheme.upload')}
            <input hidden type="file" accept=".json,application/json" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = '';
              if (f) void run(async () => { await contractApi.putScheme(await f.text()); await load(); }); }} />
          </Button>
        </Box>
      )}
      {scheme === 'staff' && view.staff ? null
        : <ContractScheme data={CUSTOMER_SCHEME} step={view.step} steps={view.steps} translate />}

      <Paper variant="outlined" sx={{ p: 1.25, bgcolor: 'var(--panel)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1 }}>
          <Typography sx={head}>{t('status.title')}</Typography>
          <Chip size="small" variant="outlined" label={t('revision', { n: view.revision })} data-testid="contract-revision" />
          <HelpTip title={t('status.tip')} />
          {STATUS_ORDER.map((s, i) => (
            <React.Fragment key={s}>
              <Chip size="small" label={t(`status.${s}`)} color={s === view.status ? 'primary' : 'default'} variant={s === view.status ? 'filled' : 'outlined'}
                data-current={s === view.status ? 'true' : undefined} />
              {i < STATUS_ORDER.length - 1 && <Typography aria-hidden sx={{ color: 'var(--text-3)' }}>→</Typography>}
            </React.Fragment>
          ))}
          <Box sx={{ flex: 1 }} />
          {view.can_manage && view.status === 'draft' && (
            <Tooltip title={t('status.sendTip')}><span>
              <Button size="small" variant="contained" sx={btn} disabled={busy || !view.current.agreement} data-testid="contract-send" onClick={() => void run(() => contractApi.status(projectId, { status: 'sent', revision: view.revision, terms_digest: view.terms_digest }))}>
                {t('status.set.sent')}
              </Button>
            </span></Tooltip>
          )}
          {view.can_manage && view.status !== 'draft' && (
            <Tooltip title={t('status.newRevisionTip')}>
              <Button size="small" sx={btn} disabled={busy} data-testid="contract-new-revision" onClick={() => void run(() => contractApi.status(projectId, { new_revision: true, revision: view.revision, terms_digest: view.terms_digest }))}>{t('status.set.newRevision')}</Button>
            </Tooltip>
          )}
          {view.can_manage && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('sampleIteration')}</Typography>
              <Select size="small" value={view.sample_iteration} disabled={busy} sx={{ fontSize: 12.5 }} aria-label={t('sampleIteration')}
                onChange={(e) => void run(() => contractApi.status(projectId, { sample_iteration: Number(e.target.value) as 1 | 2 }))}>
                <MenuItem value={1}>1</MenuItem><MenuItem value={2}>2</MenuItem>
              </Select>
            </Box>
          )}
        </Box>
        {view.pending_scan && (() => {
          const scan = view.pending_scan;
          const file = view.files.find((f) => f.id === scan.id);
          return (
            <Alert severity="info" data-testid="contract-pending" sx={{ mb: 1, alignItems: 'center' }}
              action={view.can_manage ? (
                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                  {file && <Button size="small" sx={btn} disabled={busy} onClick={() => void run(() => contractApi.download(projectId, file))}>{t('review.open')}</Button>}
                  <Button size="small" variant="contained" sx={btn} disabled={busy} data-testid="contract-confirm"
                    onClick={() => { const seen = { revision: view.revision, terms_digest: view.terms_digest }; void run(() => contractApi.confirm(projectId, scan, seen)); }}>
                    {t(scan.kind === 'signed_motres' ? 'review.confirmBoth' : 'review.confirmCustomer')}
                  </Button>
                  <Button size="small" sx={btn} disabled={busy} onClick={() => { setRejectReason(''); setRejecting({ scan, seen: { revision: view.revision, terms_digest: view.terms_digest } }); }}>{t('review.reject')}</Button>
                </Box>) : undefined}>
              {t(view.can_manage ? 'review.awaitingStaff' : 'review.awaitingCustomer', { name: scan.name })}
            </Alert>
          );
        })()}
        {view.history.length > 0 && (
          <Table size="small" data-testid="contract-history">
            <TableBody>
              {[...view.history].reverse().map((h, i) => (
                <TableRow key={i}>
                  <TableCell sx={{ fontSize: 12.5, width: 160 }}>{t(`status.${h.status}`)}</TableCell>
                  <TableCell sx={{ fontSize: 12.5 }}>{t('revisionShort', { n: h.revision ?? 1 })}</TableCell>
                  <TableCell sx={{ fontSize: 12.5 }}>{date(h.at)}</TableCell>
                  <TableCell sx={{ fontSize: 12.5 }}>{h.by}</TableCell>
                  <TableCell sx={{ fontSize: 12.5, whiteSpace: 'normal' }}>{(h.files ?? []).map((f) => <Tooltip key={f.id} title={`sha256 ${f.sha256}`}><span>{f.name} </span></Tooltip>)}{h.reason ? ` · ${h.reason}` : ''}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Paper>

      <Paper variant="outlined" sx={{ p: 1.25, bgcolor: 'var(--panel)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 0.75 }}>
          <Typography sx={head}>{t('files.title')}</Typography>
          <HelpTip title={t('files.tip')} />
          <Box sx={{ flex: 1 }} />
          {view.can_manage && <FormControlLabel control={<Switch size="small" checked={archived} onChange={(e) => setArchived(e.target.checked)} />} label={<Typography sx={{ fontSize: 12 }}>{t('showArchived')}</Typography>} />}
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {KIND_ORDER.map((k) => {
            const rows = byKind(k);
            const cur = rows.find((f) => f.id === view.current[k]);
            const canUpload = view.upload_kinds.includes(k);
            if (!rows.length && !canUpload) return null;
            return (
              <Box key={k} data-kind={k} sx={{ borderTop: '1px solid var(--line-soft)', pt: 0.75 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Typography sx={{ fontSize: 13, fontWeight: 700, flex: 1 }}>{t(`kind.${k}`)}</Typography>
                  {k === 'annex_a' && <HelpTip title={t('annexATip')} />}
                  {k === 'annex_b' && (
                    <Tooltip title={t(view.company.available ? (view.company.filled ? 'company.filledTip' : 'company.missingTip') : 'company.notYetTip')}>
                      <Chip size="small" variant="outlined" color={view.company.filled ? 'success' : 'default'}
                        label={view.company.filled ? (view.company.confirmed_on ? t('company.confirmed', { date: view.company.confirmed_on }) : t('company.filled')) : t('company.missing')} />
                    </Tooltip>
                  )}
                  {canUpload && (
                    <Button size="small" sx={btn} startIcon={<UploadIcon sx={{ fontSize: 16 }} />} disabled={busy} onClick={() => setUpload({ kind: k, seen: { revision: view.revision, terms_digest: view.terms_digest } })} data-testid={`contract-upload-${k}`}>
                      {t(rows.length ? 'uploadNew' : 'upload')}
                    </Button>
                  )}
                </Box>
                {cur && fileLine(cur, true)}
                {rows.filter((f) => f.id !== cur?.id).map((f) => fileLine(f, false))}
                {!rows.length && <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>{t('files.none')}</Typography>}
              </Box>
            );
          })}
        </Box>
      </Paper>

      <Paper variant="outlined" sx={{ p: 1.25, bgcolor: 'var(--panel)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
          <Typography sx={head}>{t('terms.title')}</Typography>
          <HelpTip title={t('terms.tip')} />
        </Box>
        <Box component="ul" data-testid="contract-terms" sx={{ m: 0, pl: 2.5 }}>
          {TERMS.map((k) => <Typography component="li" key={k} sx={{ fontSize: 13, color: 'var(--text-1)', mb: 0.25 }}>{t(`terms.${k}`)}</Typography>)}
        </Box>
      </Paper>

      <UploadDialog kind={uploadKind} busy={busy} onClose={() => setUpload(null)}
        onSubmit={(f, note) => { const u = upload; if (u) void run(async () => { try { return await contractApi.upload(projectId, u.kind, f, note, u.seen); } finally { setUpload(null); } }); }} />
      <Dialog open={rejecting !== null} onClose={busy ? undefined : () => setRejecting(null)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ fontSize: 16 }}>{t('review.rejectTitle')}</DialogTitle>
        <DialogContent sx={{ pt: '8px !important' }}>
          <TextField size="small" fullWidth autoFocus label={t('review.reason')} value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} inputProps={{ maxLength: 300 }} />
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={() => setRejecting(null)} disabled={busy}>{t('cancel')}</Button>
          <Button size="small" variant="contained" color="warning" disabled={busy || !rejectReason.trim()} data-testid="contract-reject-submit"
            onClick={() => { const r = rejecting; if (r) void run(async () => { try { return await contractApi.reject(projectId, r.scan, r.seen, rejectReason.trim()); } finally { setRejecting(null); setRejectReason(''); } }); }}>
            {t('review.reject')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ProjectContract;
