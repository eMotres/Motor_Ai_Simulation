// Admin · Sign-ups — e-mail accounts awaiting approval, plus visitors who
// asked for access (and the chats behind them).
import React from 'react';
import { Box, Typography } from '@mui/material';
import PendingSignups from '../PendingSignups';
import VisitorRequests from '../VisitorRequests';

const SignupsSection: React.FC<{ onInvite: (email: string) => void; reload: () => void }> = ({ onInvite, reload }) => (
  <Box>
    <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>Sign-ups</Typography>
    <PendingSignups onChanged={reload} />
    <VisitorRequests onInvite={onInvite} />
  </Box>
);

export default SignupsSection;
