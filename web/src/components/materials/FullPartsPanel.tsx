/**
 * Materials tab, mode "Full": the whole motor part by part.  The active parts (used by the electromagnetic
 * simulation) are listed first, read-only here; every other part of the assembled motor has a material picker (the same
 * picker as the viewer's material bar) and the small tag "not EM".  The choice is saved with the die.
 */
import React, { useMemo, useState } from 'react';
import { Box, Chip, CircularProgress, Tooltip, Typography } from '@mui/material';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { useLastMotorState } from '../../lib/lastMotor';
import MaterialPickerPopover from './MaterialPickerPopover';
import { useFullParts } from './useFullParts';
import {
  countedMassKg, formatMass, GROUP_LABEL_KEYS, groupChoices, isEditable, rowTag, rowsForMode,
  type FullPartRow, type MaterialChoice,
} from './fullPartsLogic';

const tx = nsT('motors');

const GROUP_COLOR: Record<string, string> = {
  metal: '#f59e0b', steel: 'var(--text-3)', magnet: '#ef4444', insulator: '#3fae5a', other: '#38bdf8',
};

const FullPartsPanel: React.FC = () => {
  useTranslation('motors');
  const { selection } = useLastMotorState();
  const die = selection?.die ?? null;
  const config = selection?.config ?? null;
  const { payload, rows, loading, saving, error, choose, localMode } = useFullParts(die, config, true);

  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [picking, setPicking] = useState<FullPartRow | null>(null);

  const choices: MaterialChoice[] = payload?.choices ?? [];
  const groups = useMemo(() => groupChoices(choices).map((g) => ({
    key: g.group, label: tx(GROUP_LABEL_KEYS[g.group]), color: GROUP_COLOR[g.group] ?? 'var(--text-3)',
    items: g.items.map((c) => ({ id: c.id })),
  })), [choices]);

  const ordered = rowsForMode(rows, 'full');
  const totalKg = countedMassKg(ordered);
  const partial = !!payload?.partial;
  const kit = !!payload?.kit;
  const nonEm = ordered.some((r) => !r.em);

  let body: React.ReactNode;
  if (!die) {
    body = <Typography sx={{ p: 2, fontSize: 12, color: 'var(--text-3)' }}>{tx('materialsFullNoMotor')}</Typography>;
  } else if (loading && !payload) {
    body = (
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
        <CircularProgress size={14} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tx('materialsFullLoading')}</Typography>
      </Box>);
  } else if (error || !payload || !payload.available) {
    body = (
      <Typography sx={{ p: 2, fontSize: 12, color: 'var(--text-3)' }} title={payload?.reason ?? error ?? ''}>
        {tx('materialsFullUnavailable')}
      </Typography>);
  } else {
    body = (
      <Box component="table" data-testid="materials-full-table"
        sx={{ width: '100%', borderCollapse: 'collapse', fontSize: 12,
          '& th': { textAlign: 'left', fontWeight: 600, color: 'var(--text-4)', fontSize: 10, letterSpacing: 0.5,
            textTransform: 'uppercase', p: '6px 10px', borderBottom: '1px solid var(--line)' },
          '& td': { p: '5px 10px', borderBottom: '1px solid var(--line-soft)', verticalAlign: 'middle' } }}>
        <thead>
          <tr>
            <th>{tx('materialsFullPart')}</th><th>{tx('materialsFullRole')}</th>
            <th>{tx('materialsFullMaterial')}</th><th style={{ textAlign: 'right' }}>{tx('materialsFullMass')}</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((r) => {
            const editable = isEditable(r);
            const isDefault = !r.user_set;
            const tip = !editable ? tx('materialsFullEmTip')
              : r.user_set ? tx('materialsFullChosenTip')
              : r.status === 'assumed' ? `${tx('materialsFullDefaultTip')} ${tx('materialsFullAssumedTip')}`
              : tx('materialsFullDefaultTip');
            return (
              <tr key={r.key} data-testid={`materials-full-row-${r.key}`}>
                <td>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <span>{tx(`materialsPart_${r.key}`, { defaultValue: r.name })}</span>
                    <Tooltip title={rowTag(r) === 'em' ? tx('materialsFullEmTip') : tx('materialsFullNotEmTip')}>
                      <Chip size="small" label={rowTag(r) === 'em' ? tx('materialsFullEm') : tx('materialsFullNotEm')}
                        sx={{ height: 16, fontSize: 9, '& .MuiChip-label': { px: 0.75 },
                          bgcolor: r.em ? 'var(--panel)' : 'transparent',
                          border: '1px solid var(--line)', color: 'var(--text-3)' }} />
                    </Tooltip>
                  </Box>
                </td>
                <td style={{ color: 'var(--text-3)' }}>{tx(`materialsRole${r.role.charAt(0).toUpperCase()}${r.role.slice(1)}`)}</td>
                <td>
                  <Tooltip title={tip}>
                    <Box
                      data-testid={`materials-full-pick-${r.key}`}
                      onClick={editable ? (e) => { setPicking(r); setAnchor(e.currentTarget as HTMLElement); } : undefined}
                      sx={{
                        display: 'inline-flex', alignItems: 'center', gap: 0.5, px: 1, py: 0.25, borderRadius: 1.5,
                        border: '1px solid var(--line)', cursor: editable ? 'pointer' : 'default',
                        bgcolor: editable ? 'var(--panel)' : 'transparent', maxWidth: 320,
                        fontStyle: editable && isDefault ? 'italic' : 'normal',
                        '&:hover': editable ? { borderColor: 'var(--text-3)' } : undefined,
                      }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', paddingRight: 2 }}>
                        {r.material_id.replace(/_/g, ' ')}
                      </span>
                      {editable && <ArrowDropDownIcon sx={{ fontSize: 16, color: 'var(--text-3)' }} />}
                    </Box>
                  </Tooltip>
                </td>
                <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', opacity: r.counted || r.mass_kg == null ? 1 : 0.5 }}>{formatMass(r.mass_kg)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={3} style={{ color: 'var(--text-3)' }}>
              <Tooltip title={tx(partial ? 'materialsFullPartialTip' : kit ? 'materialsFullTotalKitTip' : 'materialsFullTotalTip')}>
                <span>{tx(partial ? 'materialsFullPartial' : kit ? 'materialsFullTotalKit' : 'materialsFullTotal')}</span>
              </Tooltip>
            </td>
            <td style={{ textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{formatMass(totalKg)}</td>
          </tr>
          {!partial && payload?.active_kg != null && (
            <tr>
              <td colSpan={3} style={{ color: 'var(--text-3)' }}>
                <Tooltip title={tx('materialsFullActiveTip')}><span>{tx('materialsFullActive')}</span></Tooltip>
              </td>
              <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatMass(payload.active_kg)}</td>
            </tr>
          )}
        </tfoot>
      </Box>);
  }

  return (
    <Box sx={{ height: '100%', overflow: 'auto', bgcolor: 'var(--panel-2)', color: 'var(--text-1)' }} data-testid="materials-full-panel">
      <Box sx={{ px: 1.5, py: 1, borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 1 }}>
        <Tooltip title={tx('materialsFullHintTip')}>
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)', flex: 1 }}>{tx('materialsFullHint')}</Typography>
        </Tooltip>
        {die && <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{die}{config ? ` / ${config}` : ''}</Typography>}
        {saving && <CircularProgress size={12} />}
        {!saving && nonEm && localMode && (
          <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{tx('materialsFullLocal')}</Typography>)}
      </Box>
      {body}
      {payload?.available && payload.partial && (
        <Typography sx={{ px: 1.5, py: 1, fontSize: 11, color: 'var(--text-4)' }}>{tx('materialsFullNoCad')}</Typography>)}
      <MaterialPickerPopover
        open={!!anchor && !!picking}
        anchorEl={anchor}
        onClose={() => { setAnchor(null); setPicking(null); }}
        title={tx('materialsFullPickTitle', { part: picking ? tx(`materialsPart_${picking.key}`, { defaultValue: picking.name }) : '' }).toUpperCase()}
        groups={groups}
        current={picking?.material_id}
        disabled={saving}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        onPick={(id) => {
          if (picking) choose(picking.key, id === picking.default_material_id ? null : id);
          setAnchor(null); setPicking(null);
        }}
      />
    </Box>
  );
};

export default FullPartsPanel;
