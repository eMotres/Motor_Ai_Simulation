import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle,
  Divider, FormControlLabel, IconButton, MenuItem, Paper, Select, Stack, Tab, Tabs, TextField,
  Tooltip, Typography, Checkbox,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HelpTip from '../../../components/common/HelpTip';
import { useAuth } from '../../../contexts/AuthContext';
import { useTranslation } from 'react-i18next';
import { developmentApi, DevelopmentApiError } from '../api';
import type { Completeness, DevelopmentSchema, ProjectMotor, ProjectSummary, ProjectThreadMessage, SchemaField, SpecSnapshot, SpecState } from '../types';
import { resultKindLabel, temperatureKindForKey, temperatureLabel } from '../resultPresentation';
import { createDraftSaveCoordinator, fieldAnswer, fieldAnswerComplete, type FieldAnswer, isRequired, setFieldAnswer, shallowDiff, wizardCompleteness, type SpecDraft } from '../wizardState';
import { applyCatalogDuty } from '../../../lib/catalogDutyApply';
import { canWriteServer } from '../../../lib/localAuth';
import { useUIStore } from '../../../stores/uiStore';
import { firstProjectMotorDuty } from '../motorDuty';

type ViewTab = 'specification' | 'motors' | 'messages' | 'notes';
const STATUSES = ['spec', 'design', 'simulation', 'quote', 'prototype', 'production', 'closed'] as const;
const DEFAULT_DRAFT: SpecDraft = { fields: {}, mission_profile: [], production: {} };
const localeValue = (v: unknown, lang: string): string => {
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    return String(o[lang === 'zh-CN' ? 'zh-CN' : 'en'] ?? o.en ?? '');
  }
  return '';
};
const completenessNumbers = (c?: Completeness) => ({ filled: c?.filled_required ?? c?.filled ?? 0, total: c?.total_required ?? c?.required ?? 0 });
const normaliseDraft = (value: unknown): SpecDraft => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...DEFAULT_DRAFT, fields: {}, production: {} };
  const d = value as SpecDraft;
  return { ...DEFAULT_DRAFT, ...d, fields: { ...(d.fields ?? {}) }, production: { ...((d.production && typeof d.production === 'object') ? d.production as object : {}) }, mission_profile: Array.isArray(d.mission_profile) ? d.mission_profile : [] };
};

