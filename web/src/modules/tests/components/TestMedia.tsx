// The pictures or the videos of one test, each kind on its own line of the card (owner 2026-11-05): a button per file,
// the file opens under them.  The file API needs the session token, which <img src> and <video src> cannot send, so the
// file is fetched first and shown from memory.
import React, { useEffect, useRef, useState } from 'react';
import { Box, Button, IconButton, Tooltip, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import DownloadIcon from '@mui/icons-material/Download';
import ImageOutlinedIcon from '@mui/icons-material/ImageOutlined';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { useTranslation } from 'react-i18next';

import { mediaMime } from '../tableModel';

export interface TestFile { id: string; file_id: string; name: string }

const TestMedia: React.FC<{
  kind: 'video' | 'picture';
  files: TestFile[];
  load: (fileId: string, onProgress: (fraction: number) => void) => Promise<Blob>;
  onDownload?: (fileId: string, name: string) => void;
}> = ({ kind, files, load, onDownload }) => {
  const { t } = useTranslation('production');
  const [open, setOpen] = useState<TestFile | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [pct, setPct] = useState(0);
  const [failed, setFailed] = useState(false);
  const urlRef = useRef<string | null>(null);

  const release = () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
    setUrl(null);
  };

  useEffect(() => {
    if (!open) return undefined;
    let stale = false;
    setPct(0); setFailed(false);
    load(open.file_id, (f) => { if (!stale) setPct(Math.round(f * 100)); }).then((blob) => {
      if (stale) return;
      const u = URL.createObjectURL(new Blob([blob], { type: mediaMime(open.name) }));
      urlRef.current = u;
      setUrl(u);
    }).catch(() => { if (!stale) setFailed(true); });
    return () => { stale = true; release(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when another file is opened
  }, [open?.file_id]);

  const small = { fontSize: 11, py: 0, minHeight: 22, textTransform: 'none' } as const;
  const icon = { fontSize: '15px !important' } as const;
  const shown = { display: 'block', mt: 0.5, maxWidth: 720, maxHeight: 420, borderRadius: 1 } as const;
  return (
    <Box sx={{ mb: 0.6 }} data-testid={`test-media-${kind}`}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
        {files.map((f, i) => (
          <Tooltip key={f.id} title={f.name}>
            <Button size="small" variant={open?.id === f.id ? 'contained' : 'outlined'}
              startIcon={kind === 'video' ? <PlayArrowIcon sx={icon} /> : <ImageOutlinedIcon sx={icon} />}
              sx={small} onClick={() => setOpen(open?.id === f.id ? null : f)}>{t(`tests.media.${kind}`, { n: i + 1 })}</Button>
          </Tooltip>
        ))}
        {open && onDownload && (
          <Tooltip title={t('tests.media.download')}>
            <IconButton size="small" onClick={() => onDownload(open.file_id, open.name)}><DownloadIcon sx={{ fontSize: 16 }} /></IconButton>
          </Tooltip>
        )}
        {open && (
          <Tooltip title={t('tests.media.close')}>
            <IconButton size="small" onClick={() => setOpen(null)}><CloseIcon sx={{ fontSize: 16 }} /></IconButton>
          </Tooltip>
        )}
        {open && !url && !failed && <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{t('tests.media.loading', { pct })}</Typography>}
        {open && failed && <Typography sx={{ fontSize: 11, color: 'var(--danger, #c62828)' }}>{t('tests.media.failed')}</Typography>}
      </Box>
      {open && url && kind === 'video' && (
        <Box component="video" key={url} src={url} controls autoPlay muted playsInline onError={() => setFailed(true)}
          sx={{ ...shown, width: '100%', bgcolor: '#000' }} />
      )}
      {open && url && kind === 'picture' && (
        <Tooltip title={t('tests.media.fullSize')}>
          <a href={url} target="_blank" rel="noreferrer">
            <Box component="img" key={url} src={url} alt={open.name} onError={() => setFailed(true)} sx={shown} />
          </a>
        </Tooltip>
      )}
    </Box>
  );
};

export default TestMedia;
