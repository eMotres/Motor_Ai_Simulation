/** The cost estimate as ONE table (owner 2026-11-02: «давай посмотрим, как будет выглядеть, но с возможностью откатить»):
 *  one row per iteration (tooling, motors x price per motor, bench + testing, shipping DHL, duration, iteration total), then
 *  subtotal, contingency and the total (the only cell with a currency choice).  Edits go through the same estimate and the
 *  same autosave as the Steps view.  An empty cell shows the suggestion in grey: Enter or the check accepts it; hover a
 *  cell for its source, click the note icon to edit the note and the stage's weeks.  Pure numbers: costEstimate.ts. */
import { useEffect, useRef, useState } from 'react';
import {
  Box, Button, IconButton, MenuItem, Popover, Select, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditNoteIcon from '@mui/icons-material/EditNote';
import AddIcon from '@mui/icons-material/Add';
import LocalShippingOutlinedIcon from '@mui/icons-material/LocalShippingOutlined';
import {
  acceptSuggestion, addIteration as addIterationOf, dhlDetails, editLine, formatMoney, iterationWeeks, lineNeeded, originChip, setIterationField, setLineWeeks, setSetting,
  removeIteration as removeIterationOf, setWeeksOverride, suggestionAt, tableModel, txt, weeksText, WEEKS_MAX, WEEKS_OVERRIDE_MAX,
  type Bench, type CostEstimate, type CostLang, type CostSchema, type CostSuggestions, type LineId, type LineRef, type SuggestionCell, type T,
} from '../../costEstimate';

const money = (v: number | null | undefined): string => (v === null || v === undefined ? '' : formatMoney({ EUR: v }));
const plain = (v: number | null | undefined): string => (v === null || v === undefined ? '' : String(Number(Number(v).toFixed(2))));
const parse = (s: string): number | null | 'partial' => {
  const x = s.trim().replace(',', '.').replace(/\s/g, '');
  if (x === '') return null;
  if (!/^-?\d*\.?\d*$/.test(x) || x === '-' || x === '.') return 'partial';
  const n = Number(x);
  return Number.isFinite(n) ? n : 'partial';
};

/** One editable number of the table: shows what is typed while focused, commits each valid value (autosave debounces);
 *  an empty cell shows `suggestion` in grey - Enter (or the check) accepts it. */
function NumCell({ value, onCommit, suggestion, onAccept, acceptLabel = '✓', ariaLabel, min = 0, max = 1e12, int = false, width = 104, disabled, unit }: {
  value: number | null | undefined; onCommit: (v: number | null) => void; suggestion?: string; onAccept?: () => void; acceptLabel?: string; ariaLabel: string;
  min?: number; max?: number; int?: boolean; width?: number; disabled?: boolean; unit?: string;
}) {
  const [text, setText] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  const shown = text ?? plain(value);
  const empty = value === null || value === undefined;
  const canAccept = empty && !!suggestion && !!onAccept && !disabled;
  const commit = (s: string) => {
    const n = parse(s);
    if (n === 'partial') { setBad(true); return; }
    if (n !== null && (n < min || n > max || (int && !Number.isInteger(n)))) { setBad(true); return; }
    setBad(false);
    if (n !== (value ?? null)) onCommit(n);
  };
  return <TextField size="small" value={shown} disabled={disabled} error={bad} placeholder={empty ? suggestion : undefined}
    onFocus={() => setText(plain(value))}
    onBlur={() => { setText(null); setBad(false); }}
    onChange={(e) => { setText(e.target.value); commit(e.target.value); }}
    onKeyDown={(e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if ((text ?? '').trim() === '' && canAccept) { onAccept!(); setText(null); return; }
      commit(text ?? '');
    }}
    slotProps={{
      htmlInput: { 'aria-label': ariaLabel, inputMode: 'decimal', autoComplete: 'off', style: { textAlign: 'right' } },
      input: {
        endAdornment: canAccept
          ? <Tooltip title={suggestion}><IconButton size="small" tabIndex={-1} aria-label={`${ariaLabel}: ${acceptLabel} ${suggestion}`} onClick={() => onAccept!()} sx={{ p: 0.25, mr: -0.75 }}><CheckIcon sx={{ fontSize: 15 }} /></IconButton></Tooltip>
          : unit ? <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-3)', ml: 0.5 }}>{unit}</Typography> : undefined,
      },
    }}
    sx={{ width, '& .MuiInputBase-root': { borderRadius: '8px', pr: 1 }, '& .MuiInputBase-input': { fontSize: 13.5, fontVariantNumeric: 'tabular-nums', py: 0.75, px: 1 },
      '& .MuiInputBase-input::placeholder': { color: 'var(--text-3)', opacity: 1, fontStyle: 'italic' } }} />;
}

