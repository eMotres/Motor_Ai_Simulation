// Admin · sub-navigation — sub-tabs in the panel header row. Remembers
// the last section the owner looked at (localStorage; a private browser or a
// blocked store just falls back to Overview, never a broken panel).
import React from 'react';
import { Box } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { oneOf } from '../../lib/viewMemory';
import { useRememberedView } from '../../lib/useRememberedView';
import SubTabs from '../common/SubTabs';
import { HeaderTabs } from '../common/PanelHeader';

export type AdminSectionId =
  | 'overview' | 'users' | 'directory' | 'signups' | 'servers' | 'usage' | 'agents' | 'agentActivity' | 'catalogs' | 'motorsAccess' | 'productionAccess' | 'projects' | 'digests' | 'mailing' | 'logs' | 'downloads3d' | 'verification' | 'analytics' | 'activity';

export interface AdminSectionDef { id: AdminSectionId; label: string; badge?: number }

export const ADMIN_SECTIONS: { id: AdminSectionId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'users', label: 'Users' },
  { id: 'directory', label: 'Companies & people' },
  { id: 'signups', label: 'Sign-ups' },
  { id: 'servers', label: 'Servers' },
  { id: 'usage', label: 'Usage & pricing' },
  { id: 'agents', label: 'Agents' },
  { id: 'agentActivity', label: 'Agent activity' },
  { id: 'catalogs', label: 'Catalogs' },
  { id: 'motorsAccess', label: 'Motors access' },
  { id: 'productionAccess', label: 'Production access' },
  { id: 'projects', label: 'Projects' },
  { id: 'digests', label: 'Digests' },
  { id: 'mailing', label: 'Mailing & notices' },
  { id: 'logs', label: 'Logs / Events' },
  { id: 'downloads3d', label: '3D downloads' },
  { id: 'verification', label: 'Verification' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'activity', label: 'Activity' },
];

/** The section the admin looked at last is remembered across reloads (lib/viewMemory.ts, scope `admin.section`). */
export const useAdminSection = (): [AdminSectionId, (s: AdminSectionId) => void] =>
  useRememberedView<AdminSectionId>('admin.section', 'overview', oneOf(ADMIN_SECTIONS.map((s) => s.id)));

/** The sections as sub-tabs in the shared panel header row (owner 2026-10-30: one menu style on every tab; it was a left
 *  rail).  Scrolls sideways when the row is too narrow. */
const AdminNav: React.FC<{
  section: AdminSectionId; onSelect: (s: AdminSectionId) => void; badges?: Partial<Record<AdminSectionId, number>>;
}> = ({ section, onSelect, badges }) => {
  const { t } = useTranslation('admin');
  return (
    <HeaderTabs tab="admin">
      <SubTabs value={section} onChange={onSelect} ariaLabel={t('admin', { defaultValue: 'Admin' })} testId="admin-sections"
        items={ADMIN_SECTIONS.map((s) => {
          const badge = badges?.[s.id];
          const urgent = s.id === 'verification' || s.id === 'directory';
          const name = t(`nav.${s.id}`, { defaultValue: s.label });
          return {
            id: s.id,
            label: badge ? (
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.6 }}>
                {name}
                <Box component="span" sx={{
                  fontSize: 10, fontWeight: 700, color: urgent ? '#ffffff' : '#fbbf24', bgcolor: urgent ? '#ef4444' : 'var(--panel)',
                  borderRadius: 4, px: 0.6, lineHeight: '15px', minWidth: 15, textAlign: 'center',
                }}>{badge}</Box>
              </Box>
            ) : name,
          };
        })} />
    </HeaderTabs>
  );
};

export default AdminNav;
