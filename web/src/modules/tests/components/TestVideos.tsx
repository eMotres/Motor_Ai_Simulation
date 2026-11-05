// The videos of one test, right on its card (owner 2026-11-05): a button per video, the player opens under them.
// The file API needs the session token, which a <video src> cannot send, so the file is fetched first and played from memory.
import React, { useEffect, useRef, useState } from 'react';
import { Box, Button, IconButton, Tooltip, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import DownloadIcon from '@mui/icons-material/Download';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { useTranslation } from 'react-i18next';

import { videoMime } from '../tableModel';

export interface TestVideo { id: string; file_id: string; name: string }

const TestVideos: React.FC<{
  videos: TestVideo[];
  load: (fileId: string, onProgress: (fraction: number) => void) => Promise<Blob>;
  onDownload?: (fileId: string, name: string) => void;
}> = ({ videos, load, onDownload }) => {
  const { t } = useTranslation('production');
  const [open, setOpen] = useState<TestVideo | null>(null);
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
      const u = URL.createObjectURL(new Blob([blob], { type: videoMime(open.name) }));
      urlRef.current = u;
      setUrl(u);
    }).catch(() => { if (!stale) setFailed(true); });
    return () => { stale = true; release(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when another video is opened
  }, [open?.file_id]);

  const small = { fontSize: 11, py: 0, minHeight: 22, textTransform: 'none' } as const;
  return (
    <Box sx={{ mb: 0.6 }} data-testid="test-videos">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
        {videos.map((v, i) => (
          <Tooltip key={v.id} title={v.name}>
            <Button size="small" variant={open?.id === v.id ? 'contained' : 'outlined'} startIcon={<PlayArrowIcon sx={{ fontSize: '15px !important' }} />}
              sx={small} onClick={() => setOpen(open?.id === v.id ? null : v)}>{t('tests.video.label', { n: i + 1 })}</Button>
          </Tooltip>
        ))}
        {open && onDownload && (
          <Tooltip title={t('tests.video.download')}>
            <IconButton size="small" onClick={() => onDownload(open.file_id, open.name)}><DownloadIcon sx={{ fontSize: 16 }} /></IconButton>
          </Tooltip>
        )}
        {open && (
          <Tooltip title={t('tests.video.close')}>
            <IconButton size="small" onClick={() => setOpen(null)}><CloseIcon sx={{ fontSize: 16 }} /></IconButton>
          </Tooltip>
        )}
        {open && !url && !failed && <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{t('tests.video.loading', { pct })}</Typography>}
        {open && failed && <Typography sx={{ fontSize: 11, color: 'var(--danger, #c62828)' }}>{t('tests.video.failed')}</Typography>}
      </Box>
      {open && url && (
        <Box component="video" key={url} src={url} controls autoPlay muted playsInline onError={() => setFailed(true)}
          sx={{ display: 'block', mt: 0.5, width: '100%', maxWidth: 720, maxHeight: 420, bgcolor: '#000', borderRadius: 1 }} />
      )}
    </Box>
  );
};

export default TestVideos;
