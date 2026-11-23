// The specification of a CONTROLLER project (controller PR2): 7 steps from the server's data-file schema; every number carries
// its status (requirement / model / measured) and a source; per-build fields hold one answer per build.  Only the project owner
// and admins edit (the server decides: `can_edit`); everybody else reads.  No assistant, no submit flow: internal for now.
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Alert, Box, Button, Checkbox, CircularProgress, FormControlLabel, IconButton, MenuItem, Paper, Stack, Tab, Tabs, TextField, Tooltip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import HelpTip from '../../../components/common/HelpTip';
import { readView, writeView } from '../../../lib/viewMemory';
import {
  afterSave, answerOf, buildsOf, controllerSpecApi, isConflict, hasStatus, localized, parseNumber, payloadFields, withAnswer,
  type CtlAnswer, type CtlField, type CtlSchema, type CtlSpec, type CtlStored, type ValueStatus,
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
      <Stack spacing={1.4}>
        {section.fields.map((field) => <Box key={field.id}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700 }}>{localized(field.label, lang)}{field.unit ? `, ${field.unit}` : ''}</Typography>
            {(field.tip || field.per_build) && <HelpTip title={[localized(field.tip, lang), field.per_build ? ct('spec.perBuildTip') : ''].filter(Boolean).join(' ')} />}
          </Box>
          {field.per_build
            ? (builds.length
              ? <Stack spacing={0.8}>{builds.map((b) => <Box key={b} sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, flexWrap: 'wrap' }}><Typography sx={{ fontSize: 12, minWidth: 40, pt: 1 }}>{b}</Typography><FieldInput field={field} answer={answerOf(fields, field, b)} schema={schema} lang={lang} ct={ct} disabled={!canEdit} onChange={(a) => set(field, a, b)} /></Box>)}</Stack>
              : <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{ct('spec.noBuilds')}</Typography>)
            : <FieldInput field={field} answer={answerOf(fields, field)} schema={schema} lang={lang} ct={ct} disabled={!canEdit} onChange={(a) => set(field, a)} />}
        </Box>)}
      </Stack>
    </Paper>
    {spec.history.length > 0 && <Paper variant="outlined" sx={{ p: 1.5, mt: 1.5 }} data-testid="controller-spec-history">
      <Typography sx={{ fontSize: 13, fontWeight: 700, mb: 0.5 }}>{ct('spec.history')}</Typography>
      {spec.history.slice(0, 20).map((h) => <Box key={h.revision} sx={{ py: 0.4, borderBottom: '1px solid var(--line-soft)' }}>
        <Typography sx={{ fontSize: 12 }}>{ct('spec.revision', { n: h.revision })} · {new Date(h.at).toLocaleString()} · {h.by}</Typography>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{h.changes.map((c) => labelOf(c.field)).join(', ')}</Typography>
      </Box>)}
    </Paper>}
  </Box>;
}

const numText = (v: unknown): string => (typeof v === 'number' ? String(v) : '');

