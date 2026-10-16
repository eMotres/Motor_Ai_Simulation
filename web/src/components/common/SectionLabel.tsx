import React from 'react';
import { Typography } from '@mui/material';
import type { SxProps, Theme } from '@mui/material';

/**
 * Canonical section header used across every control panel (Geometry, Mesh,
 * Simulation, Optimization, Cost, Configure).  One source of truth so the tabs
 * read as a single app: dim slate, bold, uppercase, wide tracking.
 *
 * Pass `sx` for per-site spacing (usually `mb`) — it merges over the base style.
 */
export const SECTION_LABEL_SX = {
  fontSize: '0.65rem',
  fontWeight: 700,
  color: 'var(--text-4)',
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  display: 'block',
} as const;

const SectionLabel: React.FC<{ children: React.ReactNode; sx?: SxProps<Theme> }> = ({ children, sx }) => (
  <Typography sx={{ ...SECTION_LABEL_SX, ...sx }}>{children}</Typography>
);

export default SectionLabel;
