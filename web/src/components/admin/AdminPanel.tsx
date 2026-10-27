/**
 * AdminPanel — shell: left sub-navigation (compact top tabs on narrow
 * screens) + the active section. Each section is its own self-contained
 * panel (fetches its own data, like every other admin panel already did) —
 * this file only owns the section choice (remembered in localStorage) and
 * the Invite dialog, which two sections (Users, Sign-ups) both trigger.
 */
import React, { useState } from 'react';
import { Box, Typography } from '@mui/material';
import AdminNav, { ADMIN_SECTIONS, useAdminSection } from './AdminNav';
import type { AdminSectionId } from './AdminNav';
import { parseAdminLink } from '../../lib/adminLink';
import InviteDialog from './dialogs/InviteDialog';
import OverviewSection from './sections/OverviewSection';
import UsersSection from './sections/UsersSection';
import SignupsSection from './sections/SignupsSection';
import ServersSection from './sections/ServersSection';
import UsageSection from './sections/UsageSection';
import AgentsSection from './sections/AgentsSection';
import AgentActivitySection from './sections/AgentActivitySection';
import CatalogsSection from './sections/CatalogsSection';
import MotorsAccessSection from './sections/MotorsAccessSection';
import ProductionAccessSection from './sections/ProductionAccessSection';
import ProjectsSection from './sections/ProjectsSection';
import DigestsSection from './sections/DigestsSection';
import LogsSection from './sections/LogsSection';
import Downloads3dSection from './sections/Downloads3dSection';
import VerificationSection from './sections/VerificationSection';
import AnalyticsSection from './sections/AnalyticsSection';
import ActivitySection from './sections/ActivitySection';
import DirectorySection from './sections/DirectorySection';
import MailingCard from './MailingCard';
import NoticesPanel from './NoticesPanel';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';

// UI strings: locales/<lng>/admin.json (docs/I18N.md).
const tx = nsT('admin');
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const AdminPanel: React.FC = () => {
  useTranslation('admin'); // re-render on language change; lazy-loads the namespace
  const [section, setSection] = useAdminSection();
  // /?tab=admin&section=digests&draft=... (the owner's daily report): open that section once.
  React.useEffect(() => {
    const link = parseAdminLink(window.location.search);
    if (link && ADMIN_SECTIONS.some((s) => s.id === link.section)) setSection(link.section as AdminSectionId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invitePrefill, setInvitePrefill] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [waitingCount, setWaitingCount] = useState<number>(0);
  const [directoryWaitingCount, setDirectoryWaitingCount] = useState<number>(0);

  const fetchWaiting = React.useCallback(async () => {
    try {
      const [vRes, dRes] = await Promise.all([
        fetch(`${API}/api/admin/verification/waiting`),
        fetch(`${API}/api/admin/directory/waiting`),
      ]);
      if (vRes.ok) {
        const j = (await vRes.json()) as { count?: number };
        if (typeof j.count === 'number') {
          setWaitingCount(j.count);
        }
      }
      if (dRes.ok) {
        const j = (await dRes.json()) as { count?: number };
        if (typeof j.count === 'number') {
          setDirectoryWaitingCount(j.count);
        }
      }
    } catch { /* ignore network / auth errors */ }
  }, []);

  React.useEffect(() => {
    void fetchWaiting();
    const interval = setInterval(() => { void fetchWaiting(); }, 60_000);
    return () => clearInterval(interval);
  }, [fetchWaiting]);

  React.useEffect(() => {
    void fetchWaiting();
  }, [section, fetchWaiting]);

  const openInvite = (email?: string) => { setInvitePrefill(email ?? ''); setInviteOpen(true); };

  const renderSection = () => {
    switch (section) {
      case 'overview': return <OverviewSection onGoto={setSection} />;
      case 'users': return <UsersSection onInvite={openInvite} notice={notice} setNotice={setNotice} />;
      case 'directory': return <DirectorySection />;
      case 'signups': return <SignupsSection onInvite={openInvite} reload={() => setReloadKey((k) => k + 1)} key={reloadKey} />;
      case 'servers': return <ServersSection />;
      case 'usage': return <UsageSection />;
      case 'agents': return <AgentsSection />;
      case 'agentActivity': return <AgentActivitySection />;
      case 'catalogs': return <CatalogsSection />;
      case 'motorsAccess': return <MotorsAccessSection />;
      case 'productionAccess': return <ProductionAccessSection />;
      case 'projects': return <ProjectsSection />;
      case 'digests': return <DigestsSection />;
      case 'mailing': return <><MailingCard /><NoticesPanel /></>;
      case 'logs': return <LogsSection />;
      case 'downloads3d': return <Downloads3dSection />;
      case 'verification': return <VerificationSection />;
      case 'analytics': return <AnalyticsSection />;
      case 'activity': return <ActivitySection />;
      default: return null;
    }
  };

  return (
    <Box sx={{ height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ px: 2, pt: 2, pb: 0 }}>
        <Typography sx={{ fontSize: 18, fontWeight: 800, color: 'var(--text-0)' }}>{tx('admin')}</Typography>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: { xs: 'column', md: 'row' }, p: 2, gap: 2, overflow: 'hidden' }}>
        <AdminNav
          section={section}
          onSelect={setSection}
          badges={{
            ...(waitingCount > 0 ? { verification: waitingCount } : {}),
            ...(directoryWaitingCount > 0 ? { directory: directoryWaitingCount } : {}),
          }}
        />
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
