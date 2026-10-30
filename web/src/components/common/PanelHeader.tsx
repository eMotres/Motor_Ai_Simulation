/**
 * PanelHeader - the ONE header row of every top tab (owner 2026-10-30): main menu, then the context strip, then this
 * row: the panel's label, its sub-tabs (when the panel has sections) and its actions.  App draws the row for every
 * tab; a panel that has sections or actions hands them to it with <HeaderTabs> / <HeaderRight> (a portal into the
 * row), so the state stays in the panel and the row looks the same everywhere.
 *
 * Both portals render only while their panel's tab is the open one: a keep-mounted panel that is hidden must not
 * draw its tabs into another tab's header.
 */
import React, { createContext, useContext, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Box, Typography } from '@mui/material';
import { useUIStore } from '../../stores/uiStore';
import { SUB_TAB_HEIGHT } from './SubTabs';

interface Slots {
  tabs: HTMLElement | null;
  right: HTMLElement | null;
  setTabs: (el: HTMLElement | null) => void;
  setRight: (el: HTMLElement | null) => void;
}

const noop = () => undefined;
const SlotsCtx = createContext<Slots>({ tabs: null, right: null, setTabs: noop, setRight: noop });

export const PanelHeaderProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [tabs, setTabs] = useState<HTMLElement | null>(null);
  const [right, setRight] = useState<HTMLElement | null>(null);
  const value = useMemo(() => ({ tabs, right, setTabs, setRight }), [tabs, right]);
  return <SlotsCtx.Provider value={value}>{children}</SlotsCtx.Provider>;
};

interface HeaderProps {
  label: string;
  /** Sub-tabs the shell itself owns (the Simulations members); a panel's own tabs come in through <HeaderTabs>. */
  tabs?: React.ReactNode;
  testId?: string;
}

export const PanelHeader: React.FC<HeaderProps> = ({ label, tabs, testId }) => {
  const { setTabs, setRight } = useContext(SlotsCtx);
  return (
    <Box data-testid={testId ?? 'panel-header'} sx={{
      display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', px: 3, py: 0.25, minHeight: SUB_TAB_HEIGHT + 2,
      bgcolor: 'background.paper', borderBottom: '1px solid', borderColor: 'divider', flexShrink: 0,
    }}>
      <Typography variant="subtitle2" sx={{ whiteSpace: 'nowrap' }}>{label}</Typography>
      {/* phone width: the tabs take their own full-width line under label + actions */}
      <Box ref={setTabs} sx={{ order: { xs: 3, sm: 0 }, flex: { xs: '1 1 100%', sm: '0 1 auto' }, minWidth: 0, '&:empty': { display: 'none' } }}>
        {tabs}
      </Box>
      <Box sx={{ flex: 1, order: { xs: 1, sm: 0 } }} />
      <Box ref={setRight} sx={{ display: 'flex', alignItems: 'center', gap: 1, order: { xs: 2, sm: 0 }, '&:empty': { display: 'none' } }} />
    </Box>
  );
};

function useOpen(tab: string | readonly string[]): boolean {
  const active = useUIStore((s) => s.activeTab) as string;
  return typeof tab === 'string' ? active === tab : tab.includes(active);
}

/** The panel's sub-tabs (a <SubTabs>), drawn in the header row while `tab` is open. */
export const HeaderTabs: React.FC<{ tab: string | readonly string[]; children: React.ReactNode }> = ({ tab, children }) => {
  const { tabs } = useContext(SlotsCtx);
  const open = useOpen(tab);
  return tabs && open ? createPortal(children, tabs) : null;
};

/** The panel's header actions / info, drawn at the right end of the header row while `tab` is open. */
export const HeaderRight: React.FC<{ tab: string | readonly string[]; children: React.ReactNode }> = ({ tab, children }) => {
  const { right } = useContext(SlotsCtx);
  const open = useOpen(tab);
  return right && open ? createPortal(children, right) : null;
};
