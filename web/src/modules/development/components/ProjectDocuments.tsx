/** Documents tab of a development project (owner 2026-11-01): the production package - KiCad projects, fabrication zips,
 *  BOM, 3-D models, drawings.  Upload button + drag & drop; staff set category / revision / internal and archive. */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, IconButton, MenuItem, Paper, Select, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import DownloadIcon from '@mui/icons-material/Download';
import ArchiveOutlinedIcon from '@mui/icons-material/ArchiveOutlined';
import UnarchiveOutlinedIcon from '@mui/icons-material/UnarchiveOutlined';
import StarIcon from '@mui/icons-material/Star';
import StarBorderIcon from '@mui/icons-material/StarBorder';
import ImageOutlinedIcon from '@mui/icons-material/ImageOutlined';
import HelpTip from '../../../components/common/HelpTip';
import { NARROW } from '../../../lib/narrowLayout';
import { developmentApi, downloadProjectDocument, downloadProjectDocumentsZip } from '../api';
import type { DocumentFolder, ProjectDocument } from '../types';
import { formatBytes, isCoverImage, MAX_DOCUMENT_BYTES } from '../documentsModel';

const FOLDERS: DocumentFolder[] = ['kicad', 'fabrication', 'bom', '3d', 'docs'];
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

interface Props {
  projectId: string;
  language: string;
  t: (key: string, opts?: Record<string, unknown>) => any;
  /** the project's cover changed (new cache key, or null when removed): the page updates the header image */
  onCoverChange?: (version: string | null) => void;
}

