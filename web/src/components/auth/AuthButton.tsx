import React from 'react';
import { Button, Avatar, Box, Tooltip, Menu, MenuItem, ListItemIcon, Divider } from '@mui/material';
import LoginIcon from '@mui/icons-material/Login';
import LogoutIcon from '@mui/icons-material/Logout';
import DevicesIcon from '@mui/icons-material/Devices';
import { useAuth } from '../../contexts/AuthContext';
import SessionsDialog from './SessionsDialog';
import AgentKeysDialog from './AgentKeysDialog';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import NotificationsIcon from '@mui/icons-material/NotificationsNone';
import NotificationsDialog from './NotificationsDialog';
import { NoticeBell, NewsletterLinkHandler } from './NoticeBell';

/** Header login/logout control (self-hosted auth — see contexts/AuthContext). */
const AuthButton: React.FC = () => {
  const { user, role, signIn, logout } = useAuth();
  const [anchor, setAnchor] = React.useState<HTMLElement | null>(null);
  const [sessionsOpen, setSessionsOpen] = React.useState(false);
  const [agentsOpen, setAgentsOpen] = React.useState(false);
  const [notifOpen, setNotifOpen] = React.useState(false);
  const [noticeKey, setNoticeKey] = React.useState(0);

  if (!user) {
    return (
      <>
        <NewsletterLinkHandler />
        <Button size="small" variant="outlined"
          startIcon={<LoginIcon sx={{ fontSize: 16 }} />}
          onClick={() => signIn().catch(() => {})}
          sx={{ textTransform: 'none', fontSize: '0.75rem' }}>
          Sign in
        </Button>
      </>
    );
  }

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <NewsletterLinkHandler />
      <NoticeBell onOpen={() => setNotifOpen(true)} refreshKey={noticeKey} />
      <Tooltip title={`${user.email} · ${role} — click for sessions`} arrow>
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
        <MenuItem sx={{ fontSize: 12.5 }}
          onClick={() => { setAnchor(null); setAgentsOpen(true); }}>
          <ListItemIcon><SmartToyIcon sx={{ fontSize: 16 }} /></ListItemIcon>
          Access for agents
        </MenuItem>
        <MenuItem sx={{ fontSize: 12.5 }}
          onClick={() => { setAnchor(null); setNotifOpen(true); }}>
          <ListItemIcon><NotificationsIcon sx={{ fontSize: 16 }} /></ListItemIcon>
          Notifications
        </MenuItem>
      </Menu>
      <NotificationsDialog open={notifOpen}
        onClose={() => { setNotifOpen(false); setNoticeKey((k) => k + 1); }}
        onRead={() => setNoticeKey((k) => k + 1)} />
      <SessionsDialog open={sessionsOpen} onClose={() => setSessionsOpen(false)}
        onSignedOut={() => { void logout(); }} />
      <AgentKeysDialog open={agentsOpen} onClose={() => setAgentsOpen(false)} />
    </Box>
  );
};

export default AuthButton;
