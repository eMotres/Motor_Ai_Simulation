// Admin · Catalogs: which application each configuration is built for, and which have none yet
// (owner 2026-10-13).  Read-only: the list is edited in the configuration file / by
// scripts/set_applications.py.  A configuration without the field behaves as before.
import React, { useEffect, useState } from 'react';
import { Box, Typography, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { fetchFamilyTree } from '../../lib/familyTree';
import { applicationAudit, type AppDie, type AuditRow } from '../../lib/applications';

const tx = nsT('motors');

const ApplicationsPanel: React.FC = () => {
  useTranslation('motors');
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  useEffect(() => {
    let dead = false;
    fetchFamilyTree({ fresh: true })
      .then((t) => { if (!dead) setRows(applicationAudit((t.dies || []) as unknown as AppDie[])); })
      .catch(() => { if (!dead) setRows([]); });
    return () => { dead = true; };
  }, []);
  if (rows == null) return null;
  const bad = rows.filter((r) => r.state !== 'set');
  return (
    <Box sx={{ mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 0.5 }}>
        <Tooltip title={tx('applications.adminTip')}>
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-1)', cursor: 'help' }}>
            {tx('applications.adminTitle')}
          </Typography>
        </Tooltip>
        <Typography sx={{ fontSize: 11, color: bad.length ? '#f59e0b' : 'var(--text-4)' }}>
          {bad.length ? tx('applications.adminCount', { n: bad.length }) : tx('applications.adminAllSet')}
        </Typography>
      </Box>
      {rows.map((r) => (
        <Box key={`${r.die}/${r.config}`} sx={{ display: 'flex', gap: 1, fontSize: 11, py: 0.1 }}>
          <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-2)', minWidth: 260 }}>
            {r.die} / {r.config}
          </Typography>
          {r.state === 'set' ? (
            <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-3)' }}>
              {r.applications.map((a) => tx(`applications.${a}`)).join(', ')}
            </Typography>
          ) : (
            <Tooltip title={r.state === 'invalid' ? tx('applications.invalidTip', { error: r.error ?? '' })
                                                   : tx('applications.notSetTip')}>
              <Typography component="span" sx={{ fontSize: 11, cursor: 'help',
                color: r.state === 'invalid' ? '#f87171' : '#f59e0b' }}>
                {tx('applications.notSet')}
              </Typography>
            </Tooltip>
          )}
        </Box>
      ))}
    </Box>
  );
};

export default ApplicationsPanel;
