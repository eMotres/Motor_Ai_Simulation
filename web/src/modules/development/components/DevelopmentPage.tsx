import { deliveryConflicts, matchesFieldReference } from '../reviewPresentation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
  import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, InputAdornment,
  Divider, FormControlLabel, IconButton, MenuItem, Paper, Select, Stack, Tab, Tabs, TextField,
  Tooltip, Typography, Checkbox,
} from '@mui/material';
  import AddIcon from '@mui/icons-material/Add';
  import ArrowBackIcon from '@mui/icons-material/ArrowBack';
  import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
  import HelpTip from '../../../components/common/HelpTip';
  import { useAuth } from '../../../contexts/AuthContext';
  import { useTranslation } from 'react-i18next';
  import { developmentApi, DevelopmentApiError } from '../api';
  import type { Completeness, DevelopmentSchema, ProjectMotor, ProjectMotorPickerItem, ProjectSnapshot, ProjectSummary, ProjectThreadMessage, SchemaField, SpecSnapshot, SpecState } from '../types';
  import LoadSection from './LoadSection';
  import MissionProfileEditor from './MissionProfileEditor';
  import type { MissionRow } from '../missionProfileState';
  import { readLoadDraft, writeLoadDraft } from '../loadDraftAdapter';
  import { buildSpecificationCards, type SpecificationField } from '../specificationLayout';
  import { canApplyMigrationPreview, migrationS1Candidate, type MigrationPreviewView } from '../migrationPresentation';
  import { formatDiffLabel, formatDiffValue, semanticSpecificationDiff } from '../specificationDiff';
  import { createDraftSaveCoordinator, fieldAnswer, fieldAnswerComplete, type FieldAnswer, fieldRequired, requiredFieldIds, sectionAnswerCounts, setFieldAnswer, sectionStatus, wizardCompleteness, withS1DerivedPower, type SpecDraft } from '../wizardState';
  import { MotorOpenActions } from '../MotorOpenActions';
  import { SharedComparisonTable } from '../../../components/compare/SharedComparisonTable';
  import { developmentComparisonColumns, developmentComparisonRows } from '../comparisonRows';
  import { claimProjectOpenIntent, projectOpenEvent, restoreProjectOpenIntent } from '../projectOpenIntent.ts';
  import { filterPickerCatalog, groupPickerCatalog, pickerDisplayLabel, pickerReasonKey, snapshotSourceKey } from '../pickerModel';
  import { acceptedProjectStatus, cancelProjectStatus, pendingStatusForProject, proposeProjectStatus, visibleProjectVersions } from '../motorStatus';
