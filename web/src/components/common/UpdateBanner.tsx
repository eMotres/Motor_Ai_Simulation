// Soft updates (2026-10-18): a deploy never reloads anybody's page.  When the served build changes, a small bar offers
// "A new version is available - Reload"; when the server announces a short API restart (/api/notice), a bar says so
// beforehand.  /build.json is written into the web image at build time (deploy/Dockerfile.web, BUILD_ID).
import React, { useEffect, useRef, useState } from 'react';
import { Box, Button, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useUIStore } from '../../stores/motorStore';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const visible = () => typeof document === 'undefined' || document.visibilityState === 'visible';

const UpdateBanner: React.FC = () => {
  const { t, i18n } = useTranslation('common');
  const loaded = useRef<{ build: string; modules: Record<string, string> | null } | null>(null);
  // which modules changed since this page loaded; with no module data (an old build.json) a new build is "core"
  const [changed, setChanged] = useState<ReadonlySet<string>>(new Set());
  const { activeTab } = useUIStore();
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
          const mods = j && typeof j.modules === 'object' && j.modules ? (j.modules as Record<string, string>) : null;
          if (loaded.current === null) { loaded.current = { build: b, modules: mods }; return; }
          if (b === loaded.current.build) return;
          const old = loaded.current.modules;
          const diff = new Set<string>();
          if (!old || !mods) diff.add('core');
          else for (const k of new Set([...Object.keys(old), ...Object.keys(mods)])) if (old[k] !== mods[k]) diff.add(k);
          setChanged(diff);
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

  // a production-only update asks only the Production tab to reload (owner 2026-10-18); core changes ask everybody
  const newBuild = changed.has('core') || (changed.has('production') && (activeTab as string) === 'production');
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
