import React from 'react';
import { Button, Avatar, Box, Tooltip, CircularProgress } from '@mui/material';
import LoginIcon from '@mui/icons-material/Login';
import LogoutIcon from '@mui/icons-material/Logout';
import { useAuth } from '../../contexts/AuthContext';

/** Header login/logout control. Renders nothing until Firebase Auth is configured. */
const AuthButton: React.FC = () => {
  const { user, loading, enabled, signIn, logout } = useAuth();

  if (!enabled) return null;
  if (loading) return <CircularProgress size={18} sx={{ color: 'var(--text-2)' }} />;

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
      <Tooltip title={user.email || ''} arrow>
        <Avatar src={user.photoURL || undefined}
          sx={{ width: 26, height: 26, fontSize: '0.8rem', bgcolor: 'var(--line-accent)' }}>
          {(user.displayName || user.email || '?')[0]?.toUpperCase()}
        </Avatar>
      </Tooltip>
      <Button size="small" variant="text"
        startIcon={<LogoutIcon sx={{ fontSize: 15 }} />}
        onClick={() => logout().catch(() => {})}
        sx={{ textTransform: 'none', fontSize: '0.72rem', color: 'var(--text-2)' }}>
        Sign out
      </Button>
    </Box>
  );
};

export default AuthButton;