type ViewTab = 'specification' | 'motors' | 'messages' | 'notes';
const STATUSES = ['spec', 'design', 'simulation', 'quote', 'prototype', 'production', 'closed'] as const;
const DEFAULT_DRAFT: SpecDraft = { schema_version: 2, fields: {}, mission_profile: [], production: {} };
const localeValue = (v: unknown, lang: string): string => {
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    return String(o[lang === 'zh-CN' ? 'zh-CN' : 'en'] ?? o.en ?? '');
  }
  return '';
};
const completenessNumbers = (c?: Completeness) => ({ filled: c?.filled_required ?? c?.filled ?? 0, total: c?.total_required ?? c?.required ?? 0 });
const retainedS1Range = (draft: SpecDraft): Record<string, unknown> | null => {
  const provenance = draft.provenance as Record<string, unknown> | undefined;
  const history = provenance?.schema_migrations as Array<Record<string, unknown>> | undefined;
  const legacy = history?.at(-1)?.legacy_1_4;
  if (!legacy || typeof legacy !== 'object') return null;
  const answer = legacy as Record<string, unknown>;
  return answer.value && typeof answer.value === 'object' ? answer.value as Record<string, unknown> : answer;
};
const readableRange = (value: unknown): string => {
  if (value == null) return '';
  if (typeof value !== 'object') return String(value);
  const range = value as Record<string, unknown>;
  if ('value' in range) return readableRange(range.value);
  const lo = range.minimum ?? range.min ?? range.from;
  const hi = range.maximum ?? range.max ?? range.to;
  return lo == null && hi == null ? '' : `${lo ?? '—'}–${hi ?? '—'}`;
};
const finiteScalar = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const s1Values = (sections: ReturnType<typeof buildSpecificationCards>, draft: SpecDraft, version: number) => {
  const field = sections.flatMap((section) => section.fields).find((item) => item.id === '1_4');
  const answer = field ? fieldAnswer(draft, field.id) : undefined;
  const value = answer?.status === 'not_known_yet' ? null : answer?.value;
  if (version < 2 || !value || typeof value !== 'object') return null;
  const point = withS1DerivedPower(value as Record<string, unknown>);
  const torque = finiteScalar(point.torque_Nm), speed = finiteScalar(point.speed_rpm), power = finiteScalar(point.shaft_power_W);
  if (torque == null || speed == null || power == null) return null;
  return { torque, speed, power };
};
const s1Headline = (sections: ReturnType<typeof buildSpecificationCards>, draft: SpecDraft, version: number, locale: string) => {
  const p = s1Values(sections, draft, version);
  return p ? `${p.torque.toLocaleString(locale)} N·m @ ${p.speed.toLocaleString(locale)} rpm` : '—';
};
const s1Power = (sections: ReturnType<typeof buildSpecificationCards>, draft: SpecDraft, version: number, locale: string) => {
  const p = s1Values(sections, draft, version);
  return p ? `${(p.power / 1000).toLocaleString(locale, { maximumFractionDigits: 2 })} kW · ${p.power.toLocaleString(locale, { maximumFractionDigits: 2 })} W` : '—';
};
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
  const [migrationPreview, setMigrationPreview] = useState<Awaited<ReturnType<typeof developmentApi.migrationPreview>>['preview'] | null>(null);
  const [migrationOpen, setMigrationOpen] = useState(false);
  const [migrationAck, setMigrationAck] = useState(false);
  const [migrationBusy, setMigrationBusy] = useState(false);
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [submitError, setSubmitError] = useState<string[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [messages, setMessages] = useState<ProjectThreadMessage[]>([]);
  const [message, setMessage] = useState('');
  const [messageFieldRef, setMessageFieldRef] = useState('');
  const [reviewField, setReviewField] = useState('');
  const [reviewComment, setReviewComment] = useState('');
  const [fieldReviewOpen, setFieldReviewOpen] = useState(false);
  const [reloadPreview, setReloadPreview] = useState<{ project: string; token: string; fresh: SpecState } | null>(null);
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
  const sections = useMemo(() => schema ? buildSpecificationCards(schema) : [], [schema]);
  const completeness = spec ? wizardCompleteness(sections, spec.draft as SpecDraft) : undefined;
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
      if (epoch === projectEpochRef.current) { setProjects(list); if (!currentProjectRef.current) setSchema(s); }
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
    setReloadPreview(null); setMigrationOpen(false); setMigrationPreview(null); setMigrationAck(false); setFieldReviewOpen(false); setReviewField(''); setReviewComment('');    setError(''); setBusy(false); setLinkOpen(false); setUnlinkTarget(null); setSubmitError([]); setFieldErrors({});
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
    setReloadPreview(null); setMigrationOpen(false); setMigrationPreview(null); setMigrationAck(false); setFieldReviewOpen(false); setReviewField(''); setReviewComment('');    setVersions([]); setCompareVersions(null); setVersionA(null); setVersionB(null); setFieldErrors({}); setMessageFieldRef('');
    const token = `${p.id}:${Date.now()}`; projectToken.current = token; currentProjectRef.current = p.id;
    try {
      const [detail, sp] = await Promise.all([developmentApi.get(p.id), developmentApi.spec(p.id)]);
      if (projectToken.current !== token || identityEpoch !== projectEpochRef.current) return true;      void developmentApi.thread(p.id).then((thread) => { if (projectToken.current === token && currentProjectRef.current === p.id) setMessages(thread); }).catch(() => {});
      {
        const versionSchema = sp.schema ?? await developmentApi.schema(sp.schema_version ?? 1);
        if (projectToken.current !== token || identityEpoch !== projectEpochRef.current) return true;
        setSchema(versionSchema);
      }
      const loadedDraft = { ...normaliseDraft(sp.draft), schema_version: sp.schema_version ?? 1 };
      setSelected(detail); setSpec({ ...sp, draft: loadedDraft });
      draftRef.current = loadedDraft; revisionRef.current = sp.revision; saveCoordinator.setRevision(p.id, sp.revision);
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
      setSpec((old) => old ? { ...old, revision: revisionRef.current, completeness: wizardCompleteness(sections, draftRef.current) } : old);
    }).catch((e: unknown) => {
      saveFailed.current = true;
      if (current()) setSaveError(tr('saveFailedShort'));
      throw e;
    }).finally(() => { if (current() && value === draftRef.current && !saveTimer.current) setSaving(false); });
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
    const project = selected.id, token = projectToken.current;
    if (saveTimer.current) { clearTimeout(saveTimer.current); saveTimer.current = null; }
    await saveCoordinator.flush().catch(() => {});
    try {
      const fresh = await developmentApi.spec(project);
      if (projectToken.current === token && currentProjectRef.current === project) setReloadPreview({ project, token, fresh });
    } catch { if (projectToken.current === token) setError(tr('reloadFailed')); }
  };
  const confirmReload = () => {
    if (!reloadPreview || reloadPreview.token !== projectToken.current || reloadPreview.project !== currentProjectRef.current) return;
    const { fresh, project } = reloadPreview;
    const draft = { ...normaliseDraft(fresh.draft), schema_version: fresh.schema_version ?? 1 };
    draftRef.current = draft; revisionRef.current = fresh.revision; saveCoordinator.setRevision(project, fresh.revision);
    saveFailed.current = false; setSaveError(''); setSaving(false); setFieldErrors({}); setSubmitError([]);
    if (fresh.schema) setSchema(fresh.schema);
    setSpec({ ...fresh, draft }); setReloadPreview(null);
  };
  const requestMigrationPreview = async () => {
    if (!selected || !spec || !canEditSpec || (spec.schema_version ?? 1) >= 2) return;
    const id = selected.id, token = projectToken.current;
    setMigrationBusy(true); setError('');
    try {
      await flushSave();
      if (!isCurrentProject(id, token)) return;
      const result = await developmentApi.migrationPreview(id);
      if (!isCurrentProject(id, token)) return;
      setMigrationPreview(result.preview); setMigrationAck(!result.preview.requires_acknowledgement); setMigrationOpen(true);
    } catch (e) { if (isCurrentProject(id, token)) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (isCurrentProject(id, token)) setMigrationBusy(false); }
  };
  const applyMigration = async () => {
    if (!selected || !migrationPreview || !canApplyMigrationPreview(migrationPreview as MigrationPreviewView, revisionRef.current, migrationAck)) return;
    const id = selected.id, token = projectToken.current;
    setMigrationBusy(true); setError('');
    try {
      // Fetch the immutable target schema before mutating the saved draft or local refs.
      const nextSchema = await developmentApi.schema(2);      if (!isCurrentProject(id, token)) return;      if (!canApplyMigrationPreview(migrationPreview as MigrationPreviewView, revisionRef.current, migrationAck)) {        setError(tr('migration.stale')); return;      }
      const result = await developmentApi.migrationApply(id, { revision: migrationPreview.source_revision, source_sha256: migrationPreview.source_sha256, acknowledge_point: migrationAck });
      if (!isCurrentProject(id, token)) return;
      const draft = { ...normaliseDraft(result.draft), schema_version: result.schema_version };
      setSchema(nextSchema);      draftRef.current = draft; revisionRef.current = result.revision; saveCoordinator.setRevision(id, result.revision);
      saveFailed.current = false;
      setSpec((old) => old ? { ...old, draft, revision: result.revision, schema_version: result.schema_version, schema: nextSchema, completeness: result.completeness } : old);
      setReloadPreview(null); setMigrationOpen(false); setMigrationPreview(null); setMigrationAck(false);
    } catch (e) { if (isCurrentProject(id, token)) { if (e instanceof DevelopmentApiError && e.status === 409) { setError(tr('migration.stale')); } else setError(e instanceof Error ? e.message : String(e)); } }
    finally { if (isCurrentProject(id, token)) setMigrationBusy(false); }
  };
  const updateDraft = useCallback((fn: (d: SpecDraft) => SpecDraft) => {
    if (!selected || !spec || !canEditSpec || busy || migrationBusy || !!reloadPreview) return;
    const next = fn(draftRef.current); draftRef.current = next;    setSaving(true);
    setSpec((old) => old ? { ...old, draft: next, completeness: wizardCompleteness(sections, next) } : old);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const p = selected.id; const token = projectToken.current;
    saveTimer.current = setTimeout(() => { saveTimer.current = null; void enqueueSave(p, draftRef.current, token).catch(() => {}); }, 650);
  }, [selected, spec, canEditSpec, sections, enqueueSave, busy, migrationBusy, reloadPreview]);
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
    const missing = requiredFieldIds(sections, draftRef.current);    if (missing.length) {      setSubmitError(missing.map((id) => `${label(sections.flatMap((s) => s.fields).find((f) => f.id === id)?.short_label ?? id)}: ${tr('validationError')}`));      setFieldErrors(Object.fromEntries(missing.map((id) => [id, tr('validationError')])));      const first = sections.findIndex((s) => s.fields.some((f) => missing.includes(f.id)));      if (first >= 0) { setStep(first); document.getElementById(`spec-card-${sections[first].id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }      document.getElementById(`spec-field-${missing[0]}`)?.querySelector<HTMLElement>('input,textarea,[role="combobox"]')?.focus();      return;    }
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
  const postFieldReview = async () => {    if (!selected || !reviewField || !reviewComment.trim()) return;    const id = selected.id, token = projectToken.current;    const rowId = reviewField.startsWith('mission_profile:') ? reviewField.slice('mission_profile:'.length) : undefined;    setBusy(true); setError('');    try {      if (canEditSpec) await flushSave();      if (projectToken.current !== token || currentProjectRef.current !== id) return;      await developmentApi.postMessage(id, reviewComment.trim(), reviewField, { revision: revisionRef.current, row_id: rowId });      const thread = await developmentApi.thread(id);      if (projectToken.current === token && currentProjectRef.current === id) { setReviewComment(''); setMessages(thread); }    }    catch (e) { if (projectToken.current === token && currentProjectRef.current === id) setError(e instanceof Error ? e.message : String(e)); }    finally { if (projectToken.current === token) setBusy(false); }  };  const resolveReviewMessage = async (messageId: string, resolved: boolean) => {    if (!selected) return;    const id = selected.id, token = projectToken.current;    try { await developmentApi.resolveMessage(id, messageId, resolved); const thread = await developmentApi.thread(id); if (projectToken.current === token && currentProjectRef.current === id) setMessages(thread); }    catch (e) { if (projectToken.current === token) setError(e instanceof Error ? e.message : String(e)); }  };  const openFieldReview = (fieldRef: string) => {    setReviewField(fieldRef);    setFieldReviewOpen(true);  };  const label = (v: unknown) => localeValue(v, lang);
  const sectionTitle = (v: unknown) => { const value = label(v); return value.startsWith('wizard.') ? tr(value) : value; };
  const renderTypedValue = (shape: { id: string; label: unknown; short_label?: unknown; help?: unknown; unit?: string; type?: string; required?: boolean; required_flag?: string; allow_range?: boolean; min?: number; max?: number; options?: Array<string | { value: string; label?: unknown }>; properties?: Array<any>; items?: { type?: string; properties?: Array<any> } }, value: unknown, onChange: (next: unknown) => void, keyPrefix: string, locked = false): React.ReactNode => {
    const kind = shape.type ?? 'string';
    const options = shape.options ?? [];
    const text = (v: unknown) => v == null ? '' : String(v);
    const unitAdornment = (unit: string) => <InputAdornment position="end" sx={{ alignSelf: 'stretch', height: 'auto', maxHeight: 'none', ml: 1, mr: -1.75, px: 1, borderLeft: '1px solid var(--line)', borderRadius: '0 8px 8px 0', bgcolor: 'var(--app-bg)', color: 'var(--text-3)', '& .MuiTypography-root': { fontSize: 11 } }}>{unit}</InputAdornment>;
    const input = (v: unknown, update: (next: unknown) => void, type: string, unit?: string) => <TextField size="small" fullWidth type={type === 'number' ? 'number' : type === 'date' || unit === 'date' ? 'date' : 'text'} value={text(v)} inputProps={{ 'aria-label': `${label(shape.short_label ?? shape.label)}${unit ? ` (${unit})` : ''}`, min: type === 'number' ? shape.min : undefined, max: type === 'number' ? shape.max : undefined, step: type === 'number' ? 'any' : undefined }} InputProps={unit && unit !== 'date' ? { endAdornment: unitAdornment(unit) } : undefined} disabled={!canEditSpec || busy || migrationBusy || locked} onChange={(e) => update(type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)} />;
    if (shape.allow_range) {
      if (typeof value === 'number' || (typeof value === 'string' && value.trim())) return input(value, onChange, 'number', shape.unit);
      const bounds = value && typeof value === 'object' ? value as Record<string, unknown> : {};
      return <Box sx={{ display: 'flex', gap: 0.5 }}>{(['minimum', 'maximum'] as const).map((part) => <TextField key={part} size="small" type="number" label={tr(part)} inputProps={{ 'aria-label': `${label(shape.short_label ?? shape.label)} ${tr(part)}${shape.unit ? ` (${shape.unit})` : ''}`, min: shape.min, max: shape.max, step: 'any' }} InputProps={shape.unit ? { endAdornment: unitAdornment(shape.unit) } : undefined} disabled={!canEditSpec || busy || migrationBusy || locked} value={text(bounds[part])} onChange={(e) => onChange({ ...bounds, [part]: e.target.value === '' ? '' : Number(e.target.value) })} />)}</Box>;
    }
    if (kind === 'array') {
      const rows = Array.isArray(value) ? value : [];
      const item = shape.items ?? {};
      if (item.type === 'object' && item.properties?.length) return <Stack spacing={0.8} sx={{ gridColumn: '1 / -1' }}>
        {rows.map((row, index) => <Paper key={`${keyPrefix}-${index}`} variant="outlined" sx={{ p: 0.8 }}><Stack spacing={0.6}>
          {item.properties!.map((prop: any) => <Box key={prop.id} sx={{ display: 'grid', gridTemplateColumns: '1fr', gap: 0.3, alignItems: 'center' }}><Tooltip title={label(prop.help ?? prop.label)}><Typography tabIndex={0} sx={{ fontSize: 11, color: 'var(--text-3)' }}>{label(prop.short_label ?? prop.label)}{prop.unit ? ` (${prop.unit})` : ''}</Typography></Tooltip>{renderTypedValue(prop, (row as Record<string, unknown>)?.[prop.id], (next) => { const nextRows = [...rows]; nextRows[index] = { ...((row && typeof row === 'object') ? row as object : {}), [prop.id]: next }; onChange(nextRows); }, `${keyPrefix}-${index}-${prop.id}`, locked)}</Box>)}
          <Button size="small" disabled={!canEditSpec || busy || migrationBusy || locked} onClick={() => onChange(rows.filter((_, i) => i !== index))}>{tr('removeRow')}</Button>
        </Stack></Paper>)}
        <Button size="small" disabled={!canEditSpec || busy || migrationBusy || locked} onClick={() => onChange([...rows, Object.fromEntries((item.properties ?? []).map((prop: any) => [prop.id, '']))])}>{tr('addRow')}</Button>
      </Stack>;
      return <TextField size="small" fullWidth multiline minRows={2} disabled={!canEditSpec || busy || migrationBusy || locked} value={rows.join(', ')} onChange={(e) => onChange(e.target.value.split(',').map((part) => part.trim()).filter(Boolean).map((part) => item.type === 'number' ? Number(part) : part))} />;
    }
    if (kind === 'object' && shape.properties?.length) {
      const object = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
      return <Stack spacing={0.6} sx={{ gridColumn: '1 / -1' }}>{shape.properties.map((prop: any) => <Box key={prop.id} sx={{ display: 'grid', gridTemplateColumns: '1fr', gap: 0.3, alignItems: 'center' }}><Tooltip title={label(prop.help ?? prop.label)}><Typography tabIndex={0} sx={{ fontSize: 11, color: 'var(--text-3)' }}>{label(prop.short_label ?? prop.label)}{(prop.required || prop.required_flag === 'E') && <Box component="span" sx={{ color: 'error.main' }}> *</Box>}{prop.unit ? ` (${prop.unit})` : ''}</Typography></Tooltip>{renderTypedValue(prop, object[prop.id], (next) => onChange({ ...object, [prop.id]: next }), `${keyPrefix}-${prop.id}`, locked)}</Box>)}</Stack>;
    }
    if (options.length) return <Select size="small" displayEmpty inputProps={{ 'aria-label': label(shape.short_label ?? shape.label) }} disabled={!canEditSpec || busy || migrationBusy || locked} value={text(value)} onChange={(e) => onChange(e.target.value)}><MenuItem value="">?</MenuItem>{options.map((option) => { const v = typeof option === 'string' ? option : option.value; return <MenuItem key={v} value={v}>{typeof option === 'string' ? option : label(option.label) || v}</MenuItem>; })}</Select>;
    if (kind === 'boolean') return <Select size="small" displayEmpty disabled={!canEditSpec || busy || migrationBusy || locked} value={value == null || value === '' ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' ? '' : e.target.value === 'true')}><MenuItem value="">?</MenuItem><MenuItem value="true">{tr('yes')}</MenuItem><MenuItem value="false">{tr('no')}</MenuItem></Select>;
    return input(value, onChange, kind, shape.unit);
  };
  const renderField = (f: SpecificationField, prod = false) => {
    if (f.handled_by === 'mission_profile' || f.id === '1_3') return null;
    if (f.id === 'mission_profile') return <MissionProfile key={f.id} draft={draftRef.current} disabled={!canEditSpec || busy} t={tr} lang={lang} onUnknown={(unknown) => markUnknown(f, unknown, prod)} onChange={(rows) => updateDraft((d) => {
      return { ...d, mission_profile: rows as unknown as Array<Record<string, unknown>>, mission_profile_status: rows.length ? 'filled' : 'missing' };
    })} label={label(f.label)} columns={f.columns ?? []} error={fieldErrors[f.id]} />;
    const store = prod ? (draftRef.current.production as Record<string, { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' }>) : draftRef.current.fields ?? {};
    const raw = store[f.id];
    const answer = raw && typeof raw === 'object' ? raw as { value?: unknown; status?: 'filled' | 'missing' | 'not_known_yet' } : { value: raw };
    const unknown = answer.status === 'not_known_yet';
    const val = answer.value == null ? '' : typeof answer.value === 'string' || typeof answer.value === 'number' ? String(answer.value) : '';
    const required = fieldRequired(f, draftRef.current);
    const options = f.options ?? f.choices;
    return <Box id={`spec-field-${f.id}`} key={`${prod ? 'p' : 'f'}-${f.id}`} sx={{ minWidth: 0, gridColumn: f.properties?.length ? '1 / -1' : undefined }}>
      <Box sx={{ mb: 0.5 }}><Stack direction="row" alignItems="center" spacing={0.3}><Typography sx={{ color: 'var(--text-2)', fontSize: 11, fontWeight: 700 }}>{label(f.short_label ?? f.label)}{required && <Box component="span" sx={{ color: 'error.main' }}> *</Box>}</Typography><Tooltip title={label(f.help ?? f.label)}><IconButton size="small" aria-label={`${tr('wizard.help')}: ${label(f.short_label ?? f.label)}`} sx={{ width: 18, height: 18, p: 0, color: 'var(--text-3)', fontSize: 10, border: '1px solid var(--line)' }}>?</IconButton></Tooltip>{(() => { const ref = `${f.storage_namespace ?? (prod ? 'production' : 'fields')}.${f.id}`; const open = messages.filter((m) => !m.resolved && matchesFieldReference(m.field_ref, ref)).length; return <Tooltip title={open ? `${tr('fieldReview')} (${open})` : tr('fieldReview')}><IconButton size="small" aria-label={tr('fieldReview')} onClick={() => openFieldReview(ref)} sx={{ p: 0.3, color: open ? 'primary.main' : 'var(--text-4)' }}><ChatBubbleOutlineIcon sx={{ fontSize: 14 }} />{open > 0 && <Box component="span" sx={{ fontSize: 10, ml: 0.2 }}>{open}</Box>}</IconButton></Tooltip>; })()}</Stack>{f.id === '1_4' && (spec?.schema_version ?? 1) >= 2 && retainedS1Range(draftRef.current) && <><Typography sx={{ fontSize: 10.5, color: 'var(--text-3)' }}>{tr('migration.retainedRange')}: {tr('field.torque')} {readableRange(retainedS1Range(draftRef.current)?.torque_Nm)} N&#183;m &#183; {tr('field.speed')} {readableRange(retainedS1Range(draftRef.current)?.speed_rpm)} rpm</Typography><Typography sx={{ fontSize: 10.5, color: 'var(--text-3)' }}>{tr('migration.legacyInputPower')}: {readableRange(retainedS1Range(draftRef.current)?.input_power_W)} W &#183; {tr('migration.legacyOutputPower')}: {readableRange(retainedS1Range(draftRef.current)?.output_power_W)} W</Typography></>}</Box>
      <Stack spacing={0.5}>
        {f.properties?.length ? (
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(2,minmax(0,1fr))' }, gap: 1.5 }}>
            {f.properties.map((prop) => {
              const objectValue = answer.value && typeof answer.value === 'object' ? answer.value as Record<string, unknown> : {};
              const updateProperty = (value: unknown) => {                if ((spec?.schema_version ?? 1) >= 2 && f.id === '1_4' && prop.id === 'shaft_power_W') return;                const nextValue = { ...objectValue, [prop.id]: value };                const safeValue = (spec?.schema_version ?? 1) >= 2 && f.id === '1_4' ? withS1DerivedPower(nextValue) : nextValue;                setAnswer(f, safeValue, prod);              };
              return <Box key={prop.id} sx={{ minWidth: 0 }}>
                <Stack direction="row" alignItems="center" spacing={0.3} sx={{ mb: 0.5 }}><Typography sx={{ fontSize: 11, fontWeight: 600, color: 'var(--text-2)' }}>{label(prop.short_label ?? prop.label)}{prop.required && <Box component="span" sx={{ color: 'error.main' }}> *</Box>}</Typography><Tooltip title={label(prop.help ?? prop.label)}><IconButton size="small" aria-label={`${tr('wizard.help')}: ${label(prop.short_label ?? prop.label)}`} sx={{ width: 18, height: 18, p: 0, color: 'var(--text-3)', fontSize: 10, border: '1px solid var(--line)' }}>?</IconButton></Tooltip></Stack>
                {prop.id === 'shaft_power_W' && f.id === '1_4' && (spec?.schema_version ?? 1) >= 2 ? (() => { const derivedPower = finiteScalar(withS1DerivedPower(objectValue).shaft_power_W); return <TextField size="small" fullWidth value={unknown || derivedPower == null ? '—' : derivedPower.toLocaleString(lang, { maximumFractionDigits: 2 })} InputProps={{ readOnly: true, endAdornment: <InputAdornment position="end" sx={{ alignSelf: 'stretch', height: 'auto', maxHeight: 'none', ml: 1, mr: -1.75, px: 1, borderLeft: '1px solid var(--line)', borderRadius: '0 8px 8px 0', bgcolor: 'var(--app-bg)', color: 'var(--text-3)' }}>{prop.unit ?? 'W'}</InputAdornment> }} inputProps={{ 'aria-label': `${label(prop.short_label ?? prop.label)} (${prop.unit ?? 'W'})` }} />; })() : renderTypedValue(prop, objectValue[prop.id], updateProperty, `${f.id}-${prop.id}`, unknown)}
              </Box>;
            })}
          </Box>
        ) : options?.length ? (
          <Select size="small" fullWidth displayEmpty disabled={!canEditSpec || busy || migrationBusy || unknown} value={val} onChange={(ev) => setAnswer(f, ev.target.value, prod)}>
            <MenuItem value=""><em>?</em></MenuItem>{options.map((o) => { const value = typeof o === 'string' ? o : o.value; return <MenuItem key={value} value={value}>{typeof o === 'string' ? o : label(o.label) || value}</MenuItem>; })}
          </Select>
        ) : (
          renderTypedValue(f, answer.value, (next) => setAnswer(f, next, prod), f.id, unknown)
        )}
        {f.allow_not_known_yet !== false && <Button size="small" disabled={!canEditSpec || busy || migrationBusy} onClick={() => markUnknown(f, !unknown, prod)} sx={{ alignSelf: 'flex-start', p: '3px 0', minWidth: 0, fontSize: 11, textTransform: 'none' }}>{unknown ? tr('wizard.undoUnknown') : tr('wizard.markUnknown')}</Button>}
        {fieldErrors[f.id] && <Typography role="alert" sx={{ color: 'error.main', fontSize: 11 }}>{tr('validationError')}</Typography>}
        {required && !fieldAnswerComplete(f, { ...answer, not_known_yet: unknown }) && <Typography role="alert" sx={{ color: 'error.main', fontSize: 11 }}>{tr('requiredMissing')}</Typography>}
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
  return <Box sx={{ p: 2, overflowY: 'auto', height: '100%', width: '100%' }}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}><Button size="small" startIcon={<ArrowBackIcon />} onClick={async () => { try { await flushSave(); setSelected(null); setSpec(null); setPendingProjectStatus(null); void refreshProjects(); } catch { setError(tr('saveFailedShort')); } }}>{tr('back')}</Button><Typography sx={{ fontSize: 17, fontWeight: 800, flex: 1, color: 'var(--text-0)' }}>{selected.title}</Typography><HelpTip title={tr('projectStatusTip')} />{canEditProject ? <><Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{tr('statusLabel')}</Typography><Select size="small" value={selected.status} disabled={busy} onChange={(e) => setPendingProjectStatus(proposeProjectStatus(selected.id, selected.status, e.target.value as ProjectSummary['status']))} aria-label={tr('statusLabel')}>{STATUSES.map((s) => <MenuItem key={s} value={s}>{tr(`status.${s}`)}</MenuItem>)}</Select></> : <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{tr('statusLabel')}: {tr(`status.${selected.status}`)}</Typography>}</Box>
    <Typography sx={{ color: 'var(--text-3)', fontSize: 12, mb: 1.5 }}>{selected.customer_org} · {tr('completeness', { filled: completeCounts.filled, total: completeCounts.total })}</Typography>
    {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
    <Tabs value={view} onChange={(_, v) => void openTab(v)} sx={{ borderBottom: '1px solid var(--line-soft)', mb: 1.5 }}>
      <Tab value="specification" label={tr('tabs.specification')} /><Tab value="motors" label={tr('tabs.motors')} /><Tab value="messages" label={tr('tabs.messages')} />{canEditProject && <Tab value="notes" label={tr('tabs.notes')} />}
    </Tabs>
    {view === 'specification' && <Box sx={{ maxWidth: 1196, mx: 'auto', minWidth: 0, '& .MuiInputBase-root': { bgcolor: 'var(--input-bg)', borderRadius: '8px', fontSize: 14 }, '& .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--line)' }, '& .MuiInputBase-input': { boxSizing: 'border-box', px: '9px', py: '7px', height: 'auto' }, '& .MuiSelect-select': { pr: '32px !important' } }}>
      {!schema || !spec ? <CircularProgress size={18} /> : <>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0,1fr)', md: '220px minmax(0, 1fr)' }, gap: '13px', alignItems: 'start', minWidth: 0 }}>
          <Paper elevation={0} sx={{ p: '9px', display: { xs: 'none', md: 'block' }, position: 'sticky', top: 8, bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '8px', boxShadow: 'none' }}>
            <Typography sx={{ px: 1, py: 0.75, color: 'var(--text-3)', fontSize: 10, letterSpacing: '.07em', fontWeight: 700 }}>{tr('wizard.sections')}</Typography>
            {sections.map((s, i) => { const c = sectionAnswerCounts(s, draftRef.current), state = sectionStatus(s, draftRef.current); return <Button key={s.id} fullWidth onClick={() => { setStep(i); document.getElementById(`spec-card-${s.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }} variant="text" sx={{ justifyContent: 'flex-start', textAlign: 'left', px: 1, py: 1, gap: 1, fontSize: 12, textTransform: 'none', color: step === i ? 'primary.main' : 'var(--text-2)', borderBottom: step === i ? '2px solid var(--brand)' : '1px solid var(--line-soft)', borderRadius: 0, whiteSpace: 'normal', fontWeight: step === i ? 650 : 400 }}><Box component="span" sx={{ width: 20, height: 20, flex: '0 0 20px', border: '1px solid var(--line)', borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 10 }}>{i + 1}</Box><Typography component="span" sx={{ flex: 1, whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'left', fontSize: 12, lineHeight: 1.35 }}>{sectionTitle(s.title)}</Typography><Tooltip title={tr('wizard.counts', { answered: c.answered, required: c.required, unknown: c.unknown, missing: c.missing, invalid: c.invalid })}><Box component="span" sx={{ ml: 'auto', fontSize: 10, color: 'var(--text-3)' }}>{state === 'complete' ? '✓' : state === 'unknown' ? '?' : '!'}</Box></Tooltip></Button>; })}
            <Box sx={{ mt: 1.5, pt: 1, borderTop: '1px solid var(--line-soft)' }}><Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>{tr('wizard.complete')}</Typography><Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', mt: 0.3 }}>{tr('wizard.completenessBuckets', { answered: completeness?.answered ?? 0, unknown: completeness?.unknown ?? 0, missingInvalid: (completeness?.missing ?? 0) + (completeness?.invalid ?? 0) })}</Typography><Box sx={{ height: 4, bgcolor: 'var(--app-bg)', mt: 0.7, borderRadius: 2 }}><Box sx={{ height: '100%', width: `${completeCounts.total ? Math.min(100, (completeness?.answered ?? 0) / completeCounts.total * 100) : 0}%`, bgcolor: 'primary.main', borderRadius: 2 }} /></Box><Typography sx={{ mt: 0.5, fontSize: 10, color: 'var(--text-3)' }}>{tr('wizard.unknownHint')}</Typography></Box>
          </Paper>
          <Box sx={{ minWidth: 0 }}>
            <Box sx={{ display: { xs: 'flex', md: 'none' }, overflowX: 'auto', gap: 0.4, mb: 1, borderBottom: '1px solid var(--line-soft)' }}>{sections.map((s, i) => <Button key={s.id} size="small" onClick={() => { setStep(i); document.getElementById(`spec-card-${s.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }} sx={{ flex: '0 0 auto', color: step === i ? 'primary.main' : 'var(--text-2)', borderBottom: step === i ? '2px solid var(--brand)' : '2px solid transparent', borderRadius: 0, textTransform: 'none', fontSize: 11, whiteSpace: 'nowrap' }}>{i + 1} {sectionTitle(s.title)}</Button>)}</Box>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2,minmax(0,1fr))', sm: '1.2fr 1fr 1fr' }, gap: '9px', mb: '12px', '& > :first-of-type': { gridColumn: { xs: '1 / -1', sm: 'auto' } } }}><Paper elevation={0} sx={{ p: '10px 12px', border: '1px solid var(--line)', bgcolor: 'var(--panel)', borderRadius: '8px', boxShadow: 'none' }}><Typography sx={{ fontSize: 10, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--text-3)' }}>{tr('wizard.s1')}</Typography><Typography sx={{ mt: 0.35, fontSize: 17, fontWeight: 700, color: 'var(--text-0)' }}>{s1Headline(sections, draftRef.current, spec.schema_version ?? 1, lang)}</Typography><Typography sx={{ fontSize: 10.5, color: 'var(--text-3)' }}>{tr('wizard.s1Power')}: {s1Power(sections, draftRef.current, spec.schema_version ?? 1, lang)}</Typography></Paper><Paper elevation={0} sx={{ p: '10px 12px', border: '1px solid var(--line)', bgcolor: 'var(--panel)', borderRadius: '8px', boxShadow: 'none' }}><Typography sx={{ fontSize: 10, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--text-3)' }}>{tr('wizard.status')}</Typography><Chip size="small" variant="outlined" label={tr('wizard.openIssues', { count: (completeness?.missing ?? 0) + (completeness?.invalid ?? 0) + messages.filter(m => !m.resolved && !!m.field_ref).length })} sx={{ mt: 0.5, height: 18, borderColor: 'var(--line)', bgcolor: 'var(--panel)', color: 'var(--text-2)', fontSize: 10.5, fontWeight: 700 }} /><Typography sx={{ mt: 0.45, fontSize: 10.5, color: 'var(--text-3)' }}>{tr('wizard.statusHint')}</Typography></Paper><Paper elevation={0} sx={{ p: '10px 12px', border: '1px solid var(--line)', bgcolor: 'var(--panel)', borderRadius: '8px', boxShadow: 'none' }}><Typography sx={{ fontSize: 10, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--text-3)' }}>{tr('wizard.currentView')}</Typography><Typography sx={{ mt: 0.35, fontSize: 17, fontWeight: 700, color: 'var(--text-0)' }}>{canEditProject ? tr('wizard.engineer') : tr('wizard.customer')}</Typography><Typography noWrap sx={{ fontSize: 10.5, color: 'var(--text-3)' }}>{tr(canEditProject ? 'wizard.engineerHint' : 'wizard.customerHint')}</Typography></Paper></Box>
            <Stack spacing={1.5} sx={{ minWidth: 0 }}>              {sections.map((section, sectionIndex) => {                const delegated = section.id === 'load' ? new Set(['load_type', 'load_source', 'load_inertia_kg_m2', 'startup_mode', 'load_characteristic']) : new Set<string>();                const fields = section.fields.filter((f) => !delegated.has(f.id) && f.id !== 'mission_profile');                const required = fields.filter((f) => fieldRequired(f, draftRef.current) && !f.handled_by);                const optional = fields.filter((f) => !fieldRequired(f, draftRef.current) && !f.handled_by);                const sectionComments = messages.filter(message => !message.resolved && message.field_ref === `section:${section.id}`);                return <Paper elevation={0} id={`spec-card-${section.id}`} key={section.id} sx={{ p: { xs: '12px', md: '15px' }, minWidth: 0, scrollMarginTop: 12, bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '8px', boxShadow: 'none' }}>

              <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mb: '13px' }}><Box sx={{ flex: 1 }}><Typography sx={{ fontWeight: 700, fontSize: 14, lineHeight: 1.3, color: 'var(--text-0)' }}>{sectionTitle(section.title)}</Typography><Typography sx={{ mt: '3px', color: 'var(--text-3)', fontSize: 12 }}>{tr(`wizard.descriptions.${sectionIndex}`)}</Typography></Box><Tooltip title={tr('sectionReview')}><IconButton size="small" aria-label={`${tr('sectionReview')}: ${sectionTitle(section.title)}`} onClick={() => openFieldReview(`section:${section.id}`)} sx={{ color: sectionComments.length ? 'primary.main' : 'var(--text-4)', p: 0.5 }}><ChatBubbleOutlineIcon sx={{ fontSize: 16 }} />{sectionComments.length > 0 && <Box component="span" sx={{ fontSize: 10, ml: 0.2 }}>{sectionComments.length}</Box>}</IconButton></Tooltip></Box>
                  {section.id === 'load' && ((spec.schema_version ?? 1) < 2 ? <Alert severity="info">{tr('newLoadNotice')}</Alert> : <LoadSection value={readLoadDraft(draftRef.current)} canEdit={canEditSpec && !busy && !migrationBusy} engineerView={canEditProject} onReview={openFieldReview} getOpenReviewCount={(ref) => messages.filter((message) => !message.resolved && matchesFieldReference(message.field_ref, ref)).length} onChange={(v) => updateDraft((d) => writeLoadDraft(d, v))} t={tr} locale={lang} />)}                  {section.id === 'mission' && ((spec.schema_version ?? 1) < 2 ? <MissionProfile draft={draftRef.current} disabled={!canEditSpec || busy} t={tr} lang={lang} onUnknown={(unknown) => updateDraft((d) => ({ ...d, mission_profile_status: unknown ? 'not_known_yet' : (d.mission_profile?.length ? 'filled' : 'missing') }))} onChange={(rows) => updateDraft((d) => ({ ...d, mission_profile: rows, mission_profile_status: rows.length ? 'filled' : 'missing' }))} label={tr('wizard.mission')} columns={section.fields.find((field) => field.id === 'mission_profile')?.columns ?? []} /> : <MissionProfileEditor rows={(draftRef.current.mission_profile ?? []) as unknown as MissionRow[]} load={readLoadDraft(draftRef.current)} canEdit={canEditSpec && !busy && !migrationBusy} onReview={openFieldReview} unknown={draftRef.current.mission_profile_status === 'not_known_yet'} onUnknown={(unknown) => updateDraft((d) => ({ ...d, mission_profile_status: unknown ? 'not_known_yet' : (d.mission_profile?.length ? 'filled' : 'missing') }))} onChange={(rows) => updateDraft((d) => ({ ...d, mission_profile: rows as unknown as Array<Record<string, unknown>>, mission_profile_status: rows.length ? 'filled' : 'missing' }))} t={tr} />)}                  {section.id === 'continuous' ? <><Paper elevation={0} sx={{ p: '14px', mb: '12px', border: '1px solid var(--line)', borderRadius: '8px', bgcolor: 'var(--panel)', boxShadow: 'none' }}><Typography sx={{ fontSize: 13, fontWeight: 700, mb: '11px' }}>{tr('wizard.s1')}</Typography><Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(2,minmax(0,1fr))' }, gap: 1.5 }}>{required.filter(f => f.id === '1_4').map(f => <Box key={f.id} sx={{ gridColumn: '1 / -1' }}>{renderField(f, f.storage_namespace === 'production')}</Box>)}</Box><Typography sx={{ fontSize: 10.5, color: 'var(--text-3)' }}>{tr('wizard.s1Formula')}</Typography></Paper><Paper elevation={0} sx={{ p: '14px', border: '1px solid var(--line)', borderRadius: '8px', bgcolor: 'var(--panel)', boxShadow: 'none' }}><Typography sx={{ fontSize: 13, fontWeight: 700, mb: '11px' }}>{tr('wizard.operatingLimits')}</Typography><Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(2,minmax(0,1fr))' }, gap: 1.5 }}>{required.filter(f => f.id !== '1_4').map(f => renderField(f, f.storage_namespace === 'production'))}{optional.map(f => renderField(f, f.storage_namespace === 'production'))}</Box></Paper></> : <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(2,minmax(0,1fr))' }, gap: '12px' }}>{required.map((f) => renderField(f, f.storage_namespace === 'production'))}{optional.map(f => renderField(f, f.storage_namespace === 'production'))}</Box>}                  {section.id === 'review' && <Stack spacing={0.8} sx={{ mt: 1 }}><Typography>{tr('wizard.reviewCounts', { answered: completeness?.answered ?? 0, unknown: completeness?.unknown ?? 0, missing: completeness?.missing ?? 0, invalid: completeness?.invalid ?? 0 })}</Typography><Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tr('wizard.reviewHint')}</Typography>{submitError.map((x, i) => <Alert key={i} severity="error">{x}</Alert>)}{messages.filter(message => !message.resolved && !!message.field_ref).map(message => <Button key={message.id ?? message.created_at} size="small" sx={{ alignSelf: 'flex-start', fontSize: 11 }} onClick={() => openFieldReview(message.field_ref!)}>{tr('openComment')}: {message.field_ref}</Button>)}<VersionHistory versions={versions} onCompare={(a, b) => { setCompareVersions([a, b]); setVersionA(versions.find((v) => v.version === a) ?? null); setVersionB(versions.find((v) => v.version === b) ?? null); }} t={tr} /><Button variant="contained" disabled={!canEditSpec || busy || saving} onClick={() => void onSubmitSpec()}>{tr(((completeness?.unknown ?? 0) + (completeness?.missing ?? 0) + (completeness?.invalid ?? 0)) > 0 ? 'wizard.reviewOpen' : 'wizard.reviewReady', { count: (completeness?.unknown ?? 0) + (completeness?.missing ?? 0) + (completeness?.invalid ?? 0) })}</Button></Stack>}                  {section.id === 'delivery' && deliveryConflicts(draftRef.current).length > 0 && <Alert severity="warning">{tr('deliveryConflict')}</Alert>}{section.id === 'delivery' && <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mt: 1 }}>{tr('productionTip')}</Typography>}
                </Paper>;              })}            </Stack>
          </Box>
        </Box>
        {canEditSpec && (spec.schema_version ?? 1) < 2 && <Alert severity="warning" sx={{ mt: 1.2 }} action={<Button color="inherit" size="small" disabled={migrationBusy || saving} onClick={() => void requestMigrationPreview()}>{tr('migration.action')}</Button>}>{tr('migration.body')}</Alert>}
        {submitError.length > 0 && <Alert severity="error" sx={{ mt: 1 }}><Typography sx={{ fontWeight: 700, fontSize: 12 }}>{tr('submitBlocked')}</Typography>{submitError.map((x, i) => <Typography key={`${i}-${x}`} sx={{ fontSize: 12 }}>• {x}</Typography>)}</Alert>}
        {saveError && <Alert severity="error" sx={{ mt: 1 }}>{tr('saveFailed', { reason: saveError })}<Button size="small" onClick={() => selected && void enqueueSave(selected.id, draftRef.current, projectToken.current).catch(() => {})}>{tr('retrySave')}</Button><Button size="small" onClick={() => void reloadDraft()}>{tr('reloadDraft')}</Button></Alert>}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1.2 }}><Typography role="status" sx={{ flex: 1, fontSize: 11, color: saveError ? 'error.main' : saving ? 'var(--text-3)' : 'success.main' }}>{saveError ? tr('saveFailedShort') : saving ? tr('saving') : tr('autosaved')}</Typography><Button size="small" disabled={busy || step <= 0} onClick={() => setStep((v) => Math.max(0, v - 1))}>{tr('previous')}</Button><Button size="small" disabled={busy || step >= sections.length - 1} onClick={() => setStep((v) => Math.min(sections.length - 1, v + 1))}>{tr('next')}</Button></Box>
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
        <Dialog open={fieldReviewOpen} onClose={() => setFieldReviewOpen(false)} fullWidth maxWidth="sm"><DialogTitle>{reviewField.startsWith('section:') ? tr('sectionReview') : tr('fieldReview')}: {reviewField.startsWith('mission_profile:') ? tr('field.missionProfileRow', { row: reviewField.slice('mission_profile:'.length) }) : (() => { const id = reviewField.split('.').pop(); const field = sections.flatMap((section) => section.fields).find((item) => item.id === id); if (field) return label(field.short_label ?? field.label); if (reviewField.startsWith('section:')) { const section = sections.find((item) => item.id === reviewField.slice('section:'.length)); return section ? sectionTitle(section.title) : tr('sectionReview'); } return reviewField; })()}</DialogTitle><DialogContent><Stack spacing={0.8} sx={{ pt: 0.5 }}>{messages.filter((message) => matchesFieldReference(message.field_ref, reviewField)).map((message, index) => <Paper key={message.id ?? `${message.created_at}-${index}`} variant="outlined" sx={{ p: 1 }}><Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{message.author_display_name || tr('projectMember')} · {message.version ? `v${message.version}` : tr('currentDraft')}</Typography><Typography sx={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{message.text}</Typography>{message.resolved && <Chip size="small" label={tr('resolved')} />}{canEditSpec && message.id && <Button size="small" onClick={() => void resolveReviewMessage(message.id!, !message.resolved)}>{message.resolved ? tr('reopen') : tr('resolve')}</Button>}</Paper>)}{!messages.some((message) => matchesFieldReference(message.field_ref, reviewField)) && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tr('noFieldComments')}</Typography>}{canEditSpec && <><TextField size="small" multiline minRows={3} value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} placeholder={tr('writeFieldReview')} /><Button disabled={busy || !reviewComment.trim()} onClick={() => void postFieldReview()}>{tr('addFieldReview')}</Button></>}</Stack></DialogContent><DialogActions><Button onClick={() => setFieldReviewOpen(false)}>{tr('close')}</Button></DialogActions></Dialog>    <Dialog open={!!historyDetail} onClose={() => setHistoryDetail(null)} fullWidth maxWidth="sm"><DialogTitle>{tr('snapshots.history')} · {historyDetail?.die} / {historyDetail?.config}</DialogTitle><DialogContent><Stack spacing={0.7} sx={{ pt: 0.5 }}><Typography variant="body2">{historyDetail?.author_display_name} · {historyDetail?.created_at ? new Date(historyDetail.created_at).toLocaleString() : ''}</Typography>{historyDetail?.source && <Typography variant="body2">{tr('snapshots.source')}: {snapshotSourceLabel(historyDetail.source, tr)}</Typography>}{historyDetail?.note && <Tooltip title={historyDetail.note}><Typography variant="body2" noWrap sx={{ maxWidth: '100%' }}>{historyDetail.note}</Typography></Tooltip>}</Stack></DialogContent><DialogActions><Button onClick={() => setHistoryDetail(null)}>{tr('close')}</Button></DialogActions></Dialog>
    {canEditProject && <Button size="small" sx={{ mt: 1 }} onClick={() => setInviteOpen(true)}>{tr('invite')}</Button>}
    {compareVersions && <VersionDiffDialog open onClose={() => setCompareVersions(null)} a={versionA} b={versionB} t={tr} schema={schema} lang={lang} />}
    <Dialog open={!!reloadPreview} onClose={() => setReloadPreview(null)} fullWidth maxWidth="md">
      <DialogTitle>{tr('reloadReview')}</DialogTitle><DialogContent>
        <Typography variant="body2" sx={{ mb: 1 }}>{tr('reloadConfirm')}</Typography>
        {reloadPreview && semanticSpecificationDiff(draftRef.current, reloadPreview.fresh.draft, { before: schema ?? undefined, after: reloadPreview.fresh.schema }).map((entry, index) => <Box key={index} sx={{ py: 0.6, borderBottom: '1px solid var(--line-soft)' }}>
          <Typography sx={{ fontSize: 12, fontWeight: 600 }}>{formatDiffLabel(entry, reloadPreview.fresh.schema ?? schema ?? undefined, lang, key => tr(key.replace('versionDiff.property.', 'diffProperty.')))}</Typography>
          <Typography sx={{ fontSize: 12 }}>{tr('reloadLocal')}: {formatDiffValue(entry.before, entry, schema ?? undefined, lang, tr)} · {tr('reloadServer')}: {formatDiffValue(entry.after, entry, reloadPreview.fresh.schema ?? schema ?? undefined, lang, tr)}</Typography>
        </Box>)}
      </DialogContent><DialogActions><Button onClick={() => setReloadPreview(null)}>{tr('cancel')}</Button><Button color="warning" onClick={confirmReload}>{tr('reloadDraft')}</Button></DialogActions>
    </Dialog>
    <Dialog open={migrationOpen} onClose={() => !migrationBusy && setMigrationOpen(false)} fullWidth maxWidth="sm">
      <DialogTitle>{tr('migration.title')}</DialogTitle>
      <DialogContent><Stack spacing={1.2} sx={{ pt: 0.5 }}>
        <Typography variant="body2">{tr('migration.body')}</Typography>
        {(() => {          const candidate = migrationS1Candidate(migrationPreview as MigrationPreviewView | null);          const old = (migrationPreview?.draft.provenance as Record<string, unknown> | undefined)?.schema_migrations as Array<Record<string, unknown>> | undefined;          const previous = old?.at(-1)?.legacy_1_4;          const oldAnswer = previous && typeof previous === 'object' ? previous as Record<string, unknown> : {}; const oldPoint = oldAnswer.value && typeof oldAnswer.value === 'object' ? oldAnswer.value as Record<string, unknown> : oldAnswer;          const rangeValue = (key: string) => {            const x = oldPoint[key];            if (x && typeof x === 'object') { const outer = x as Record<string, unknown>; const r = outer.value && typeof outer.value === 'object' ? outer.value as Record<string, unknown> : outer; const min = r.minimum ?? r.min ?? r.from; const max = r.maximum ?? r.max ?? r.to; return `${min ?? '—'}–${max ?? '—'}`; }            return x == null ? '—' : String(x);          };          const stale = !!migrationPreview && migrationPreview.source_revision !== revisionRef.current;          return <Paper variant="outlined" sx={{ p: 1.2, bgcolor: 'var(--panel-2)' }}>            <Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{tr('migration.preview')}</Typography>            <Typography sx={{ fontSize: 12 }}>{label(schema?.sections.find((s) => s.id === '1')?.fields?.find((f) => f.id === '1_4')?.short_label ?? schema?.sections.find((s) => s.id === '1')?.fields?.find((f) => f.id === '1_4')?.label) || tr('specification')}</Typography>            {candidate ? <Stack direction="row" spacing={1.2} flexWrap="wrap"><Typography sx={{ fontSize: 12 }}>{tr('field.torque')}: {candidate.torque_Nm} N·m</Typography><Typography sx={{ fontSize: 12 }}>{tr('field.speed')}: {candidate.speed_rpm} rpm</Typography><Typography sx={{ fontSize: 12 }}>{tr('field.power')}: {candidate.shaft_power_W.toFixed(2)} W</Typography></Stack> : <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{tr('unknown')}</Typography>}            {previous !== undefined && <><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{tr('migration.retainedRange')}: {tr('field.torque')} {rangeValue('torque_Nm')} N·m · {tr('field.speed')} {rangeValue('speed_rpm')} rpm</Typography><Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{tr('migration.legacyInputPower')}: {rangeValue('input_power_W')} W &#183; {tr('migration.legacyOutputPower')}: {rangeValue('output_power_W')} W</Typography></>}            {migrationPreview?.issues.map((issue, i) => { const key = `migration.issues.${issue.code}`; const translated = tr(key); return <Typography key={`${issue.code}-${i}`} role="status" sx={{ fontSize: 12, mt: 0.5 }}>{translated === key ? tr('migration.issueFallback') : translated}</Typography>; })}            {stale && <Alert severity="warning">{tr('migration.stale')}</Alert>}          </Paper>
;        })()}
        {migrationPreview?.requires_acknowledgement && <FormControlLabel control={<Checkbox checked={migrationAck} onChange={(e) => setMigrationAck(e.target.checked)} />} label={<Typography sx={{ fontSize: 12 }}>{tr('migration.ack')}</Typography>} />}
      </Stack></DialogContent>
      <DialogActions><Button disabled={migrationBusy} onClick={() => setMigrationOpen(false)}>{tr('migration.cancel')}</Button><Button variant="contained" disabled={migrationBusy || !migrationPreview || !canApplyMigrationPreview(migrationPreview as MigrationPreviewView, revisionRef.current, migrationAck)} onClick={() => void applyMigration()}>{migrationBusy ? <CircularProgress size={16} /> : tr('migration.apply')}</Button></DialogActions>
    </Dialog>
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
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<ProjectMotorPickerItem | null>(null);
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
function NotesPanel({ project, onSave, t }: { project: ProjectSummary; onSave: (s: string) => Promise<void>; t: (key: string) => string }) { const [value, setValue] = useState(project.notes ?? ''); return <Stack spacing={1}><Typography tabIndex={0} sx={{ fontSize: 11, color: 'var(--text-3)' }}>{t('internalNotesTip')}</Typography><TextField multiline minRows={5} value={value} onChange={(e) => setValue(e.target.value)} /><Button sx={{ alignSelf: 'start' }} onClick={() => void onSave(value)}>{t('save')}</Button></Stack>; }
function VersionHistory({ versions, onCompare, t }: { versions: SpecSnapshot[]; onCompare: (a: number, b: number) => void; t: (key: string, args?: Record<string, unknown>) => string }) { const [a, setA] = useState('');
  const [b, setB] = useState(''); return <Box><Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{t('history')}</Typography>{versions.length === 0 ? <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{t('noVersions')}</Typography> : <Box sx={{ display: 'flex', gap: 0.7, alignItems: 'center', flexWrap: 'wrap' }}><Select size="small" value={a} displayEmpty onChange={(e) => setA(e.target.value)}><MenuItem value="">{t('versionA')}</MenuItem>{versions.map((v) => <MenuItem key={v.version} value={String(v.version)}>v{v.version}</MenuItem>)}</Select><Select size="small" value={b} displayEmpty onChange={(e) => setB(e.target.value)}><MenuItem value="">{t('versionB')}</MenuItem>{versions.map((v) => <MenuItem key={v.version} value={String(v.version)}>v{v.version}</MenuItem>)}</Select><Button size="small" disabled={!a || !b || a === b} onClick={() => onCompare(Number(a), Number(b))}>{t('compare')}</Button>{versions.map((v) => <Chip key={v.version} size="small" label={`v${v.version} · ${v.author_display_name || t('projectMember')}`} title={v.created_at ?? ''} />)}</Box>}</Box>; }
function VersionDiffDialog({ open, onClose, a, b, t, schema, lang }: { open: boolean; onClose: () => void; a: SpecSnapshot | null; b: SpecSnapshot | null; t: (key: string, args?: Record<string, unknown>) => string; schema: DevelopmentSchema | null; lang: string }) {
  const before = a?.spec ?? a?.draft;  const after = b?.spec ?? b?.draft;  const diff = useMemo(() => semanticSpecificationDiff(before, after, {    before: a?.schema ?? schema ?? undefined,    after: b?.schema ?? schema ?? undefined,  }), [a, b, schema]);
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="md"><DialogTitle>{t('versionDiff')} · {t('versionFrom', { version: a?.version ?? '?' })} → {t('versionTo', { version: b?.version ?? '?' })}</DialogTitle><DialogContent>{diff.length ? diff.map((entry, index) => {    const fieldLabel = formatDiffLabel(entry, b?.schema ?? a?.schema ?? schema ?? undefined, lang, (key) => t(key.replace('versionDiff.property.', 'diffProperty.')));    const row = entry.rowId ? [...(Array.isArray(after?.mission_profile) ? after.mission_profile : []), ...(Array.isArray(before?.mission_profile) ? before.mission_profile : [])].find((item: any) => String(item.row_id ?? '') === entry.rowId) : undefined;    const label = row?.phase ? `${String(row.phase)} · ${fieldLabel}` : fieldLabel;    return <Box key={`${entry.namespace}:${entry.fieldId}:${entry.rowId ?? ''}:${entry.path}:${index}`} sx={{ py: 0.8, borderBottom: '1px solid var(--line-soft)' }}><Typography sx={{ fontWeight: 700, fontSize: 12 }}>{label}</Typography><Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{t(`versionChange.${entry.change}`)}</Typography><Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>{t('versionBefore')}: {formatDiffValue(entry.before, entry, a?.schema ?? schema ?? undefined, lang, t)}</Typography><Typography sx={{ fontSize: 11, color: 'var(--text-1)' }}>{t('versionAfter')}: {formatDiffValue(entry.after, entry, b?.schema ?? schema ?? undefined, lang, t)}</Typography></Box>;  }) : <Typography sx={{ fontSize: 12 }}>{t('noDiff')}</Typography>}</DialogContent><DialogActions><Button onClick={onClose}>{t('close')}</Button></DialogActions></Dialog>;
}