export interface CellCtx { estimate: CostEstimate; schema: CostSchema; lang: CostLang; t: T; who: string; today: string; suggestions: CostSuggestions | null; change: (next: CostEstimate) => void;
  /** put a manual shipping line back to automatic DHL (the page asks the server for the numbers first) */ onDhlAuto?: (ref: LineRef) => void }

/** The hover text of a line cell: where its number comes from (or the suggestion / why none), the note, the weeks. */
function cellTip(ctx: CellCtx, ref: LineRef, sug: SuggestionCell | undefined): string[] {
  const { estimate, t, schema, lang } = ctx;
  const it = estimate.iterations.find((x) => x.index === ref.iteration)!;
  const line = it.lines[ref.line];
  const chip = originChip(line, it, ref.line, t);
  const excluded = txt(schema.shipping?.excluded, lang);
  const out: string[] = [];
  if (line.status === 'missing' && sug?.amount !== undefined) {
    out.push(`${t('costEstimate.table.suggested')}: ${money(sug.amount)}`);
    out.push(sug.source === 'dhl' ? t('costEstimate.origin.dhl', { date: sug.source_ref?.dhl?.quoted_on ?? '?', from: sug.source_ref?.dhl?.from ?? '?', to: sug.source_ref?.dhl?.to ?? '?', weight: weeksText(sug.source_ref?.dhl?.weight_kg ?? null), margin: weeksText(sug.source_ref?.dhl?.margin_pct ?? 0) }) : (sug.origin ?? ''));
  } else out.push(`${chip.label}: ${chip.tip}`);
  if (line.status === 'missing' && sug?.hint) out.push(sug.hint);
  if (line.status === 'filled' && line.source === 'dhl' && line.hint) out.push(line.hint);      // an automatic line that could not be recomputed
  if (ref.line === 'shipping') {
    const d = line.source === 'dhl' ? line.source_ref?.dhl : line.status === 'missing' ? sug?.source_ref?.dhl : undefined;
    out.push(...(d ? dhlDetails(d, it.motors, t, excluded) : [excluded]));
  }
  if (line.note) out.push(`${t('costEstimate.note')}: ${line.note}`);
  out.push(`${t(`costEstimate.durations.${ref.line}`)}: ${line.weeks === null || line.weeks === undefined ? '—' : t('costEstimate.weeksN', { n: weeksText(line.weeks) })}`);
  return out.filter(Boolean);
}

