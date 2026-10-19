// The Fusion version guard (docs/FUSION_SYNC.md section 5.3) in the 3-D and Production tabs: one short line + a tooltip.
// Only admins get an answer from /api/fusion/sync (403 for everybody else, and for dies that are not synced): then nothing shows.
import React, { useEffect, useState } from 'react';
import { Alert, Chip, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

export interface FusionGuard {
  latest_export?: { version: number; document?: string; exported_at?: string } | null;
  model_built_from?: { version: number; document?: string } | null;
  warnings?: string[];
}

export const guardUrl = (die: string, config: string): string =>
  `${API}/api/fusion/sync/${encodeURIComponent(die)}/${encodeURIComponent(config)}/status`;

const FusionSyncBadge: React.FC<{ die?: string | null; config?: string | null; compact?: boolean }> = ({ die, config, compact }) => {
  const { t } = useTranslation('model3d');
  const [g, setG] = useState<FusionGuard | null>(null);

  useEffect(() => {
    setG(null);
    if (!die || !config) return undefined;
    let live = true;
    (async () => {
      try {
        const r = await fetch(guardUrl(die, config));
        if (!r.ok) return;                                  // not an admin, or the die is not synced: show nothing
        const j = (await r.json()) as FusionGuard;
        if (live && j && j.latest_export) setG(j);
      } catch { /* the guard is advisory: no line on a network error */ }
    })();
    return () => { live = false; };
  }, [die, config]);

  if (!g || !g.latest_export) return null;
  const doc = g.latest_export.document ?? 'Fusion';
  const warnings = g.warnings ?? [];
  if (!warnings.length) {
    return (
      <Tooltip title={t('fusion.okTip', { document: doc, version: g.latest_export.version, at: (g.latest_export.exported_at ?? '').slice(0, 16) })}>
        <Chip size="small" data-testid="fusion-guard-ok" label={t('fusion.ok', { version: g.latest_export.version })}
          sx={{ height: 20, fontSize: 11, bgcolor: 'rgba(16, 185, 129, 0.12)', color: 'var(--text-1)' }} />
      </Tooltip>
    );
  }
  return (
    <Tooltip title={<>{warnings.map((w) => <div key={w}>{w}</div>)}</>}>
      <Alert severity="warning" data-testid="fusion-guard-warning"
        sx={{ py: 0, px: 1, fontSize: 11.5, alignItems: 'center', ...(compact ? { '& .MuiAlert-icon': { fontSize: 16, mr: 0.5 } } : {}) }}>
        {warnings.length > 1 ? t('fusion.warnMore', { first: warnings[0], n: warnings.length - 1 }) : warnings[0]}
      </Alert>
    </Tooltip>
  );
};

export default FusionSyncBadge;
