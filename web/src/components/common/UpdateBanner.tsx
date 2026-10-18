// Soft updates (2026-10-18): a deploy never reloads anybody's page.  When the served build changes, a small bar offers
// "A new version is available - Reload"; when the server announces a short API restart (/api/notice), a bar says so
// beforehand.  /build.json is written into the web image at build time (deploy/Dockerfile.web, BUILD_ID).
import React, { useEffect, useRef, useState } from 'react';
import { Box, Button, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const visible = () => typeof document === 'undefined' || document.visibilityState === 'visible';

const UpdateBanner: React.FC = () => {
  const { t, i18n } = useTranslation('common');
  const loaded = useRef<string | null>(null);
  const [newBuild, setNewBuild] = useState(false);
  const [notice, setNotice] = useState<{ en?: string; zh?: string } | null>(null);

  useEffect(() => {
    let alive = true;
    const readBuild = () => {
      if (!visible()) return;
      fetch(`/build.json?t=${Date.now()}`, { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => {
          const b = j && typeof j.build === 'string' ? j.build : null;
          if (!alive || !b) return;
          if (loaded.current === null) loaded.current = b;
          else if (b !== loaded.current) setNewBuild(true);
        })
        .catch(() => { /* local dev or offline: no bar */ });
    };
    const readNotice = () => {
      if (!visible()) return;
      fetch(`${API}/api/notice`, { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => { if (alive) setNotice(j?.notice?.message ?? null); })
        .catch(() => { /* the restart itself: keep the last answer */ });
    };
    readBuild(); readNotice();
    const a = setInterval(readBuild, 120000);
    const b = setInterval(readNotice, 30000);
    const onShow = () => { readBuild(); readNotice(); };
    document.addEventListener('visibilitychange', onShow);
    return () => { alive = false; clearInterval(a); clearInterval(b); document.removeEventListener('visibilitychange', onShow); };
  }, []);

  const zh = (i18n.resolvedLanguage ?? 'en').startsWith('zh');
  const noticeText = notice ? ((zh ? notice.zh : notice.en) || notice.en || '') : '';
  if (!newBuild && !noticeText) return null;
  return (
    <Box sx={{ position: 'fixed', bottom: 16, left: '50%', transform: 'translateX(-50%)', zIndex: 2000,
      display: 'flex', flexDirection: 'column', gap: 1, maxWidth: 'calc(100vw - 32px)' }}>
      {noticeText && (
        <Box sx={{ bgcolor: '#1e3a8a', color: '#fff', px: 2, py: 1, borderRadius: 1.5, boxShadow: 3 }}>
          <Typography sx={{ fontSize: 13 }}>{noticeText}</Typography>
        </Box>
      )}
      {newBuild && (
        <Box sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line-soft)', px: 2, py: 0.75, borderRadius: 1.5,
          boxShadow: 3, display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Typography sx={{ fontSize: 13 }}>{t('update.newVersion')}</Typography>
          <Button size="small" variant="contained" onClick={() => window.location.reload()} sx={{ textTransform: 'none' }}>
            {t('update.reload')}
          </Button>
        </Box>
      )}
    </Box>
  );
};

export default UpdateBanner;
