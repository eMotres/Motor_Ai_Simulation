/**
 * The way back from an open project to the project list, in the header row before the project's tabs.
 *
 * Owner 2026-11-18: "the return arrow is quite unnoticeable, better make it a button so that it is seen" - a bare 18 px
 * arrow icon sat between the panel label and the tabs.  Now: an outlined button with the arrow and its label, as tall as
 * a sub-tab, a clear gap before the tabs, the tooltip kept, a visible keyboard focus ring.  It never shrinks or wraps:
 * the tabs beside it scroll sideways (SubTabs is scrollable) instead of being pushed to a second line.
 */
import React from 'react';
import { Button, Tooltip } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import { SUB_TAB_HEIGHT } from '../../../components/common/SubTabs';

/** Height of the button: the sub-tab's own height (task: "same height as the tabs"). */
export const BACK_BUTTON_HEIGHT = SUB_TAB_HEIGHT;

interface Props {
  label: string;
  tip: string;
  onClick: () => void;
}

const ProjectBackButton: React.FC<Props> = ({ label, tip, onClick }) => (
  <Tooltip title={tip}>
    <Button
      variant="outlined"
      size="small"
      data-testid="project-back"
      startIcon={<ArrowBackIcon sx={{ fontSize: 18 }} />}
      onClick={onClick}
      sx={{
        height: BACK_BUTTON_HEIGHT, flexShrink: 0, whiteSpace: 'nowrap', mr: 1.5, px: 1.25,
        fontSize: '0.8rem', textTransform: 'none', letterSpacing: 0,
        '&.Mui-focusVisible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: 2 },
      }}
    >
      {label}
    </Button>
  </Tooltip>
);

export default ProjectBackButton;
