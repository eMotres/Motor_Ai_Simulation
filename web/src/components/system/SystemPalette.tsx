/** Palette of available software capabilities for dragging onto the System canvas. */
import React, { useMemo, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Card,
  InputAdornment,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';

import { typeColor } from '../../lib/systemGraph';
import { BUILTIN_CAPABILITIES } from './systemComposeTypes';
import type { CapabilityDef } from './systemComposeTypes';

interface SystemPaletteProps {
  customCapabilities?: CapabilityDef[];
  onSelectCapability?: (cap: CapabilityDef) => void;
}

export default function SystemPalette({ customCapabilities = [], onSelectCapability }: SystemPaletteProps) {
  const [filter, setFilter] = useState('');

  const allCapabilities = useMemo(() => {
    const list = [...BUILTIN_CAPABILITIES];
    for (const c of customCapabilities) {
      if (!list.some((x) => x.capability === c.capability)) {
        list.push(c);
      }
    }
    return list;
  }, [customCapabilities]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return allCapabilities;
    const q = filter.toLowerCase().trim();
    return allCapabilities.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.capability.toLowerCase().includes(q) ||
        c.category.toLowerCase().includes(q) ||
        c.summary.toLowerCase().includes(q)
    );
  }, [allCapabilities, filter]);

  const grouped = useMemo(() => {
    const groups: Record<string, CapabilityDef[]> = {};
    for (const c of filtered) {
      const cat = c.category || 'Other';
      (groups[cat] ||= []).push(c);
    }
    return groups;
  }, [filtered]);

  const onDragStart = (e: React.DragEvent, cap: CapabilityDef) => {
    e.dataTransfer.setData('application/system-graph-block', JSON.stringify(cap));
    e.dataTransfer.effectAllowed = 'move';
  };

  return (
    <Box sx={{ width: 260, height: '100%', display: 'flex', flexDirection: 'column', borderRight: '1px solid #e2e8f0', bgcolor: '#f8fafc' }}>
      {/* Palette Header */}
      <Box sx={{ p: 1.5, borderBottom: '1px solid #e2e8f0' }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
          Module Palette
        </Typography>
        <TextField
          size="small"
          fullWidth
          placeholder="Search modules..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
              </InputAdornment>
            ),
          }}
          sx={{
            '& .MuiInputBase-root': { fontSize: 12, height: 32, bgcolor: '#ffffff' },
          }}
        />
        <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mt: 0.8, fontSize: 10.5 }}>
          Drag a block onto the canvas to add it.
        </Typography>
      </Box>

      {/* Categories and Block Cards */}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 1 }}>
        {Object.entries(grouped).map(([category, items]) => (
          <Accordion key={category} defaultExpanded disableGutters sx={{ mb: 1, '&:before': { display: 'none' }, boxShadow: 'none', border: '1px solid #e2e8f0', borderRadius: '6px !important' }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ fontSize: 18 }} />} sx={{ minHeight: 34, height: 34, px: 1.2, bgcolor: '#f1f5f9' }}>
              <Typography variant="caption" sx={{ fontWeight: 700, fontSize: 11, color: '#334155' }}>
                {category} ({items.length})
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ p: 0.8, display: 'flex', flexDirection: 'column', gap: 0.8 }}>
              {items.map((cap) => (
                <Card
                  key={cap.capability}
                  draggable
                  onDragStart={(e) => onDragStart(e, cap)}
                  onClick={() => onSelectCapability?.(cap)}
                  sx={{
                    p: 1,
                    cursor: 'grab',
                    bgcolor: '#ffffff',
                    border: '1px solid #cbd5e1',
                    borderRadius: 1,
                    boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
                    transition: 'all 0.15s ease',
                    '&:hover': {
                      borderColor: '#3b82f6',
                      boxShadow: '0 2px 5px rgba(59, 130, 246, 0.15)',
                    },
                    '&:active': { cursor: 'grabbing' },
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={0.6}>
                    <DragIndicatorIcon sx={{ fontSize: 14, color: '#94a3b8' }} />
                    <Typography variant="body2" sx={{ fontWeight: 700, fontSize: 12, lineHeight: 1.2, flex: 1 }} noWrap>
                      {cap.title}
                    </Typography>
                  </Stack>

                  <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: 10, display: 'block', mt: 0.3 }} noWrap>
                    {cap.capability}
                  </Typography>

                  {/* Ports preview */}
                  <Stack direction="row" spacing={0.4} flexWrap="wrap" sx={{ mt: 0.6, gap: 0.4 }}>
                    {cap.inputs.map((inp) => (
                      <Tooltip key={inp.port} title={`Input: ${inp.port} (${inp.type})`}>
                        <Box sx={{ display: 'inline-flex', alignItems: 'center', px: 0.5, height: 14, borderRadius: 4, bgcolor: `${typeColor(inp.type)}18`, border: `1px solid ${typeColor(inp.type)}` }}>
                          <Box sx={{ width: 4, height: 4, borderRadius: '50%', bgcolor: typeColor(inp.type), mr: 0.4 }} />
                          <Typography variant="caption" sx={{ fontSize: 9, color: '#1e293b' }}>
                            {inp.port}
                          </Typography>
                        </Box>
                      </Tooltip>
                    ))}
                    {cap.outputs.map((out) => (
                      <Tooltip key={out.port} title={`Output: ${out.port} (${out.type})`}>
                        <Box sx={{ display: 'inline-flex', alignItems: 'center', px: 0.5, height: 14, borderRadius: 4, bgcolor: `${typeColor(out.type)}18`, border: `1px solid ${typeColor(out.type)}` }}>
                          <Typography variant="caption" sx={{ fontSize: 9, color: '#1e293b', mr: 0.4 }}>
                            {out.port}
                          </Typography>
                          <Box sx={{ width: 4, height: 4, borderRadius: '50%', bgcolor: typeColor(out.type) }} />
                        </Box>
                      </Tooltip>
                    ))}
                  </Stack>
                </Card>
              ))}
            </AccordionDetails>
          </Accordion>
        ))}
      </Box>
    </Box>
  );
}
