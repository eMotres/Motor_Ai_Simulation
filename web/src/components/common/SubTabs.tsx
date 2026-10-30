/**
 * SubTabs - the ONE sub-menu of every panel (owner 2026-10-30: "make all menus one style, like the main menu").
 *
 * It is the main navigation bar's tab (App.tsx: MUI Tabs/Tab, 0.8rem, sentence case, blue underline on the active one,
 * the theme's own hover and colours), only shorter, and it sits INSIDE the panel's header row right after the panel
 * label - never as a separate segmented-button row.  MUI supplies the tablist/tab roles and the arrow/Home/End keys.
 * Too many tabs for the row scroll sideways instead of squeezing the header (the row itself may wrap).
 *
 * Use it for NAVIGATION (which view of the panel is open).  A real value switch (units, on/off, a numeric option)
 * stays a ToggleButtonGroup / Switch.
 */
import React from 'react';
import { Tab, Tabs, Tooltip } from '@mui/material';
import type { SxProps, Theme } from '@mui/material';

export interface SubTabItem<T extends string = string> {
  id: T;
  label: React.ReactNode;
  /** Tooltip text (the rule: a short label + the explanation in a tooltip). */
  tip?: string;
  disabled?: boolean;
  testId?: string;
}

/** Height of a sub-tab in a panel header row (the main bar's tab is 40). */
export const SUB_TAB_HEIGHT = 36;

/** Same look as the main bar's Tab (App.tsx), a little roomier sideways because the tabs share a row with other things. */
export const SUB_TAB_SX = {
  minHeight: SUB_TAB_HEIGHT, minWidth: 0, px: 1.25, py: 0,
  fontSize: '0.8rem', textTransform: 'none', letterSpacing: 0,
} as const;

interface Props<T extends string> {
  value: T;
  items: readonly SubTabItem<T>[];
  onChange: (id: T) => void;
  ariaLabel: string;
  testId?: string;
  sx?: SxProps<Theme>;
}

function SubTabs<T extends string>({ value, items, onChange, ariaLabel, testId, sx }: Props<T>): React.ReactElement {
  // MUI warns (and draws no indicator) for a value that is not a tab: an unknown value shows no selection instead.
  const known = items.some((i) => i.id === value);
  return (
    <Tabs
      value={known ? value : false}
      onChange={(_e, next: T) => onChange(next)}
      variant="scrollable"
      scrollButtons="auto"
      allowScrollButtonsMobile
      aria-label={ariaLabel}
      data-testid={testId}
      sx={[{ minHeight: SUB_TAB_HEIGHT, minWidth: 0, flex: '0 1 auto', maxWidth: '100%',
        '& .MuiTabs-scrollButtons': { width: 24 } }, ...(Array.isArray(sx) ? sx : [sx])]}
    >
      {items.map((i) => (
        <Tab
          key={i.id}
          value={i.id}
          disabled={i.disabled}
          data-testid={i.testId}
          label={i.tip ? <Tooltip title={i.tip}><span>{i.label}</span></Tooltip> : i.label}
          sx={SUB_TAB_SX}
        />
      ))}
    </Tabs>
  );
}

export default SubTabs;
