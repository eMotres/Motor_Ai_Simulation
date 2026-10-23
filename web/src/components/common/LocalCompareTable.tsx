/**
 * LocalCompareTable — the Configure tab's "Saved configurations" stack, as a
 * component any physics tab can host.
 *
 * User 2026-09-07: *"make a local comparison of the thermal simulation
 * parameters, just like in Configure; do the same in mechanics"*.  The Compare
 * tab is the engineer's library — server-side, permanent, every physics in one
 * table.  What was missing is the thing the Configure tab has had since
 * 2026-08-25: press the blue button and the variant you are looking at becomes
 * a ROW right here, so two cooling designs (or two fits) can be read against
 * each other without leaving the tab you are tuning on.
 *
 * The look is Configure's, deliberately to the pixel: the same TH/TD, amber for
 * what was SET, green for what came OUT, green = best · red = worst, a pencil
 * to rename, a ✕ to drop, "Clear all".  One action, one look, wherever it is.
 *
 * Two things it does that Configure's hand-rolled table does not:
 *
 *   • an input IDENTICAL in every row is not a column.  Twenty-two cooling and
 *     operating-point inputs would push the temperatures off the right-hand
 *     edge to say "40 °C" twenty times; they collapse into one "same for all"
 *     line whose tooltip lists them, so the table shows what DIFFERS — which is
 *     the only reason to put two variants side by side.
 *   • every result cell carries its Δ against the first row in its tooltip.
 *     The colours say which is best; the tooltip says by how much.
 *
 * One short line + a tooltip everywhere, never a paragraph (project UI rule).
 *
 * It knows nothing about temperatures or stresses: rows and columns are given,
 * so the Thermal and Mechanical tabs (and whatever comes next) get the same
 * table without a second copy of it.
 */
import React, { useMemo, useState } from 'react';
import { Alert, Box, Button, IconButton, Tooltip, Typography } from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';

import { TextPromptDialog } from './PromptDialogs';
import type { TextPromptState } from './PromptDialogs';
import type { LocalRow } from '../compare/resultRows';
import { SharedComparisonTable, type SharedComparisonColumn } from '../compare/SharedComparisonTable';
import { buildComparisonModel } from '../compare/comparisonModel';
import { localCellText, localCellValue, localComparisonColumns, type LocalColumnDef } from './localComparisonModel';

/* The row shape is the row BUILDERS' (compare/resultRows), not a second
   definition of the same thing: what a stacked variant contains is one
   contract, pinned by one test. */
export type { LocalRow };

/** One column of the table.
 *
 *  `kind` says which half of the row it reads — `input` (what was set: amber,
 *  collapsible when identical) or `result` (what came out: green, best/worst
 *  coloured).  `better` is what "best" MEANS for this quantity; a column
 *  without it is never coloured, which is the honest state for a temperature
 *  nobody has decided the sign of and for text. */
export type ColumnDef = LocalColumnDef;

/* ── Configure's table skin, copied so the two read as one control ────────── */
const TH = {
  px: 1.25, py: 0.7, fontSize: 10, color: 'var(--text-3)', fontWeight: 700,
  textTransform: 'uppercase', letterSpacing: '0.03em', whiteSpace: 'nowrap',
  textAlign: 'right', borderBottom: '1px solid var(--line-soft)',
  bgcolor: 'var(--panel-2)',
} as const;
const TD = {
  px: 1.25, py: 0.5, fontSize: 12, whiteSpace: 'nowrap', textAlign: 'right',
  borderBottom: '1px solid var(--app-bg)', fontFamily: 'monospace',
  color: 'var(--text-1)',
} as const;

export interface LocalCompareTableProps {
  title: string;
  rows: LocalRow[];
  columns: ColumnDef[];
  onRemove: (id: string) => void;
  onClear: () => void;
  /** absent = the rows cannot be renamed (no pencil is drawn) */
  onRename?: (id: string, name: string) => void;
  /** the empty state's one line — what pressing the button will do */
  emptyHint?: React.ReactNode;
}

