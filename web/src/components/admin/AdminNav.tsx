// Admin · left sub-navigation — compact top tabs on narrow screens. Remembers
// the last section the owner looked at (localStorage; a private browser or a
// blocked store just falls back to Overview, never a broken panel).
import React, { useEffect, useState } from 'react';
import { Box, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

export type AdminSectionId =
  | 'overview' | 'users' | 'signups' | 'servers' | 'usage' | 'agents' | 'agentActivity' | 'catalogs' | 'motorsAccess' | 'newsletter' | 'logs';

export interface AdminSectionDef { id: AdminSectionId; label: string; badge?: number }

export const ADMIN_SECTIONS: { id: AdminSectionId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'users', label: 'Users' },
  { id: 'signups', label: 'Sign-ups' },
  { id: 'servers', label: 'Servers' },
  { id: 'usage', label: 'Usage & pricing' },
  { id: 'agents', label: 'Agents' },
  { id: 'agentActivity', label: 'Agent activity' },
  { id: 'catalogs', label: 'Catalogs' },
  { id: 'motorsAccess', label: 'Motors access' },
  { id: 'newsletter', label: 'Newsletter & notices' },
  { id: 'logs', label: 'Logs / Events' },
];

const LS_KEY = 'admin.section';

export const useAdminSection = (): [AdminSectionId, (s: AdminSectionId) => void] => {
  const [section, setSectionState] = useState<AdminSectionId>('overview');

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(LS_KEY);
      if (saved && ADMIN_SECTIONS.some((s) => s.id === saved)) setSectionState(saved as AdminSectionId);
    } catch { /* private mode / blocked storage — stay on Overview */ }
  }, []);

  const setSection = (s: AdminSectionId) => {
    setSectionState(s);
    try { window.localStorage.setItem(LS_KEY, s); } catch { /* not persisted this time */ }
  };

  return [section, setSection];
};

/** Left rail ≥900 px, horizontal scroll-tabs below that — one component, CSS
 *  decides the layout so there is nothing to keep in sync. */
const AdminNav: React.FC<{
  section: AdminSectionId; onSelect: (s: AdminSectionId) => void; badges?: Partial<Record<AdminSectionId, number>>;
}> = ({ section, onSelect, badges }) => {
  const { t } = useTranslation('admin');
  return (
  <Box
    sx={{
      display: 'flex',
      flexDirection: { xs: 'row', md: 'column' },
      overflowX: { xs: 'auto', md: 'visible' },
      gap: 0.5,
      flexShrink: 0,
      width: { xs: '100%', md: 168 },
      borderRight: { md: '1px solid var(--line-soft)' },
      borderBottom: { xs: '1px solid var(--line-soft)', md: 'none' },
      pr: { md: 1.5 },
      pb: { xs: 0.75, md: 0 },
      mb: { xs: 1.5, md: 0 },
    }}
  >
    {ADMIN_SECTIONS.map((s) => {
      const active = s.id === section;
      const badge = badges?.[s.id];
      return (
        <Box
          key={s.id}
          onClick={() => onSelect(s.id)}
          sx={{
            display: 'flex', alignItems: 'center', gap: 0.75,
            px: 1.25, py: 0.75, borderRadius: 1, cursor: 'pointer',
            whiteSpace: 'nowrap', flexShrink: 0,
            bgcolor: active ? 'var(--panel-2)' : 'transparent',
            border: active ? '1px solid var(--line-soft)' : '1px solid transparent',
            '&:hover': { bgcolor: 'var(--panel-2)' },
          }}
        >
          <Typography sx={{ fontSize: 12.5, fontWeight: active ? 700 : 500, color: active ? 'var(--text-0)' : 'var(--text-2)' }}>
            {t(`nav.${s.id}`, { defaultValue: s.label })}
          </Typography>
          {!!badge && (
            <Box sx={{
              fontSize: 10, fontWeight: 700, color: '#fbbf24', bgcolor: 'var(--panel)',
              borderRadius: 4, px: 0.6, lineHeight: '15px', minWidth: 15, textAlign: 'center',
            }}>
              {badge}
            </Box>
          )}
        </Box>
      );
    })}
  </Box>
  );
};

export default AdminNav;
