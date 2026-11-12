/**
 * A production file shown IN the page: a PDF in the browser's viewer, a picture as an image, with Full screen and Download
 * (owner 2026-10-18).  Shared by the Assembly step panel and the Tooling tab.  The files need the session header, so they are
 * fetched as typed blobs.
 */
import React, { useEffect, useState } from 'react';
import { Box, Button, Paper, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { downloadFile, fileObjectUrl, openFileInNewWindow } from '../api';
import { extOf, IMAGE_EXT } from '../helpers';
import type { DocumentRec, ProdCtx } from '../types';


/** One drawing / document shown IN the page (owner 2026-10-18: "show the content of these files on the page, so
 *  everything is visible and can be downloaded"): a PDF in the browser's viewer, a picture as an image, with
 *  full-screen and download buttons.  The file needs the session header, so it is fetched as a typed blob. */
export const DocPreview: React.FC<{ ctx: ProdCtx; doc: Pick<DocumentRec, 'file_id' | 'name'>; onError: (e: string) => void }> = ({ ctx, doc, onError }) => {
  const { t } = useTranslation('production');
  const ext = extOf(doc.name);
  const kind = ext === 'pdf' ? 'pdf' : IMAGE_EXT.includes(ext) ? 'image' : null;
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!kind) return undefined;
    let dead = false; let u: string | null = null;
    const type = kind === 'pdf' ? 'application/pdf' : `image/${ext === 'jpg' ? 'jpeg' : ext}`;
    void fileObjectUrl(ctx.die, ctx.config, doc.file_id)
      .then((blobUrl) => fetch(blobUrl).then((r) => r.blob()).then((b) => { URL.revokeObjectURL(blobUrl); return URL.createObjectURL(new Blob([b], { type })); }))
      .then((x) => { if (dead) URL.revokeObjectURL(x); else { u = x; setUrl(x); } })
      .catch((e) => onError(String(e)));
    return () => { dead = true; if (u) URL.revokeObjectURL(u); };
  }, [ctx.die, ctx.config, doc.file_id, kind, ext, onError]);
  const btn = { textTransform: 'none', fontSize: 11.5, py: 0, minHeight: 0 } as const;
  return (
    <Paper variant="outlined" sx={{ bgcolor: 'var(--panel-2)', borderRadius: 1.5, overflow: 'hidden' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.25, py: 0.6, borderBottom: kind ? '1px solid var(--line-soft)' : 'none' }}>
        <Typography sx={{ fontSize: 12.5, fontWeight: 700, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{doc.name}</Typography>
        {kind && <Button size="small" sx={btn} onClick={() => void openFileInNewWindow(ctx.die, ctx.config, doc.file_id, ext === 'jpeg' ? 'jpg' : ext).catch((e) => onError(String(e)))}>
          {t('process.fullScreen', 'Full screen')}</Button>}
        <Button size="small" variant="outlined" sx={btn} onClick={() => void downloadFile(ctx.die, ctx.config, doc.file_id, doc.name).catch((e) => onError(String(e)))}>
          {t('process.downloadFile', 'Download')}</Button>
      </Box>
      {kind === 'pdf' && (url
        ? <Box component="iframe" src={`${url}#view=FitH&navpanes=0&pagemode=none`} title={doc.name} sx={{ display: 'block', width: '100%', height: { xs: 360, md: 560 }, border: 0, bgcolor: '#fff' }} />
        : <Typography sx={{ fontSize: 11, color: 'var(--text-4)', p: 1.5 }}>…</Typography>)}
      {kind === 'image' && url && <Box component="img" src={url} alt={doc.name} sx={{ display: 'block', maxWidth: '100%', maxHeight: 560, mx: 'auto', bgcolor: '#fff' }} />}
    </Paper>
  );
};

/** A small picture of a drawing (the part's PNG) for a collapsed row; a click opens `openId` (the PDF of the same drawing, else the picture) full screen. */
export const FileThumb: React.FC<{ ctx: ProdCtx; file: Pick<DocumentRec, 'file_id' | 'name'>; openId?: string; openKind?: string }> = ({ ctx, file, openId, openKind }) => {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let dead = false; let u: string | null = null;
    void fileObjectUrl(ctx.die, ctx.config, file.file_id).then((x) => { if (dead) URL.revokeObjectURL(x); else { u = x; setUrl(x); } }).catch(() => undefined);
    return () => { dead = true; if (u) URL.revokeObjectURL(u); };
  }, [ctx.die, ctx.config, file.file_id]);
  const ext = extOf(file.name);
  return (
    <Box title={file.name} onClick={() => void openFileInNewWindow(ctx.die, ctx.config, openId ?? file.file_id, openKind ?? (ext === 'jpeg' ? 'jpg' : ext)).catch(() => undefined)}
      sx={{ width: 96, height: 64, flexShrink: 0, borderRadius: 1, overflow: 'hidden', border: '1px solid var(--line-soft)', cursor: 'zoom-in',
            bgcolor: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      {url ? <img src={url} alt={file.name} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
        : <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>…</Typography>}
    </Box>
  );
};
