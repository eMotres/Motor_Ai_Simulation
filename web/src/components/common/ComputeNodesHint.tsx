/**
 * ComputeNodesHint - ONE line on the simulation tabs for the user and engineer roles:
 * FEM runs are heavy, connect your own server (docs/BYO_COMPUTE.md).  The tooltip says
 * what a node does; the two links open "My compute nodes" (the account menu's dialog,
 * via a window event) and the worker install docs.
 */
import React from 'react';
import { Box, Link, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { COMPUTE_WORKER_DOCS_URL, OPEN_COMPUTE_NODES_EVENT } from '../../lib/accessUi';

const ComputeNodesHint: React.FC = () => {
  const { t } = useTranslation('common');
  return (
    <Box role="note" sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.25, minHeight: 24,
      borderBottom: '1px solid var(--line-soft)', flexShrink: 0 }}>
      <Tooltip arrow title={t('nodes.hintTip')}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)', cursor: 'help', overflow: 'hidden',
          textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {t('nodes.hint')}
        </Typography>
      </Tooltip>
      <Link component="button" type="button" underline="hover" sx={{ fontSize: 12, flexShrink: 0 }}
        onClick={() => window.dispatchEvent(new Event(OPEN_COMPUTE_NODES_EVENT))}>
        {t('nodes.hintNodes')}
      </Link>
      <Link href={COMPUTE_WORKER_DOCS_URL} target="_blank" rel="noopener noreferrer" underline="hover"
        sx={{ fontSize: 12, flexShrink: 0 }}>
        {t('nodes.hintDocs')}
      </Link>
    </Box>
  );
};

export default ComputeNodesHint;
