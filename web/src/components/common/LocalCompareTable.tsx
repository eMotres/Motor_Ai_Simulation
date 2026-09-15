/**
 * LocalCompareTable — the Configure tab's "Saved configurations" stack, as a
 * component any physics tab can host.
 *
 * User 2026-09-07: *"сделай локальное сравнение по параметрам тепловой
 * симуляции, только как в Configure; так же сделай в механике"*.  The Compare
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
export interface ColumnDef {
  key: string;
  label: string;
  unit?: string;
  /** decimals for a numeric cell (default 1) */
  d?: number;
  better?: 'hi' | 'lo';
  kind: 'input' | 'result';
  /** full control of the cell's text — for booleans and verdicts */
  fmt?: (v: unknown) => string;
}

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

const cellOf = (r: LocalRow, c: ColumnDef): unknown =>
  (c.kind === 'input' ? r.inputs : r.results)[c.key];

/** A real number, or `undefined` — the only values that take part in the
 *  best/worst colouring and in a Δ.  A stored `true` is not a quantity. */
const numOf = (v: unknown): number | undefined =>
  (typeof v === 'number' && Number.isFinite(v) ? v : undefined);

const fixed = (n: number, d: number): string =>
  n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

/** What one cell prints.  An ABSENT value prints "—", which is the truth: this
 *  variant does not have that quantity (a sleeveless rotor has no sleeve
 *  stress, a still-air row has no coolant). */
