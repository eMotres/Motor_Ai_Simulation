import React from 'react';
import { Box } from '@mui/material';

export interface GeometryLayoutProps {
  emContent: React.ReactNode;
  cadContent?: React.ReactNode;
  viewer: React.ReactNode;
  panelWidth: number;
  onDividerMouseDown: (event: React.MouseEvent) => void;
}

/** Shared Geometry Active/Full shell: one EM column, optional CAD editor below it, and the same viewer column. */
const GeometryLayout: React.FC<GeometryLayoutProps> = ({ emContent, cadContent, viewer, panelWidth, onDividerMouseDown }) => (
  <Box data-testid="geometry-layout" sx={{ display:'flex', height:'100%', minHeight:0, minWidth:0 }}>
    <Box data-testid="geometry-left-column" sx={{ width:panelWidth, flexShrink:0, overflowY:'auto', p:1.5, borderRight:'1px solid', borderColor:'divider' }}>
      <Box data-testid="geometry-em-content">{emContent}</Box>
      {cadContent != null && <Box data-testid="geometry-cad-content" sx={{ mt:2, pt:1.5, borderTop:'1px solid var(--line-soft)' }}>{cadContent}</Box>}
    </Box>
    <Box data-testid="geometry-resizer" onMouseDown={onDividerMouseDown} sx={{ width:5, flexShrink:0, cursor:'col-resize',
      bgcolor:'divider', transition:'background-color 0.15s', '&:hover':{ bgcolor:'primary.main' }, userSelect:'none' }} />
    <Box data-testid="geometry-viewer-column" sx={{ flex:1, minWidth:0, minHeight:0, position:'relative', overflow:'hidden' }}>{viewer}</Box>
  </Box>
);

export default GeometryLayout;
