/**
 * Small line icons for the application cards on the landing page.  Inline SVG
 * on `currentColor`, so they follow the theme with no asset and no request.
 */
import React from 'react';
import { Box } from '@mui/material';

const Icon: React.FC<{ children: React.ReactNode; size?: number }> = ({ children, size = 44 }) => (
  <Box component="svg" viewBox="0 0 48 48" width={size} height={size} aria-hidden="true"
    focusable="false" sx={{ flexShrink: 0, color: 'primary.main' }}
    fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round"
    strokeLinejoin="round">
    {children}
  </Box>
);

/** Quadcopter seen from above. */
export const DroneIcon: React.FC = () => (
  <Icon>
    <rect x="19" y="19" width="10" height="10" rx="2.5" />
    <path d="M21 21 11 11M27 21 37 11M21 27 11 37M27 27 37 37" />
    <ellipse cx="9" cy="9" rx="6" ry="2.4" transform="rotate(-20 9 9)" />
    <ellipse cx="39" cy="9" rx="6" ry="2.4" transform="rotate(20 39 9)" />
    <ellipse cx="9" cy="39" rx="6" ry="2.4" transform="rotate(20 9 39)" />
    <ellipse cx="39" cy="39" rx="6" ry="2.4" transform="rotate(-20 39 39)" />
  </Icon>
);

/** A two-link arm with a gripper. */
export const RobotIcon: React.FC = () => (
  <Icon>
    <path d="M8 42h20" />
    <rect x="13" y="36" width="10" height="6" rx="1.5" />
    <path d="M18 36 18 22 32 12" />
    <circle cx="18" cy="22" r="3" />
    <circle cx="32" cy="12" r="3" />
    <path d="M34 14 40 20M30 15 36 23" />
  </Icon>
);

/** A hull on waves. */
export const BoatIcon: React.FC = () => (
  <Icon>
    <path d="M24 6v22M24 8l12 18H24" />
    <path d="M8 30h32l-5 9H13z" />
    <path d="M4 44c3 0 3-2 6-2s3 2 6 2 3-2 6-2 3 2 6 2 3-2 6-2 3 2 6 2" />
  </Icon>
);

/** An open slot: yours. */
export const PlusIcon: React.FC = () => (
  <Icon>
    <circle cx="24" cy="24" r="17" strokeDasharray="4 4.5" />
    <path d="M24 16v16M16 24h16" />
  </Icon>
);