const LocalCompareTable: React.FC<LocalCompareTableProps> = ({
  title, rows, columns, onRemove, onClear, onRename, emptyHint,
}) => {
  const [askName, setAskName] = useState<TextPromptState | null>(null);

  /* ── which columns there are at all ──────────────────────────────────────
     A column no row carries is not a column: printing "—" down a whole column
     is a quantity this machine does not have, said fifteen times. */
  const sharedColumns: SharedComparisonColumn<LocalRow>[] = useMemo(() => localComparisonColumns(rows, columns), [columns, rows]);
  const model = useMemo(() => buildComparisonModel(rows, sharedColumns,
    (a, b) => String(a ?? '') === String(b ?? '')), [rows, sharedColumns]);
  const orderedSharedColumns = [...sharedColumns.filter((c) => c.kind === 'input'), ...sharedColumns.filter((c) => c.kind === 'result')];
  const same = columns.filter((c) => c.kind === 'input' && rows.some((r) => localCellValue(r, c) !== undefined)
    && !model.differingInputKeys.includes(`input:${c.key}`));

  const rename = (r: LocalRow) => setAskName({
    title: 'Rename variant', label: 'Name', initial: r.name,
    onSubmit: (v) => { const n = v.trim(); if (n) onRename?.(r.id, n); },
  });
  /* The inputs every row agrees on — one line, the full list in the tooltip
     (never a paragraph on a page). */
  const sameBits = rows.length
    ? same.map((c) => `${c.label} ${localCellText(localCellValue(rows[0], c), c)}${c.unit ? ` ${c.unit}` : ''}`)
    : [];
  const sameShort = sameBits.slice(0, 4).join(' · ')
    + (sameBits.length > 4 ? ` · +${sameBits.length - 4} more` : '');

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75 }}>
        <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>
          {title}
        </Typography>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          ({rows.length}){rows.length > 1 ? ' — green = best · red = worst' : ''}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {rows.length > 0 && (
          <Tooltip title="Drop every variant stacked here. It does not touch the Compare tab — rows sent there are a separate, permanent library.">
            <Button onClick={onClear} size="small"
              sx={{ fontSize: 11, textTransform: 'none', color: '#7f1d1d' }}>
              Clear all
            </Button>
          </Tooltip>
        )}
      </Box>

      {rows.length === 0 ? (
        <Alert severity="info" sx={{ fontSize: 12 }}>
          {emptyHint ?? <>Press <b>Add to comparison</b> to stack variants here.</>}
        </Alert>
      ) : (
        <>
          {sameBits.length > 0 && (
            <Tooltip title={`Identical in every row, so they are not columns — the table shows what DIFFERS. In full: ${sameBits.join(' · ')}.`}>
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 0.5,
                fontFamily: 'monospace', cursor: 'help', display: 'inline-block',
                borderBottom: '1px dotted var(--text-4)' }}>
                same for all: {sameShort}
              </Typography>
            </Tooltip>
          )}
          <SharedComparisonTable rows={rows} columns={orderedSharedColumns} rowKey={(r) => r.id} labelHeader="Variant"
            sameValue={(a, b) => String(a ?? '') === String(b ?? '')}
            headerCellSx={TH} bodyCellSx={TD}
            unitColor="var(--line)" unitFontWeight={400}
            rowLabel={(r) => <><Tooltip title={`Stacked ${r.at ? new Date(r.at).toLocaleString() : 'earlier'}${r === rows[0] ? ' — every Δ in this table is measured against this row.' : '.'}`}><Box component="span" sx={{ color: r === rows[0] ? '#60a5fa' : 'var(--text-1)', fontWeight: 600, cursor: 'help' }}>{r.name}</Box></Tooltip>{onRename && <IconButton size="small" onClick={() => rename(r)} title="Rename" sx={{ color: 'var(--text-3)', p: 0.2, ml: 0.5, fontSize: 12 }}>✎</IconButton>}</>}
            renderHeadEnd={() => <Box component="th" sx={{ ...TH, textAlign: 'center' }}>✕</Box>}
            renderRowEnd={(r) => <Box component="td" sx={{ ...TD, textAlign: 'center' }}><IconButton size="small" onClick={() => onRemove(r.id)} title="Remove this variant" sx={{ color: 'var(--text-3)', p: 0.25, '&:hover': { color: '#f87171' } }}><DeleteOutlineIcon sx={{ fontSize: 15 }} /></IconButton></Box>} />
        </>
      )}

      <TextPromptDialog state={askName} onClose={() => setAskName(null)} />
    </Box>
  );
};

export default LocalCompareTable;