function text(v: unknown, c: ColumnDef): string {
  if (c.fmt) return c.fmt(v);
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number') return Number.isFinite(v) ? fixed(v, c.d ?? 1) : '—';
  return String(v);
}

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
  const { same, diff, results, ext } = useMemo(() => {
    const present = columns.filter((c) => rows.some((r) => cellOf(r, c) !== undefined));
    const ins = present.filter((c) => c.kind === 'input');
    const res = present.filter((c) => c.kind === 'result');
    const sameCols = rows.length
      ? ins.filter((c) => {
        const first = text(cellOf(rows[0], c), c);
        return rows.every((r) => text(cellOf(r, c), c) === first);
      })
      : ins;
    const diffCols = ins.filter((c) => !sameCols.includes(c));
    /* best/worst per result column, across the rows that HAVE a number there
       (Configure's rule: nothing is coloured while every row agrees). */
    const e: Record<string, { min: number; max: number } | null> = {};
    res.forEach((c) => {
      const ns = rows.map((r) => numOf(cellOf(r, c)))
        .filter((x): x is number => x !== undefined);
      e[c.key] = ns.length ? { min: Math.min(...ns), max: Math.max(...ns) } : null;
    });
    return { same: sameCols, diff: diffCols, results: res, ext: e };
  }, [rows, columns]);

  const colourOf = (c: ColumnDef, v: unknown): string | null => {
    const e = ext[c.key];
    const n = numOf(v);
    if (!e || n === undefined || !c.better || e.min === e.max) return null;
    const best = c.better === 'hi' ? e.max : e.min;
    const worst = c.better === 'hi' ? e.min : e.max;
    if (Math.abs(n - best) < 1e-12) return '#4ade80';
    if (Math.abs(n - worst) < 1e-12) return '#f87171';
    return null;
  };

  /** The cell's own sentence: what it is, and how far it is from the FIRST
   *  row — which is the variant every other one is being judged against. */
  const tipOf = (c: ColumnDef, v: unknown, i: number): string => {
    const shown = typeof v === 'string' ? v : text(v, c);
    const head = `${c.label}${c.unit ? ` (${c.unit})` : ''}: ${shown}`;
    const n = numOf(v);
    const base = rows.length ? numOf(cellOf(rows[0], c)) : undefined;
    if (i === 0 || n === undefined || base === undefined) return head;
    const dv = n - base;
    const d = c.d ?? 1;
    const pct = base !== 0
      ? ` (${dv > 0 ? '+' : ''}${((dv / Math.abs(base)) * 100).toFixed(1)} %)` : '';
    return `${head} · Δ vs "${rows[0].name}": ${dv > 0 ? '+' : ''}${fixed(dv, d)}${pct}`;
  };

  const rename = (r: LocalRow) => setAskName({
    title: 'Rename variant', label: 'Name', initial: r.name,
    onSubmit: (v) => { const n = v.trim(); if (n) onRename?.(r.id, n); },
  });

  /* The inputs every row agrees on — one line, the full list in the tooltip
     (never a paragraph on a page). */
  const sameBits = rows.length
    ? same.map((c) => `${c.label} ${text(cellOf(rows[0], c), c)}${c.unit ? ` ${c.unit}` : ''}`)
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
          <Box sx={{ overflow: 'auto' }}>
            <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
              <Box component="thead"><Box component="tr">
                <Box component="th" sx={{ ...TH, textAlign: 'left' }}>Variant</Box>
                {diff.map((c) => (
                  <Box component="th" key={`i_${c.key}`} sx={{ ...TH, color: '#fbbf24' }}
                    title={`${c.label}${c.unit ? ` (${c.unit})` : ''} — an INPUT, and one that differs between these variants. Inputs identical in every row are in the line above the table.`}>
                    {c.label}
                    {c.unit && (
                      <Box component="span" sx={{ color: 'var(--line)', fontWeight: 400 }}>
                        {' '}{c.unit}
                      </Box>
                    )}
                  </Box>
                ))}
                {results.map((c) => (
                  <Box component="th" key={`r_${c.key}`} sx={{ ...TH, color: '#4ade80' }}
                    title={`${c.label}${c.unit ? ` (${c.unit})` : ''} — a RESULT.${c.better ? ` Green is the best of the stack (${c.better === 'hi' ? 'highest' : 'lowest'}), red the worst.` : ' Not coloured: there is no better or worse for this one.'} Hover a cell for its Δ against the first row.`}>
                    {c.label}
                    {c.unit && (
                      <Box component="span" sx={{ color: 'var(--line)', fontWeight: 400 }}>
                        {' '}{c.unit}
                      </Box>
                    )}
                  </Box>
                ))}
                <Box component="th" sx={{ ...TH, textAlign: 'center' }}>✕</Box>
              </Box></Box>
              <Box component="tbody">
                {rows.map((r, i) => (
                  <Box component="tr" key={r.id} sx={{ '&:hover': { bgcolor: 'var(--panel-2)' } }}>
                    <Box component="td" sx={{ ...TD, textAlign: 'left',
                      fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
                      <Tooltip title={`Stacked ${r.at ? new Date(r.at).toLocaleString() : 'earlier'}${i === 0 ? ' — every Δ in this table is measured against this row.' : '.'}`}>
                        <Box component="span" sx={{ color: i === 0 ? '#60a5fa' : 'var(--text-1)',
                          fontWeight: 600, cursor: 'help' }}>
                          {r.name}
                        </Box>
                      </Tooltip>
                      {onRename && (
                        <IconButton size="small" onClick={() => rename(r)} title="Rename"
                          sx={{ color: 'var(--text-3)', p: 0.2, ml: 0.5, fontSize: 12 }}>
                          ✎
                        </IconButton>
                      )}
                    </Box>
                    {/* The cell hints are NATIVE titles, not MUI tooltips: a
                        full stack is sixteen rows of twenty-five columns, and
                        four hundred poppers is a table that scrolls badly for a
                        hint nobody has hovered yet. */}
                    {diff.map((c) => (
                      <Box component="td" key={`i_${c.key}`} title={tipOf(c, cellOf(r, c), i)}
                        sx={{ ...TD, color: '#fbbf24', cursor: 'help' }}>
                        {text(cellOf(r, c), c)}
                      </Box>
                    ))}
                    {results.map((c) => {
                      const v = cellOf(r, c);
                      const col = colourOf(c, v);
                      return (
                        <Box component="td" key={`r_${c.key}`} title={tipOf(c, v, i)}
                          sx={{ ...TD, cursor: 'help',
                            color: col ?? 'var(--text-1)', fontWeight: col ? 700 : 400 }}>
                          {text(v, c)}
                        </Box>
                      );
                    })}
                    <Box component="td" sx={{ ...TD, textAlign: 'center' }}>
                      <IconButton size="small" onClick={() => onRemove(r.id)}
                        title="Remove this variant"
                        sx={{ color: 'var(--text-3)', p: 0.25, '&:hover': { color: '#f87171' } }}>
                        <DeleteOutlineIcon sx={{ fontSize: 15 }} />
                      </IconButton>
                    </Box>
                  </Box>
                ))}
              </Box>
            </Box>
          </Box>
        </>
      )}

      <TextPromptDialog state={askName} onClose={() => setAskName(null)} />
    </Box>
  );
};

export default LocalCompareTable;
