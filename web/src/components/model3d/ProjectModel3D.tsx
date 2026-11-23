// The project's "3D model" tab (owner 2026-11-22): the SAME viewer as Configure, two sources behind one switch.
//
// Generated = the 3-D model of the project's linked configuration, exactly what Configure shows for it (Model3DPanel limited to
// the project's motors, read-only here).  Uploaded = the STEP of the real CAD (full assembly or the simplified integration model),
// uploaded by staff with a revision and a "what changed" note; the viewer opens the newest one that is not archived.  The server
// decides who sees which upload (/api/projects/{id}/model3d): the customer side only the ones marked visible, and the STEP itself
// only through the NDA / verification gate - looking at it here never hands out the file.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton,
  MenuItem, Select, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import UploadIcon from '@mui/icons-material/Upload';
import ArchiveOutlinedIcon from '@mui/icons-material/ArchiveOutlined';
import UnarchiveOutlinedIcon from '@mui/icons-material/UnarchiveOutlined';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { UnifiedViewer } from '../../modules/viewer3d';
import { filenameFromDisposition, formatBytes } from '../../lib/model3d';
import Model3DPanel from './Model3DPanel';
import { useRememberedView } from '../../lib/useRememberedView';
import { bool, oneOf, readView, writeView } from '../../lib/viewMemory';
import { openingSource, sourceScope, type ModelSource } from './projectModelSource';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');
const enc = encodeURIComponent;

export interface ProjectModelRow {
  id: string; name: string; revision: string; note: string; size: number; sha256: string; uploaded_at: string; uploader: string;
  /** staff only */
  visible_to_customer?: boolean; archived?: boolean; archived_at?: string | null;
}
interface ProjectModelList { models: ProjectModelRow[]; latest: string | null; can_upload: boolean; can_download: boolean }

const base = (projectId: string) => `${API}/api/projects/${enc(projectId)}/model3d`;

async function failure(r: Response): Promise<string> {
  try {
    const body = await r.json();
    const d = body?.detail;
    return typeof d === 'string' ? d : typeof d?.message === 'string' ? d.message : `HTTP ${r.status}`;
  } catch { return `HTTP ${r.status}`; }
}

const UploadDialog: React.FC<{ open: boolean; busy: boolean; onClose: () => void; onSubmit: (f: File, revision: string, note: string, visible: boolean) => void }> =
  ({ open, busy, onClose, onSubmit }) => {
    const { t } = useTranslation('model3d');
    const [file, setFile] = useState<File | null>(null);
    const [revision, setRevision] = useState('');
    const [note, setNote] = useState('');
    const [visible, setVisible] = useState(false);
    useEffect(() => { if (open) { setFile(null); setRevision(''); setNote(''); setVisible(false); } }, [open]);
    return (
      <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ fontSize: 16 }}>{t('project.uploadTitle')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: '8px !important' }}>
          <Button variant="outlined" component="label" size="small" sx={{ textTransform: 'none', justifyContent: 'flex-start' }}>
            {file ? `${file.name} (${formatBytes(file.size)})` : t('project.pickFile')}
            <input hidden type="file" accept=".step,.stp" data-testid="project-model-file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Button>
          <TextField size="small" label={t('project.revision')} value={revision} onChange={(e) => setRevision(e.target.value)} inputProps={{ maxLength: 40 }} required />
          <TextField size="small" label={t('project.note')} value={note} onChange={(e) => setNote(e.target.value)} inputProps={{ maxLength: 500 }} multiline minRows={2} />
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <FormControlLabel control={<Checkbox size="small" checked={visible} onChange={(e) => setVisible(e.target.checked)} />}
              label={<Typography sx={{ fontSize: 13 }}>{t('project.visible')}</Typography>} />
            <HelpTip title={t('project.visibleTip')} />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={onClose} disabled={busy}>{t('project.cancel')}</Button>
          <Button size="small" variant="contained" disabled={busy || !file || !revision.trim()} data-testid="project-model-upload-submit"
            startIcon={busy ? <CircularProgress size={14} /> : undefined} onClick={() => file && onSubmit(file, revision.trim(), note.trim(), visible)}>
            {t('project.upload')}
          </Button>
        </DialogActions>
      </Dialog>
    );
  };

