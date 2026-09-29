/**
 * HelpTip — the project's "one short line + tooltip" rule, made safe to click
 * around (2026-09-15).
 *
 * A TOOLTIP MUST NEVER WRAP A SELECT OR AN INPUT.  MUI draws tooltips at
 * z-index 1500 and menus at 1300, and a tooltip is INTERACTIVE by default —
 * its popper takes pointer events — so a hint wrapped round a Select lands on
 * top of the very options it is explaining and swallows the click that should
 * pick one.  The duty-cycle editor's kind select could not be opened at all
 * because of it (user 2026-09-15: «всплывающее меню всё закрывает»), and the
 * cooling selects on the Thermal tab had already been patched once by hand
 * with a z-index (2026-09-07) — twice is a pattern, so it lives here now.
 *
 * The hint hangs on a small ⓘ BESIDE the control instead, and the popper is
 * pushed UNDER the menu layer, made non-interactive (no pointer events at all)
 * and delayed, so a cursor merely passing over a row cannot cover the row.
 */
import React from 'react';
import { Box, Tooltip } from '@mui/material';

/** Under the Menu (1300) and the Modal, above nothing that can be clicked. */
export const TIP_SLOTS = { popper: { sx: { zIndex: 1250 } } } as const;

/** What every tooltip in these panels is given: never interactive, never
 *  instant, never over a menu.  Spread it onto a Tooltip that legitimately
 *  wraps TEXT (a readout, a warning) — controls get a `HelpTip` beside them. */
export const TIP_PROPS = {
  disableInteractive: true,
  enterDelay: 500,
  enterNextDelay: 500,
  slotProps: TIP_SLOTS,
} as const;

/** A control and its hint on one line — the control first, the ⓘ after it. */
export const CTRL_ROW = {
  display: 'inline-flex', alignItems: 'center', gap: 0.375,
} as const;

interface Props {
  /** ONE short line. If it needs a second sentence it belongs on screen. */
  title: string;
  placement?: 'top' | 'bottom' | 'left' | 'right';
}

const HelpTip: React.FC<Props> = ({ title, placement = 'top' }) => (
  <Tooltip title={title} placement={placement} {...TIP_PROPS}>
    <Box component="span" aria-label="help" role="img" sx={{
      fontSize: 12, lineHeight: 1, color: 'var(--text-4)', cursor: 'help',
      userSelect: 'none', px: 0.125,
      '&:hover': { color: 'var(--text-2)' },
    }}>
      ⓘ
    </Box>
  </Tooltip>
);

export default HelpTip;