/** A money cell: the amount (grey suggestion when empty) and a note icon opening the source, note and weeks. */
function LineCell({ ctx, at, label, perMotor = false, disabled }: { ctx: CellCtx; at: LineRef; label: string; perMotor?: boolean; disabled?: boolean }) {
  const { estimate, t, who, today, change, suggestions } = ctx;
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const it = estimate.iterations.find((x) => x.index === at.iteration)!;
  const line = it.lines[at.line];
  const sug = suggestionAt(suggestions, at);
  const tip = cellTip(ctx, at, sug);
  const accept = () => change(acceptSuggestion(estimate, at, sug, who, today));
  const aria = `${t('costEstimate.iteration', { n: at.iteration })} · ${label}`;
  return <Stack direction="row" alignItems="center" spacing={0.25}>
    <Tooltip title={<Box sx={{ whiteSpace: 'pre-line' }}>{tip.join('\n')}</Box>} placement="top" enterDelay={400}>
      <span><NumCell value={line.amount} disabled={disabled} ariaLabel={aria + (perMotor ? ` (${t('costEstimate.perMotor')})` : '')}
        suggestion={sug?.amount !== undefined ? plain(sug.amount) : undefined} onAccept={sug?.amount !== undefined ? accept : undefined} acceptLabel={t('costEstimate.table.accept')}
        onCommit={(n) => change(editLine(estimate, at, { amount: n }, who, today))} unit={perMotor ? '€/m' : '€'} /></span>
    </Tooltip>
    {at.line === 'shipping' && line.status === 'filled' && (line.source === 'dhl'
      ? <Tooltip title={line.hint || t('costEstimate.dhl.autoOnTip')}><LocalShippingOutlinedIcon data-testid="dhl-auto-on" titleAccess={t('costEstimate.dhl.autoOnTip')} sx={{ fontSize: 17, mx: 0.4, color: line.hint ? 'warning.main' : 'primary.main' }} /></Tooltip>
      : ctx.onDhlAuto && estimate.settings.ship_to
        ? <Tooltip title={t('costEstimate.dhl.autoTip')}><IconButton size="small" tabIndex={-1} data-testid="dhl-auto-set" aria-label={`${aria}: ${t('costEstimate.dhl.autoTip')}`} onClick={() => ctx.onDhlAuto!(at)}
          sx={{ p: 0.4, color: 'var(--text-4)' }}><LocalShippingOutlinedIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip> : null)}
    <Tooltip title={t('costEstimate.table.noteTip')}><IconButton size="small" tabIndex={-1} aria-label={`${aria}: ${t('costEstimate.table.noteTip')}`} onClick={(e) => setAnchor(e.currentTarget)}
      sx={{ p: 0.4, color: line.note ? 'primary.main' : 'var(--text-4)' }}><EditNoteIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
    <Popover open={!!anchor} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
      <Stack spacing={1} sx={{ p: 1.5, width: 300, maxWidth: '86vw' }}>
        <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{aria}</Typography>
        {tip.slice(0, -1).filter((s) => !s.startsWith(`${t('costEstimate.note')}:`)).map((s, i) => <Typography key={i} sx={{ fontSize: 12, color: 'var(--text-2)' }}>{s}</Typography>)}
        <TextField size="small" label={t('costEstimate.note')} value={line.note} onChange={(e) => change(editLine(estimate, at, { note: e.target.value }, who, today))}
          slotProps={{ htmlInput: { maxLength: 500 } }} multiline maxRows={4} />
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)', flex: 1 }}>{t(`costEstimate.durations.${at.line}`)}</Typography>
          <NumCell value={line.weeks} max={WEEKS_MAX} width={96} unit={t('costEstimate.weeksUnit')} ariaLabel={`${aria}: ${t(`costEstimate.durations.${at.line}`)}`}
            suggestion={sug?.weeks !== undefined ? weeksText(sug.weeks) : undefined} onAccept={sug?.weeks !== undefined ? accept : undefined} acceptLabel={t('costEstimate.table.accept')}
            onCommit={(n) => change(setLineWeeks(estimate, at, n))} />
        </Stack>
      </Stack>
    </Popover>
  </Stack>;
}

