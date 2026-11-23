// The archived configurations of the catalog (owner 2026-11-22), for admins: a "Show archived" switch and, when it is on, the
// Archived group with Open and Restore.  An archived configuration has left every listing; nothing of it was deleted.
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Button, FormControlLabel, Switch, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { configArchiveApi, type ArchivedConfig } from '../../lib/configArchiveApi';

const ArchivedConfigs: React.FC<{ busy: boolean; onOpen: (c: ArchivedConfig) => void; onRestore: (c: ArchivedConfig) => void;
  /** for the tests: the list given instead of fetched */ given?: ArchivedConfig[] }> = ({ busy, onOpen, onRestore, given }) => {
  const { t } = useTranslation('motors');
  const [rows, setRows] = useState<ArchivedConfig[]>(given ?? []);
  const [show, setShow] = useState(false);
  const load = useCallback(async () => {
    if (given) return;
    try { setRows((await configArchiveApi.list()).configs); } catch { setRows([]); }
  }, [given]);
  useEffect(() => {
    void load();
    const again = () => { void load(); };
    window.addEventListener('family-changed', again);
    return () => window.removeEventListener('family-changed', again);
  }, [load]);
  if (!rows.length) return null;
  return (
    <Box data-testid="archived-configs" sx={{ mt: 1 }}>
      <FormControlLabel control={<Switch size="small" checked={show} onChange={(e) => setShow(e.target.checked)} />}
        label={<Typography sx={{ fontSize: 12 }}>{t('configArchive.show', { n: rows.length })}</Typography>} />
      {show && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, pl: 1, borderLeft: '2px dotted var(--line)' }}>
          {rows.map((c) => (
            <Box key={`${c.die}/${c.config}`} data-testid="archived-config" sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', opacity: 0.75 }}>
              <Tooltip title={[c.by, c.at, c.reason].filter(Boolean).join(' · ')}>
                <Typography sx={{ fontSize: 12.5, color: 'var(--text-1)' }}>{c.die} / {c.config}</Typography>
              </Tooltip>
              {c.reason && <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{c.reason}</Typography>}
              {c.duty && <Button size="small" disabled={busy} onClick={() => onOpen(c)} sx={{ textTransform: 'none', fontSize: 12, minWidth: 0 }}>{t('configArchive.open')}</Button>}
              <Button size="small" disabled={busy} onClick={() => onRestore(c)} sx={{ textTransform: 'none', fontSize: 12, minWidth: 0 }}>{t('configArchive.restore')}</Button>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};

export default ArchivedConfigs;
