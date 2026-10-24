/**
 * Phone / narrow-tablet overrides for the "control column + workspace" tabs.
 *
 * Every rule lives inside the NARROW media query, so the desktop layout (>= 900 px) is byte-for-byte what it was:
 * spread these into an existing `sx` and only narrow screens change.  Below 900 px the fixed-width control column
 * takes the full width, the workspace (viewer, results) goes underneath it with a usable height, and the whole tab
 * scrolls vertically instead of squeezing the workspace into a strip (owner 2026-10-24, mobile pass).
 */
export const NARROW = '@media (max-width:899.95px)';

/** The row that holds the control column and the workspace. */
export const narrowSplitOuter = {
  [NARROW]: {
    flexDirection: 'column',
    overflowY: 'auto',
    overflowX: 'hidden',
    '& > [data-split-divider]': { display: 'none' },
    '& > [data-split-workspace]': { flex: 'none', minHeight: '75vh', width: '100%' },
  },
} as const;

/** The fixed-width control column. */
export const narrowSplitColumn = {
  [NARROW]: {
    width: '100%',
    overflowY: 'visible',
    borderRight: 'none',
    borderBottom: '1px solid var(--line-soft)',
  },
} as const;
