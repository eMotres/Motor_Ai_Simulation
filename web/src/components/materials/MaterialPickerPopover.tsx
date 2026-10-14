/**
 * The material picker popover shared by the viewer's floating material bar (active parts) and the Materials tab
 * "Full" list (every other part): a grouped list of materials, the current one ticked.
 * Extracted from MaterialBar so both pick materials the same way.
 */
import React from 'react';
import { Box, Typography, Popover, List, ListItem, ListItemButton, ListItemText, Divider } from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';

export interface PickerItem { id: string; label?: string }
export interface PickerGroup { key: string; label: string; color: string; items: PickerItem[] }

interface Props {
  open: boolean;
  anchorEl: HTMLElement | null;
  onClose: () => void;
  /** Header line, e.g. "MATERIALS - STATOR CORE". */
  title: string;
  groups: PickerGroup[];
  current: string | null | undefined;
  disabled?: boolean;
  onPick: (id: string) => void;
  emptyText?: string;
  anchorOrigin?: { vertical: 'top' | 'bottom'; horizontal: 'left' | 'center' | 'right' };
  transformOrigin?: { vertical: 'top' | 'bottom'; horizontal: 'left' | 'center' | 'right' };
}

const MaterialPickerPopover: React.FC<Props> = ({
  open, anchorEl, onClose, title, groups, current, disabled, onPick, emptyText = 'No materials available',
  anchorOrigin = { vertical: 'top', horizontal: 'center' },
  transformOrigin = { vertical: 'bottom', horizontal: 'center' },
}) => (
  <Popover
    open={open}
    anchorEl={anchorEl}
    onClose={onClose}
    anchorOrigin={anchorOrigin}
    transformOrigin={transformOrigin}
    slotProps={{
      paper: {
        sx: {
          bgcolor: 'var(--panel-2)',
          border: '1px solid var(--line-soft)',
          borderRadius: 2,
          minWidth: 260,
          maxHeight: 520,
          overflow: 'hidden',
          boxShadow: '0 8px 32px var(--overlay)',
        },
      },
    }}
  >
    {/* Header */}
    <Box sx={{ px: 2, py: 1, borderBottom: '1px solid var(--line-soft)' }}>
      <Typography sx={{ fontSize: 10, fontWeight: 700, color: 'var(--text-4)', letterSpacing: 1 }}>
        {title}
      </Typography>
    </Box>

    {/* Material list - all categories grouped */}
    <List dense disablePadding sx={{ overflowY: 'auto', maxHeight: 460 }}>
      {groups.map((group, gi) => (
        <React.Fragment key={group.key}>
          <Box sx={{
            px: 2, py: '4px',
            bgcolor: 'var(--panel-2)',
            borderLeft: `2px solid ${group.color}`,
            mx: 1, mt: gi > 0 ? 0.5 : 0, borderRadius: '2px',
          }}>
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: group.color, letterSpacing: 1 }}>
              {group.label.toUpperCase()}
            </Typography>
          </Box>

          {group.items.map((item, i) => {
            const isActive = item.id === current;
            return (
              <React.Fragment key={item.id}>
                {i > 0 && <Divider sx={{ borderColor: 'var(--app-bg)' }} />}
                <ListItemButton
                  onClick={() => onPick(item.id)}
                  disabled={disabled}
                  sx={{
                    py: 0.75, px: 2,
                    bgcolor: isActive ? `${group.color}18` : 'transparent',
                    '&:hover': { bgcolor: `${group.color}28` },
                  }}
                >
                  <ListItemText
                    primary={(item.label ?? item.id).replace(/_/g, ' ')}
                    primaryTypographyProps={{
                      sx: {
                        fontSize: 11,
                        fontWeight: isActive ? 700 : 400,
                        color: isActive ? group.color : 'var(--text-1)',
                      },
                    }}
                  />
                  {isActive && <CheckIcon sx={{ fontSize: 14, color: group.color, ml: 1 }} />}
                </ListItemButton>
              </React.Fragment>
            );
          })}
        </React.Fragment>
      ))}
      {groups.length === 0 && (
        <ListItem>
          <ListItemText primary={emptyText} primaryTypographyProps={{ sx: { fontSize: 11, color: 'var(--text-4)' } }} />
        </ListItem>
      )}
    </List>
  </Popover>
);

export default MaterialPickerPopover;
