// The specification of a CONTROLLER project (controller PR2): 7 steps from the server's data-file schema; every number carries
// its status (requirement / model / measured) and a source; per-build fields hold one answer per build.  Only the project owner
// and admins edit (the server decides: `can_edit`); everybody else reads.  No assistant, no submit flow: internal for now.
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Alert, Box, Button, Checkbox, CircularProgress, FormControlLabel, IconButton, InputAdornment, MenuItem, Paper, Stack, Tab, Tabs, TextField, Tooltip, Typography } from '@mui/material';
import { NARROW } from '../../../lib/narrowLayout';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import HelpTip from '../../../components/common/HelpTip';
import { readView, writeView } from '../../../lib/viewMemory';
import {
  afterSave, answerOf, buildsOf, cellOf, COLUMNS, controllerSpecApi, hasColumns, isConflict, localized, parseNumber, payloadFields, withAnswer, withCell,
  type CtlAnswer, type CtlCell, type CtlField, type CtlSchema, type CtlSpec, type CtlStored, type CtlThreshold, type ValueStatus,
} from '../controllerSpec';

type T = (key: string, args?: Record<string, unknown>) => string;
interface Props { projectId: string; lang: string; ct: T; memoryScope: string | null }

export default function ControllerSpecForm({ projectId, lang, ct, memoryScope }: Props) {
  const [spec, setSpec] = useState<CtlSpec | null>(null);
  const [fields, setFields] = useState<Record<string, CtlStored>>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [step, setStep] = useState(0);
  const [conflict, setConflict] = useState(false);
  const edits = useRef(0);                               // counts edits, so one typed while a save is in flight is not lost

  useEffect(() => {
    let live = true;
    setSpec(null); setError(''); setDirty(false); setConflict(false);
    controllerSpecApi.get(projectId).then((s) => {
      if (!live) return;
      setSpec(s); setFields(s.fields ?? {});
      const remembered = memoryScope ? readView<number>(memoryScope, (raw) => (typeof raw === 'number' && raw >= 0 && raw < s.schema.sections.length ? raw : null)) : null;
      setStep(remembered ?? 0);
    }).catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [projectId, memoryScope]);

  const schema = spec?.schema;
  const builds = useMemo(() => buildsOf(fields), [fields]);
  if (error && !spec) return <Alert severity="error">{error}</Alert>;
  if (!spec || !schema) return <CircularProgress size={18} />;
  const canEdit = spec.can_edit;
  const section = schema.sections[Math.min(step, schema.sections.length - 1)];
  const set = (field: CtlField, answer: CtlAnswer, build?: string) => { edits.current += 1; setFields((f) => withAnswer(f, field, answer, build)); setDirty(true); };
  const chooseStep = (i: number) => { setStep(i); if (memoryScope) writeView(memoryScope, i); };
  const save = async () => {
    setBusy(true); setError(''); setConflict(false);
    const startedAt = edits.current;
    try {
      // fields AND revision from this one answer (Astra round 1 of #832): never a revision read later than the fields
      const saved = await controllerSpecApi.save(projectId, payloadFields(fields, schema), spec.revision);
      const next = afterSave(fields, saved, edits.current !== startedAt);
      setSpec((s) => (s ? { ...s, revision: next.revision, updated_at: saved.updated_at, updated_by: saved.updated_by } : s));
      if (!next.dirty) setFields(next.fields);
      setDirty(next.dirty);
      controllerSpecApi.get(projectId).then((fresh) => setSpec((s) => (s ? { ...s, history: fresh.history } : s))).catch(() => { /* the list waits for the next open */ });
    } catch (e) {
      setConflict(isConflict(e));                       // somebody saved in between: the typed values stay, reload is the user's choice
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };
  const reload = async () => {
    setBusy(true);
    try { const fresh = await controllerSpecApi.get(projectId); setSpec(fresh); setFields(fresh.fields ?? {}); setDirty(false); setConflict(false); setError(''); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const labelOf = (id: string) => { const f = schema.sections.flatMap((s) => s.fields).find((x) => x.id === id); return f ? localized(f.label, lang) : id; };

  return <Box sx={{ minWidth: 0 }} data-testid="controller-spec">
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
      <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>{localized(schema.title, lang)}</Typography>
      <HelpTip title={ct(canEdit ? 'spec.tipEdit' : 'spec.tipRead')} />
      <Box sx={{ flex: 1 }} />
      {spec.revision > 0 && <Tooltip title={ct('spec.revisionTip', { by: spec.updated_by ?? '', at: spec.updated_at ? new Date(spec.updated_at).toLocaleString() : '' })}><Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{ct('spec.revision', { n: spec.revision })}</Typography></Tooltip>}
      {canEdit && <Button size="small" variant="contained" disabled={busy || !dirty} data-testid="controller-spec-save" onClick={() => void save()}>{busy ? <CircularProgress size={14} /> : ct('spec.save')}</Button>}
    </Box>
    {error && <Alert severity="error" sx={{ mb: 1 }} action={conflict ? <Button color="inherit" size="small" disabled={busy} data-testid="controller-spec-reload" onClick={() => void reload()}>{ct('spec.reload')}</Button> : undefined}>{conflict ? ct('spec.conflict') : error}</Alert>}
    <Tabs value={step} onChange={(_e, v: number) => chooseStep(v)} variant="scrollable" scrollButtons="auto" sx={{ mb: 1, minHeight: 32 }}>
      {schema.sections.map((s, i) => <Tab key={s.id} value={i} label={localized(s.title, lang)} sx={{ minHeight: 32, py: 0.5, fontSize: 12 }} />)}
    </Tabs>
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      {section.fields.some((f) => hasColumns(f.kind) || f.kind === 'thresholds') && <Box sx={{ ...GRID, mb: 0.5, [NARROW]: { display: 'none' } }} data-testid="controller-spec-columns">
        <Box />{COLUMNS.map((col) => <Box key={col} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}><Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-1)' }}>{columnLabel(schema, col, lang)}</Typography>{col === 'requirement' && <HelpTip title={ct('spec.statusTip')} />}</Box>)}
      </Box>}
      <Stack spacing={1.4}>
        {section.fields.map((field) => <Box key={field.id}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700 }}>{localized(field.label, lang)}{field.unit ? `, ${field.unit}` : ''}</Typography>
            {(field.tip || field.per_build) && <HelpTip title={[localized(field.tip, lang), field.per_build ? ct('spec.perBuildTip') : ''].filter(Boolean).join(' ')} />}
          </Box>
          {field.per_build
            ? (builds.length
              ? <Stack spacing={0.8}>{builds.map((b) => <FieldRow key={b} build={b} field={field} answer={answerOf(fields, field, b)} schema={schema} lang={lang} ct={ct} disabled={!canEdit} onChange={(a) => set(field, a, b)} />)}</Stack>
              : <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{ct('spec.noBuilds')}</Typography>)
            : <FieldRow build="" field={field} answer={answerOf(fields, field)} schema={schema} lang={lang} ct={ct} disabled={!canEdit} onChange={(a) => set(field, a)} />}
        </Box>)}
      </Stack>
    </Paper>
    {spec.history.length > 0 && <Paper variant="outlined" sx={{ p: 1.5, mt: 1.5 }} data-testid="controller-spec-history">
      <Typography sx={{ fontSize: 13, fontWeight: 700, mb: 0.5 }}>{ct('spec.history')}</Typography>
      {spec.history.slice(0, 20).map((h) => <Box key={h.revision} sx={{ py: 0.4, borderBottom: '1px solid var(--line-soft)' }}>
        <Typography sx={{ fontSize: 12 }}>{ct('spec.revision', { n: h.revision })} · {new Date(h.at).toLocaleString()} · {h.by}</Typography>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{h.changes.map((c) => labelOf(c.field) + (c.columns?.length ? ` (${c.columns.map((k) => changedColumn(k, schema, lang)).join(', ')})` : '')).join('; ')}</Typography>
      </Box>)}
    </Paper>}
  </Box>;
}

const numText = (v: unknown): string => (typeof v === 'number' ? String(v) : '');
/** label | Requirement | Model | Measured; one column on a phone. */
const GRID = { display: 'grid', gridTemplateColumns: '150px repeat(3, minmax(0, 1fr))', gap: 1, alignItems: 'start', [NARROW]: { gridTemplateColumns: 'minmax(0, 1fr)' } } as const;
const columnLabel = (schema: CtlSchema, col: ValueStatus, lang: string) => localized(schema.statuses.find((x) => x.value === col)?.label, lang) || col;
/** "12S.model.over_voltage" -> "12S Model": the build and the column a save changed (the field is named beside it). */
function changedColumn(key: string, schema: CtlSchema, lang: string): string {
  const parts = key.split('.');
  const at = parts.findIndex((p) => (COLUMNS as readonly string[]).includes(p));
  return at < 0 ? key : [...parts.slice(0, at), columnLabel(schema, parts[at] as ValueStatus, lang)].join(' ');
}

interface RowProps { build: string; field: CtlField; answer: CtlAnswer; schema: CtlSchema; lang: string; ct: T; disabled: boolean; onChange: (a: CtlAnswer) => void }

/** One answer of one field (one build of a per-build field): three columns for a number / range / protection, else one input. */
function FieldRow({ build, field, answer, schema, lang, ct, disabled, onChange }: RowProps) {
  const head = <Typography sx={{ fontSize: 12, pt: 1, color: 'var(--text-1)' }}>{build}</Typography>;
  if (hasColumns(field.kind)) {
    return <Box sx={GRID}>{head}{COLUMNS.map((col) => <ColumnCell key={col} col={col} schema={schema} lang={lang} ct={ct} disabled={disabled} cell={cellOf(answer, col)}
      onChange={(c) => onChange(withCell(answer, col, c))} input={(value, put) => <ValueInput field={field} value={value} ct={ct} disabled={disabled} onChange={put} />} />)}</Box>;
  }
  if (field.kind === 'thresholds') {
    const map = (answer.value && typeof answer.value === 'object' ? answer.value : {}) as Record<string, CtlThreshold>;
    const setItem = (opt: string, next: CtlThreshold | undefined) => { const copy = { ...map }; if (next) copy[opt] = next; else delete copy[opt]; onChange({ value: copy }); };
    return <Stack spacing={0.8}>{(field.options ?? []).map((o) => { const item = map[o.value];
      return <Box key={o.value} sx={GRID}>
        <Box><FormControlLabel control={<Checkbox size="small" disabled={disabled} checked={!!item} onChange={(e) => setItem(o.value, e.target.checked ? { unit: '', values: {} } : undefined)} />} label={<Typography sx={{ fontSize: 12 }}>{[build, localized(o.label, lang)].filter(Boolean).join(' · ')}</Typography>} />
          {item && <TextField size="small" sx={{ width: 90 }} disabled={disabled} value={item.unit ?? ''} label={ct('spec.unit')} onChange={(e) => setItem(o.value, { ...item, unit: e.target.value })} inputProps={{ maxLength: 20 }} />}</Box>
        {item ? COLUMNS.map((col) => <ColumnCell key={col} col={col} schema={schema} lang={lang} ct={ct} disabled={disabled} cell={cellOf(item, col)}
          onChange={(c) => setItem(o.value, withCell(item, col, c))} input={(value, put) => <NumberBox value={value} unit={item.unit} disabled={disabled} label={ct('spec.threshold')} onChange={put} />} />) : null}
      </Box>; })}</Stack>;
  }
  return <Box sx={GRID}>{head}<Box sx={{ gridColumn: '2 / span 3', [NARROW]: { gridColumn: 'auto' } }}><OtherInput field={field} value={answer.value} lang={lang} ct={ct} disabled={disabled} onChange={(value) => onChange({ value })} /></Box></Box>;
}

/** One of the three columns: the value input(s) and its source. */
function ColumnCell({ col, schema, lang, ct, disabled, cell, onChange, input }: { col: ValueStatus; schema: CtlSchema; lang: string; ct: T; disabled: boolean; cell: CtlCell; onChange: (c: CtlCell) => void; input: (value: unknown, put: (v: unknown) => void) => ReactNode }) {
  return <Stack spacing={0.5} sx={{ minWidth: 0 }} data-column={col}>
    <Typography sx={{ display: 'none', fontSize: 11, fontWeight: 700, [NARROW]: { display: 'block' } }}>{columnLabel(schema, col, lang)}</Typography>
    {input(cell.value, (value) => onChange({ ...cell, value }))}
    <TextField size="small" fullWidth disabled={disabled} value={cell.ref ?? ''} placeholder={ct('spec.source')} onChange={(e) => onChange({ ...cell, ref: e.target.value })} inputProps={{ maxLength: 200, 'aria-label': `${columnLabel(schema, col, lang)} · ${ct('spec.source')}` }} />
  </Stack>;
}

/** The value of a number (one box) or a range (min / nom / max), with the unit in the box instead of a cut label. */
function ValueInput({ field, value, ct, disabled, onChange }: { field: CtlField; value: unknown; ct: T; disabled: boolean; onChange: (v: unknown) => void }) {
  if (field.kind === 'number') return <NumberBox value={value} unit={field.unit} disabled={disabled} onChange={onChange} />;
  const r = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  return <Box sx={{ display: 'flex', gap: 0.5 }}>{(['min', 'nom', 'max'] as const).map((k) => <NumberBox key={k} value={r[k]} unit={field.unit} disabled={disabled} label={ct(`spec.${k}`)} onChange={(n) => onChange({ min: null, nom: null, max: null, ...r, [k]: n })} />)}</Box>;
}

/** Every kind without columns: text, choice, multi, list, rows. */
function OtherInput({ field, value: v, lang, ct, disabled, onChange: put }: { field: CtlField; value: unknown; lang: string; ct: T; disabled: boolean; onChange: (v: unknown) => void }) {
  const opts = field.options ?? [];
  switch (field.kind) {
    case 'text': case 'longtext':
      return <TextField size="small" fullWidth disabled={disabled} multiline={field.kind === 'longtext'} minRows={field.kind === 'longtext' ? 2 : undefined} value={typeof v === 'string' ? v : ''} onChange={(e) => put(e.target.value)} inputProps={{ maxLength: field.kind === 'text' ? 200 : 4000, 'aria-label': localized(field.label, lang) }} />;
    case 'choice':
      return <TextField select size="small" sx={{ minWidth: 220 }} disabled={disabled} value={typeof v === 'string' ? v : ''} onChange={(e) => put(e.target.value || null)} inputProps={{ 'aria-label': localized(field.label, lang) }}><MenuItem value="">{ct('spec.notSet')}</MenuItem>{opts.map((o) => <MenuItem key={o.value} value={o.value}>{localized(o.label, lang)}</MenuItem>)}</TextField>;
    case 'multi': {
      const on = new Set(Array.isArray(v) ? v as string[] : []);
      return <Box sx={{ display: 'flex', flexWrap: 'wrap' }}>{opts.map((o) => <FormControlLabel key={o.value} control={<Checkbox size="small" disabled={disabled} checked={on.has(o.value)} onChange={(e) => { const next = new Set(on); if (e.target.checked) next.add(o.value); else next.delete(o.value); put(opts.map((x) => x.value).filter((x) => next.has(x))); }} />} label={<Typography sx={{ fontSize: 12 }}>{localized(o.label, lang)}</Typography>} />)}</Box>;
    }
    case 'list':
      return <TextField size="small" fullWidth disabled={disabled} value={Array.isArray(v) ? (v as string[]).join(', ') : ''} onChange={(e) => put(e.target.value.split(',').map((s) => s.trim()).filter((s, i, all) => s || i === all.length - 1))} inputProps={{ 'aria-label': localized(field.label, lang) }} />;
    case 'rows': {
      const rows = Array.isArray(v) ? v as Record<string, string>[] : [];
      const cols = field.columns ?? [];
      return <Stack spacing={0.5}>{rows.map((row, i) => <Box key={i} sx={{ display: 'flex', gap: 0.8, alignItems: 'center' }}>{cols.map((c) => <TextField key={c.id} size="small" disabled={disabled} value={row[c.id] ?? ''} placeholder={localized(c.label, lang)} onChange={(e) => put(rows.map((r, j) => (j === i ? { ...r, [c.id]: e.target.value } : r)))} inputProps={{ maxLength: 200, 'aria-label': localized(c.label, lang) }} />)}{!disabled && <IconButton size="small" aria-label={ct('spec.removeRow')} onClick={() => put(rows.filter((_r, j) => j !== i))}><DeleteOutlineIcon sx={{ fontSize: 16 }} /></IconButton>}</Box>)}{!disabled && <Box><Button size="small" startIcon={<AddIcon />} onClick={() => put([...rows, Object.fromEntries(cols.map((c) => [c.id, '']))])}>{ct('spec.addRow')}</Button></Box>}</Stack>;
    }
    default:
      return null;
  }
}

function NumberBox({ value, unit, disabled, label, onChange }: { value: unknown; unit?: string; disabled: boolean; label?: string; onChange: (n: number | null) => void }) {
  const [text, setText] = useState(numText(value));
  useEffect(() => { setText((t) => (parseNumber(t) === (typeof value === 'number' ? value : null) ? t : numText(value))); }, [value]);
  const bad = parseNumber(text) === undefined;
  return <TextField size="small" fullWidth sx={{ minWidth: 64 }} disabled={disabled} value={text} error={bad} label={label} onChange={(e) => { setText(e.target.value); const n = parseNumber(e.target.value); if (n !== undefined) onChange(n); }}
    inputProps={{ inputMode: 'decimal' }} InputProps={unit ? { endAdornment: <InputAdornment position="end"><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{unit}</Typography></InputAdornment> } : undefined} />;
}
