/**
 * Canonical English part name display.
 * Requirement E.2 & E.4: Part names stay canonical English for both UI languages (name.en).
 * Do NOT show a secondary Chinese line under part names.
 */
import React from 'react';
import { Typography } from '@mui/material';
import type { Bilingual } from '../types';

export const PartName: React.FC<{
  name: Bilingual | null | undefined;
  bold?: boolean;
  size?: number;
}> = ({ name, bold = false, size = 12.5 }) => {
  if (!name) return <Typography sx={{ fontSize: size, color: 'var(--text-4)' }}>—</Typography>;

  const en = (name.en ?? '').trim();
  const zh = (name.zh ?? '').trim();
  const primary = en || zh || '—';

  return (
    <Typography
      sx={{
        fontSize: size,
        fontWeight: bold ? 700 : 500,
        color: 'var(--text-0)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        lineHeight: 1.3,
      }}
    >
      {primary}
    </Typography>
  );
};

export default PartName;