/** Project list, specification wizard and collaboration views for the Development tab. */
export default function DevelopmentPage() {
  const { t: tr, i18n } = useTranslation('development');
  const { user, isAdmin, role } = useAuth();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selected, setSelected] = useState<ProjectSummary | null>(null);
  const [schema, setSchema] = useState<DevelopmentSchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [view, setView] = useState<ViewTab>('specification');
  const [createOpen, setCreateOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [org, setOrg] = useState('');
  const [projectId, setProjectId] = useState('');
  const [email, setEmail] = useState('');
  const [memberRole, setMemberRole] = useState('customer');
  const [die, setDie] = useState('');
  const [config, setConfig] = useState('');
  const [spec, setSpec] = useState<SpecState | null>(null);
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [submitError, setSubmitError] = useState<string[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [messages, setMessages] = useState<ProjectThreadMessage[]>([]);
  const [message, setMessage] = useState('');
  const [messageFieldRef, setMessageFieldRef] = useState('');
  const [motors, setMotors] = useState<ProjectMotor[]>([]);
  const projectEpochRef = useRef(0);
  const [versions, setVersions] = useState<SpecSnapshot[]>([]);
  const [compareVersions, setCompareVersions] = useState<[number, number] | null>(null);
  const [versionA, setVersionA] = useState<SpecSnapshot | null>(null);
  const [versionB, setVersionB] = useState<SpecSnapshot | null>(null);
  const [lang, setLang] = useState(i18n.resolvedLanguage || document.documentElement.lang || 'en');
  const canManageGlobally = isAdmin || role === 'engineer';
  const canEditSpec = !!selected && (canManageGlobally || selected.can_edit_spec === true);
  const canEditProject = !!selected && (canManageGlobally || selected.can_edit === true);
  const sections = schema?.sections ?? [];
  const activeSection = sections[step];
  const completeness = spec?.completeness ?? (spec ? wizardCompleteness(sections, spec.draft as SpecDraft) : undefined);
  const completeCounts = completenessNumbers(completeness);
  const draftRef = useRef<SpecDraft>(DEFAULT_DRAFT);
  const revisionRef = useRef(0);
  const saveCoordinator = useMemo(() => createDraftSaveCoordinator<SpecDraft>(async (project, draft, revision) => developmentApi.saveDraft(project, draft as Record<string, unknown>, revision)), []);
  const saveFailed = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const projectToken = useRef('');
  const currentProject = selected?.id ?? '';
  const currentProjectRef = useRef(currentProject);
  currentProjectRef.current = currentProject;
  useEffect(() => {
    const onLang = (lng: string) => setLang(lng);
    i18n.on('languageChanged', onLang);
    return () => { i18n.off('languageChanged', onLang); };
  }, [i18n]);

  const refreshProjects = useCallback(async () => {
    const epoch = projectEpochRef.current;
    setLoading(true); setError('');
    try {
      const [list, s] = await Promise.all([developmentApi.list(), developmentApi.schema()]);
      if (epoch === projectEpochRef.current) { setProjects(list); setSchema(s); }
    } catch (e) { if (epoch === projectEpochRef.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (epoch === projectEpochRef.current) setLoading(false); }
  }, []);
  useEffect(() => {
    if (saveTimer.current) { clearTimeout(saveTimer.current); saveTimer.current = null; }
    projectEpochRef.current += 1;
    projectToken.current = `identity:${user?.email ?? 'signed-out'}:${Date.now()}`;
    currentProjectRef.current = '';
    setSelected(null); setSpec(null); setProjects([]); setMessages([]); setMotors([]); setVersions([]); setCompareVersions(null);
    setError(''); setSubmitError([]); setFieldErrors({});
    draftRef.current = { ...DEFAULT_DRAFT }; revisionRef.current = 0;
    if (user?.email) void refreshProjects();
  }, [user?.email, refreshProjects]);

  const loadProject = useCallback(async (p: ProjectSummary) => {
    if (selected && spec) {
      try { await flushSaveRef.current(); }
      catch { setError(tr('saveFailedShort')); return; }
    }
    const identityEpoch = projectEpochRef.current;
    setSelected(p); setView('specification'); setStep(0); setSpec(null); setMessages([]); setMotors([]); setError(''); setSubmitError([]);
    setVersions([]); setCompareVersions(null); setVersionA(null); setVersionB(null); setFieldErrors({}); setMessageFieldRef('');
    const token = `${p.id}:${Date.now()}`; projectToken.current = token; currentProjectRef.current = p.id;
    try {
      const [detail, sp] = await Promise.all([developmentApi.get(p.id), developmentApi.spec(p.id)]);
      if (projectToken.current !== token || identityEpoch !== projectEpochRef.current) return;
      setSelected(detail); setSpec({ ...sp, draft: normaliseDraft(sp.draft) });
      draftRef.current = normaliseDraft(sp.draft); revisionRef.current = sp.revision; saveCoordinator.setRevision(p.id, sp.revision);
    } catch (e) { if (projectToken.current === token) setError(e instanceof Error ? e.message : String(e)); }
  }, [selected, spec]);

  const enqueueSave = useCallback((project: string, value: SpecDraft, token: string) => {
    setSaving(true); setSaveError(''); saveFailed.current = false;
    const current = () => projectToken.current === token && currentProjectRef.current === project;
    const pending = saveCoordinator.enqueue(project, value, current).then(() => {
      if (!current()) return;
      revisionRef.current = saveCoordinator.revision(project);
      setSpec((old) => old ? { ...old, revision: revisionRef.current, completeness: wizardCompleteness(sections, value) } : old);
    }).catch((e: unknown) => {
      saveFailed.current = true;
      if (current()) setSaveError(tr('saveFailedShort'));
      throw e;
    }).finally(() => { if (current()) setSaving(false); });
    return pending;
  }, [saveCoordinator, sections, tr]);
  const flushSave = useCallback(async () => {
    if (saveTimer.current) { clearTimeout(saveTimer.current); saveTimer.current = null; }
    if (!selected || !spec || !canEditSpec) return;
    const project = selected.id; const token = projectToken.current; const snapshot = draftRef.current;
    await enqueueSave(project, snapshot, token);
    await saveCoordinator.flush();
    if (saveFailed.current) throw new Error('Draft save failed');
  }, [selected, spec, canEditSpec, enqueueSave, saveCoordinator]);
  const flushSaveRef = useRef<() => Promise<void>>(async () => {});
  flushSaveRef.current = flushSave;
  const reloadDraft = async () => {
    if (!selected) return;
    const id = selected.id; const token = projectToken.current;
    try {
      const fresh = await developmentApi.spec(id);
      if (projectToken.current !== token || currentProjectRef.current !== id) return;
      const draft = normaliseDraft(fresh.draft);
      draftRef.current = draft; revisionRef.current = fresh.revision; saveCoordinator.setRevision(id, fresh.revision);
      saveFailed.current = false; setSaveError(''); setFieldErrors({}); setSubmitError([]);
      setSpec((old) => old ? { ...old, ...fresh, draft } : old);
    } catch { if (projectToken.current === token) setError(tr('reloadFailed')); }
  };

  const updateDraft = useCallback((fn: (d: SpecDraft) => SpecDraft) => {
    if (!selected || !spec || !canEditSpec || busy) return;
    const next = fn(draftRef.current); draftRef.current = next;
    setSpec((old) => old ? { ...old, draft: next, completeness: wizardCompleteness(sections, next) } : old);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const p = selected.id; const token = projectToken.current;
    saveTimer.current = setTimeout(() => { saveTimer.current = null; void enqueueSave(p, draftRef.current, token).catch(() => {}); }, 650);
  }, [selected, spec, canEditSpec, sections, enqueueSave, busy]);
  useEffect(() => () => { if (saveTimer.current) clearTimeout(saveTimer.current); }, []);

  const setAnswer = (field: SchemaField, value: unknown, production = false) => updateDraft((d) => {
    const old = production ? ((d.production as Record<string, { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' }>)?.[field.id] ?? {}) : fieldAnswer(d, field.id);
    const complete = fieldAnswerComplete(field, { value, status: 'filled' });
    const answer: FieldAnswer = { ...old, value, status: complete ? 'filled' : 'missing' };
    if (production) return { ...d, production: { ...(d.production as object), [field.id]: answer } };
    return setFieldAnswer(d, field.id, answer);
  });
  const markUnknown = (field: SchemaField, unknown: boolean, production = false) => updateDraft((d) => {
    if (field.id === 'mission_profile') return { ...d, mission_profile_status: unknown ? 'not_known_yet' : (d.mission_profile?.length ? 'filled' : 'missing') };
    const old = production ? ((d.production as Record<string, { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' }>)?.[field.id] ?? {}) : fieldAnswer(d, field.id);
    const answer: FieldAnswer = { ...old, status: unknown ? 'not_known_yet' : fieldAnswerComplete(field, { value: old.value, status: 'filled' }) ? 'filled' : 'missing' };
    if (production) return { ...d, production: { ...(d.production as object), [field.id]: answer } };
    return setFieldAnswer(d, field.id, answer);
  });

  const onSubmitSpec = async () => {
    if (!selected || !spec) return;
    if (saveTimer.current) { clearTimeout(saveTimer.current); saveTimer.current = null; }
    setBusy(true); setSubmitError([]); setFieldErrors({});
    const token = projectToken.current;
    const project = selected.id;
    let requestSubmitted = false;
    try {
      const result = await saveCoordinator.submit<Awaited<ReturnType<typeof developmentApi.submitSpec>>>(project, draftRef.current, () => projectToken.current === token && currentProjectRef.current === project, (revision) => { requestSubmitted = true; return developmentApi.submitSpec(project, revision); });
      if (projectToken.current !== token || currentProjectRef.current !== project) return;
      if (typeof result.revision === 'number') revisionRef.current = result.revision;
      else { const refreshed = await developmentApi.spec(project); if (projectToken.current !== token) return; revisionRef.current = refreshed.revision; saveCoordinator.setRevision(project, refreshed.revision); }
      setSpec((old) => old ? { ...old, version: result.version, completeness: result.completeness, revision: (result as any).revision ?? old.revision } : old);
      await loadSpecHistory(project, token);
    } catch (e) {
      const body = e instanceof DevelopmentApiError ? e.body as { detail?: unknown } : null;
      const detail = body?.detail as { errors?: Array<{ field?: string; section?: string; message?: string }>; message?: string } | null;
      const errors = detail && Array.isArray(detail.errors) ? detail.errors : [];
      const fieldForError = (path: string) => {
        const all = sections.flatMap((section) => section.fields ?? []);
        return all.find((candidate) => path.includes(candidate.id)) ?? (path.toLowerCase().includes('mission') ? all.find((candidate) => candidate.id === 'mission_profile') : undefined);
      };
      const fieldLabel = (path: string) => { const field = fieldForError(path); return field ? label(field.short_label ?? field.label) : tr('specification'); };
      const byField = Object.fromEntries(errors.filter((x) => x.field).flatMap((x) => { const field = fieldForError(x.field!); return field ? [[field.id, tr('validationError')]] : []; }));
      setFieldErrors(byField);
      const firstInvalidSection = sections.findIndex((section) => (section.fields ?? []).some((field) => byField[field.id]));
      if (firstInvalidSection >= 0) setStep(firstInvalidSection);
      setSubmitError(!requestSubmitted ? [tr('saveBeforeSubmit')] : errors.length ? errors.map((x) => x.field ? `${fieldLabel(x.field)}: ${tr('validationError')}` : tr('validationError')) : [tr('submitFailed')]);
    } finally { setBusy(false); }
  };
  const loadSpecHistory = async (id: string, token = projectToken.current) => {
    try {
      const rows = await developmentApi.versions(id);
      if (projectToken.current !== token || currentProjectRef.current !== id) return;
      const snaps = await Promise.all((rows.versions ?? []).map((v) => developmentApi.version(id, v.version)));
      if (projectToken.current === token && currentProjectRef.current === id) setVersions(snaps);
    } catch { /* history remains as the API supplied in the spec response */ }
  };
  useEffect(() => { if (selected?.id) { const token = projectToken.current; void loadSpecHistory(selected.id, token); } }, [selected?.id]);

  const openTab = async (tab: ViewTab) => {
    setView(tab); if (!selected) return;
    const id = selected.id; const token = projectToken.current;
    try {
      if (tab === 'messages') { const result = await developmentApi.thread(id); if (projectToken.current === token && currentProjectRef.current === id) setMessages(result); }
      if (tab === 'motors') { const result = await developmentApi.motors(id); if (projectToken.current === token && currentProjectRef.current === id) setMotors(result); }
    } catch (e) { if (projectToken.current === token && currentProjectRef.current === id) setError(e instanceof Error ? e.message : String(e)); }
  };
  const submitCreate = async () => {
    setBusy(true); setError('');
    try { const p = await developmentApi.create({ id: projectId.trim(), title: title.trim(), customer_org: org.trim() }); setCreateOpen(false); setTitle(''); setOrg(''); setProjectId(''); await refreshProjects(); await loadProject(p); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const submitInvite = async () => {
    if (!selected) return;
    setBusy(true); setError('');
    try { await developmentApi.invite(selected.id, email.trim(), memberRole); setInviteOpen(false); setEmail(''); await loadProject(selected); await refreshProjects(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const submitLink = async () => {
    if (!selected) return;
    setBusy(true); setError('');
    try { await developmentApi.linkMotor(selected.id, die.trim(), config.trim()); setLinkOpen(false); setDie(''); setConfig(''); setMotors(await developmentApi.motors(selected.id)); await refreshProjects(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const postMessage = async () => {
    if (!selected || !message.trim()) return;
    const id = selected.id; const token = projectToken.current; setBusy(true); setError('');
    try { await developmentApi.postMessage(id, message.trim(), messageFieldRef || undefined); const thread = await developmentApi.thread(id); if (projectToken.current === token && currentProjectRef.current === id) { setMessage(''); setMessageFieldRef(''); setMessages(thread); } }
    catch (e) { if (projectToken.current === token && currentProjectRef.current === id) setError(e instanceof Error ? e.message : String(e)); } finally { if (projectToken.current === token) setBusy(false); }
  };

  const label = (v: unknown) => localeValue(v, lang);
  const renderTypedValue = (shape: { id: string; label: unknown; short_label?: unknown; help?: unknown; unit?: string; type?: string; required?: boolean; required_flag?: string; allow_range?: boolean; options?: Array<string | { value: string; label?: unknown }>; properties?: Array<any>; items?: { type?: string; properties?: Array<any> } }, value: unknown, onChange: (next: unknown) => void, keyPrefix: string, locked = false): React.ReactNode => {
    const kind = shape.type ?? 'string';
    const options = shape.options ?? [];
    const text = (v: unknown) => v == null ? '' : String(v);
    const input = (v: unknown, update: (next: unknown) => void, type: string, unit?: string) => <TextField size="small" fullWidth type={type === 'number' ? 'number' : type === 'date' || unit === 'date' ? 'date' : 'text'} value={text(v)} disabled={!canEditSpec || busy || locked} onChange={(e) => update(type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)} />;
    if (shape.allow_range) {
      if (typeof value === 'number' || (typeof value === 'string' && value.trim())) return input(value, onChange, 'number', shape.unit);
      const bounds = value && typeof value === 'object' ? value as Record<string, unknown> : {};
      return <Box sx={{ display: 'flex', gap: 0.5 }}>{(['minimum', 'maximum'] as const).map((part) => <TextField key={part} size="small" type="number" label={tr(part)} disabled={!canEditSpec || busy || locked} value={text(bounds[part])} onChange={(e) => onChange({ ...bounds, [part]: e.target.value === '' ? '' : Number(e.target.value) })} />)}</Box>;
    }
    if (kind === 'array') {
      const rows = Array.isArray(value) ? value : [];
      const item = shape.items ?? {};
      if (item.type === 'object' && item.properties?.length) return <Stack spacing={0.8} sx={{ gridColumn: '1 / -1' }}>
        {rows.map((row, index) => <Paper key={`${keyPrefix}-${index}`} variant="outlined" sx={{ p: 0.8 }}><Stack spacing={0.6}>
          {item.properties!.map((prop: any) => <Box key={prop.id} sx={{ display: 'grid', gridTemplateColumns: 'minmax(100px,0.7fr) minmax(130px,1fr)', gap: 0.6, alignItems: 'center' }}><Tooltip title={label(prop.help ?? prop.label)}><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{label(prop.short_label ?? prop.label)}{prop.unit ? ` (${prop.unit})` : ''}</Typography></Tooltip>{renderTypedValue(prop, (row as Record<string, unknown>)?.[prop.id], (next) => { const nextRows = [...rows]; nextRows[index] = { ...((row && typeof row === 'object') ? row as object : {}), [prop.id]: next }; onChange(nextRows); }, `${keyPrefix}-${index}-${prop.id}`, locked)}</Box>)}
          <Button size="small" disabled={!canEditSpec || busy || locked} onClick={() => onChange(rows.filter((_, i) => i !== index))}>{tr('removeRow')}</Button>
        </Stack></Paper>)}
        <Button size="small" disabled={!canEditSpec || busy || locked} onClick={() => onChange([...rows, Object.fromEntries((item.properties ?? []).map((prop: any) => [prop.id, '']))])}>{tr('addRow')}</Button>
      </Stack>;
      return <TextField size="small" fullWidth multiline minRows={2} disabled={!canEditSpec || busy || locked} value={rows.join(', ')} onChange={(e) => onChange(e.target.value.split(',').map((part) => part.trim()).filter(Boolean).map((part) => item.type === 'number' ? Number(part) : part))} />;
    }
    if (kind === 'object' && shape.properties?.length) {
      const object = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
      return <Stack spacing={0.6} sx={{ gridColumn: '1 / -1' }}>{shape.properties.map((prop: any) => <Box key={prop.id} sx={{ display: 'grid', gridTemplateColumns: 'minmax(110px,0.7fr) minmax(130px,1fr)', gap: 0.6, alignItems: 'center' }}><Tooltip title={label(prop.help ?? prop.label)}><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{label(prop.short_label ?? prop.label)}{(prop.required || prop.required_flag === 'E') && <Box component="span" sx={{ color: '#f87171' }}> *</Box>}{prop.unit ? ` (${prop.unit})` : ''}</Typography></Tooltip>{renderTypedValue(prop, object[prop.id], (next) => onChange({ ...object, [prop.id]: next }), `${keyPrefix}-${prop.id}`, locked)}</Box>)}</Stack>;
    }
    if (options.length) return <Select size="small" displayEmpty disabled={!canEditSpec || busy || locked} value={text(value)} onChange={(e) => onChange(e.target.value)}><MenuItem value="">?</MenuItem>{options.map((option) => { const v = typeof option === 'string' ? option : option.value; return <MenuItem key={v} value={v}>{typeof option === 'string' ? option : label(option.label) || v}</MenuItem>; })}</Select>;
    if (kind === 'boolean') return <Select size="small" displayEmpty disabled={!canEditSpec || busy || locked} value={value == null || value === '' ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' ? '' : e.target.value === 'true')}><MenuItem value="">?</MenuItem><MenuItem value="true">{tr('yes')}</MenuItem><MenuItem value="false">{tr('no')}</MenuItem></Select>;
    return input(value, onChange, kind, shape.unit);
  };
  const renderField = (f: SchemaField, prod = false) => {
    if (f.handled_by === 'mission_profile' || f.id === '1_3') return null;
    if (f.id === 'mission_profile') return <MissionProfile key={f.id} draft={draftRef.current} disabled={!canEditSpec || busy} t={tr} lang={lang} onUnknown={(unknown) => markUnknown(f, unknown, prod)} onChange={(rows) => updateDraft((d) => {
      return { ...d, mission_profile: rows, mission_profile_status: rows.length ? 'filled' : 'missing' };
    })} label={label(f.label)} columns={f.columns ?? []} error={fieldErrors[f.id]} />;
    const store = prod ? (draftRef.current.production as Record<string, { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' }>) : draftRef.current.fields ?? {};
    const raw = store[f.id];
    const answer = raw && typeof raw === 'object' ? raw as { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' } : { value: raw };
    const unknown = answer.status === 'not_known_yet';
    const val = answer.value == null ? '' : typeof answer.value === 'string' || typeof answer.value === 'number' ? String(answer.value) : '';
    const required = isRequired(f);
    const options = f.options ?? f.choices;
    return <Box key={`${prod ? 'p' : 'f'}-${f.id}`} sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'minmax(220px, 0.8fr) minmax(240px, 1fr)' }, gap: 1.2, alignItems: 'start', py: 1, borderBottom: '1px solid var(--line-soft)' }}>
      <Box><Tooltip title={label(f.help ?? f.label)}><Typography sx={{ color: 'var(--text-1)', fontSize: 12.5, fontWeight: 600 }}>{label(f.short_label ?? f.label)}{required && <Box component="span" sx={{ color: '#f87171' }}> *</Box>}</Typography></Tooltip>{f.unit && <Typography sx={{ color: 'var(--text-4)', fontSize: 11 }}>{f.unit}</Typography>}</Box>
      <Stack spacing={0.5}>
        {f.properties?.length ? (
          <Stack spacing={0.7}>
            {f.properties.map((prop) => {
              const objectValue = answer.value && typeof answer.value === 'object' ? answer.value as Record<string, unknown> : {};
              const updateProperty = (value: unknown) => setAnswer(f, { ...objectValue, [prop.id]: value }, prod);
              return <Box key={prop.id} sx={{ display: 'grid', gridTemplateColumns: 'minmax(120px,0.7fr) minmax(150px,1fr)', gap: 0.7, alignItems: 'center' }}>
                <Tooltip title={label(prop.help ?? prop.label)}><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{label(prop.short_label ?? prop.label)}{prop.required && <Box component="span" sx={{ color: '#f87171' }}> *</Box>}{prop.unit ? ` (${prop.unit})` : ''}</Typography></Tooltip>
                {renderTypedValue(prop, objectValue[prop.id], updateProperty, `${f.id}-${prop.id}`, unknown)}
              </Box>;
            })}
          </Stack>
        ) : options?.length ? (
          <Select size="small" fullWidth displayEmpty disabled={!canEditSpec || busy || unknown} value={val} onChange={(ev) => setAnswer(f, ev.target.value, prod)}>
            <MenuItem value=""><em>?</em></MenuItem>{options.map((o) => { const value = typeof o === 'string' ? o : o.value; return <MenuItem key={value} value={value}>{typeof o === 'string' ? o : label(o.label) || value}</MenuItem>; })}
          </Select>
        ) : (
          renderTypedValue(f, answer.value, (next) => setAnswer(f, next, prod), f.id, unknown)
        )}
        {f.allow_not_known_yet !== false && <FormControlLabel sx={{ my: -0.6 }} control={<Checkbox size="small" checked={unknown} disabled={!canEditSpec || busy} onChange={(ev) => markUnknown(f, ev.target.checked, prod)} />} label={<Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{tr('unknown')}</Typography>} />}
        {fieldErrors[f.id] && <Typography role="alert" sx={{ color: '#f87171', fontSize: 11 }}>{tr('validationError')}</Typography>}
        {required && !fieldAnswerComplete(f, { ...answer, not_known_yet: unknown }) && <Typography role="alert" sx={{ color: '#f87171', fontSize: 11 }}>{tr('requiredMissing')}</Typography>}
      </Stack>
    </Box>;
  };

  if (loading) return <Box sx={{ p: 3, display: 'flex', gap: 1, alignItems: 'center' }}><CircularProgress size={18} /><Typography>{tr('loading')}</Typography></Box>;
  if (!selected) return <Box sx={{ p: 2.5, overflowY: 'auto', height: '100%' }}>
    <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, gap: 1 }}><Typography sx={{ fontSize: 18, fontWeight: 800, color: 'var(--text-0)' }}>{tr('title')}</Typography><HelpTip title={tr('titleTip')} /><Box sx={{ flex: 1 }} />{canManageGlobally && <Button size="small" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>{tr('newProject')}</Button>}</Box>
    {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
    {projects.length === 0 ? <Typography sx={{ color: 'var(--text-3)', fontSize: 13 }}>{tr('noProjects')}</Typography> : <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(260px,1fr))', gap: 1.5 }}>{projects.map((p) => { const c = completenessNumbers(p.completeness); return <Paper key={p.id} onClick={() => void loadProject(p)} sx={{ p: 1.8, cursor: 'pointer', bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', '&:hover': { borderColor: 'primary.main' } }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'start' }}><Typography sx={{ flex: 1, fontWeight: 700, color: 'var(--text-0)' }}>{p.title}</Typography><Chip size="small" label={tr(`status.${p.status}`)} /></Box><Typography sx={{ color: 'var(--text-3)', fontSize: 12, mt: 0.5 }}>{p.customer_org}</Typography><Box sx={{ mt: 1.2, display: 'flex', alignItems: 'center', gap: 1 }}><Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>{tr('completeness', { filled: c.filled, total: c.total })}</Typography><Box sx={{ flex: 1, height: 5, bgcolor: 'var(--panel)', borderRadius: 2 }}><Box sx={{ width: `${c.total ? Math.min(100, c.filled / c.total * 100) : 0}%`, height: '100%', bgcolor: 'primary.main', borderRadius: 2 }} /></Box></Box>
    </Paper>; })}</Box>}
    <CreateDialog open={createOpen} busy={busy} title={title} org={org} id={projectId} onTitle={setTitle} onOrg={setOrg} onId={setProjectId} onClose={() => setCreateOpen(false)} onSubmit={() => void submitCreate()} t={tr} />
  </Box>;

  return <Box sx={{ p: 2, overflowY: 'auto', height: '100%' }}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}><Button size="small" startIcon={<ArrowBackIcon />} onClick={async () => { try { await flushSave(); setSelected(null); setSpec(null); void refreshProjects(); } catch { setError(tr('saveFailedShort')); } }}>{tr('back')}</Button><Typography sx={{ fontSize: 17, fontWeight: 800, flex: 1, color: 'var(--text-0)' }}>{selected.title}</Typography><Chip size="small" label={tr(`status.${selected.status}`)} />{canEditProject && <Select size="small" value={selected.status} onChange={async (e) => { try { const p = await developmentApi.update(selected!.id, { status: e.target.value as ProjectSummary['status'] }); setSelected(p); await refreshProjects(); } catch (err) { setError(err instanceof Error ? err.message : String(err)); } }} aria-label={tr('statusLabel')}>{STATUSES.map((s) => <MenuItem key={s} value={s}>{tr(`status.${s}`)}</MenuItem>)}</Select>}</Box>
    <Typography sx={{ color: 'var(--text-3)', fontSize: 12, mb: 1.5 }}>{selected.customer_org} · {tr('completeness', { filled: completeCounts.filled, total: completeCounts.total })}</Typography>
    {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
    <Tabs value={view} onChange={(_, v) => void openTab(v)} sx={{ borderBottom: '1px solid var(--line-soft)', mb: 1.5 }}>
      <Tab value="specification" label={tr('tabs.specification')} /><Tab value="motors" label={tr('tabs.motors')} /><Tab value="messages" label={tr('tabs.messages')} />{canEditProject && <Tab value="notes" label={tr('tabs.notes')} />}
    </Tabs>
    {view === 'specification' && <Box>
      {!schema || !spec ? <CircularProgress size={18} /> : <>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}><Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{tr('step', { current: step + 1, total: sections.length })}</Typography><Box sx={{ flex: 1, height: 6, bgcolor: 'var(--panel)', borderRadius: 3 }}><Box sx={{ height: '100%', width: `${completeCounts.total ? Math.min(100, completeCounts.filled / completeCounts.total * 100) : 0}%`, bgcolor: 'primary.main', borderRadius: 3 }} /></Box><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{tr('completeness', { filled: completeCounts.filled, total: completeCounts.total })}</Typography><HelpTip title={tr('requiredTip')} /></Box>
        <Box sx={{ display: 'flex', gap: 0.7, overflowX: 'auto', pb: 1 }}>{sections.map((s, i) => { const n = spec.completeness?.sections?.[s.id]; return <Chip key={s.id} clickable onClick={() => setStep(i)} variant={i === step ? 'filled' : 'outlined'} color={i === step ? 'primary' : 'default'} label={`${i + 1}. ${label(s.title)}${n ? ` (${n.filled}/${n.required})` : ''}`} sx={{ flexShrink: 0 }} />; })}</Box>
        <Paper sx={{ p: { xs: 1.3, md: 2 }, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.7, mb: 1 }}><Typography sx={{ fontWeight: 700, color: 'var(--text-0)' }}>{activeSection ? label(activeSection.title) : ''}</Typography><HelpTip title={tr('requiredTip')} /></Box>
          {activeSection?.fields?.map((f) => renderField(f, activeSection.id === 'production' || activeSection.production))}
          {activeSection?.id === 'production' && <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mt: 1 }}>{tr('productionTip')}</Typography>}
        </Paper>
        {submitError.length > 0 && <Alert severity="error" sx={{ mt: 1 }}><Typography sx={{ fontWeight: 700, fontSize: 12 }}>{tr('submitBlocked')}</Typography>{submitError.map((x, i) => <Typography key={`${i}-${x}`} sx={{ fontSize: 12 }}>• {x}</Typography>)}</Alert>}
        {saveError && <Alert severity="error" sx={{ mt: 1 }}>{tr('saveFailed', { reason: saveError })}<Button size="small" onClick={() => void reloadDraft()}>{tr('reloadDraft')}</Button></Alert>}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1.2 }}><Typography sx={{ flex: 1, fontSize: 11, color: saving ? 'var(--text-3)' : '#34d399' }}>{saving ? tr('saving') : tr('autosaved')}</Typography><Button size="small" disabled={busy || step <= 0} onClick={() => setStep((v) => Math.max(0, v - 1))}>{tr('previous')}</Button><Button size="small" disabled={busy || step >= sections.length - 1} onClick={() => setStep((v) => Math.min(sections.length - 1, v + 1))}>{tr('next')}</Button><Button size="small" variant="contained" disabled={!canEditSpec || busy || saving} onClick={() => void onSubmitSpec()}>{tr('submit')}</Button></Box>
        <Divider sx={{ my: 1.5 }} /><VersionHistory versions={versions} onCompare={(a, b) => { setCompareVersions([a, b]); const va = versions.find((v) => v.version === a); const vb = versions.find((v) => v.version === b); setVersionA(va ?? null); setVersionB(vb ?? null); }} t={tr} />
      </>}
    </Box>}
    {view === 'motors' && <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}><Typography sx={{ flex: 1, fontSize: 13, color: 'var(--text-2)' }}>{tr('motorsTip')}</Typography>{canEditProject && <Button size="small" startIcon={<AddIcon />} onClick={() => setLinkOpen(true)}>{tr('linkMotor')}</Button>}</Box>
      {canEditProject && !!selected.grant_removal_candidates?.length && <Tooltip title={<Stack spacing={0.4}><Typography sx={{ fontSize: 11 }}>{tr('grantReviewTip')}</Typography>{selected.grant_removal_candidates.map((candidate) => <Typography key={`${candidate.email}/${candidate.die}/${candidate.config}`} sx={{ fontSize: 11 }}>{candidate.email} · {candidate.die}/{candidate.config} · {tr(`grantReason.${candidate.reason}`)}</Typography>)}</Stack>}><Alert severity="info" sx={{ mb: 1, py: 0 }}>{tr('grantReviewCount', { count: selected.grant_removal_candidates.length })}</Alert></Tooltip>}
      {motors.length === 0 ? <Typography sx={{ color: 'var(--text-3)', fontSize: 12 }}>{tr('noMotors')}</Typography> : <Stack spacing={1}>{motors.map((m) => <Paper key={`${m.canonical_die ?? m.die}/${m.config}`} sx={{ p: 1.5, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)' }}><Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}><Typography sx={{ flex: 1, fontWeight: 700, color: 'var(--text-0)' }}>{m.canonical_die ?? m.die} / {m.config}</Typography>{canEditProject && <Button size="small" color="error" onClick={async () => { try { await developmentApi.unlinkMotor(selected.id, m.canonical_die ?? m.die, m.config); const [freshProject, freshMotors] = await Promise.all([developmentApi.get(selected.id), developmentApi.motors(selected.id)]); setSelected(freshProject); setMotors(freshMotors); await refreshProjects(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } }}>{tr('unlink')}</Button>}<MotorOpenLink motor={m} engineer={canManageGlobally} t={tr} /></Box>
        {(m.results ?? []).length === 0 ? <Typography sx={{ color: 'var(--text-3)', fontSize: 11, mt: 0.8 }}>{tr('noResults')}</Typography> : <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(170px,1fr))', gap: 0.7, mt: 1 }}>{m.results!.flatMap((r, i) => resultCards(r, i, tr)).map((card) => { const { metrics, title: resultTitle, key } = card; return <Box key={key} sx={{ border: '1px solid var(--line-soft)', borderRadius: 1, px: 1, py: 0.7 }}><Typography sx={{ fontWeight: 600, fontSize: 11, color: 'var(--text-1)' }}>{resultTitle}</Typography><Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>{metrics.torque != null && `${tr('torque')}: ${metrics.torque} N·m`}{metrics.power != null && ` · ${tr('power')}: ${metrics.power} W`}</Typography><Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>{metrics.efficiency != null && `${tr('efficiency')}: ${metrics.efficiency}%`}{metrics.losses != null && ` · ${tr('losses')}: ${metrics.losses} W`}</Typography>{metrics.temperatures && <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>{tr('temperatures')}: {typeof metrics.temperatures === 'number' ? `${metrics.temperatures} °C` : Object.entries(metrics.temperatures).map(([k, v]) => `${temperatureLabel(k, tr)} ${v} °C`).join(', ')}</Typography>}</Box>; })}</Box>}</Paper>)}</Stack>}
      {canEditProject && <Tooltip title={tr('handoverTip')}><span><Button disabled size="small" sx={{ mt: 1 }}>{tr('handover')}</Button></span></Tooltip>}
    </Box>}
    {view === 'messages' && <Box><Stack spacing={0.8} sx={{ mb: 1 }}>{messages.map((m, i) => <Paper key={`${m.created_at}-${i}`} sx={{ p: 1, bgcolor: 'var(--panel-2)' }}><Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{m.author_display_name || tr('projectMember')} · {m.created_at}</Typography><Typography sx={{ fontSize: 12, whiteSpace: 'pre-wrap', color: 'var(--text-1)' }}>{m.text}</Typography>{m.field_ref && <Chip size="small" label={m.field_ref} />}</Paper>)}</Stack><Box sx={{ display: 'flex', gap: 1 }}><TextField size="small" fullWidth multiline maxRows={4} value={message} onChange={(e) => setMessage(e.target.value)} placeholder={tr('writeMessage')} /><Select size="small" displayEmpty value={messageFieldRef} onChange={(e) => setMessageFieldRef(e.target.value)}><MenuItem value="">{tr('noFieldReference')}</MenuItem>{sections.flatMap((section) => (section.fields ?? []).map((f) => <MenuItem key={`${section.id}:${f.id}`} value={`${section.id}:${f.id}`}>{label(f.short_label ?? f.label)}</MenuItem>))}</Select><Button disabled={busy || !message.trim()} onClick={() => void postMessage()}>{tr('send')}</Button></Box><Typography sx={{ mt: 0.5, fontSize: 10.5, color: 'var(--text-4)' }}>{tr('noEmail')}</Typography></Box>}
    {view === 'notes' && canEditProject && <NotesPanel project={selected} onSave={async (notes) => { try { const p = await developmentApi.update(selected.id, { notes }); setSelected(p); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } }} t={tr} />}
    <InviteDialog open={inviteOpen} busy={busy} email={email} role={memberRole} onEmail={setEmail} onRole={setMemberRole} onClose={() => setInviteOpen(false)} onSubmit={() => void submitInvite()} t={tr} />
    <LinkDialog open={linkOpen} busy={busy} die={die} config={config} onDie={setDie} onConfig={setConfig} onClose={() => setLinkOpen(false)} onSubmit={() => void submitLink()} t={tr} />
    {canEditProject && <Button size="small" sx={{ mt: 1 }} onClick={() => setInviteOpen(true)}>{tr('invite')}</Button>}
    {compareVersions && <VersionDiffDialog open onClose={() => setCompareVersions(null)} a={versionA} b={versionB} t={tr} schema={schema} lang={lang} />}
  </Box>;
}

function MissionProfile({ draft, disabled, onChange, onUnknown, label, columns, lang, t, error }: { draft: SpecDraft; disabled: boolean; onChange: (rows: Array<Record<string, unknown>>) => void; onUnknown: (unknown: boolean) => void; label: string; columns: Array<{ id: string; label: unknown; unit?: string }>; lang: string; t: (key: string) => string; error?: string }) {
  const rows = draft.mission_profile ?? [];
  const keys = columns.length ? columns : [{ id: 'phase', label: 'Phase' }, { id: 'torque_Nm', label: 'Torque', unit: 'N·m' }, { id: 'speed_rpm', label: 'Speed', unit: 'rpm' }, { id: 'duration_s', label: 'Duration', unit: 's' }, { id: 'battery_voltage_V', label: 'Battery voltage', unit: 'V' }];
  return <Box sx={{ py: 1, borderBottom: '1px solid var(--line-soft)' }}><Typography sx={{ color: 'var(--text-1)', fontSize: 12.5, fontWeight: 600, mb: 0.7 }}>{label} *</Typography><Box sx={{ overflowX: 'auto' }}><Box sx={{ minWidth: 700 }}><Box sx={{ display: 'grid', gridTemplateColumns: `repeat(${keys.length}, minmax(110px,1fr)) 36px`, gap: 0.5, mb: 0.5 }}>{keys.map((c) => <Typography key={c.id} sx={{ fontSize: 10.5, color: 'var(--text-3)' }}>{localeValue(c.label, lang)}{c.unit ? ` (${c.unit})` : ''}</Typography>)}<span /></Box>{rows.map((r, ix) => <Box key={ix} sx={{ display: 'grid', gridTemplateColumns: `repeat(${keys.length}, minmax(110px,1fr)) 36px`, gap: 0.5, mb: 0.5 }}>{keys.map((c) => <TextField key={c.id} size="small" disabled={disabled} type={c.id === 'phase' ? 'text' : 'number'} value={r[c.id] ?? ''} inputProps={{ 'aria-label': `${label} ${localeValue(c.label, lang)}` }} onChange={(e) => { const copy = rows.map((x) => ({ ...x })); copy[ix][c.id] = c.id === 'phase' ? e.target.value : e.target.value === '' ? '' : Number(e.target.value); onChange(copy); }} />)}<IconButton size="small" disabled={disabled} aria-label={t('remove')} onClick={() => onChange(rows.filter((_, i) => i !== ix))}>×</IconButton></Box>)}</Box></Box><Button size="small" disabled={disabled} onClick={() => onChange([...rows, Object.fromEntries(keys.map((c) => [c.id, '']))])}>{t('addPhase')}</Button>{<FormControlLabel control={<Checkbox size="small" disabled={disabled} checked={draft.mission_profile_status === 'not_known_yet'} onChange={(e) => onUnknown(e.target.checked)} />} label={<Typography sx={{ fontSize: 11 }}>{t('unknown')}</Typography>} />}<Typography sx={{ color: 'var(--text-4)', fontSize: 10.5 }}>{error || (rows.length === 0 && draft.mission_profile_status !== 'not_known_yet' ? t('requiredMissing') : '')}</Typography></Box>;
}

function CreateDialog({ open, busy, title, org, id, onTitle, onOrg, onId, onClose, onSubmit, t }: any) { return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm"><DialogTitle>{t('newProject')}</DialogTitle><DialogContent><Stack spacing={1.2} sx={{ pt: 1 }}><TextField label={t('projectTitle')} value={title} onChange={(e) => onTitle(e.target.value)} /><TextField label={t('customerOrg')} value={org} onChange={(e) => onOrg(e.target.value)} /><TextField label={t('projectId')} value={id} onChange={(e) => onId(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))} /></Stack></DialogContent><DialogActions><Button onClick={onClose}>{t('cancel')}</Button><Button disabled={busy || !title.trim() || !org.trim() || !id.trim()} onClick={onSubmit}>{t('create')}</Button></DialogActions></Dialog>; }
function InviteDialog({ open, busy, email, role, onEmail, onRole, onClose, onSubmit, t }: any) { return <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs"><DialogTitle>{t('invite')}</DialogTitle><DialogContent><Stack spacing={1.2} sx={{ pt: 1 }}><TextField label={t('email')} value={email} onChange={(e) => onEmail(e.target.value)} /><Select value={role} onChange={(e) => onRole(e.target.value)}>{['customer', 'engineer', 'owner'].map((v) => <MenuItem key={v} value={v}>{t(`memberRole.${v}`)}</MenuItem>)}</Select></Stack></DialogContent><DialogActions><Button onClick={onClose}>{t('cancel')}</Button><Button disabled={busy || !email.includes('@')} onClick={onSubmit}>{t('invite')}</Button></DialogActions></Dialog>; }
function LinkDialog({ open, busy, die, config, onDie, onConfig, onClose, onSubmit, t }: any) { return <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs"><DialogTitle>{t('linkMotor')}</DialogTitle><DialogContent><Stack spacing={1.2} sx={{ pt: 1 }}><TextField label={t('die')} value={die} onChange={(e) => onDie(e.target.value)} /><TextField label={t('config')} value={config} onChange={(e) => onConfig(e.target.value)} /></Stack></DialogContent><DialogActions><Button onClick={onClose}>{t('cancel')}</Button><Button disabled={busy || !die.trim() || !config.trim()} onClick={onSubmit}>{t('link')}</Button></DialogActions></Dialog>; }
function NotesPanel({ project, onSave, t }: { project: ProjectSummary; onSave: (s: string) => Promise<void>; t: (key: string) => string }) { const [value, setValue] = useState(project.notes ?? ''); return <Stack spacing={1}><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{t('internalNotesTip')}</Typography><TextField multiline minRows={5} value={value} onChange={(e) => setValue(e.target.value)} /><Button sx={{ alignSelf: 'start' }} onClick={() => void onSave(value)}>{t('save')}</Button></Stack>; }
function resultCards(r: import('../types').MotorResult, index: number, t: (key: string) => string) {
  const cards: Array<{ key: string; title: string; metrics: ReturnType<typeof resultMetrics> }> = [];
  const duty = r.duty || t('result');
  cards.push({ key: `${duty}-${index}-em`, title: duty, metrics: resultMetrics({ ...(r.operating_point ?? {}), ...(r.electromagnetic ?? {}), ...(r as Record<string, unknown>) }) });
  for (const [kind, value] of Object.entries(r.stored_results ?? {})) if (value && typeof value === 'object') cards.push({ key: `${duty}-${index}-${kind}`, title: `${duty} · ${resultKindLabel(kind, t)}`, metrics: resultMetrics(value as Record<string, unknown>) });
  return cards.filter((c) => Object.values(c.metrics).some((v) => v != null));
}
function resultMetrics(value: Record<string, unknown>) {
  const sources: Array<Record<string, unknown>> = [];
  const walk = (x: unknown, depth = 0) => { if (!x || typeof x !== 'object' || Array.isArray(x) || depth > 4) return; const rec = x as Record<string, unknown>; sources.push(rec); Object.values(rec).forEach((child) => walk(child, depth + 1)); };
  walk(value);
  const pick = (...keys: string[]) => { for (const source of sources) for (const key of keys) { const found = Object.entries(source).find(([name]) => name.toLowerCase() === key.toLowerCase())?.[1]; if (found != null && Number.isFinite(Number(found))) return Number(found); } return undefined; };
  const percent = pick('efficiency_pct', 'efficiency_percent', 'eta_pct');
  const ratio = pick('eta_shaft', 'efficiency_shaft', 'eta_shaft_est');
  const temps: Record<string, number> = {};
  for (const source of sources) for (const [key, raw] of Object.entries(source)) {
    if (key.toLowerCase().endsWith('_c') || key.toLowerCase().endsWith('temperature_c') || key.toLowerCase() === 't_max' || key.toLowerCase() === 't_min') {
      const n = Number(raw); if (Number.isFinite(n)) { const name = temperatureKindForKey(key); temps[name] = n; }
    }
    if ((key.toLowerCase() === 'temperatures_c' || key.toLowerCase() === 'temperatures') && raw && typeof raw === 'object') Object.assign(temps, Object.fromEntries(Object.entries(raw as Record<string, unknown>).map(([name, temperature]) => { const key = temperatureKindForKey(name); return [key, temperature]; }))); 
  }
  const genericTemps = sources.map((x) => x.temperatures_C ?? x.temperatures ?? x.temperature_C).find((x) => typeof x === 'number');
  return {
    torque: pick('t_em_nm', 'torque_nm', 't_avg_nm', 'torque_avg_nm', 't_em_avg_nm', 'shaft_torque_nm'),
    power: pick('p_shaft_out_w', 'p_shaft_w', 'p_out_w', 'power_w'),
    efficiency: percent ?? (ratio == null ? undefined : (ratio * 100)),
    losses: pick('p_loss_total_incl_mech_w', 'p_loss_total_w', 'losses_w', 'loss_w'),
    temperatures: Object.keys(temps).length ? temps : genericTemps as number | undefined,
  };
}

function MotorOpenLink({ motor, engineer, t }: { motor: ProjectMotor; engineer: boolean; t: (key: string) => string }) {
  const { user } = useAuth();
  const [error, setError] = useState('');
  useEffect(() => { setError(''); }, [user?.email, motor.die, motor.config]);
  const open = (mode: 'configure' | 'simulation') => {
    setError('');
    const duty = firstProjectMotorDuty(motor.duties, motor.default_duty);
    if (!duty) { setError(t('noSavedDuty')); return; }
    void applyCatalogDuty(motor.die, motor.config, duty, canWriteServer(), () => useUIStore.getState().setActiveTab((mode === 'simulation' ? 'simulation' : 'compare') as never)).catch(() => setError(t('configureFailed')));
  };
  return <>
    <Button size="small" disabled={motor.available === false} title={motor.available === false ? t('motorUnavailable') : undefined} onClick={() => void open('configure')}>{t('openConfigure')}</Button>
    {engineer && <Button size="small" disabled={motor.available === false} title={motor.available === false ? t('motorUnavailable') : undefined} onClick={() => void open('simulation')}>{t('openSimulation')}</Button>}
    {error && <Tooltip title={error}><Chip size="small" color="error" label="!" /></Tooltip>}
  </>;
}

function VersionHistory({ versions, onCompare, t }: { versions: SpecSnapshot[]; onCompare: (a: number, b: number) => void; t: (key: string, args?: Record<string, unknown>) => string }) { const [a, setA] = useState(''); const [b, setB] = useState(''); return <Box><Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{t('history')}</Typography>{versions.length === 0 ? <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{t('noVersions')}</Typography> : <Box sx={{ display: 'flex', gap: 0.7, alignItems: 'center', flexWrap: 'wrap' }}><Select size="small" value={a} displayEmpty onChange={(e) => setA(e.target.value)}><MenuItem value="">{t('versionA')}</MenuItem>{versions.map((v) => <MenuItem key={v.version} value={String(v.version)}>v{v.version}</MenuItem>)}</Select><Select size="small" value={b} displayEmpty onChange={(e) => setB(e.target.value)}><MenuItem value="">{t('versionB')}</MenuItem>{versions.map((v) => <MenuItem key={v.version} value={String(v.version)}>v{v.version}</MenuItem>)}</Select><Button size="small" disabled={!a || !b || a === b} onClick={() => onCompare(Number(a), Number(b))}>{t('compare')}</Button>{versions.map((v) => <Chip key={v.version} size="small" label={`v${v.version} · ${v.author_display_name || t('projectMember')}`} title={v.created_at ?? ''} />)}</Box>}</Box>; }
function VersionDiffDialog({ open, onClose, a, b, t, schema, lang }: { open: boolean; onClose: () => void; a: SpecSnapshot | null; b: SpecSnapshot | null; t: (key: string) => string; schema: DevelopmentSchema | null; lang: string }) {
  const diff = useMemo(() => shallowDiff(a?.spec ?? a?.draft, b?.spec ?? b?.draft), [a, b]);
  const labels = new Map<string, string>();
  for (const section of schema?.sections ?? []) for (const field of section.fields ?? []) { labels.set(field.id, localeValue(field.short_label ?? field.label, lang)); for (const prop of field.properties ?? []) labels.set(`${field.id}.${prop.id}`, localeValue(prop.short_label ?? prop.label, lang)); }
  const display = (path: string) => path.split('.').map((part, i, all) => labels.get(all.slice(0, i + 1).join('.')) ?? labels.get(part) ?? (part === 'fields' || part === 'production' ? '' : part)).filter(Boolean).join(' ? ') || t('specification');
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="md"><DialogTitle>{t('versionDiff')}</DialogTitle><DialogContent>{diff.length ? diff.map((d) => <Box key={d.path} sx={{ py: 0.6, borderBottom: '1px solid var(--line-soft)' }}><Typography sx={{ fontWeight: 700, fontSize: 12 }}>{display(d.path)}</Typography><Typography sx={{ fontSize: 11, color: '#fca5a5' }}>? {JSON.stringify(d.before) ?? '?'}</Typography><Typography sx={{ fontSize: 11, color: '#86efac' }}>+ {JSON.stringify(d.after) ?? '?'}</Typography></Box>) : <Typography sx={{ fontSize: 12 }}>{t('noDiff')}</Typography>}</DialogContent><DialogActions><Button onClick={onClose}>{t('close')}</Button></DialogActions></Dialog>;
}
