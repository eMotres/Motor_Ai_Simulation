/** Pure comparison row model shared by the Compare, local and Development tables. */
export type ComparisonDirection = 'hi' | 'lo';
export interface ComparisonColumn<T = Record<string, unknown>> {
  key: string;
  label: string;
  unit?: string;
  kind: 'input' | 'result';
  better?: ComparisonDirection;
  value: (row: T) => unknown;
  identityValue?: (row: T) => unknown;
  rankValues?: (rows: T[]) => unknown[];
  tolerance?: number;
}
export interface ComparisonModel<T> {
  inputColumns: ComparisonColumn<T>[];
  resultColumns: ComparisonColumn<T>[];
  extents: Record<string, { min: number; max: number } | null>;
  differingInputKeys: string[];
}
export function comparisonNumber(value: unknown): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (typeof value !== 'string' || value.trim() === '') return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}
export function buildComparisonModel<T>(rows: T[], columns: ComparisonColumn<T>[],
    sameValue: (a: unknown, b: unknown) => boolean = Object.is): ComparisonModel<T> {
  const inputColumns = columns.filter((c) => c.kind === 'input' && rows.some((r) => c.value(r) !== undefined));
  const resultColumns = columns.filter((c) => c.kind === 'result' && rows.some((r) => c.value(r) !== undefined));
  const differingInputKeys = inputColumns.filter((c) => {
    const values = rows.map((row) => c.identityValue ? c.identityValue(row) : c.value(row));
    return values.some((v) => !sameValue(v, values[0]));
  }).map((c) => c.key);
  const extents: ComparisonModel<T>['extents'] = {};
  for (const c of resultColumns) {
    const values = (c.rankValues ? c.rankValues(rows) : rows.map(c.value)).map(comparisonNumber).filter((n): n is number => n !== undefined);
    extents[c.key] = values.length ? { min: Math.min(...values), max: Math.max(...values) } : null;
  }
  return { inputColumns: inputColumns.filter((c) => differingInputKeys.includes(c.key)), resultColumns, extents, differingInputKeys };
}
export function comparisonTableColumns<T>(columns: ComparisonColumn<T>[], model: ComparisonModel<T>, showAllInputs = false): ComparisonColumn<T>[] {
  const inputs = showAllInputs ? columns.filter((c) => c.kind === 'input') : model.inputColumns;
  return [...inputs, ...model.resultColumns];
}
export function shouldSuppressMixedBasisColor(value: unknown, activeBasis: boolean, libraryHasFullBasis: boolean): boolean {
  return comparisonNumber(value) !== undefined && activeBasis && libraryHasFullBasis;
}
export function bestValueColor(value: unknown, direction: ComparisonDirection | undefined,
    extent: { min: number; max: number } | null | undefined, tolerance = 1e-12): 'best' | 'worst' | null {
  const n = comparisonNumber(value);
  if (n === undefined || !direction || !extent || extent.min === extent.max) return null;
  if (Math.abs(n - (direction === 'hi' ? extent.max : extent.min)) < tolerance) return 'best';
  if (Math.abs(n - (direction === 'hi' ? extent.min : extent.max)) < tolerance) return 'worst';
  return null;
}
