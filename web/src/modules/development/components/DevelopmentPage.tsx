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
import type { Completeness, DevelopmentSchema, ProjectMotor, ProjectMotorPickerItem, ProjectSnapshot, ProjectSummary, ProjectThreadMessage, SchemaField, SpecSnapshot, SpecState } from '../types';
import { createDraftSaveCoordinator, fieldAnswer, fieldAnswerComplete, type FieldAnswer, isRequired, setFieldAnswer, shallowDiff, wizardCompleteness, type SpecDraft } from '../wizardState';
import { MotorOpenActions } from '../MotorOpenActions';
import { SharedComparisonTable } from '../../../components/compare/SharedComparisonTable';
import { developmentComparisonColumns, developmentComparisonRows } from '../comparisonRows';
import { claimProjectOpenIntent, projectOpenEvent, restoreProjectOpenIntent } from '../projectOpenIntent.ts';
import { filterPickerCatalog, groupPickerCatalog, pickerDisplayLabel, pickerReasonKey, snapshotSourceKey } from '../pickerModel';
import { acceptedProjectStatus, cancelProjectStatus, pendingStatusForProject, proposeProjectStatus, visibleProjectVersions } from '../motorStatus';

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
  const [picker, setPicker] = useState<{ this_project: ProjectMotorPickerItem[]; catalog: ProjectMotorPickerItem[] }>({ this_project: [], catalog: [] });
  const [snapshots, setSnapshots] = useState<ProjectSnapshot[]>([]);
  const [showAllVersions, setShowAllVersions] = useState(false);
  const [unlinkTarget, setUnlinkTarget] = useState<ProjectMotor | null>(null);
  const [pendingProjectStatus, setPendingProjectStatus] = useState<import('../motorStatus').PendingProjectStatus | null>(null);
  const [historyDetail, setHistoryDetail] = useState<ProjectMotor | null>(null);
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
  const projectOpenEpochRef = useRef(0);
  const projectOpenInFlightRef = useRef('');
  const motorLoadEpochRef = useRef(0);
  const failedOpenIntentRef = useRef('');
  const [versions, setVersions] = useState<SpecSnapshot[]>([]);
  const [compareVersions, setCompareVersions] = useState<[number, number] | null>(null);
  const [versionA, setVersionA] = useState<SpecSnapshot | null>(null);
  const [versionB, setVersionB] = useState<SpecSnapshot | null>(null);
  const [lang, setLang] = useState(i18n.resolvedLanguage || document.documentElement.lang || 'en');
  const canManageGlobally = isAdmin || role === 'engineer';
  const canEditSpec = !!selected && (canManageGlobally || selected.can_edit_spec === true);
  const canEditProject = !!selected && (canManageGlobally || selected.can_edit === true);
  const canManageProjectMotors = canManageGlobally && canEditProject;
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
  const isCurrentProject = (id: string, token: string) => projectToken.current === token && currentProjectRef.current === id;
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
    projectOpenEpochRef.current += 1;
    projectOpenInFlightRef.current = '';
    failedOpenIntentRef.current = '';
    projectToken.current = `identity:${user?.email ?? 'signed-out'}:${Date.now()}`;
    setHistoryDetail(null);
    currentProjectRef.current = '';
    setSelected(null); setSpec(null); setProjects([]); setMessages([]); setMotors([]); setSnapshots([]); setShowAllVersions(false); setPendingProjectStatus(null); setVersions([]); setCompareVersions(null);
    setError(''); setBusy(false); setLinkOpen(false); setUnlinkTarget(null); setSubmitError([]); setFieldErrors({});
    draftRef.current = { ...DEFAULT_DRAFT }; revisionRef.current = 0;
    if (user?.email) void refreshProjects();
  }, [user?.email, refreshProjects]);

  const loadProject = useCallback(async (p: ProjectSummary, requestedEpoch?: number): Promise<boolean> => {
    const openEpoch = requestedEpoch ?? ++projectOpenEpochRef.current;
    if (selected && spec) {
      try { await flushSaveRef.current(); }
      catch {
        if (openEpoch !== projectOpenEpochRef.current) return true;
        setError(tr('saveFailedShort')); return false;
      }
    }
    if (openEpoch !== projectOpenEpochRef.current) return true;
    const identityEpoch = projectEpochRef.current;
    setSelected(p); setView('specification'); setStep(0); setSpec(null); setMessages([]); setMotors([]); setSnapshots([]); setShowAllVersions(false); setPendingProjectStatus(null); setError(''); setBusy(false); setLinkOpen(false); setUnlinkTarget(null); setHistoryDetail(null); motorLoadEpochRef.current += 1; setSubmitError([]);
    setVersions([]); setCompareVersions(null); setVersionA(null); setVersionB(null); setFieldErrors({}); setMessageFieldRef('');
    const token = `${p.id}:${Date.now()}`; projectToken.current = token; currentProjectRef.current = p.id;
    try {
      const [detail, sp] = await Promise.all([developmentApi.get(p.id), developmentApi.spec(p.id)]);
      if (projectToken.current !== token || identityEpoch !== projectEpochRef.current) return true;
      setSelected(detail); setSpec({ ...sp, draft: normaliseDraft(sp.draft) });
      draftRef.current = normaliseDraft(sp.draft); revisionRef.current = sp.revision; saveCoordinator.setRevision(p.id, sp.revision);
      return true;
    } catch (e) {
      if (projectToken.current !== token || identityEpoch !== projectEpochRef.current) return true;
      setError(e instanceof Error ? e.message : String(e)); return false;
    }
  }, [selected, spec]);
  useEffect(() => {
    const openProject = async (projectId: string | null | undefined): Promise<boolean> => {
      if (!projectId) return true;
      if (projectOpenInFlightRef.current === projectId) return true;
      if (selected?.id === projectId && spec) { setView('specification'); return true; }
      projectOpenInFlightRef.current = projectId;
      const openEpoch = ++projectOpenEpochRef.current;
      try {
        const known = projects.find((p) => p.id === projectId);
        if (known) return await loadProject(known, openEpoch);
        const project = await developmentApi.get(projectId);
        if (openEpoch !== projectOpenEpochRef.current) return true;
        return await loadProject(project, openEpoch);
      } catch (e) {
        if (openEpoch !== projectOpenEpochRef.current) return true;
        setError(e instanceof Error ? e.message : String(e)); return false;
      } finally {
        if (projectOpenInFlightRef.current === projectId) projectOpenInFlightRef.current = '';
      }
    };
    const openClaimed = (projectId: string) => {
      void openProject(projectId).then((opened) => {
        if (!opened) {
          failedOpenIntentRef.current = projectId;
          try { restoreProjectOpenIntent(sessionStorage, projectId); } catch { /* storage unavailable */ }
        }
      });
    };
    const consumePending = () => {
      try {
        const queued = sessionStorage.getItem('development.openProject');
        if (queued && queued === failedOpenIntentRef.current) return;
        const pending = claimProjectOpenIntent(sessionStorage);
        if (pending) openClaimed(pending);
      } catch { /* session storage is unavailable in some embedded contexts */ }
    };
    const onOpenProject = (event: Event) => {
      const projectId = (event as CustomEvent<{ projectId?: string }>).detail?.projectId;
      if (!projectId) return;
      failedOpenIntentRef.current = '';
      try { claimProjectOpenIntent(sessionStorage, projectId); } catch { /* the event still carries the project id */ }
      openClaimed(projectId);
    };
    consumePending();
    window.addEventListener(projectOpenEvent, onOpenProject);
    return () => window.removeEventListener(projectOpenEvent, onOpenProject);
  }, [projects, selected?.id, spec, loadProject]);

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
  const showAllVersionsRef = useRef(showAllVersions);
  showAllVersionsRef.current = showAllVersions;
  const loadProjectMotors = async (id: string, token: string, showAll = showAllVersionsRef.current) => {
    const epoch = ++motorLoadEpochRef.current;
    try {
      const [result, history] = await Promise.all([
        developmentApi.motors(id, showAll),
        showAll ? developmentApi.snapshots(id) : Promise.resolve([] as ProjectSnapshot[]),
      ]);
      if (epoch === motorLoadEpochRef.current && projectToken.current === token && currentProjectRef.current === id && showAllVersionsRef.current === showAll) {
        setMotors(result); setSnapshots(history);
      }
    } catch (e) {
      if (epoch === motorLoadEpochRef.current && projectToken.current === token && currentProjectRef.current === id) setError(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => { if (selected?.id) { const token = projectToken.current; void loadSpecHistory(selected.id, token); } }, [selected?.id]);

  const openTab = async (tab: ViewTab) => {
    setView(tab); if (!selected) return;
    const id = selected.id; const token = projectToken.current;
    try {
      if (tab === 'messages') { const result = await developmentApi.thread(id); if (projectToken.current === token && currentProjectRef.current === id) setMessages(result); }
      if (tab === 'motors') await loadProjectMotors(id, token);
    } catch (e) { if (projectToken.current === token && currentProjectRef.current === id) setError(e instanceof Error ? e.message : String(e)); }
  };
  const changeShowAllVersions = (value: boolean) => {
    setShowAllVersions(value);
    showAllVersionsRef.current = value;
    if (selected) void loadProjectMotors(selected.id, projectToken.current, value);
  };
  const confirmProjectStatus = async () => {
    const pending = pendingStatusForProject(pendingProjectStatus, selected?.id ?? '');
    const acceptedStatus = acceptedProjectStatus(pending, selected?.id ?? '', selected?.status ?? 'spec');
    if (!pending || !selected || acceptedStatus === null) { setPendingProjectStatus((value) => cancelProjectStatus(value)); return; }
    const id = selected.id; const token = projectToken.current;
    setBusy(true); setError('');
    try {
      const updated = await developmentApi.update(id, { status: acceptedStatus });
      if (!isCurrentProject(id, token) || updated.status !== pending.proposed) return;
      setSelected(updated); setPendingProjectStatus((value) => cancelProjectStatus(value)); await refreshProjects();
    } catch (err) {
      if (isCurrentProject(id, token)) setError(err instanceof Error ? err.message : String(err));
    } finally { if (isCurrentProject(id, token)) setBusy(false); }
  };
  const changeMotorStatus = async (motor: ProjectMotor, status: 'current' | 'trial') => {
    if (!selected) return;
    const id = selected.id; const token = projectToken.current;
    setBusy(true); setError('');
    try {
      await developmentApi.setMotorStatus(id, motor.canonical_die ?? motor.die, motor.config, status);
      if (isCurrentProject(id, token)) { await loadProjectMotors(id, token); await refreshProjects(); }
    } catch (err) { if (isCurrentProject(id, token)) setError(err instanceof Error ? err.message : String(err)); }
    finally { if (isCurrentProject(id, token)) setBusy(false); }
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
  const submitLink = async (item: ProjectMotorPickerItem) => {
    if (!selected) return;
    const id = selected.id; const token = projectToken.current;
    const isCurrent = () => projectToken.current === token && currentProjectRef.current === id;
    setBusy(true); setError('');
    try { await developmentApi.linkMotor(id, item.die, item.config); if (isCurrent()) { setLinkOpen(false); await loadProjectMotors(id, token); await refreshProjects(); } }
    catch (e) { if (isCurrent()) setError(e instanceof Error ? e.message : String(e)); } finally { if (isCurrent()) setBusy(false); }
  };
  const restoreMotor = async (item: ProjectMotorPickerItem) => { if (!selected) return; const id = selected.id; const token = projectToken.current; const isCurrent = () => projectToken.current === token && currentProjectRef.current === id; setBusy(true); setError(''); try { await developmentApi.linkMotor(id, item.die, item.config); const choices = await developmentApi.motorPicker(id); if (isCurrent()) { setPicker(choices); await loadProjectMotors(id, token); await refreshProjects(); } } catch (e) { if (isCurrent()) setError(e instanceof Error ? e.message : String(e)); } finally { if (isCurrent()) setBusy(false); } };
  const confirmUnlink = async () => { if (!selected || !unlinkTarget) return; const id = selected.id; const token = projectToken.current; const isCurrent = () => projectToken.current === token && currentProjectRef.current === id; const target = unlinkTarget; setBusy(true); setError(''); try { await developmentApi.unlinkMotor(id, target.canonical_die ?? target.die, target.config); const choices = await developmentApi.motorPicker(id); if (isCurrent()) { setUnlinkTarget(null); setPicker(choices); await loadProjectMotors(id, token); await refreshProjects(); } } catch (e) { if (isCurrent()) setError(e instanceof Error ? e.message : String(e)); } finally { if (isCurrent()) setBusy(false); } };
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
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}><Button size="small" startIcon={<ArrowBackIcon />} onClick={async () => { try { await flushSave(); setSelected(null); setSpec(null); setPendingProjectStatus(null); void refreshProjects(); } catch { setError(tr('saveFailedShort')); } }}>{tr('back')}</Button><Typography sx={{ fontSize: 17, fontWeight: 800, flex: 1, color: 'var(--text-0)' }}>{selected.title}</Typography><HelpTip title={tr('projectStatusTip')} />{canEditProject ? <><Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{tr('statusLabel')}</Typography><Select size="small" value={selected.status} disabled={busy} onChange={(e) => setPendingProjectStatus(proposeProjectStatus(selected.id, selected.status, e.target.value as ProjectSummary['status']))} aria-label={tr('statusLabel')}>{STATUSES.map((s) => <MenuItem key={s} value={s}>{tr(`status.${s}`)}</MenuItem>)}</Select></> : <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{tr('statusLabel')}: {tr(`status.${selected.status}`)}</Typography>}</Box>
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
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}><Typography sx={{ flex: 1, fontSize: 13, color: 'var(--text-2)' }}>{tr('motorsTip')}</Typography>{canManageGlobally && <Button size="small" startIcon={<AddIcon />} onClick={async () => { if (selected) { try { setPicker(await developmentApi.motorPicker(selected.id)); setLinkOpen(true); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } } }}>{tr('linkMotor')}</Button>}</Box>
      {canEditProject && !!selected.grant_removal_candidates?.length && <Tooltip title={<Stack spacing={0.4}><Typography sx={{ fontSize: 11 }}>{tr('grantReviewTip')}</Typography>{selected.grant_removal_candidates.map((candidate) => <Typography key={`${candidate.email}/${candidate.die}/${candidate.config}`} sx={{ fontSize: 11 }}>{candidate.email} · {candidate.die}/{candidate.config} · {tr(`grantReason.${candidate.reason}`)}</Typography>)}</Stack>}><Alert severity="info" sx={{ mb: 1, py: 0 }}>{tr('grantReviewCount', { count: selected.grant_removal_candidates.length })}</Alert></Tooltip>}
      {(() => { const visibleMotors = visibleProjectVersions(motors, snapshots, showAllVersions); const rows = developmentComparisonRows(visibleMotors); return <>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.7 }}><Typography sx={{ flex: 1, fontSize: 11, color: 'var(--text-3)' }}>{tr('comparisonCount', { variants: visibleMotors.length, results: rows.filter((r) => r.hasResult).length })}</Typography><FormControlLabel control={<Checkbox size="small" checked={showAllVersions} onChange={(e) => changeShowAllVersions(e.target.checked)} />} label={tr('showAllVersions')} sx={{ '& .MuiFormControlLabel-label': { fontSize: 11 } }} /></Stack>
        {visibleMotors.length === 0 ? <Typography sx={{ color: 'var(--text-3)', fontSize: 12 }}>{tr('noMotors')}</Typography> : <SharedComparisonTable rows={rows} columns={developmentComparisonColumns(tr, rows)} rowKey={(r) => r.id} rowLabel={(r) => r.history ? <Stack spacing={0.1}><Stack direction="row" spacing={0.5} alignItems="center"><Button size="small" onClick={() => setHistoryDetail(r.motor)}>{tr('snapshots.history')} · {r.variant}</Button>{r.estimate && <Chip size="small" label={tr('comparison.estimate')} />}</Stack><Typography sx={{ fontSize: 9, color: 'var(--text-4)' }}>{[r.motor.author_display_name, r.motor.created_at ? new Date(r.motor.created_at).toLocaleString() : '', r.motor.source ? snapshotSourceLabel(r.motor.source, tr) : ''].filter(Boolean).join(' · ')}</Typography></Stack> : <Stack direction="row" spacing={0.5} alignItems="center"><span>{r.variant}</span>{r.estimate && <Chip size="small" label={tr('comparison.estimate')} />}</Stack>} labelHeader={tr('variant')} showAllInputs enableColumnPicker columnPickerTitle={tr('comparison.columns')} columnPickerLabel={tr('comparison.chooseColumns')} empty={<Typography>{tr('noResults')}</Typography>} renderHeadEnd={() => <Box component="th" sx={{ px: 1, py: 0.65, fontSize: 10, color: 'var(--text-3)', borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)', textAlign: 'left' }}>{tr('comparison.actions')}</Box>} renderRowEnd={(r) => <Box component="td" sx={{ px: 0.5, borderBottom: '1px solid var(--app-bg)', whiteSpace: 'nowrap' }}>{r.history ? null : <Stack direction="row" spacing={0.5} alignItems="center"><MotorOpenActions motor={r.motor} duty={r.result.duty} engineer={canManageGlobally} t={tr} /><Tooltip title={tr('motorStatus.tip')}><span>{canManageProjectMotors ? <Select size="small" value={r.motor.status === 'trial' ? 'trial' : 'current'} disabled={busy} onChange={(e) => void changeMotorStatus(r.motor, e.target.value as 'current' | 'trial')} aria-label={tr('motorStatus.label')} sx={{ minWidth: 90, '& .MuiSelect-select': { py: 0.35, fontSize: 11 } }}><MenuItem value="current">{tr('motorStatus.current')}</MenuItem><MenuItem value="trial">{tr('motorStatus.trial')}</MenuItem></Select> : <Chip size="small" label={tr(`motorStatus.${r.motor.status === 'trial' ? 'trial' : 'current'}`)} />}</span></Tooltip>{canManageProjectMotors && <Button size="small" color="error" disabled={busy} onClick={() => setUnlinkTarget(r.motor)}>{tr('unlink')}</Button>}</Stack>}</Box>} />}
      </>; })()}
    </Box>}
    {view === 'messages' && <Box><Stack spacing={0.8} sx={{ mb: 1 }}>{messages.map((m, i) => <Paper key={`${m.created_at}-${i}`} sx={{ p: 1, bgcolor: 'var(--panel-2)' }}><Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{m.author_display_name || tr('projectMember')} · {m.created_at}</Typography><Typography sx={{ fontSize: 12, whiteSpace: 'pre-wrap', color: 'var(--text-1)' }}>{m.text}</Typography>{m.field_ref && <Chip size="small" label={m.field_ref} />}</Paper>)}</Stack><Box sx={{ display: 'flex', gap: 1 }}><TextField size="small" fullWidth multiline maxRows={4} value={message} onChange={(e) => setMessage(e.target.value)} placeholder={tr('writeMessage')} /><Select size="small" displayEmpty value={messageFieldRef} onChange={(e) => setMessageFieldRef(e.target.value)}><MenuItem value="">{tr('noFieldReference')}</MenuItem>{sections.flatMap((section) => (section.fields ?? []).map((f) => <MenuItem key={`${section.id}:${f.id}`} value={`${section.id}:${f.id}`}>{label(f.short_label ?? f.label)}</MenuItem>))}</Select><Button disabled={busy || !message.trim()} onClick={() => void postMessage()}>{tr('send')}</Button></Box><Typography sx={{ mt: 0.5, fontSize: 10.5, color: 'var(--text-4)' }}>{tr('noEmail')}</Typography></Box>}
    {view === 'notes' && canEditProject && <NotesPanel project={selected} onSave={async (notes) => { try { const p = await developmentApi.update(selected.id, { notes }); setSelected(p); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } }} t={tr} />}
    <InviteDialog open={inviteOpen} busy={busy} email={email} role={memberRole} onEmail={setEmail} onRole={setMemberRole} onClose={() => setInviteOpen(false)} onSubmit={() => void submitInvite()} t={tr} />
    <LinkDialog open={linkOpen} busy={busy} picker={picker} onClose={() => setLinkOpen(false)} onSubmit={(item: ProjectMotorPickerItem) => void submitLink(item)} onRestore={(item: ProjectMotorPickerItem) => void restoreMotor(item)} t={tr} />
    <Dialog open={!!unlinkTarget} onClose={() => !busy && setUnlinkTarget(null)}><DialogTitle>{tr('unlink')} {unlinkTarget?.die} / {unlinkTarget?.config}?</DialogTitle><DialogContent>{tr('unlinkConfirm')}</DialogContent><DialogActions><Button disabled={busy} onClick={() => setUnlinkTarget(null)}>{tr('cancel')}</Button><Button color="error" disabled={busy} onClick={() => void confirmUnlink()}>{tr('unlink')}</Button></DialogActions></Dialog>
    <Dialog open={!!pendingStatusForProject(pendingProjectStatus, selected.id)} onClose={() => !busy && setPendingProjectStatus((value) => cancelProjectStatus(value))}><DialogTitle>{tr('confirmProjectStatus.title')}</DialogTitle><DialogContent>{tr('confirmProjectStatus.body', { status: pendingProjectStatus ? tr(`status.${pendingProjectStatus.proposed}`) : '' })}</DialogContent><DialogActions><Button disabled={busy} onClick={() => setPendingProjectStatus((value) => cancelProjectStatus(value))}>{tr('cancel')}</Button><Button disabled={busy} variant="contained" onClick={() => void confirmProjectStatus()}>{tr('confirmProjectStatus.confirm')}</Button></DialogActions></Dialog>
    <Dialog open={!!historyDetail} onClose={() => setHistoryDetail(null)} fullWidth maxWidth="sm"><DialogTitle>{tr('snapshots.history')} · {historyDetail?.die} / {historyDetail?.config}</DialogTitle><DialogContent><Stack spacing={0.7} sx={{ pt: 0.5 }}><Typography variant="body2">{historyDetail?.author_display_name} · {historyDetail?.created_at ? new Date(historyDetail.created_at).toLocaleString() : ''}</Typography>{historyDetail?.source && <Typography variant="body2">{tr('snapshots.source')}: {snapshotSourceLabel(historyDetail.source, tr)}</Typography>}{historyDetail?.note && <Tooltip title={historyDetail.note}><Typography variant="body2" noWrap sx={{ maxWidth: '100%' }}>{historyDetail.note}</Typography></Tooltip>}</Stack></DialogContent><DialogActions><Button onClick={() => setHistoryDetail(null)}>{tr('close')}</Button></DialogActions></Dialog>
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
function snapshotSourceLabel(source: string, t: (key: string) => string): string { return t(snapshotSourceKey(source) ?? 'snapshots.sourceOther'); }

function LinkDialog({ open, busy, picker, onClose, onSubmit, onRestore, t }: { open: boolean; busy: boolean; picker: { this_project: ProjectMotorPickerItem[]; catalog: ProjectMotorPickerItem[] }; onClose: () => void; onSubmit: (item: ProjectMotorPickerItem) => void; onRestore: (item: ProjectMotorPickerItem) => void; t: (key: string, args?: Record<string, unknown>) => string }) {
  const [query, setQuery] = useState(''); const [selected, setSelected] = useState<ProjectMotorPickerItem | null>(null);
  useEffect(() => { if (open) { setQuery(''); setSelected(null); } }, [open]);
  const groups = useMemo(() => groupPickerCatalog(filterPickerCatalog(picker.catalog, query)), [picker.catalog, query]);
  return <Dialog open={open} onClose={() => !busy && onClose()} fullWidth maxWidth="sm"><DialogTitle>{t('linkMotor')}</DialogTitle><DialogContent>
    <Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{t('picker.thisProject')}</Typography>
    {picker.this_project.length ? picker.this_project.map((item) => <Box key={`${item.die}/${item.config}`} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.35 }}><Box sx={{ flex: 1 }}><Typography sx={{ fontSize: 12 }}>{pickerDisplayLabel(item, t)}</Typography><Tooltip title={item.unlinked_by ? `${item.unlinked_by} · ${item.unlinked_at ?? ''}` : item.unlinked_at ?? ''}><Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{item.die} / {item.config}</Typography></Tooltip></Box><Tooltip title={item.disabled ? t(pickerReasonKey(item.reason)) : ''}><span><Button size="small" disabled={busy || item.disabled} onClick={() => onRestore(item)}>{t('picker.restore')}</Button></span></Tooltip></Box>) : <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mb: 1 }}>{t('picker.noRestore')}</Typography>}
    <Divider sx={{ my: 1 }} /><Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{t('picker.catalog')}</Typography>
    <TextField size="small" fullWidth placeholder={t('picker.search')} value={query} onChange={(e) => setQuery(e.target.value)} sx={{ mb: 0.7 }} />
    <Box sx={{ maxHeight: 300, overflowY: 'auto' }}>{groups.map(([die, items]) => <Box key={die}><Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', py: 0.45 }}>{die}</Typography>{items.map((item) => <Button key={`${die}/${item.config}`} fullWidth disabled={busy || item.disabled} onClick={() => setSelected(item)} sx={{ justifyContent: 'space-between', textAlign: 'left', color: selected === item ? 'primary.main' : 'var(--text-1)', textTransform: 'none', borderBottom: '1px solid var(--line-soft)' }}><span>{item.config} · {pickerDisplayLabel(item, t)}</span>{item.disabled && <Tooltip title={t(pickerReasonKey(item.reason))}><span>{t('picker.unavailable')}</span></Tooltip>}</Button>)}</Box>)}</Box>
  </DialogContent><DialogActions><Button disabled={busy} onClick={onClose}>{t('cancel')}</Button><Button disabled={busy || !selected || selected.disabled} onClick={() => selected && onSubmit(selected)}>{t('link')}</Button></DialogActions></Dialog>;
}
function NotesPanel({ project, onSave, t }: { project: ProjectSummary; onSave: (s: string) => Promise<void>; t: (key: string) => string }) { const [value, setValue] = useState(project.notes ?? ''); return <Stack spacing={1}><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{t('internalNotesTip')}</Typography><TextField multiline minRows={5} value={value} onChange={(e) => setValue(e.target.value)} /><Button sx={{ alignSelf: 'start' }} onClick={() => void onSave(value)}>{t('save')}</Button></Stack>; }
function VersionHistory({ versions, onCompare, t }: { versions: SpecSnapshot[]; onCompare: (a: number, b: number) => void; t: (key: string, args?: Record<string, unknown>) => string }) { const [a, setA] = useState(''); const [b, setB] = useState(''); return <Box><Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{t('history')}</Typography>{versions.length === 0 ? <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{t('noVersions')}</Typography> : <Box sx={{ display: 'flex', gap: 0.7, alignItems: 'center', flexWrap: 'wrap' }}><Select size="small" value={a} displayEmpty onChange={(e) => setA(e.target.value)}><MenuItem value="">{t('versionA')}</MenuItem>{versions.map((v) => <MenuItem key={v.version} value={String(v.version)}>v{v.version}</MenuItem>)}</Select><Select size="small" value={b} displayEmpty onChange={(e) => setB(e.target.value)}><MenuItem value="">{t('versionB')}</MenuItem>{versions.map((v) => <MenuItem key={v.version} value={String(v.version)}>v{v.version}</MenuItem>)}</Select><Button size="small" disabled={!a || !b || a === b} onClick={() => onCompare(Number(a), Number(b))}>{t('compare')}</Button>{versions.map((v) => <Chip key={v.version} size="small" label={`v${v.version} · ${v.author_display_name || t('projectMember')}`} title={v.created_at ?? ''} />)}</Box>}</Box>; }
function VersionDiffDialog({ open, onClose, a, b, t, schema, lang }: { open: boolean; onClose: () => void; a: SpecSnapshot | null; b: SpecSnapshot | null; t: (key: string) => string; schema: DevelopmentSchema | null; lang: string }) {
  const diff = useMemo(() => shallowDiff(a?.spec ?? a?.draft, b?.spec ?? b?.draft), [a, b]);
  const labels = new Map<string, string>();
  for (const section of schema?.sections ?? []) for (const field of section.fields ?? []) { labels.set(field.id, localeValue(field.short_label ?? field.label, lang)); for (const prop of field.properties ?? []) labels.set(`${field.id}.${prop.id}`, localeValue(prop.short_label ?? prop.label, lang)); }
  const display = (path: string) => path.split('.').map((part, i, all) => labels.get(all.slice(0, i + 1).join('.')) ?? labels.get(part) ?? (part === 'fields' || part === 'production' ? '' : part)).filter(Boolean).join(' ? ') || t('specification');
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="md"><DialogTitle>{t('versionDiff')}</DialogTitle><DialogContent>{diff.length ? diff.map((d) => <Box key={d.path} sx={{ py: 0.6, borderBottom: '1px solid var(--line-soft)' }}><Typography sx={{ fontWeight: 700, fontSize: 12 }}>{display(d.path)}</Typography><Typography sx={{ fontSize: 11, color: '#fca5a5' }}>? {JSON.stringify(d.before) ?? '?'}</Typography><Typography sx={{ fontSize: 11, color: '#86efac' }}>+ {JSON.stringify(d.after) ?? '?'}</Typography></Box>) : <Typography sx={{ fontSize: 12 }}>{t('noDiff')}</Typography>}</DialogContent><DialogActions><Button onClick={onClose}>{t('close')}</Button></DialogActions></Dialog>;
}
