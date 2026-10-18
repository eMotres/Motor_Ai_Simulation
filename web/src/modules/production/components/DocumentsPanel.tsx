/** Documents: upload (owner/engineer/admin) and traced download (every member). */
import React, { useRef, useState } from 'react';
import { Box, Button, Chip, MenuItem, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { PartName } from './PartName';
import HelpTip from '../../../components/common/HelpTip';
import ProcessView from './ProcessView';
import { downloadFile, openFileInNewWindow, processDocument, productionApi } from '../api';
import type { DocumentRec, Part, ProcessSection, ProdCtx } from '../types';

const DocumentsPanel: React.FC<{ documents: DocumentRec[]; parts: Part[]; ctx: ProdCtx; sections?: ProcessSection[] }> = ({ documents, parts, ctx, sections = [] }) => {
  const { t } = useTranslation('production');
  const fileRef = useRef<HTMLInputElement>(null);
  const [partId, setPartId] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const byId = new Map(parts.map((p) => [p.id, p]));
  const [showSections, setShowSections] = useState(false);
  const procDoc = (lang: 'en' | 'zh', mode: 'view' | 'download') =>
    void processDocument(ctx.die, ctx.config, lang, mode).catch((e) => setErr(t('failed', { reason: String(e) })));

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setErr(null);
    try {
      for (const f of Array.from(files)) await productionApi.uploadDocument(ctx.die, ctx.config, f, partId || null);
      ctx.reload();
    } catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ''; }
  };

  return (
    <Box>
      {/* the process document, generated from the assembly and test steps (owner 2026-10-18); its sections are edited here */}
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.25, mb: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Typography sx={{ fontSize: 13.5, fontWeight: 800 }}>{t('process.docTitle', 'Process document')}</Typography>
          <HelpTip title={t('process.docTip')} />
          <Box sx={{ flex: 1 }} />
          {(['en', 'zh'] as const).map((lg) => (
            <React.Fragment key={lg}>
              <Button size="small" sx={{ textTransform: 'none' }} onClick={() => procDoc(lg, 'view')}>{t('process.view', 'View')} {lg === 'en' ? 'EN' : '中文'}</Button>
              <Button size="small" sx={{ textTransform: 'none' }} onClick={() => procDoc(lg, 'download')}>{t('process.download', 'Download')} {lg === 'en' ? 'EN' : '中文'}</Button>
            </React.Fragment>
          ))}
          {ctx.canEdit && <Button size="small" variant="outlined" sx={{ textTransform: 'none' }} onClick={() => setShowSections((v) => !v)}>
            {showSections ? t('process.hideSections', 'Hide sections') : t('process.editSections', 'Edit process sections')}</Button>}
        </Box>
        {showSections && <Box sx={{ mt: 1.25 }}><ProcessView sections={sections} ctx={ctx} /></Box>}
      </Paper>
      {ctx.canEdit && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
          <TextField select size="small" label={t('docs.forPart')} value={partId} onChange={(e) => setPartId(e.target.value)} sx={{ minWidth: 200 }}>
            <MenuItem value="">{t('docs.general')}</MenuItem>
            {parts.map((p) => <MenuItem key={p.id} value={p.id}>{p.name.en || p.name.zh}</MenuItem>)}
          </TextField>
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} />
          <Button size="small" variant="contained" disabled={busy} sx={{ textTransform: 'none' }} onClick={() => fileRef.current?.click()}>{t('docs.upload')}</Button>
          <HelpTip title={t('docs.uploadTip')} />
          {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
        </Box>
      )}
      {documents.length === 0 ? <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('docs.none')}</Typography> : (
        <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto' }}>
          <Table size="small" sx={{
            '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
            '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
          }}>
            <TableHead>
              <TableRow>
                <TableCell>{t('docs.name')}</TableCell><TableCell>{t('docs.forPart')}</TableCell>
                <TableCell>{t('docs.uploader')}</TableCell><TableCell>{t('docs.date')}</TableCell><TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {documents.map((d) => {
                const p = d.part_id ? byId.get(d.part_id) : undefined;
                return (
                  <TableRow key={d.id} hover>
                    <TableCell>
                      {/* a click opens the document in a new window (pdf / pictures); other types download (owner 2026-10-17) */}
                      <Box component="span" title={t('parts.openFull', 'Open full screen')}
                        onClick={() => {
                          const ext = (d.name.split('.').pop() || '').toLowerCase();
                          const viewable = ['pdf', 'png', 'jpg', 'jpeg'].includes(ext);
                          const run = viewable ? openFileInNewWindow(ctx.die, ctx.config, d.file_id, ext === 'jpeg' ? 'jpg' : ext)
                                               : downloadFile(ctx.die, ctx.config, d.file_id, d.name);
                          void run.catch((e) => setErr(t('failed', { reason: String(e) })));
                        }}
                        sx={{ cursor: 'pointer', color: '#2563eb', '&:hover': { textDecoration: 'underline' } }}>
                        {d.name}
                      </Box>
                    </TableCell>
                    <TableCell>{p ? <PartName name={p.name} size={12.5} /> : <span style={{ color: 'var(--text-4)' }}>{t('docs.general')}</span>}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>
                      {d.uploader_email}
                      {/* internal = hidden from the factory; editors switch it (owner 2026-10-18) */}
                      {(d.internal || ctx.canEdit) && (
                        <Chip size="small" label={t(d.internal ? 'docs.internal' : 'docs.shared')}
                          title={t('docs.internalTip')}
                          onClick={ctx.canEdit ? () => void productionApi.setDocumentInternal(ctx.die, ctx.config, d.id, !d.internal)
                            .then(() => ctx.reload()).catch((e) => setErr(t('failed', { reason: String(e) }))) : undefined}
                          sx={{ ml: 1, height: 18, fontSize: 10, cursor: ctx.canEdit ? 'pointer' : 'default',
                            bgcolor: d.internal ? '#7f1d1d' : 'transparent', color: d.internal ? '#fff' : 'var(--text-3)',
                            border: d.internal ? 'none' : '1px solid var(--line-soft)' }} />
                      )}
                    </TableCell>
                    <TableCell sx={{ color: 'var(--text-3)' }}>{d.uploaded_at.slice(0, 10)}</TableCell>
                    <TableCell align="right">
                      <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }}
                        onClick={() => void downloadFile(ctx.die, ctx.config, d.file_id, d.name).catch((e) => setErr(t('failed', { reason: String(e) })))}>
                        {t('docs.download')}
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Paper>
      )}
    </Box>
  );
};

export default DocumentsPanel;