export default function ProjectDocuments({ projectId, language, t, onCoverChange }: Props) {
  const [docs, setDocs] = useState<ProjectDocument[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  // the INTERNAL switch is MOTRES staff's: a customer-side owner uploads and archives, but never sees or ticks it
  const [showInternal, setShowInternal] = useState(false);   // the server says who is MOTRES staff: a customer never marks or sees internal
  const [showArchived, setShowArchived] = useState(false);
  const [filter, setFilter] = useState<DocumentFolder | 'all'>('all');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const coverInput = useRef<HTMLInputElement>(null);
  const coverVersion = useRef<string | null>(null);
  const projectRef = useRef(projectId);
  projectRef.current = projectId;

  const load = useCallback(async () => {
    const id = projectId;
    try {
      const list = await developmentApi.documents(id, showArchived);
      if (projectRef.current !== id) return;
      setDocs(list.documents);
      setCanEdit(list.can_edit);
      setShowInternal(!!list.is_staff);
      const version = list.cover_version ?? null;
      if (version !== coverVersion.current) { coverVersion.current = version; onCoverChange?.(version); }
    } catch (e) { if (projectRef.current === id) setError(msg(e)); }
  }, [projectId, showArchived]);
  useEffect(() => { setDocs([]); setError(''); void load(); }, [load]);

  const uploadFiles = async (files: File[]) => {
    if (!canEdit || !files.length) return;
    setError('');
    const id = projectId;
    for (const file of files) {
      if (file.size > MAX_DOCUMENT_BYTES) { setError(t('documents.tooLarge', { name: file.name })); continue; }
      setBusy(t('documents.uploading', { name: file.name }));
      try { await developmentApi.uploadDocument(id, file, filter === 'all' ? {} : { folder: filter }); }
      catch (e) { setError(`${file.name}: ${msg(e)}`); }
    }
    setBusy('');
    if (projectRef.current === id) await load();
  };
  const patch = async (doc: ProjectDocument, body: Parameters<typeof developmentApi.patchDocument>[2]) => {
    setError('');
    try { await developmentApi.patchDocument(projectId, doc.id, body); await load(); } catch (e) { setError(msg(e)); await load(); }
  };
  const setCover = async (docId: string) => {
    setError('');
    try { const r = await developmentApi.setCover(projectId, docId); coverVersion.current = r.cover_version; onCoverChange?.(r.cover_version); await load(); }
    catch (e) { setError(msg(e)); }
  };
  const clearCover = async () => {
    setError('');
    try { await developmentApi.clearCover(projectId); coverVersion.current = null; onCoverChange?.(null); await load(); } catch (e) { setError(msg(e)); }
  };
  /** A direct upload of the cover: the image is stored as an ordinary document first, then marked as the cover. */
  const uploadCover = async (file: File | undefined) => {
    if (!file || !canEdit) return;
    setError('');
    if (!isCoverImage(file.name)) { setError(t('documents.coverNotImage', { name: file.name })); return; }
    setBusy(t('documents.uploading', { name: file.name }));
    try { const doc = await developmentApi.uploadDocument(projectId, file, { folder: 'docs' }); await setCover(doc.id); }
    catch (e) { setError(`${file.name}: ${msg(e)}`); }
    finally { setBusy(''); }
  };
  const run = async (action: () => Promise<unknown>) => { setError(''); try { await action(); } catch (e) { setError(msg(e)); } };

  const shown = docs.filter((d) => filter === 'all' || d.folder === filter);
  const dateOf = (iso: string) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(language); };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragging(false);
    void uploadFiles(Array.from(e.dataTransfer.files));
  };

  return <Box
    onDragOver={(e) => { if (canEdit) { e.preventDefault(); setDragging(true); } }}
    onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
    onDrop={onDrop}
    sx={{ minWidth: 0, borderRadius: 1.5, outline: dragging ? '2px dashed var(--accent, #1976d2)' : '2px dashed transparent', outlineOffset: 2 }}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
      <HelpTip title={t('documents.tip')} />
      {canEdit && <>
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => { void uploadFiles(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
        <Button size="small" startIcon={<UploadFileIcon />} disabled={!!busy} onClick={() => fileInput.current?.click()}>{t('documents.upload')}</Button>
        <input ref={coverInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => { void uploadCover(e.target.files?.[0]); e.target.value = ''; }} />
        <Tooltip title={t('documents.coverTip')}><span><Button size="small" startIcon={<ImageOutlinedIcon />} disabled={!!busy} onClick={() => coverInput.current?.click()}>{t('documents.coverUpload')}</Button></span></Tooltip>
      </>}
      <Button size="small" startIcon={<DownloadIcon />} disabled={!docs.length} onClick={() => void run(() => downloadProjectDocumentsZip(projectId))}>{t('documents.zipAll')}</Button>
      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
        {(['all', ...FOLDERS] as const).map((f) => <Chip key={f} size="small" clickable color={filter === f ? 'primary' : 'default'}
          label={f === 'all' ? t('documents.all') : t(`documents.folders.${f}`)} onClick={() => setFilter(f)} />)}
      </Box>
      {canEdit && <Box sx={{ display: 'flex', alignItems: 'center', ml: 'auto' }}>
        <Switch size="small" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} inputProps={{ 'aria-label': t('documents.showArchived') }} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('documents.showArchived')}</Typography>
        <HelpTip title={t('documents.archiveTip')} />
      </Box>}
    </Box>
    {busy && <Typography sx={{ fontSize: 12, color: 'var(--text-2)', mb: 1 }}>{busy}</Typography>}
    {error && <Alert severity="error" onClose={() => setError('')} sx={{ mb: 1 }}>{error}</Alert>}
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto' }}>
      <Table size="small" sx={{
        [NARROW]: { minWidth: 760 },
        '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
        '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
      }}>
        <TableHead><TableRow>
          <TableCell>{t('documents.colName')}</TableCell><TableCell>{t('documents.colCategory')}</TableCell>
          <TableCell>{t('documents.colRevision')}</TableCell><TableCell align="right">{t('documents.colSize')}</TableCell>
          <TableCell>{t('documents.colUploader')}</TableCell><TableCell>{t('documents.colDate')}</TableCell>
          {showInternal && <TableCell><Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>{t('documents.colInternal')}<HelpTip title={t('documents.internalTip')} /></Box></TableCell>}
          <TableCell />
        </TableRow></TableHead>
        <TableBody>
          {shown.map((d) => <TableRow key={d.id} hover sx={d.archived ? { opacity: 0.55 } : undefined}>
            <TableCell sx={{ maxWidth: 320, overflowWrap: 'anywhere' }}>
              {d.name}{d.archived && <Chip size="small" label={t('documents.archived')} sx={{ ml: 0.5, height: 16, fontSize: 10 }} />}
            </TableCell>
            <TableCell>{canEdit
              ? <Select size="small" variant="standard" value={d.folder} onChange={(e) => void patch(d, { folder: e.target.value as DocumentFolder })} sx={{ fontSize: 12.5 }}>
                {FOLDERS.map((f) => <MenuItem key={f} value={f}>{t(`documents.folders.${f}`)}</MenuItem>)}
              </Select>
              : t(`documents.folders.${d.folder}`)}</TableCell>
            <TableCell>{canEdit
              ? <RevisionField value={d.revision} placeholder={t('documents.revisionPlaceholder')} onCommit={(revision) => void patch(d, { revision })} />
              : d.revision}</TableCell>
            <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>{formatBytes(d.size, language)}</TableCell>
            <TableCell>{d.uploader || t('documents.member')}</TableCell>
            <TableCell sx={{ whiteSpace: 'nowrap' }}>{dateOf(d.uploaded_at)}</TableCell>
            {showInternal && <TableCell><Switch size="small" checked={d.internal} onChange={(e) => void patch(d, { internal: e.target.checked })} inputProps={{ 'aria-label': t('documents.colInternal') }} /></TableCell>}
            <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
              <Tooltip title={t('documents.download')}><IconButton size="small" aria-label={t('documents.download')} onClick={() => void run(() => downloadProjectDocument(projectId, d))}><DownloadIcon fontSize="small" /></IconButton></Tooltip>
              {canEdit && !d.archived && (d.is_cover
                ? <Tooltip title={t('documents.coverClear')}><IconButton size="small" color="primary" aria-label={t('documents.coverClear')} onClick={() => void clearCover()}><StarIcon fontSize="small" /></IconButton></Tooltip>
                : (!d.internal && isCoverImage(d.name)) && <Tooltip title={t('documents.coverSet')}><IconButton size="small" aria-label={t('documents.coverSet')} onClick={() => void setCover(d.id)}><StarBorderIcon fontSize="small" /></IconButton></Tooltip>)}
              {canEdit && (d.archived
                ? <Tooltip title={t('documents.restore')}><IconButton size="small" aria-label={t('documents.restore')} onClick={() => void patch(d, { archived: false })}><UnarchiveOutlinedIcon fontSize="small" /></IconButton></Tooltip>
                : <Tooltip title={t('documents.archive')}><IconButton size="small" aria-label={t('documents.archive')} onClick={() => void run(async () => { await developmentApi.archiveDocument(projectId, d.id); await load(); })}><ArchiveOutlinedIcon fontSize="small" /></IconButton></Tooltip>)}
            </TableCell>
          </TableRow>)}
          {!shown.length && <TableRow><TableCell colSpan={showInternal ? 8 : 7}>
            <Stack alignItems="center" sx={{ py: 3, color: 'var(--text-3)' }}>
              <Typography sx={{ fontSize: 13 }}>{canEdit ? t('documents.dropHere') : t('documents.empty')}</Typography>
            </Stack>
          </TableCell></TableRow>}
        </TableBody>
      </Table>
    </Paper>
  </Box>;
}

/** The revision label is saved when the field is left (or Enter is pressed), not on every key. */
function RevisionField({ value, placeholder, onCommit }: { value: string; placeholder: string; onCommit: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => { const v = text.trim(); if (v !== value) onCommit(v); };
  return <TextField size="small" variant="standard" value={text} placeholder={placeholder} inputProps={{ maxLength: 40, style: { fontSize: 12.5, width: 70 } }}
    onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />;
}