/** One answer of one field (one build of a per-build field). */
function FieldInput({ field, answer, schema, lang, ct, disabled, onChange }: { field: CtlField; answer: CtlAnswer; schema: CtlSchema; lang: string; ct: T; disabled: boolean; onChange: (a: CtlAnswer) => void }) {
  const v = answer.value;
  const put = (value: unknown) => onChange({ ...answer, value });
  const opts = field.options ?? [];
  let input: ReactNode;
  switch (field.kind) {
    case 'text': case 'longtext':
      input = <TextField size="small" fullWidth disabled={disabled} multiline={field.kind === 'longtext'} minRows={field.kind === 'longtext' ? 2 : undefined} value={typeof v === 'string' ? v : ''} onChange={(e) => put(e.target.value)} inputProps={{ maxLength: field.kind === 'text' ? 200 : 4000, 'aria-label': localized(field.label, lang) }} />;
      break;
    case 'number':
      input = <NumberBox value={v} disabled={disabled} label={localized(field.label, lang)} onChange={put} />;
      break;
    case 'range': {
      const r = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>;
      input = <Box sx={{ display: 'flex', gap: 0.8 }}>{(['min', 'nom', 'max'] as const).map((k) => <NumberBox key={k} value={r[k]} disabled={disabled} label={ct(`spec.${k}`)} onChange={(n) => put({ min: null, nom: null, max: null, ...r, [k]: n })} />)}</Box>;
      break;
    }
    case 'choice':
      input = <TextField select size="small" sx={{ minWidth: 220 }} disabled={disabled} value={typeof v === 'string' ? v : ''} onChange={(e) => put(e.target.value || null)} inputProps={{ 'aria-label': localized(field.label, lang) }}><MenuItem value="">{ct('spec.notSet')}</MenuItem>{opts.map((o) => <MenuItem key={o.value} value={o.value}>{localized(o.label, lang)}</MenuItem>)}</TextField>;
      break;
    case 'multi': {
      const on = new Set(Array.isArray(v) ? v as string[] : []);
      input = <Box sx={{ display: 'flex', flexWrap: 'wrap' }}>{opts.map((o) => <FormControlLabel key={o.value} control={<Checkbox size="small" disabled={disabled} checked={on.has(o.value)} onChange={(e) => { const next = new Set(on); if (e.target.checked) next.add(o.value); else next.delete(o.value); put(opts.map((x) => x.value).filter((x) => next.has(x))); }} />} label={<Typography sx={{ fontSize: 12 }}>{localized(o.label, lang)}</Typography>} />)}</Box>;
      break;
    }
    case 'list':
      input = <TextField size="small" fullWidth disabled={disabled} value={Array.isArray(v) ? (v as string[]).join(', ') : ''} onChange={(e) => put(e.target.value.split(',').map((s) => s.trim()).filter((s, i, all) => s || i === all.length - 1))} inputProps={{ 'aria-label': localized(field.label, lang) }} />;
      break;
    case 'thresholds': {
      const map = (v && typeof v === 'object' ? v : {}) as Record<string, { threshold?: number | null; unit?: string; status?: ValueStatus | null }>;
      input = <Stack spacing={0.5}>{opts.map((o) => { const item = map[o.value]; const setItem = (next: typeof item | undefined) => { const copy = { ...map }; if (next) copy[o.value] = next; else delete copy[o.value]; put(copy); };
        return <Box key={o.value} sx={{ display: 'flex', alignItems: 'center', gap: 0.8, flexWrap: 'wrap' }}>
          <FormControlLabel sx={{ minWidth: 220 }} control={<Checkbox size="small" disabled={disabled} checked={!!item} onChange={(e) => setItem(e.target.checked ? { threshold: null, unit: '', status: null } : undefined)} />} label={<Typography sx={{ fontSize: 12 }}>{localized(o.label, lang)}</Typography>} />
          {item && <><NumberBox value={item.threshold} disabled={disabled} label={ct('spec.threshold')} onChange={(n) => setItem({ ...item, threshold: n })} /><TextField size="small" sx={{ width: 80 }} disabled={disabled} value={item.unit ?? ''} onChange={(e) => setItem({ ...item, unit: e.target.value })} inputProps={{ maxLength: 20, 'aria-label': ct('spec.unit') }} placeholder={ct('spec.unit')} /><StatusSelect value={item.status ?? null} schema={schema} lang={lang} ct={ct} disabled={disabled} onChange={(s) => setItem({ ...item, status: s })} /></>}
        </Box>; })}</Stack>;
      break;
    }
    case 'rows': {
      const rows = Array.isArray(v) ? v as Record<string, string>[] : [];
      const cols = field.columns ?? [];
      input = <Stack spacing={0.5}>{rows.map((row, i) => <Box key={i} sx={{ display: 'flex', gap: 0.8, alignItems: 'center' }}>{cols.map((c) => <TextField key={c.id} size="small" disabled={disabled} value={row[c.id] ?? ''} placeholder={localized(c.label, lang)} onChange={(e) => put(rows.map((r, j) => (j === i ? { ...r, [c.id]: e.target.value } : r)))} inputProps={{ maxLength: 200, 'aria-label': localized(c.label, lang) }} />)}{!disabled && <IconButton size="small" aria-label={ct('spec.removeRow')} onClick={() => put(rows.filter((_r, j) => j !== i))}><DeleteOutlineIcon sx={{ fontSize: 16 }} /></IconButton>}</Box>)}{!disabled && <Box><Button size="small" startIcon={<AddIcon />} onClick={() => put([...rows, Object.fromEntries(cols.map((c) => [c.id, '']))])}>{ct('spec.addRow')}</Button></Box>}</Stack>;
      break;
    }
    default:
      input = <></>;
  }
  if (!hasStatus(field.kind)) return input;
  return <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8, flexWrap: 'wrap' }}>{input}
    <StatusSelect value={answer.status ?? null} schema={schema} lang={lang} ct={ct} disabled={disabled} onChange={(s) => onChange({ ...answer, status: s })} />
    <TextField size="small" sx={{ width: 200 }} disabled={disabled} value={answer.ref ?? ''} placeholder={ct('spec.source')} onChange={(e) => onChange({ ...answer, ref: e.target.value })} inputProps={{ maxLength: 200, 'aria-label': ct('spec.source') }} />
  </Box>;
}

function NumberBox({ value, disabled, label, onChange }: { value: unknown; disabled: boolean; label: string; onChange: (n: number | null) => void }) {
  const [text, setText] = useState(numText(value));
  useEffect(() => { setText((t) => (parseNumber(t) === (typeof value === 'number' ? value : null) ? t : numText(value))); }, [value]);
  const bad = parseNumber(text) === undefined;
  return <TextField size="small" sx={{ width: 110 }} disabled={disabled} value={text} error={bad} label={label} onChange={(e) => { setText(e.target.value); const n = parseNumber(e.target.value); if (n !== undefined) onChange(n); }} inputProps={{ inputMode: 'decimal' }} />;
}

function StatusSelect({ value, schema, lang, ct, disabled, onChange }: { value: ValueStatus | null; schema: CtlSchema; lang: string; ct: T; disabled: boolean; onChange: (s: ValueStatus | null) => void }) {
  return <Tooltip title={ct('spec.statusTip')}><TextField select size="small" sx={{ width: 140 }} disabled={disabled} value={value ?? ''} label={ct('spec.status')} onChange={(e) => onChange((e.target.value || null) as ValueStatus | null)}><MenuItem value="">{ct('spec.notSet')}</MenuItem>{schema.statuses.map((s) => <MenuItem key={s.value} value={s.value}>{localized(s.label, lang)}</MenuItem>)}</TextField></Tooltip>;
}
