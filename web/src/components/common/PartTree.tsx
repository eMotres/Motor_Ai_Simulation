// THE part tree component - ONE component for every surface that lists the parts of a motor (owner 2026-10-15): Geometry > Full,
// Materials > Full (with a material column), the 3-D model tab (with show/hide boxes).  The data is the server's single tree
// (motor_ai_sim/part_tree.py): same ids, names (EN / zh-CN), grouping and order everywhere.  Selecting a part (or a group) reports
// it; the 3-D viewer highlights the GLB nodes of the selection.
import React, { useEffect, useState } from 'react';
import { Box, Checkbox, Chip, Tooltip, Typography } from '@mui/material';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { groupMassKg, initialOpen, nameOf, partsOfGroup, type PartTreeData, type TreePart } from '../../lib/partTree';
import { formatMass } from '../materials/fullPartsLogic';

const tx = nsT('geometry');

export interface PartTreeProps {
  tree: PartTreeData;
  selectedPart?: string | null;
  selectedGroup?: string | null;
  /** click on a part (null = the click cleared the selection) */
  onSelectPart?: (id: string | null) => void;
  onSelectGroup?: (id: string | null) => void;
  /** show/hide boxes (the 3-D model): `hidden` = hidden part ids */
  visibility?: { hidden: ReadonlySet<string>; onToggle: (partId: string) => void };
  /** show the mass of every part and group */
  showMass?: boolean;
  /** content at the right end of a part row (e.g. the material picker) */
  renderRight?: (part: TreePart) => React.ReactNode;
  /** a CSS grid column width for the right cell (default: auto) */
  rightWidth?: number | string;
  testId?: string;
}

const PartTree: React.FC<PartTreeProps> = ({
  tree, selectedPart = null, selectedGroup = null, onSelectPart, onSelectGroup, visibility, showMass = false,
  renderRight, rightWidth, testId = 'part-tree',
}) => {
  const { i18n } = useTranslation('geometry');
  const lang = i18n.resolvedLanguage ?? 'en';
  const [open, setOpen] = useState<Record<string, boolean>>(() => initialOpen(tree));
  const groupKey = tree.groups.map((g) => g.id).join('|');
  useEffect(() => {
    setOpen((o) => ({ ...initialOpen(tree), ...Object.fromEntries(Object.entries(o).filter(([k]) => tree.groups.some((g) => g.id === k))) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupKey]);

  return (
    <Box role="tree" data-testid={testId} sx={{ fontSize: 12.5, color: 'var(--text-1)' }}>
      {tree.groups.map((g) => {
        const parts = partsOfGroup(tree, g.id);
        const isOpen = open[g.id] !== false;
        const gm = showMass ? groupMassKg(tree, g.id) : null;
        const gSel = selectedGroup === g.id && !selectedPart;
        return (
          <Box key={g.id} role="group" data-testid={`part-group-${g.id}`}>
            <Box role="treeitem" aria-expanded={isOpen} aria-selected={gSel}
              onClick={() => { setOpen((o) => ({ ...o, [g.id]: !isOpen })); onSelectGroup?.(gSel ? null : g.id); onSelectPart?.(null); }}
              sx={{ display: 'flex', alignItems: 'center', gap: 0.5, px: 0.5, py: 0.35, cursor: 'pointer', userSelect: 'none',
                bgcolor: gSel ? 'var(--panel-3, rgba(59,130,246,.12))' : 'transparent', '&:hover': { bgcolor: 'var(--panel-3, rgba(59,130,246,.08))' } }}>
              {isOpen ? <ExpandMoreIcon sx={{ fontSize: 16 }} /> : <ChevronRightIcon sx={{ fontSize: 16 }} />}
              <Typography sx={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color: 'var(--text-3)', flex: 1 }}>
                {nameOf(g.name, lang)}
              </Typography>
              <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{parts.length}</Typography>
              {gm != null && <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', minWidth: 52, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatMass(gm)}</Typography>}
            </Box>
            {isOpen && parts.map((p) => {
              const sel = selectedPart === p.id;
              const hiddenNow = visibility?.hidden.has(p.id) ?? false;
              return (
                <Box key={p.id} role="treeitem" aria-selected={sel} data-testid={`part-${p.id}`}
                  onClick={() => { onSelectPart?.(sel ? null : p.id); onSelectGroup?.(sel ? null : g.id); }}
                  sx={{ display: 'grid', gridTemplateColumns: `${visibility ? '24px ' : ''}minmax(0,1fr) ${showMass ? 'auto ' : ''}${renderRight ? (typeof rightWidth === 'number' ? `${rightWidth}px` : (rightWidth ?? 'auto')) : ''}`,
                    alignItems: 'center', columnGap: 0.75, pl: visibility ? 1.5 : 3, pr: 0.75, py: 0.2, cursor: 'pointer',
                    bgcolor: sel ? 'var(--panel-3, rgba(59,130,246,.18))' : 'transparent',
                    borderLeft: sel ? '2px solid #2563eb' : '2px solid transparent',
                    '&:hover': { bgcolor: 'var(--panel-3, rgba(59,130,246,.08))' } }}>
                  {visibility && (
                    <Checkbox size="small" sx={{ p: 0.25 }} checked={!hiddenNow} inputProps={{ 'aria-label': nameOf(p.names, lang) }}
                      onClick={(e) => e.stopPropagation()} onChange={() => visibility.onToggle(p.id)} />
                  )}
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0 }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{nameOf(p.names, lang)}</span>
                    {tree.parts.some((x) => x.em !== undefined) && (
                      <Tooltip title={p.em ? tx('partEmTip') : tx('partNotEmTip')}>
                        <Chip size="small" label={p.em ? tx('partEm') : tx('partNotEm')}
                          sx={{ height: 15, fontSize: 9, '& .MuiChip-label': { px: 0.6 }, bgcolor: p.em ? 'var(--panel)' : 'transparent',
                            border: '1px solid var(--line)', color: 'var(--text-3)', flexShrink: 0 }} />
                      </Tooltip>
                    )}
                  </Box>
                  {showMass && (
                    <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-3)', textAlign: 'right', fontVariantNumeric: 'tabular-nums',
                      opacity: p.counted === false ? 0.5 : 1 }}>{formatMass(p.mass_kg ?? null)}</Typography>
                  )}
                  {renderRight && <Box onClick={(e) => e.stopPropagation()}>{renderRight(p)}</Box>}
                </Box>
              );
            })}
          </Box>
        );
      })}
    </Box>
  );
};

export default PartTree;
