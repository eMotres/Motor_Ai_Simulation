/**
 * AdminPanel — shell: left sub-navigation (compact top tabs on narrow
 * screens) + the active section. Each section is its own self-contained
 * panel (fetches its own data, like every other admin panel already did) —
 * this file only owns the section choice (remembered in localStorage) and
 * the Invite dialog, which two sections (Users, Sign-ups) both trigger.
 */
import React, { useState } from 'react';
import { Box, Typography } from '@mui/material';
import AdminNav, { useAdminSection } from './AdminNav';
import InviteDialog from './dialogs/InviteDialog';
import OverviewSection from './sections/OverviewSection';
import UsersSection from './sections/UsersSection';
import SignupsSection from './sections/SignupsSection';
import ServersSection from './sections/ServersSection';
import UsageSection from './sections/UsageSection';
import AgentsSection from './sections/AgentsSection';
import CatalogsSection from './sections/CatalogsSection';
import MotorsAccessSection from './sections/MotorsAccessSection';
import LogsSection from './sections/LogsSection';
import NewsletterSection from './NewsletterSection';

const AdminPanel: React.FC = () => {
  const [section, setSection] = useAdminSection();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invitePrefill, setInvitePrefill] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const openInvite = (email?: string) => { setInvitePrefill(email ?? ''); setInviteOpen(true); };

  const renderSection = () => {
    switch (section) {
      case 'overview': return <OverviewSection onGoto={setSection} />;
      case 'users': return <UsersSection onInvite={openInvite} notice={notice} setNotice={setNotice} />;
      case 'signups': return <SignupsSection onInvite={openInvite} reload={() => setReloadKey((k) => k + 1)} key={reloadKey} />;
      case 'servers': return <ServersSection />;
      case 'usage': return <UsageSection />;
      case 'agents': return <AgentsSection />;
      case 'catalogs': return <CatalogsSection />;
      case 'motorsAccess': return <MotorsAccessSection />;
      case 'newsletter': return <NewsletterSection />;
      case 'logs': return <LogsSection />;
      default: return null;
    }
  };

  return (
    <Box sx={{ height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ px: 2, pt: 2, pb: 0 }}>
        <Typography sx={{ fontSize: 18, fontWeight: 800, color: 'var(--text-0)' }}>Admin</Typography>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: { xs: 'column', md: 'row' }, p: 2, gap: 2, overflow: 'hidden' }}>
        <AdminNav section={section} onSelect={setSection} />
        <Box sx={{ flex: 1, minWidth: 0, overflowY: 'auto', pr: 0.5 }}>
          {renderSection()}
        </Box>
      </Box>

      <InviteDialog open={inviteOpen} email={invitePrefill} onClose={() => setInviteOpen(false)}
        onInvited={(m) => { setNotice(m); setInviteOpen(false); }} />
    </Box>
  );
};

export default AdminPanel;
