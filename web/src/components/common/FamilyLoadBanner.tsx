/**
 * FamilyLoadBanner - ONE line under the header strip while a motor loads in the background
 * (the tab it was started for is already open), and the reason in red when the load fails.
 * State lives in lib/familyLoad (module store), so it survives the unmount of the panel that
 * started the load.
 */
import React, { useSyncExternalStore } from 'react';
import { Box, CircularProgress, IconButton, Tooltip, Typography } from '@mui/material';
import { nsT } from '../../i18n/nsT';
import {
  dismissFamilyLoad, familyLoadName, getFamilyLoad, subscribeFamilyLoad,
} from '../../lib/familyLoad';

const tx = nsT('motors');

const FamilyLoadBanner: React.FC = () => {
  const s = useSyncExternalStore(subscribeFamilyLoad, getFamilyLoad, getFamilyLoad);
  if (s.status === 'idle') return null;
  const name = familyLoadName(s);
  const loading = s.status === 'loading';
  return (
    <Box role="status" aria-live="polite"
      sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.25, minHeight: 24,
            borderBottom: '1px solid var(--line-soft)',
            bgcolor: loading ? 'rgba(96,165,250,0.10)' : 'rgba(248,113,113,0.12)' }}>
      {loading && <CircularProgress size={12} thickness={5} />}
      <Tooltip title={loading ? tx('loadingInBackgroundTip') : s.message}>
        <Typography sx={{ fontSize: 12, fontWeight: 600, color: loading ? '#60a5fa' : '#fca5a5',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {loading ? tx('loadingMotor', { name }) : `✗ ${name}: ${s.message}`}
        </Typography>
      </Tooltip>
      {!loading && (
        <IconButton size="small" aria-label={tx('dismiss')} onClick={dismissFamilyLoad}
          sx={{ ml: 'auto', p: 0.2, fontSize: 12, color: '#fca5a5' }}>{'×'}</IconButton>
      )}
    </Box>
  );
};

export default FamilyLoadBanner;