const ProjectModel3D: React.FC<{ projectId: string; motors: ReadonlyArray<{ die: string; config: string }>; language?: string; controller?: boolean }> = ({ projectId, motors, language, controller = false }) => {
  const { t, ready } = useTranslation('model3d');
  // remembered across reloads (owner rule: every page remembers where the user was): per project the source chosen and the revision
  // picked, and Show archived; a picked revision that has gone (archived, not visible) falls back to the latest.  With no choice made
  // in this project the tab opens on Uploaded when an uploaded model exists (owner 2026-11-23); a controller project has no Generated.
  const [chosen, setChosen] = useState<ModelSource | null>(() => readView(sourceScope(projectId), oneOf(['generated', 'uploaded'] as const)));
  useEffect(() => { setChosen(readView(sourceScope(projectId), oneOf(['generated', 'uploaded'] as const))); }, [projectId]);
  const setSource = (next: ModelSource) => { setChosen(next); writeView(sourceScope(projectId), next); };
  const [list, setList] = useState<ProjectModelList | null>(null);
  const [showArchived, setShowArchived] = useRememberedView<boolean>('project.model3d.showArchived', false, (raw) => bool(raw) ?? null);
  const pickKey = `project.model3d.pick.${projectId}`;
  const [picked, setPickedState] = useState<string>('');
  const setPicked = (next: string | ((cur: string) => string)) => setPickedState((cur) => {
    const value = typeof next === 'function' ? next(cur) : next;
    if (value) writeView(pickKey, value);
    return value;
  });
  const [uploadOpen, setUploadOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  // one key per linked configuration, so the generated view does not reload on every project refresh
  const linked = useMemo(() => motors.filter((m, i) => motors.findIndex((o) => o.die === m.die && o.config === m.config) === i).map((m) => ({ die: m.die, config: m.config })),
    [motors.map((m) => `${m.die}\u0000${m.config}`).join('\u0001')]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const r = await fetch(`${base(projectId)}${showArchived ? '?archived=1' : ''}`);
        if (!r.ok) { if (live) setErr(await failure(r)); return; }
        const j = (await r.json()) as ProjectModelList;
        if (!live) return;
        setList(j);
        // the latest upload opens first (and again after an upload)
        const isLive = (id: string | null | undefined) => !!id && j.models.some((m) => m.id === id && !m.archived);
        const remembered = readView(pickKey, (raw) => (typeof raw === 'string' ? raw : null));
        setPicked((cur) => (isLive(cur) ? cur : isLive(remembered) ? remembered! : j.latest ?? ''));
      } catch (e: any) { if (live) setErr(e?.message ?? String(e)); }
    })();
    return () => { live = false; };
  }, [projectId, showArchived, reload]);

  const shown = useMemo(() => list?.models.find((m) => m.id === picked) ?? null, [list, picked]);
  const staff = !!list?.can_upload;
  const date = (iso: string) => { try { return new Date(iso).toLocaleDateString(language || undefined); } catch { return iso.slice(0, 10); } };

  const upload = async (file: File, revision: string, note: string, visible: boolean) => {
    setBusy(true); setErr(null);
    try {
      const form = new FormData();
      form.append('file', file, file.name);
      form.append('revision', revision);
      if (note) form.append('note', note);
      if (visible) form.append('visible_to_customer', 'true');
      const r = await fetch(base(projectId), { method: 'POST', body: form });
      if (!r.ok) { setErr(await failure(r)); return; }
      const row = (await r.json()) as ProjectModelRow;
      setUploadOpen(false); setPicked(row.id); setReload((n) => n + 1);
    } catch (e: any) { setErr(e?.message ?? String(e)); } finally { setBusy(false); }
  };

  const patch = useCallback(async (row: ProjectModelRow, body: { archived?: boolean; visible_to_customer?: boolean }) => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${base(projectId)}/${enc(row.id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) { setErr(await failure(r)); return; }
      setReload((n) => n + 1);
    } catch (e: any) { setErr(e?.message ?? String(e)); } finally { setBusy(false); }
  }, [projectId]);

  const download = async (row: ProjectModelRow) => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${base(projectId)}/${enc(row.id)}/download`);
      if (!r.ok) { setErr(await failure(r)); return; }
      const href = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = href; a.download = filenameFromDisposition(r.headers.get('Content-Disposition'), row.name || 'model.step');
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch (e: any) { setErr(e?.message ?? String(e)); } finally { setBusy(false); }
  };

  if (!ready) return <CircularProgress size={18} />;
  const live = (list?.models ?? []).filter((m) => !m.archived);
  const source: ModelSource = openingSource({ controller, remembered: chosen, liveUploads: live.length });
  const btn = { textTransform: 'none', fontSize: 12.5 } as const;
  return (
    <Box data-testid="project-model3d" sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, flexWrap: 'wrap' }}>
        {controller
          ? <Typography sx={{ fontSize: 13, fontWeight: 700 }}>{t('project.uploaded')}{live.length ? ` (${live.length})` : ''}</Typography>
          : <ToggleButtonGroup size="small" exclusive value={source} onChange={(_e, v) => v && setSource(v)} aria-label={t('project.source')}>
              <ToggleButton value="uploaded" sx={btn} data-testid="project-model-uploaded">{t('project.uploaded')}{live.length ? ` (${live.length})` : ''}</ToggleButton>
              <ToggleButton value="generated" sx={btn} data-testid="project-model-generated">{t('project.generated')}</ToggleButton>
            </ToggleButtonGroup>}
        <HelpTip title={t(source === 'generated' ? 'project.generatedTip' : 'project.uploadedTip')} />
        {source === 'uploaded' && live.length > 1 && (
          <Select size="small" value={picked} onChange={(e) => setPicked(String(e.target.value))} sx={{ fontSize: 13, minWidth: 200 }} aria-label={t('project.revision')}>
            {live.map((m) => <MenuItem key={m.id} value={m.id} sx={{ fontSize: 13 }}>{m.revision} · {m.name}</MenuItem>)}
          </Select>
        )}
        {source === 'uploaded' && shown && (
          list?.can_download
            ? <Button size="small" variant="outlined" sx={btn} disabled={busy} startIcon={<DownloadIcon sx={{ fontSize: 16 }} />} onClick={() => void download(shown)} data-testid="project-model-download">
                {t('downloadStep')} ({formatBytes(shown.size)})
              </Button>
            : <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}><Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('downloadLocked')}</Typography><HelpTip title={t('downloadLockedHelp')} /></Box>
        )}
        <Box sx={{ flex: 1 }} />
        {source === 'uploaded' && staff && (
          <Button size="small" variant="contained" sx={btn} startIcon={<UploadIcon sx={{ fontSize: 16 }} />} onClick={() => setUploadOpen(true)} data-testid="project-model-upload">
            {t('project.upload')}
          </Button>
        )}
      </Box>
      {err && <Alert severity="error" onClose={() => setErr(null)} sx={{ fontSize: 12 }}>{err}</Alert>}

      {source === 'generated' && (
        <Box sx={{ height: { xs: 420, md: 620 }, border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'hidden' }}>
          {linked.length === 0
            ? <Typography sx={{ p: 2, fontSize: 13, color: 'var(--text-3)' }}>{t('project.noLinkedMotor')}</Typography>
            : <Model3DPanel only={linked} />}
        </Box>
      )}

      {source === 'uploaded' && (
        <>
          {shown && (
            <Tooltip title={`sha256 ${shown.sha256}`}>
              <Typography data-testid="project-model-line" sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>
                {shown.name} · {t('project.revision')} {shown.revision} · {date(shown.uploaded_at)} · sha256 {shown.sha256.slice(0, 12)}…{shown.note ? ` · ${shown.note}` : ''}
              </Typography>
            </Tooltip>
          )}
          <Box sx={{ height: { xs: 420, md: 620 }, position: 'relative', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'hidden' }}>
            {shown
              ? <UnifiedViewer sourceKey={`project-model:${projectId}:${shown.id}`} resetKey={`project-model:${projectId}:${shown.id}`}
                  glbUrl={`${base(projectId)}/${enc(shown.id)}/model.glb`} toolbarSpec="full" tree={null}
                  contextLostText={t('contextLost')} noViewText="" onError={(m) => m && setErr(m)} />
              : <Typography sx={{ p: 2, fontSize: 13, color: 'var(--text-3)' }}>{list ? t('project.noUpload') : ''}</Typography>}
          </Box>
          {staff && (   // always for staff: archiving the last live model must not hide Show archived and Restore
            <Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Typography sx={{ fontSize: 13, fontWeight: 700, flex: 1 }}>{t('project.history')}</Typography>
                <FormControlLabel control={<Switch size="small" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />}
                  label={<Typography sx={{ fontSize: 12 }}>{t('project.showArchived')}</Typography>} />
              </Box>
              <Table size="small" data-testid="project-model-history">
                <TableHead><TableRow>
                  <TableCell>{t('project.revision')}</TableCell><TableCell>{t('project.file')}</TableCell><TableCell>{t('project.note')}</TableCell>
                  <TableCell>{t('project.date')}</TableCell><TableCell>{t('project.visibleShort')}</TableCell><TableCell />
                </TableRow></TableHead>
                <TableBody>
                  {list!.models.length === 0 && <TableRow><TableCell colSpan={6} sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>{t(showArchived ? 'project.noUpload' : 'project.noLive')}</TableCell></TableRow>}
                  {list!.models.map((m) => (
                    <TableRow key={m.id} hover selected={m.id === picked} sx={{ opacity: m.archived ? 0.55 : 1, cursor: m.archived ? 'default' : 'pointer' }}
                      onClick={() => { if (!m.archived) setPicked(m.id); }}>
                      <TableCell sx={{ fontSize: 12.5 }}>{m.revision}{m.archived ? ` · ${t('project.archived')}` : ''}</TableCell>
                      <TableCell sx={{ fontSize: 12.5 }}><Tooltip title={`sha256 ${m.sha256}`}><span>{m.name} ({formatBytes(m.size)})</span></Tooltip></TableCell>
                      <TableCell sx={{ fontSize: 12.5, whiteSpace: 'normal', maxWidth: 320 }}>{m.note}</TableCell>
                      <TableCell sx={{ fontSize: 12.5 }}>{date(m.uploaded_at)}{m.uploader ? ` · ${m.uploader}` : ''}</TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <Switch size="small" checked={!!m.visible_to_customer} disabled={busy} inputProps={{ 'aria-label': t('project.visible') }}
                          onChange={(e) => void patch(m, { visible_to_customer: e.target.checked })} />
                      </TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <Tooltip title={t(m.archived ? 'project.restore' : 'project.archive')}>
                          <span><IconButton size="small" disabled={busy} aria-label={t(m.archived ? 'project.restore' : 'project.archive')} onClick={() => void patch(m, { archived: !m.archived })}>
                            {m.archived ? <UnarchiveOutlinedIcon sx={{ fontSize: 17 }} /> : <ArchiveOutlinedIcon sx={{ fontSize: 17 }} />}
                          </IconButton></span>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </>
      )}
      <UploadDialog open={uploadOpen} busy={busy} onClose={() => setUploadOpen(false)} onSubmit={(f, r, n, v) => void upload(f, r, n, v)} />
    </Box>
  );
};

export default ProjectModel3D;
