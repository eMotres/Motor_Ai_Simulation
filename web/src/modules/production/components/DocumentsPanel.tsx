/** Documents: upload (owner/engineer/admin) and traced download (every member). */
import React, { useRef, useState } from 'react';
import { Box, Button, MenuItem, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText from './BilingualText';
import HelpTip from '../../../components/common/HelpTip';
import { downloadFile, productionApi } from '../api';
import type { DocumentRec, Part, ProdCtx } from '../types';

const DocumentsPanel: React.FC<{ documents: DocumentRec[]; parts: Part[]; ctx: ProdCtx }> = ({ documents, parts, ctx }) => {
  const { t } = useTranslation('production');
  const fileRef = useRef<HTMLInputElement>(null);
  const [partId, setPartId] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const byId = new Map(parts.map((p) => [p.id, p]));

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
      {ctx.canEdit && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
          <TextField select size="small" label={t('docs.forPart')} value={partId} onChange={(e) => setPartId(e.target.value)} sx={{ minWidth: 200 }}>
            <MenuItem value="">{t('docs.general')}</MenuItem>
            {parts.map((p) => <MenuItem key={p.id} value={p.id}>{p.name[p.name.src_lang]}</MenuItem>)}
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
                    <TableCell>{d.name}</TableCell>
                    <TableCell>{p ? <BilingualText value={p.name} size={12.5} /> : <span style={{ color: 'var(--text-4)' }}>{t('docs.general')}</span>}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{d.uploader_email}</TableCell>
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
