import React from 'react';
import { Tooltip } from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';

/**
 * HelpTip — a small ⓘ icon that reveals parameter help on hover, replacing the
 * inline `helperText` captions under fields so panels stay compact.  Drop it into
 * a field's `InputProps.endAdornment`, or render it next to a label/heading.
 *
 *   <TextField … InputProps={{ endAdornment: <HelpTip title="…explanation…" /> }} />
 */
const HelpTip: React.FC<{ title: React.ReactNode }> = ({ title }) => (
  <Tooltip title={title} placement="top" arrow enterTouchDelay={0}
    componentsProps={{ tooltip: { sx: { fontSize: 11, maxWidth: 300, lineHeight: 1.4 } } }}>
    <InfoOutlinedIcon
      sx={{ fontSize: 15, color: 'var(--text-4)', cursor: 'help', flexShrink: 0,
        '&:hover': { color: 'var(--text-2)' } }} />
  </Tooltip>
);

export default HelpTip;
