/** Custom React Flow block node for user-composed graphs (stage 2).
 * Renders input handles (left) and output handles (right) colored by contract type.
 */
import { memo } from 'react';
import { Box, Chip, IconButton, Stack, Tooltip, Typography } from '@mui/material';
import SettingsIcon from '@mui/icons-material/Settings';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import { Handle, Position } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';

import { STATE_COLORS, formatTime, typeColor } from '../../lib/systemGraph';
import type { ComposeNodeData } from './systemComposeTypes';

const NODE_WIDTH = 220;

function ComposeBlockNodeComponent({ id, data, selected }: NodeProps & { data: ComposeNodeData }) {
  const st = data.status || { state: 'unknown' };
  const paramCount = Object.keys(data.parameters || {}).length;

  const statusColor = STATE_COLORS[st.state] || '#6b7280';
  const isRunning = st.state === 'running';
  const isDone = st.state === 'done';
  const isError = st.state === 'error';

  return (
    <Box
      sx={{
        width: NODE_WIDTH,
        borderRadius: 1.5,
        bgcolor: '#ffffff',
        border: selected ? '2px solid #2563eb' : '1px solid #cbd5e1',
        boxShadow: selected ? '0 0 0 3px rgba(37, 99, 235, 0.2)' : '0 1px 3px rgba(0,0,0,0.08)',
        overflow: 'visible',
        position: 'relative',
        fontSize: 12,
      }}
    >
      {/* Node Header */}
      <Box
        sx={{
          px: 1.2,
          py: 0.8,
          bgcolor: '#f8fafc',
          borderTopLeftRadius: 6,
          borderTopRightRadius: 6,
          borderBottom: '1px solid #e2e8f0',
          display: 'flex',
          alignItems: 'center',
          gap: 0.8,
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="subtitle2" noWrap sx={{ fontWeight: 700, fontSize: 13, lineHeight: 1.2 }}>
            {data.title}
          </Typography>
          <Typography variant="caption" noWrap sx={{ color: 'text.secondary', fontSize: 10, display: 'block' }}>
            {data.capability}
          </Typography>
        </Box>

        {/* Status Indicator */}
        {isRunning && (
          <Tooltip title="Running...">
            <HourglassEmptyIcon sx={{ fontSize: 16, color: statusColor, animation: 'spin 2s linear infinite' }} />
          </Tooltip>
        )}
        {isDone && (
          <Tooltip title={st.summary || 'Done'}>
            <CheckCircleOutlineIcon sx={{ fontSize: 16, color: statusColor }} />
          </Tooltip>
        )}
        {isError && (
          <Tooltip title={st.summary || 'Error'}>
            <ErrorOutlineIcon sx={{ fontSize: 16, color: statusColor }} />
          </Tooltip>
        )}

        {/* Block Actions */}
        <Tooltip title="Parameters">
          <IconButton size="small" onClick={() => data.onEditParams?.(id)} sx={{ p: 0.4 }}>
            <SettingsIcon sx={{ fontSize: 15 }} />
          </IconButton>
        </Tooltip>
        <Tooltip title="Delete Block">
          <IconButton size="small" onClick={() => data.onDeleteNode?.(id)} sx={{ p: 0.4, color: 'text.secondary', '&:hover': { color: 'error.main' } }}>
            <DeleteOutlineIcon sx={{ fontSize: 15 }} />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Ports and Content */}
      <Box sx={{ px: 1.2, py: 1 }}>
        {/* Input Ports (Left) */}
        {data.inputs && data.inputs.length > 0 && (
          <Box sx={{ mb: 1 }}>
            <Typography variant="caption" sx={{ fontSize: 9.5, fontWeight: 700, color: 'text.secondary', textTransform: 'uppercase' }}>
              Inputs
            </Typography>
            <Stack spacing={0.6} sx={{ mt: 0.4 }}>
              {data.inputs.map((inp) => {
                const color = typeColor(inp.type);
                // Calculate handle vertical offset
                return (
                  <Box key={inp.port} sx={{ position: 'relative', display: 'flex', alignItems: 'center', minHeight: 18 }}>
                    <Handle
                      type="target"
                      position={Position.Left}
                      id={inp.port}
                      style={{
                        left: -14,
                        width: 10,
                        height: 10,
                        background: color,
                        border: '2px solid #ffffff',
                        boxShadow: `0 0 0 1px ${color}`,
                      }}
                    />
                    <Box sx={{ width: 6, height: 6, borderRadius: '50%', bgcolor: color, mr: 0.6, flexShrink: 0 }} />
                    <Typography variant="caption" sx={{ fontSize: 11, fontFamily: 'monospace' }} noWrap>
                      {inp.port} <span style={{ color: '#64748b' }}>({inp.type})</span>
                    </Typography>
                  </Box>
                );
              })}
            </Stack>
          </Box>
        )}

        {/* Output Ports (Right) */}
        {data.outputs && data.outputs.length > 0 && (
          <Box sx={{ mb: 0.5 }}>
            <Typography variant="caption" sx={{ fontSize: 9.5, fontWeight: 700, color: 'text.secondary', textTransform: 'uppercase' }}>
              Outputs
            </Typography>
            <Stack spacing={0.6} sx={{ mt: 0.4 }}>
              {data.outputs.map((out) => {
                const color = typeColor(out.type);
                return (
                  <Box key={out.port} sx={{ position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', minHeight: 18 }}>
                    <Typography variant="caption" sx={{ fontSize: 11, fontFamily: 'monospace', mr: 0.6 }} noWrap>
                      {out.port} <span style={{ color: '#64748b' }}>({out.type})</span>
                    </Typography>
                    <Box sx={{ width: 6, height: 6, borderRadius: '50%', bgcolor: color, flexShrink: 0 }} />
                    <Handle
                      type="source"
                      position={Position.Right}
                      id={out.port}
                      style={{
                        right: -14,
                        width: 10,
                        height: 10,
                        background: color,
                        border: '2px solid #ffffff',
                        boxShadow: `0 0 0 1px ${color}`,
                      }}
                    />
                  </Box>
                );
              })}
            </Stack>
          </Box>
        )}

        {/* Bottom Bar: Parameter Count & Elapsed Time */}
        <Box sx={{ mt: 1, pt: 0.8, borderTop: '1px solid #f1f5f9', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Chip
            size="small"
            label={`${paramCount} param${paramCount === 1 ? '' : 's'}`}
            sx={{ height: 18, fontSize: 10, bgcolor: '#f1f5f9', color: '#475569' }}
            onClick={() => data.onEditParams?.(id)}
          />
          {st.time_s ? (
            <Typography variant="caption" sx={{ fontSize: 10, color: '#64748b', fontWeight: 600 }}>
              {formatTime(st.time_s)}
            </Typography>
          ) : null}
        </Box>
      </Box>
    </Box>
  );
}

export const ComposeBlockNode = memo(ComposeBlockNodeComponent);
