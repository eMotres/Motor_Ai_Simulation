/**
 * Certification tab (owner 2026-11-19): the certification package of a motor (analysis, technical file, declarations of conformity,
 * labelling, supplier declarations, test reports) kept apart from the Documents tab.  Staff (owner / engineer / admin) upload, set the
 * free-text group label and the status (draft / signed / evidence received) and may move a document back to Documents; the factory
 * (OEM) is shown the non-internal ones only (the server filters).  A click opens the file in the standard viewer window, like Documents.
 */
import React, { useRef, useState } from 'react';
import { Box, Button, Checkbox, Chip, FormControlLabel, MenuItem, Paper, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { downloadFile, openFileInNewWindow, productionApi } from '../api';
import { CERT_STATUSES, certificationGroups, isCertificationDoc } from '../helpers';
import type { CertStatus, DocumentRec, ProdCtx } from '../types';

const STATUS_COLOR: Record<CertStatus, 'default' | 'success' | 'info'> = { draft: 'default', signed: 'success', evidence_received: 'info' };

const CertificationPanel: React.FC<{ documents: DocumentRec[]; ctx: ProdCtx }> = ({ documents, ctx }) => {
  const { t } = useTranslation('production');
  const fileRef = useRef<HTMLInputElement>(null);
  const [group, setGroup] = useState('');
  const [internal, setInternal] = useState(true);       // certification drafts are staff-only until released
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const groups = certificationGroups(documents);
  const known = [...new Set(documents.filter(isCertificationDoc).map((d) => (d.group || '').trim()).filter(Boolean))].sort();
  const fail = (e: unknown) => setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) }));

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setErr(null);
    try {
      for (const f of Array.from(files)) await productionApi.uploadDocument(ctx.die, ctx.config, f, null, null, { group: group.trim(), internal });
      ctx.reload();
    } catch (e) { fail(e); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ''; }
  };
  const patch = (d: DocumentRec, body: Parameters<typeof productionApi.updateDocument>[3]) =>
    void productionApi.updateDocument(ctx.die, ctx.config, d.id, body).then(() => ctx.reload()).catch(fail);
  const open = (d: DocumentRec) => {
    const ext = (d.name.split('.').pop() || '').toLowerCase();
    const viewable = ['pdf', 'png', 'jpg', 'jpeg'].includes(ext);
    void (viewable ? openFileInNewWindow(ctx.die, ctx.config, d.file_id, ext === 'jpeg' ? 'jpg' : ext)
                   : downloadFile(ctx.die, ctx.config, d.file_id, d.name)).catch(fail);
  };

  return (
    <Box data-testid="certification-panel">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('tabs.certification')}</Typography>
        <HelpTip title={t('cert.tip')} />
      </Box>
      {ctx.canEdit && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
          <TextField size="small" label={t('cert.group')} value={group} onChange={(e) => setGroup(e.target.value)}
            inputProps={{ list: 'cert-groups', maxLength: 60 }} sx={{ minWidth: 200, flex: '1 1 200px', maxWidth: 320 }} />
          <datalist id="cert-groups">{known.map((g) => <option key={g} value={g} />)}</datalist>
          <FormControlLabel sx={{ m: 0 }} control={<Checkbox size="small" checked={internal} onChange={(e) => setInternal(e.target.checked)} />}
            label={<Typography sx={{ fontSize: 12.5 }}>{t('cert.internal')}</Typography>} />
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} />
          <Button size="small" variant="contained" disabled={busy} sx={{ textTransform: 'none' }} onClick={() => fileRef.current?.click()}>{t('docs.upload')}</Button>
          <HelpTip title={t('cert.uploadTip')} />
        </Box>
      )}
      {err && <Typography role="alert" sx={{ fontSize: 11, color: '#f87171', mb: 1 }}>{err}</Typography>}
      {groups.length === 0 ? <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('cert.none')}</Typography> : groups.map((g) => (
        <Box key={g.group || '-'} sx={{ mb: 2 }} data-testid="cert-group">
          <Typography sx={{ fontSize: 12.5, fontWeight: 800, mb: 0.75 }}>{g.group || t('cert.ungrouped')}</Typography>
          <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 }}>
            {g.docs.map((d, i) => (
              <Box key={d.id} data-testid="cert-doc" sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', p: 1,
                borderTop: i ? '1px solid var(--panel)' : 'none' }}>
                <Box sx={{ flex: '1 1 240px', minWidth: 0 }}>
                  <Box component="span" title={t('parts.openFull', 'Open full screen')} onClick={() => open(d)}
                    sx={{ cursor: 'pointer', color: '#2563eb', fontSize: 12.5, overflowWrap: 'anywhere', '&:hover': { textDecoration: 'underline' } }}>{d.name}</Box>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{d.uploaded_at.slice(0, 10)} · {d.uploader_email}</Typography>
                </Box>
                {ctx.canEdit ? (
                  <TextField select size="small" value={d.cert_status || 'draft'} label={t('cert.status')} sx={{ minWidth: 150 }}
                    onChange={(e) => patch(d, { cert_status: e.target.value as CertStatus })}>
                    {CERT_STATUSES.map((s) => <MenuItem key={s} value={s}>{t(`cert.statuses.${s}`)}</MenuItem>)}
                  </TextField>
                ) : (
                  <Chip size="small" color={STATUS_COLOR[d.cert_status || 'draft']} label={t(`cert.statuses.${d.cert_status || 'draft'}`)} sx={{ height: 20, fontSize: 10.5 }} />
                )}
                {(d.internal || ctx.canEdit) && (
                  <Chip size="small" label={t(d.internal ? 'docs.internal' : 'docs.shared')} title={t('docs.internalTip')}
                    onClick={ctx.canEdit ? () => patch(d, { internal: !d.internal }) : undefined}
                    sx={{ height: 18, fontSize: 10, cursor: ctx.canEdit ? 'pointer' : 'default',
                      bgcolor: d.internal ? '#7f1d1d' : 'transparent', color: d.internal ? '#fff' : 'var(--text-3)',
                      border: d.internal ? 'none' : '1px solid var(--line-soft)' }} />
                )}
                <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }} onClick={() => void downloadFile(ctx.die, ctx.config, d.file_id, d.name).catch(fail)}>{t('docs.download')}</Button>
                {ctx.canEdit && <Button size="small" sx={{ textTransform: 'none', fontSize: 11 }} title={t('cert.moveTip')} onClick={() => patch(d, { section: null })}>{t('cert.move')}</Button>}
              </Box>
            ))}
          </Paper>
        </Box>
      ))}
    </Box>
  );
};

export default CertificationPanel;
