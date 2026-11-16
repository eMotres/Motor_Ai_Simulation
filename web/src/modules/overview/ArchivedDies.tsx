/** The dies that left the catalog listing (owner 2026-11-17): this workspace's archive and the shared dies retired for all
 *  users, each with Restore.  Pure on its props, so it renders server-side in the tests with the real locale files. */
import React from 'react';
import { Box, Chip, Tooltip, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTranslation } from 'react-i18next';
import type { ArchivedDie } from '../../lib/dieArchiveApi';

const ArchivedDies: React.FC<{ dies: ArchivedDie[]; onRestore: (d: ArchivedDie) => void }> = ({ dies, onRestore }) => {
  const { t } = useTranslation('common');
  if (!dies.length) return null;
  return (
    <Box sx={{ mb: 2 }} data-testid="overview-archived-dies">
      <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 0.5 }}>{t('overview.archive.list', { n: dies.length })}</Typography>
      {dies.map((d) => (
        <Tooltip key={`${d.layer}/${d.die}`} title={[d.retired ? t('overview.archive.retiredTip') : t('overview.archive.archivedTip'), d.by, d.at, d.reason].filter(Boolean).join(' · ')}>
          <Chip size="small" variant="outlined" label={d.configs ? `${d.die} · ${d.configs.length}` : d.die} data-testid="overview-archived-die"
            onDelete={() => onRestore(d)} deleteIcon={<RefreshIcon aria-label={t('overview.archive.restore')} />}
            sx={{ m: 0.25, height: 20, fontSize: 11, opacity: 0.55, borderStyle: 'dotted' }} />
        </Tooltip>
      ))}
    </Box>
  );
};

export default ArchivedDies;
