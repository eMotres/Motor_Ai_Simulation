import React from 'react';
import { Button, Avatar, Box, Tooltip, Menu, MenuItem, ListItemIcon, Divider } from '@mui/material';
import LoginIcon from '@mui/icons-material/Login';
import LogoutIcon from '@mui/icons-material/Logout';
import DevicesIcon from '@mui/icons-material/Devices';
import { useAuth } from '../../contexts/AuthContext';
import SessionsDialog from './SessionsDialog';

/** Header login/logout control (self-hosted auth — see contexts/AuthContext). */
const AuthButton: React.FC = () => {
  const { user, tier, signIn, logout } = useAuth();
  const [anchor, setAnchor] = React.useState<HTMLElement | null>(null);
  const [sessionsOpen, setSessionsOpen] = React.useState(false);

  if (!user) {
    return (
      <Button size="small" variant="outlined"
        startIcon={<LoginIcon sx={{ fontSize: 16 }} />}
        onClick={() => signIn().catch(() => {})}
        sx={{ textTransform: 'none', fontSize: '0.75rem' }}>
        Sign in
      </Button>
    );
  }

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <Tooltip title={`${user.email} · ${tier} — click for sessions`} arrow>
        <Avatar src={user.photoURL || undefined}
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ width: 26, height: 26, fontSize: '0.8rem', bgcolor: 'var(--line-accent)', cursor: 'pointer' }}>
          {(user.displayName || user.email || '?')[0]?.toUpperCase()}
        </Avatar>
      </Tooltip>
      <Button size="small" variant="text"
        startIcon={<LogoutIcon sx={{ fontSize: 15 }} />}
        onClick={() => logout().catch(() => {})}
        sx={{ textTransform: 'none', fontSize: '0.72rem', color: 'var(--text-2)' }}>
        Sign out
      </Button>

      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}>
        <MenuItem disabled sx={{ fontSize: 11, opacity: '0.7 !important' }}>{user.email}</MenuItem>
        <Divider />
        <MenuItem sx={{ fontSize: 12.5 }}
          onClick={() => { setAnchor(null); setSessionsOpen(true); }}>
          <ListItemIcon><DevicesIcon sx={{ fontSize: 16 }} /></ListItemIcon>
          Sessions
        </MenuItem>
      </Menu>
      <SessionsDialog open={sessionsOpen} onClose={() => setSessionsOpen(false)}
        onSignedOut={() => { void logout(); }} />
    </Box>
  );
};

export default AuthButton;
