import React, { useMemo, useState } from 'react';
import { Box, Button, Checkbox, FormControlLabel, Popover, Typography } from '@mui/material';
import { buildComparisonModel, bestValueColor, comparisonTableColumns, type ComparisonColumn } from './comparisonModel';

const TH = { px: 1.1, py: 0.65, fontSize: 10, color: 'var(--text-3)', fontWeight: 700, whiteSpace: 'nowrap', textAlign: 'right', borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)' } as const;
const TD = { px: 1.1, py: 0.5, fontSize: 12, whiteSpace: 'nowrap', textAlign: 'right', borderBottom: '1px solid var(--app-bg)', fontFamily: 'monospace', color: 'var(--text-1)' } as const;
export interface SharedComparisonColumn<T> extends ComparisonColumn<T> {
  format?: (value: unknown, row: T) => React.ReactNode;
  title?: (value: unknown, row: T) => string | undefined;
  noColor?: (row: T, rows: T[]) => boolean;
  cellSx?: (row: T) => Record<string, unknown>;
  cellColor?: (row: T) => string | undefined;
  headerTitle?: string;
}
export interface SharedComparisonTableProps<T> {
  rows: T[];
  columns: SharedComparisonColumn<T>[];
  rowKey: (row: T) => string;
  rowLabel: (row: T) => React.ReactNode;
  empty?: React.ReactNode;
  labelHeader?: string;
  /** Optional existing actions, selection controls, or parity-specific row cells. */
  renderRowStart?: (row: T) => React.ReactNode;
  renderRowEnd?: (row: T) => React.ReactNode;
  renderHeadEnd?: () => React.ReactNode;
  /** Rendered above a row (a full-width `<tr>` or a fragment of them): group headers.  `columnCount` is the number of cells of a row. */
  renderGroupHeader?: (row: T, index: number, rows: T[], columnCount: number) => React.ReactNode;
  /** A colour accent on the left edge of the row's first cell (one colour per group). */
  rowAccent?: (row: T) => string | undefined;
  /** A click on a row (not on its own buttons: the end cell stops the click) - Development opens the motor of the row. */
  onRowClick?: (row: T) => void;
  renderSupplementaryRows?: (row: T, model: ReturnType<typeof buildComparisonModel<T>>) => React.ReactNode;
  renderAfterRows?: React.ReactNode;
  /** Explicit column subset preserves caller-specific picker state and defaults. */
  visibleKeys?: string[];
  renderCell?: (row: T, column: SharedComparisonColumn<T>, color: 'best' | 'worst' | null) => React.ReactNode;
  sameValue?: (a: unknown, b: unknown) => boolean;
  enableColumnPicker?: boolean;
  columnPickerTitle?: string;
  columnPickerLabel?: string;
  showAllInputs?: boolean;
  renderLabelCell?: (row: T) => React.ReactNode;
  tableSx?: Record<string, unknown>;
  headerCellSx?: Record<string, unknown>;
  bodyCellSx?: Record<string, unknown>;
  inputHeaderColor?: string;
  resultHeaderColor?: string;
  labelHeaderSx?: Record<string, unknown>;
  unitGap?: (unit: string) => string;
  unitColor?: string;
  unitFontWeight?: number;
  overflow?: 'auto' | 'visible' | 'hidden';
}
/** Common variants-as-rows table skin, unit headers, differing inputs, and best-value semantics. */
export function SharedComparisonTable<T>({ rows, columns, rowKey, rowLabel, empty, labelHeader = 'Variant', renderRowStart, renderRowEnd, renderHeadEnd, renderGroupHeader, rowAccent, onRowClick, renderSupplementaryRows, renderAfterRows, visibleKeys, renderCell, sameValue, enableColumnPicker, columnPickerTitle = 'Columns', columnPickerLabel = 'Choose visible columns', showAllInputs, renderLabelCell, tableSx, headerCellSx, bodyCellSx, inputHeaderColor, resultHeaderColor, labelHeaderSx, unitGap = () => ' ', unitColor = 'var(--text-3)', unitFontWeight = 500, overflow = 'auto' }: SharedComparisonTableProps<T>) {
  const model = useMemo(() => buildComparisonModel(rows, columns, sameValue), [rows, columns, sameValue]);
  const candidates = comparisonTableColumns(columns, model, showAllInputs) as SharedComparisonColumn<T>[];
  const [pickedKeys, setPickedKeys] = useState<string[] | null>(null);
  const [pickerAnchor, setPickerAnchor] = useState<HTMLElement | null>(null);
  const baseKeys = visibleKeys ?? candidates.map((c) => c.key);
  const visible = columns.filter((c) => baseKeys.includes(c.key) && (!enableColumnPicker || pickedKeys === null || pickedKeys.includes(c.key)));
  if (!rows.length) return <>{empty ?? null}</>;
  return <Box>{enableColumnPicker && <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 0.5 }}><Button size="small" onClick={(e) => setPickerAnchor(e.currentTarget)}>{columnPickerTitle} ({visible.length})</Button><Popover open={!!pickerAnchor} anchorEl={pickerAnchor} onClose={() => setPickerAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}><Box sx={{ p: 1.2, maxHeight: 360, overflowY: 'auto', minWidth: 220 }}><Typography sx={{ fontSize: 11, fontWeight: 700, mb: 0.5 }}>{columnPickerLabel}</Typography>{candidates.map((c) => <FormControlLabel key={c.key} sx={{ display: 'flex', m: 0 }} control={<Checkbox size="small" checked={(pickedKeys ?? baseKeys).includes(c.key)} onChange={(e) => setPickedKeys((prev) => { const active = prev ?? baseKeys; return e.target.checked ? [...active, c.key] : active.filter((k) => k !== c.key); })} />} label={<Typography sx={{ fontSize: 11 }}>{c.label}{c.unit ? ` (${c.unit})` : ''}</Typography>} />)}</Box></Popover></Box>}<Box sx={{ overflow }}><Box component="table" sx={{ borderCollapse: 'collapse', width: '100%', ...tableSx }}>
    <Box component="thead"><Box component="tr"><Box component="th" sx={{ ...TH, ...headerCellSx, ...labelHeaderSx, textAlign: 'left' }}>{labelHeader}</Box>{visible.map((c) => <Box component="th" key={c.key} title={c.headerTitle} sx={{ ...TH, ...headerCellSx, color: c.kind === 'input' && model.differingInputKeys.includes(c.key) ? inputHeaderColor ?? '#fbbf24' : c.kind === 'result' ? resultHeaderColor ?? '#4ade80' : 'var(--text-2)' }}>{c.label}{c.unit && <Box component="span" sx={{ color: unitColor, fontWeight: unitFontWeight }}>{unitGap(c.unit)}{c.unit}</Box>}</Box>)}{renderHeadEnd?.()}</Box></Box>
    <Box component="tbody">{rows.map((row, rowIndex) => <React.Fragment key={rowKey(row)}>{renderGroupHeader?.(row, rowIndex, rows, visible.length + 1 + (renderRowStart ? 1 : 0) + (renderRowEnd ? 1 : 0))}<Box component="tr" onClick={onRowClick ? () => onRowClick(row) : undefined} sx={{ '&:hover': { bgcolor: 'var(--panel-2)' }, ...(onRowClick ? { cursor: 'pointer' } : {}) }}>{renderRowStart?.(row)}{renderLabelCell ? renderLabelCell(row) : <Box component="td" sx={{ ...TD, ...bodyCellSx, textAlign: 'left', fontFamily: 'inherit', ...(rowAccent?.(row) ? { boxShadow: `inset 4px 0 0 ${rowAccent(row)}` } : {}) }}>{rowLabel(row)}</Box>}{visible.map((c) => { const color = c.noColor?.(row, rows) ? null : bestValueColor(c.value(row), c.better, model.extents[c.key], c.tolerance); const cellColor = color === 'best' ? '#4ade80' : color === 'worst' ? '#f87171' : c.cellColor?.(row) ?? (c.kind === 'input' && model.differingInputKeys.includes(c.key) ? '#fbbf24' : 'var(--text-1)'); return <Box component="td" key={c.key} title={c.title?.(c.value(row), row)} sx={{ ...TD, ...bodyCellSx, ...c.cellSx?.(row), color: cellColor, fontWeight: color ? 700 : 400 }}>{renderCell ? renderCell(row, c, color) : c.format?.(c.value(row), row) ?? (c.value(row) == null || c.value(row) === '' ? '-' : String(c.value(row)))}</Box>; })}{renderRowEnd?.(row)}</Box>{renderSupplementaryRows?.(row, model)}</React.Fragment>)}{renderAfterRows}</Box>
  </Box></Box></Box>;
}
