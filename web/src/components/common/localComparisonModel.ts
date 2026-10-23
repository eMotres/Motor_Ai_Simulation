import type { LocalRow } from '../compare/resultRows';
import type { ComparisonColumn } from '../compare/comparisonModel';

export interface LocalColumnDef {
  key: string;
  label: string;
  unit?: string;
  d?: number;
  better?: 'hi' | 'lo';
  kind: 'input' | 'result';
  fmt?: (value: unknown) => string;
}

export interface LocalComparisonColumn extends ComparisonColumn<LocalRow> {
  format: (value: unknown, row: LocalRow) => string;
  title: (value: unknown, row: LocalRow) => string;
  noColor: (row: LocalRow) => boolean;
  cellSx: () => { cursor: 'help' };
  headerTitle: string;
}

export const localCellValue = (row: LocalRow, column: LocalColumnDef): unknown =>
  (column.kind === 'input' ? row.inputs : row.results)[column.key];

export const localComparisonNumber = (value: unknown): number | undefined =>
  (typeof value === 'number' && Number.isFinite(value) ? value : undefined);

const fixed = (number: number, digits: number): string =>
  number.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function localCellText(value: unknown, column: LocalColumnDef): string {
  if (column.fmt) return column.fmt(value);
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') return Number.isFinite(value) ? fixed(value, column.d ?? 1) : '—';
  return String(value);
}

export function localCellTitle(rows: LocalRow[], column: LocalColumnDef, value: unknown, index: number): string {
  const shown = typeof value === 'string' ? value : localCellText(value, column);
  const head = `${column.label}${column.unit ? ` (${column.unit})` : ''}: ${shown}`;
  const number = localComparisonNumber(value);
  const base = rows.length ? localComparisonNumber(localCellValue(rows[0], column)) : undefined;
  if (index === 0 || number === undefined || base === undefined) return head;
  const delta = number - base;
  const percent = base !== 0 ? ` (${delta > 0 ? '+' : ''}${((delta / Math.abs(base)) * 100).toFixed(1)} %)` : '';
  return `${head} · Δ vs "${rows[0].name}": ${delta > 0 ? '+' : ''}${fixed(delta, column.d ?? 1)}${percent}`;
}

export function localComparisonColumns(rows: LocalRow[], columns: LocalColumnDef[]): LocalComparisonColumn[] {
  return columns.map((column) => ({
    ...column,
    key: `${column.kind}:${column.key}`,
    value: (row) => localCellValue(row, column),
    identityValue: (row) => column.kind === 'input' ? localCellText(localCellValue(row, column), column) : localCellValue(row, column),
    rankValues: (tableRows) => column.kind === 'result' ? tableRows.map((row) => localComparisonNumber(localCellValue(row, column))) : [],
    noColor: (row) => column.kind === 'result' && localComparisonNumber(localCellValue(row, column)) === undefined,
    format: (_value, row) => localCellText(localCellValue(row, column), column),
    title: (_value, row) => localCellTitle(rows, column, localCellValue(row, column), rows.indexOf(row)),
    cellSx: () => ({ cursor: 'help' }),
    headerTitle: column.kind === 'input'
      ? `${column.label}${column.unit ? ` (${column.unit})` : ''} — an INPUT, and one that differs between these variants. Inputs identical in every row are in the line above the table.`
      : `${column.label}${column.unit ? ` (${column.unit})` : ''} — a RESULT.${column.better ? ` Green is the best of the stack (${column.better === 'hi' ? 'highest' : 'lowest'}), red the worst.` : ' Not coloured: there is no better or worse for this one.'} Hover a cell for its Δ against the first row.`,
  }));
}