export default function CostEstimateTable({ ctx, displayCurrency, onDisplayCurrency, fxText }: {
  ctx: CellCtx; displayCurrency: string; onDisplayCurrency: (cur: string) => void; fxText: string | null;
}) {
  const { estimate, schema, lang, t, change } = ctx;
  const model = tableModel(estimate, displayCurrency);
  const lineLabel = (id: LineId) => txt(schema.lines.find((l) => l.id === id)?.label, lang);
  const benchField = schema.iteration_fields.find((f) => f.id === 'bench');
  const lastRow = useRef<number>(model.rows.length);
  const tableRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    // after "+ iteration": the first cell of the new row takes the focus
    if (model.rows.length > lastRow.current) tableRef.current?.querySelector<HTMLInputElement>(`[data-row="${model.rows.length}"] input`)?.focus();
    lastRow.current = model.rows.length;
  }, [model.rows.length]);
  const head = (key: string, tip?: string) => <TableCell sx={{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
    {tip ? <Tooltip title={tip}><span>{t(`costEstimate.table.col.${key}`)}</span></Tooltip> : t(`costEstimate.table.col.${key}`)}</TableCell>;
  const num = { fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', textAlign: 'right' } as const;
  const durTip = (r: (typeof model.rows)[number]) => {
    const w = r.weeks;
    return ([['tooling', w.tooling], ['manufacturing', w.manufacturing], ['testing', w.testing], ['shipping', w.shipping]] as Array<[LineId, number | null]>)
      .filter(([id]) => id !== 'testing' || r.testingNeeded)
      .map(([id, v]) => `${t(`costEstimate.durations.${id}`)}: ${v === null ? '—' : t('costEstimate.weeksN', { n: weeksText(v) })}`)
      .concat([t('costEstimate.durationSum', { n: weeksText(w.sum) }), w.override !== null ? t('costEstimate.weeksOverridden', { n: weeksText(w.override) }) : t('costEstimate.weeksOverrideTip')]).join('\n');
  };

  return <Box data-testid="cost-table" sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', p: { xs: 1, md: 2 }, minWidth: 0 }}>
    <Box ref={tableRef} sx={{ overflowX: 'auto', minWidth: 0 }}>
      <Table size="small" sx={{ minWidth: 960, '& td, & th': { px: 0.75, borderColor: 'var(--line-soft)' } }}>
        <TableHead><TableRow>
          {head('iteration')}{head('tooling', txt(schema.lines[0].help, lang))}{head('motors', txt(schema.lines[1].help, lang))}{head('testing', txt(schema.lines[2].help, lang))}
          {head('shipping', txt(schema.lines[3].help, lang) + ' ' + txt(schema.shipping?.excluded, lang))}{head('duration', txt(schema.durations?.help, lang))}{head('total')}
        </TableRow></TableHead>
        <TableBody>
          {model.rows.map((r) => {
            const it = estimate.iterations.find((x) => x.index === r.index)!;
            const at = (line: LineId): LineRef => ({ iteration: r.index, line });
            const w = iterationWeeks(it);
            return <TableRow key={r.index} data-row={r.index}>
              <TableCell sx={{ whiteSpace: 'nowrap' }}>
                <Stack direction="row" alignItems="center" spacing={0.25}>
                  <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{t('costEstimate.iterationShort', { n: r.index })}</Typography>
                  <Tooltip title={t('costEstimate.table.remove', { n: r.index })}><span><IconButton size="small" disabled={!model.canRemove} aria-label={t('costEstimate.table.remove', { n: r.index })}
                    onClick={() => change(removeIterationOf(estimate, r.index))} sx={{ p: 0.4 }}><DeleteOutlineIcon sx={{ fontSize: 16 }} /></IconButton></span></Tooltip>
                </Stack>
              </TableCell>
              <TableCell><LineCell ctx={ctx} at={at('tooling')} label={lineLabel('tooling')} /></TableCell>
              <TableCell>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <NumCell value={r.motors} int min={1} max={50} width={56} ariaLabel={`${t('costEstimate.iteration', { n: r.index })} · ${t('costEstimate.table.col.motors')}`}
                    onCommit={(n) => n !== null && change(setIterationField(estimate, r.index, 'motors', n))} />
                  <Typography component="span" sx={{ color: 'var(--text-3)' }}>×</Typography>
                  <LineCell ctx={ctx} at={at('manufacturing')} label={lineLabel('manufacturing')} perMotor />
                </Stack>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)', ...num, textAlign: 'left', pl: 0.5 }}>{r.manufacturing === null ? '' : `= ${money(r.manufacturing)}`}</Typography>
              </TableCell>
              <TableCell>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <Select size="small" value={r.bench} onChange={(e) => change(setIterationField(estimate, r.index, 'bench', e.target.value as Bench))}
                    inputProps={{ 'aria-label': `${t('costEstimate.iteration', { n: r.index })} · ${txt(benchField?.label, lang)}` }}
                    sx={{ minWidth: 92, fontSize: 13, '& .MuiSelect-select': { py: 0.75 } }}>
                    {(benchField?.options ?? []).map((o) => <MenuItem key={o} value={o}>{benchField?.option_labels?.[o] ? txt(benchField.option_labels[o], lang) : o}</MenuItem>)}
                  </Select>
                  <LineCell ctx={ctx} at={at('testing')} label={lineLabel('testing')} disabled={!lineNeeded(it, 'testing')} />
                </Stack>
              </TableCell>
              <TableCell><LineCell ctx={ctx} at={at('shipping')} label={lineLabel('shipping')} /></TableCell>
              <TableCell>
                <Tooltip title={<Box sx={{ whiteSpace: 'pre-line' }}>{durTip(r)}</Box>} placement="top" enterDelay={300}>
                  <span><NumCell value={it.weeks_override} max={WEEKS_OVERRIDE_MAX} width={92} unit={t('costEstimate.weeksUnit')} suggestion={weeksText(w.sum)}
                    ariaLabel={`${t('costEstimate.iteration', { n: r.index })} · ${t('costEstimate.table.col.duration')}`}
                    onCommit={(n) => change(setWeeksOverride(estimate, r.index, n))} /></span>
                </Tooltip>
                {w.missing.length > 0 && <Typography sx={{ fontSize: 10.5, color: 'warning.main', whiteSpace: 'nowrap' }}>{t('costEstimate.weeksMissing', { count: w.missing.length })}</Typography>}
              </TableCell>
              <TableCell sx={{ ...num, fontWeight: 600 }}>
                {money(r.total)}
                {r.missing > 0 && <Typography sx={{ fontSize: 10.5, color: 'warning.main' }}>{t('costEstimate.missingCount', { count: r.missing })}</Typography>}
              </TableCell>
            </TableRow>;
          })}
          <TableRow>
            <TableCell colSpan={7} sx={{ borderBottom: 'none' }}>
              <Tooltip title={t('costEstimate.table.addTip')}><span><Button size="small" startIcon={<AddIcon />} disabled={!model.canAdd} onClick={() => change(addIterationOf(estimate, schema))}
                sx={{ textTransform: 'none' }}>{t('costEstimate.table.add')}</Button></span></Tooltip>
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell colSpan={5} sx={{ fontWeight: 600 }}>{t('costEstimate.subtotal')}</TableCell>
            <TableCell sx={num}>{t('costEstimate.weeksN', { n: weeksText(model.weeksTotal) })}</TableCell>
            <TableCell sx={{ ...num, fontWeight: 600 }} data-testid="table-subtotal">{money(model.subtotal)}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell colSpan={5}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Tooltip title={txt(schema.settings.find((f) => f.id === 'contingency_pct')?.help, lang)}><Typography sx={{ fontSize: 13, fontWeight: 600 }}>{t('costEstimate.table.contingency')}</Typography></Tooltip>
                <NumCell value={model.contingencyPct} max={100} width={76} unit="%" ariaLabel={t('costEstimate.table.contingency')}
                  onCommit={(n) => change(setSetting(estimate, schema, 'contingency_pct', n ?? 0))} />
              </Stack>
            </TableCell>
            <TableCell />
            <TableCell sx={num}>{money(model.contingency)}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell colSpan={5}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography sx={{ fontSize: 14, fontWeight: 700 }}>{t('costEstimate.total')}</Typography>
                <Tooltip title={t('costEstimate.table.currencyTip')}>
                  <Select size="small" value={displayCurrency} onChange={(e) => onDisplayCurrency(String(e.target.value))}
                    inputProps={{ 'aria-label': t('costEstimate.table.totalCurrency') }} sx={{ minWidth: 84, fontSize: 13, '& .MuiSelect-select': { py: 0.5 } }}>
                    {schema.currencies.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
                  </Select>
                </Tooltip>
              </Stack>
            </TableCell>
            <TableCell sx={{ ...num, fontWeight: 700 }}>{t('costEstimate.weeksN', { n: weeksText(model.weeksTotal) })}</TableCell>
            <TableCell sx={{ ...num, fontWeight: 700, fontSize: 15 }} data-testid="table-total">
              {model.display.currency !== 'EUR' && model.display.total !== null
                ? <Tooltip title={money(model.total)}><span>{formatMoney({ [model.display.currency]: model.display.total })}</span></Tooltip>
                : money(model.total)}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </Box>
    {model.display.currency !== 'EUR' && <Typography data-testid="table-fx" sx={{ fontSize: 12, color: 'var(--text-2)', mt: 1, textAlign: 'right' }}>
      {model.display.total !== null ? fxText : t('costEstimate.table.noRate', { currency: model.display.currency })}</Typography>}
    {/* a narrow screen sees the total without scrolling the table sideways */}
    <Typography sx={{ display: { xs: 'block', md: 'none' }, mt: 1, fontSize: 13.5, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
      {t('costEstimate.total')}: {model.display.currency !== 'EUR' && model.display.total !== null ? formatMoney({ [model.display.currency]: model.display.total }) : money(model.total)} · {t('costEstimate.weeksN', { n: weeksText(model.weeksTotal) })}
    </Typography>
  </Box>;
}


