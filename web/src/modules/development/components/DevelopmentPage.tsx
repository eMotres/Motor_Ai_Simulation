import { matchesFieldReference } from '../reviewPresentation';
import { accumulateHighlight, firstUnreadIndex, refreshUnread, unreadFor, unreadIdsOf, unreadLabel } from '../../../lib/unreadMessages';
import { useUnreadCounts } from '../../../lib/useUnreadMessages';
import { pageVisible } from '../../../lib/pageVisible';
import SubTabs from '../../../components/common/SubTabs';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
  import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle,
  Divider, FormControlLabel, IconButton, MenuItem, Paper, Select, Stack, TextField, ToggleButton, ToggleButtonGroup,
  Tooltip, Typography, Checkbox,
} from '@mui/material';
  import AddIcon from '@mui/icons-material/Add';
  import ArrowBackIcon from '@mui/icons-material/ArrowBack';
  import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
  import HelpTip from '../../../components/common/HelpTip';
  import ViewModeToggle, { useViewMode } from '../../../components/common/ViewModeToggle';
  import { VIEW_MODE_KEYS } from '../../../lib/viewMode';
  import ProjectOverview from './ProjectOverview';
  import MotorRevisionText from '../../../components/common/MotorRevisionText';
  import { useAuth } from '../../../contexts/AuthContext';
  import { useTranslation } from 'react-i18next';
  import { developmentApi, DevelopmentApiError } from '../api';
  import type { Completeness, DevelopmentSchema, ProjectMotor, ProjectMotorPickerItem, ProjectSnapshot, ProjectSummary, ProjectThreadMessage, SpecSnapshot, SpecState } from '../types';
  import { canApplyMigrationPreview, migrationS1Candidate, type MigrationPreviewView } from '../migrationPresentation';
  import { formatDiffLabel, formatDiffValue, semanticSpecificationDiff } from '../specificationDiff';
  import { createDraftSaveCoordinator, invertedWindowFieldIds, requiredFieldIds, wizardCompleteness, withSpecDefaults, type SpecDraft } from '../wizardState';
  import type { CatalogController } from '../controllerCatalog';
  import { submitFailureKind, submitFailureKey } from '../submitFailure';
  import { MotorOpenActions } from '../MotorOpenActions';
  import { VariantControls } from './VariantControls';
  import SpecAssistantPanel from './SpecAssistantPanel';
  import SpecWizard from './specWizard/SpecWizard';
  import { useSpecFields } from './specWizard/useSpecFields';
  import { buildWizardSteps, stepCommentRefs } from './specWizard/steps';
  import { wizardProfile } from './specWizard/profile';
  import { ProjectMotorTests } from '../ProjectMotorTests';
  import { ActivityChip } from '../ActivityChip';
  import { ProjectStatusBar, VariantStageBar } from '../StageBar';
  import { useProjectActivity } from '../useProjectActivity';
  import { isRecommended } from '../projectFlags';
  import { openProjectMotor } from '../../../lib/openProjectMotor';
  import { headerFlags, latestAndRecommended, motorColor, orderByGroup, variantKey } from '../motorGroups';
  import HistoryIcon from '@mui/icons-material/History';
  import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
  import { useProjectStripContext } from '../projectStripContext';
  import { SharedComparisonTable } from '../../../components/compare/SharedComparisonTable';
  import { developmentComparisonColumns, developmentComparisonRows } from '../comparisonRows';
  import { claimProjectOpenIntent, projectOpenEvent, restoreProjectOpenIntent } from '../projectOpenIntent.ts';
  import { filterPickerCatalog, groupPickerCatalog, pickerDisplayLabel, pickerReasonKey, snapshotSourceKey } from '../pickerModel';
  import { acceptedProjectStatus, cancelProjectStatus, pendingStatusForProject, proposeProjectStatus, visibleProjectVersions } from '../motorStatus';
  import { NARROW } from '../../../lib/narrowLayout';
  import { clearView, readView, writeView } from '../../../lib/viewMemory';
  import { developmentViewScope, parseDevelopmentView, restorableView, type DevelopmentView } from '../viewState';
  import { appLinkPath, parseAppLink, type AppLink } from '../../../lib/appLink';
  import DigestSettings from '../../../components/digest/DigestSettings';
type ViewTab = 'specification' | 'motors' | 'messages' | 'notes';
const STATUSES = ['spec', 'design', 'simulation', 'quote', 'prototype', 'production', 'closed'] as const;
const DEFAULT_DRAFT: SpecDraft = { schema_version: 2, fields: {}, mission_profile: [], production: {} };
const completenessNumbers = (c?: Completeness) => ({ filled: c?.filled_required ?? c?.filled ?? 0, total: c?.total_required ?? c?.required ?? 0 });
const normaliseDraft = (value: unknown): SpecDraft => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...DEFAULT_DRAFT, fields: {}, production: {} };
  const d = value as SpecDraft;
  return withSpecDefaults({ ...DEFAULT_DRAFT, ...d, fields: { ...(d.fields ?? {}) }, production: { ...((d.production && typeof d.production === 'object') ? d.production as object : {}) }, mission_profile: Array.isArray(d.mission_profile) ? d.mission_profile : [] });
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
  const [viewMode, setViewMode] = useViewMode(VIEW_MODE_KEYS.development, 'cards');
  const unreadCounts = useUnreadCounts();
  const projectUnread = (p: ProjectSummary) => unreadFor(unreadCounts, 'projects', p.id, p.unread_messages);
  const [shownUnread, setShownUnread] = useState<ReadonlySet<string>>(new Set());   // messages that were unread when the thread was opened
  const markedUpToRef = useRef('');
  const [visibleTick, setVisibleTick] = useState(0);
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
  // the variant header rows span the whole (wide) comparison table; their content is pinned to the visible width of the
  // Motors view so the controls stay on the page while the table itself scrolls sideways (owner 2026-10-27)
  const motorsViewRef = useRef<HTMLDivElement | null>(null);
  const [motorsViewW, setMotorsViewW] = useState(0);
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
  const [submitConflict, setSubmitConflict] = useState(false);
  const [catalog, setCatalog] = useState<CatalogController[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [messages, setMessages] = useState<ProjectThreadMessage[]>([]);
  const [message, setMessage] = useState('');
  const [messageFieldRef, setMessageFieldRef] = useState('');
  const [reviewField, setReviewField] = useState('');
  const [reviewComment, setReviewComment] = useState('');
  const [fieldReviewOpen, setFieldReviewOpen] = useState(false);
  const [reloadPreview, setReloadPreview] = useState<{ project: string; token: string; fresh: SpecState } | null>(null);
  const [motors, setMotors] = useState<ProjectMotor[]>([]);
  useProjectStripContext(selected, motors);   // the strip under the tabs names this project and its motors
  const activity = useProjectActivity(view === 'motors' || view === 'specification' ? selected?.id : null);   // live runs, queue place + ETA and the five stages of the project's motors: one poll per project, 30 s, only while the page is visible
  const activityRows = activity.rows;
  useEffect(() => {
    const el = motorsViewRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => setMotorsViewW(el.clientWidth));
    ro.observe(el);
    setMotorsViewW(el.clientWidth);
    return () => ro.disconnect();
  }, [view, selected?.id]);
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
  const canEditSpec = !!selected && selected.can_edit_spec === true;
  const canEditProject = !!selected && (canManageGlobally || selected.can_edit === true);
  const canManageProjectMotors = canManageGlobally && canEditProject;
  const profile = spec ? wizardProfile(spec.draft as SpecDraft) : 'industrial';
  const sections = useMemo(() => schema ? buildWizardSteps(schema, profile, { staff: canManageGlobally }) : [], [schema, profile, canManageGlobally]);
  // the catalogue of complete controllers (read-only reference data); the picker degrades to free text without it
  useEffect(() => {
    if (!user?.email) return;
    let live = true;
    void developmentApi.controllerCatalog().then((rows) => { if (live) setCatalog(rows); }).catch(() => { if (live) setCatalog([]); });
    return () => { live = false; };
  }, [user?.email]);
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
  // A reload reopens the same project, tab and card (lib/viewMemory.ts).  Once per identity, after the project list is
  // known; a pending "open this project" link wins; a project that is gone or no longer visible is dropped silently.
  const viewRestoredFor = useRef('');
  const restoringView = useRef(false);
  const [pendingView, setPendingView] = useState<DevelopmentView | null>(null);
  useEffect(() => {
    const identity = user?.email;
    if (!identity || loading || selected || viewRestoredFor.current === identity) return;
    viewRestoredFor.current = identity;
    let linkPending = false;
    try { linkPending = !!sessionStorage.getItem('development.openProject'); } catch { /* storage blocked */ }
    const saved = readView(developmentViewScope(identity), parseDevelopmentView);
    const known = saved ? projects.find((item) => item.id === saved.project) : undefined;
    if (!saved || !known || linkPending) { if (saved && !known) clearView(developmentViewScope(identity)); return; }
    restoringView.current = true;
    void loadProject(known).then((opened) => {
      if (opened) { setPendingView(saved); return; }
      restoringView.current = false;
      clearView(developmentViewScope(identity)); setSelected(null); setSpec(null); setError('');
    });
  }, [user?.email, loading, projects, selected, loadProject]);
  useEffect(() => {
    if (!pendingView || !selected || !spec || selected.id !== pendingView.project) return;
    const next = restorableView(pendingView, sections.map((item) => item.id), canEditProject);
    setPendingView(null);
    setStep(next.step);
    if (next.view === 'specification') { setView('specification'); if (next.step > 0) showCard(next.step); } else void openTab(next.view);
    restoringView.current = false;
  }, [pendingView, selected, spec, sections.length, canEditProject]);
  useEffect(() => {
    const identity = user?.email;
    if (!identity || viewRestoredFor.current !== identity || restoringView.current || pendingView) return;
    if (selected) writeView(developmentViewScope(identity), { project: selected.id, view, step, stepId: sections[step]?.id });
    else clearView(developmentViewScope(identity));
  }, [user?.email, selected?.id, view, step, pendingView]);
  // A link to a project (/projects/<id>[/<view>][?die=&config=], e.g. from an activity e-mail): open that project once, on the view
  // the link names.  Read from the address bar once at mount; the link then leaves the bar (a reload goes back to the remembered view).
  const linkTarget = useRef<Extract<AppLink, { kind: 'project' }> | null>(null);
  const linkRead = useRef(false);
  useEffect(() => {
    if (linkRead.current) return;
    linkRead.current = true;
    let link: AppLink | null = null;
    try { link = parseAppLink(window.location.pathname, window.location.search); } catch { /* no window */ }
    if (!link || link.kind !== 'project') return;
    linkTarget.current = link;
    if (user?.email) viewRestoredFor.current = user.email;       // the link wins over the remembered project
    try { sessionStorage.setItem('development.openProject', link.id); } catch { /* the event below still carries the id */ }
    window.dispatchEvent(new CustomEvent(projectOpenEvent, { detail: { projectId: link.id } }));
    try { if (appLinkPath(link) === window.location.pathname + window.location.search) window.history.replaceState(null, '', '/'); } catch { /* no history */ }
  }, []);
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
  const showCard = (index: number) => { if (sections[index]) setStep(index); };
  /** Ask the wizard to open a field: it goes to the field's step, opens its group and focuses the first input. */
  const [reveal, setReveal] = useState<{ field: string; n: number } | null>(null);
  const revealField = (fieldId: string) => setReveal((old) => ({ field: fieldId, n: (old?.n ?? 0) + 1 }));
  const onSubmitSpec = async () => {
    if (!selected || !spec) return;
    const missing = [...requiredFieldIds(sections, draftRef.current), ...invertedWindowFieldIds(sections, draftRef.current)];    if (missing.length) {      setSubmitError(missing.map((id) => `${label(sections.flatMap((s) => s.fields).find((f) => f.id === id)?.short_label ?? id)}: ${tr('validationError')}`));      setFieldErrors(Object.fromEntries(missing.map((id) => [id, tr('validationError')])));      revealField(missing[0]);      return;    }
    if (saveTimer.current) { clearTimeout(saveTimer.current); saveTimer.current = null; }
    setBusy(true); setSubmitError([]); setSubmitConflict(false); setFieldErrors({});
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
      const firstInvalid = Object.keys(byField)[0];
      if (firstInvalid) revealField(firstInvalid);
      if (!requestSubmitted) setSubmitConflict(submitFailureKind(e) === 'conflict');
      setSubmitError(!requestSubmitted ? [tr(submitFailureKey(e))] : errors.length ? errors.map((x) => x.field ? `${fieldLabel(x.field)}: ${tr('validationError')}` : tr('validationError')) : [tr('submitFailed')]);
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
  useEffect(() => { const on = () => setVisibleTick((n) => n + 1); document.addEventListener('visibilitychange', on); return () => document.removeEventListener('visibilitychange', on); }, []);
  // Unread messages (owner 2026-10-28): highlighted while the Messages tab is open, marked read on the server once they are shown
  // (never while the page is hidden); leaving the tab drops the highlight, the next visit shows them as read.
  useEffect(() => {
    const id = selected?.id;
    if (view !== 'messages' || !id) { setShownUnread((s) => (s.size ? new Set() : s)); markedUpToRef.current = ''; return; }
    const flagged = unreadIdsOf(messages);
    if (flagged.length) setShownUnread((s) => accumulateHighlight(s, flagged));
    const last = messages[messages.length - 1]?.id;
    if (!flagged.length || !last || markedUpToRef.current === `${id}:${last}` || !pageVisible()) return;
    markedUpToRef.current = `${id}:${last}`;
    void developmentApi.markThreadRead(id, last).then(() => refreshUnread()).catch(() => { markedUpToRef.current = ''; });
  }, [view, selected?.id, messages, visibleTick]);
  const openTab = async (tab: ViewTab) => {
    setView(tab); if (!selected) return;
    const id = selected.id; const token = projectToken.current;
    try {
      if (tab === 'messages') { const result = await developmentApi.thread(id); if (projectToken.current === token && currentProjectRef.current === id) setMessages(result); }
      if (tab === 'motors') await loadProjectMotors(id, token);
    } catch (e) { if (projectToken.current === token && currentProjectRef.current === id) setError(e instanceof Error ? e.message : String(e)); }
  };
  // ...and once that project is loaded, show the view the link named (a motor step opens the Motors view)
  useEffect(() => {
    const link = linkTarget.current;
    if (!link || !selected || !spec || selected.id !== link.id) return;
    linkTarget.current = null;
    const wanted: ViewTab = link.view ?? (link.die ? 'motors' : 'specification');
    const next: ViewTab = wanted === 'notes' && !canEditProject ? 'specification' : wanted;
    if (next === 'specification') setView('specification'); else void openTab(next);
  }, [selected, spec, canEditProject]);
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
  const postFieldReview = async () => {    if (!selected || !reviewField || !reviewComment.trim()) return;    const id = selected.id, token = projectToken.current;    const rowId = reviewField.startsWith('mission_profile:') ? reviewField.slice('mission_profile:'.length) : undefined;    setBusy(true); setError('');    try {      if (canEditSpec) await flushSave();      if (projectToken.current !== token || currentProjectRef.current !== id) return;      await developmentApi.postMessage(id, reviewComment.trim(), reviewField, { revision: revisionRef.current, row_id: rowId });      const thread = await developmentApi.thread(id);      if (projectToken.current === token && currentProjectRef.current === id) { setReviewComment(''); setMessages(thread); }    }    catch (e) { if (projectToken.current === token && currentProjectRef.current === id) setError(e instanceof Error ? e.message : String(e)); }    finally { if (projectToken.current === token) setBusy(false); }  };  const resolveReviewMessage = async (messageId: string, resolved: boolean) => {    if (!selected) return;    const id = selected.id, token = projectToken.current;    try { await developmentApi.resolveMessage(id, messageId, resolved); const thread = await developmentApi.thread(id); if (projectToken.current === token && currentProjectRef.current === id) setMessages(thread); }    catch (e) { if (projectToken.current === token) setError(e instanceof Error ? e.message : String(e)); }  };  const reviewRefs = (ref: string) => { const card = ref.startsWith('section:') ? sections.find((item) => stepCommentRefs(item).includes(ref)) : undefined; return card ? stepCommentRefs(card) : [ref]; };  const openFieldReview = (fieldRef: string) => {    setReviewField(fieldRef);    setFieldReviewOpen(true);  };
  const fields = useSpecFields({ tr, lang, draftRef, updateDraft, spec, steps: sections, profile, canEditSpec, busy, migrationBusy, fieldErrors, messages, catalog, openFieldReview });
  const { label, sectionTitle } = fields;
  if (loading) return <Box sx={{ p: 3, display: 'flex', gap: 1, alignItems: 'center' }}><CircularProgress size={18} /><Typography>{tr('loading')}</Typography></Box>;
  if (!selected) return <Box sx={{ p: 2.5, overflowY: 'auto', height: '100%' }}>
    <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, gap: 1 }}><Typography sx={{ fontSize: 18, fontWeight: 800, color: 'var(--text-0)' }}>{tr('title')}</Typography><HelpTip title={tr('titleTip')} /><Box sx={{ flex: 1 }} /><ViewModeToggle mode={viewMode} onChange={setViewMode} />{canManageGlobally && <Button size="small" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>{tr('newProject')}</Button>}</Box>
    {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
    {projects.length === 0 ? <Typography sx={{ color: 'var(--text-3)', fontSize: 13 }}>{tr('noProjects')}</Typography> : <ProjectOverview projects={projects} mode={viewMode} unread={projectUnread} email={user?.email ?? ''} language={lang} onOpen={(p) => void loadProject(p)} t={tr} />}
    <CreateDialog open={createOpen} busy={busy} title={title} org={org} id={projectId} onTitle={setTitle} onOrg={setOrg} onId={setProjectId} onClose={() => setCreateOpen(false)} onSubmit={() => void submitCreate()} t={tr} />
  </Box>;
  return <Box sx={{ p: 2, overflowY: 'auto', height: '100%', width: '100%' }}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}><Button size="small" startIcon={<ArrowBackIcon />} onClick={async () => { try { await flushSave(); setSelected(null); setSpec(null); setPendingProjectStatus(null); void refreshProjects(); } catch { setError(tr('saveFailedShort')); } }}>{tr('back')}</Button><Tooltip title={selected.customer_org || ''}><Typography sx={{ fontSize: 17, fontWeight: 800, flex: 1, color: 'var(--text-0)', [NARROW]: { flex: '1 1 100%' } }}>{selected.title}</Typography></Tooltip>{view !== 'specification' && <Chip size="small" label={tr('completeness', { filled: completeCounts.filled, total: completeCounts.total })} />}{!canEditSpec && view !== 'specification' && <Tooltip title={tr('specReadOnlyTip')}><Chip size="small" label={tr('specReadOnly')} /></Tooltip>}<HelpTip title={tr('projectStatusTip')} />{canEditProject ? <><Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{tr('statusLabel')}</Typography><Select size="small" value={selected.status} disabled={busy} onChange={(e) => setPendingProjectStatus(proposeProjectStatus(selected.id, selected.status, e.target.value as ProjectSummary['status']))} aria-label={tr('statusLabel')}>{STATUSES.map((s) => <MenuItem key={s} value={s}>{tr(`status.${s}`)}</MenuItem>)}</Select></> : <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{tr('statusLabel')}: {tr(`status.${selected.status}`)}</Typography>}</Box>
    {canEditSpec && <DigestSettings target={{ kind: 'project', id: selected.id }} />}
    
    {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
    <Box sx={{ borderBottom: '1px solid var(--line-soft)', mb: 1.5 }}>
      <SubTabs value={view} onChange={(v) => void openTab(v)} ariaLabel={selected.title} items={[
        { id: 'specification', label: tr('tabs.specification') },
        { id: 'motors', label: tr('tabs.motors') },
        { id: 'messages', label: projectUnread(selected) > 0 ? <Tooltip title={tr('unread.tip', { count: projectUnread(selected) })}><span>{tr('tabs.messages')} <Chip size="small" color="primary" label={unreadLabel(projectUnread(selected))} sx={{ height: 16, fontSize: 10, ml: 0.5 }} /></span></Tooltip> : tr('tabs.messages') },
        ...(canEditProject ? [{ id: 'notes' as const, label: tr('tabs.notes') }] : []),
      ]} />
    </Box>
    {(view === 'specification' || view === 'motors') && selected && <ProjectStatusBar data={activity} t={tr} />}
    {view === 'specification' && <Box sx={{ minWidth: 0 }}>
      {!schema || !spec || !selected ? <CircularProgress size={18} /> : <SpecWizard key={selected.id} memoryScope={user?.email ? `${developmentViewScope(user.email)}.optional.${selected.id}` : null}
        project={selected} step={step} onStep={showCard} completeness={completeness} saving={saving} saveError={saveError}
        onRetrySave={() => void enqueueSave(selected.id, draftRef.current, projectToken.current).catch(() => {})} onReloadDraft={() => void reloadDraft()}
        submitError={submitError} submitConflict={submitConflict} submitBusy={busy || saving} onSubmit={() => void onSubmitSpec()}
        reveal={reveal} messages={messages} onOpenFieldReview={openFieldReview}
        env={{ tr, lang, draft: draftRef.current, profile, spec, steps: sections, canEditSpec, busy, migrationBusy, updateDraft, fields, fieldErrors }}
        history={<VersionHistory versions={versions} onCompare={(a, b) => { setCompareVersions([a, b]); setVersionA(versions.find((v) => v.version === a) ?? null); setVersionB(versions.find((v) => v.version === b) ?? null); }} t={tr} />}
        banner={canEditSpec && (spec.schema_version ?? 1) < 2 ? <Alert severity="warning" action={<Button color="inherit" size="small" disabled={migrationBusy || saving} onClick={() => void requestMigrationPreview()}>{tr('migration.action')}</Button>}>{tr('migration.body')}</Alert> : null}
        assistant={(docked) => <SpecAssistantPanel projectId={selected.id} schema={schema} lang={lang} docked={docked} beforeApply={flushSave} onApplied={() => { void reloadDraft(); }} />} />}
    </Box>}
    {view === 'motors' && <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}><Box sx={{ flex: 1 }}><HelpTip title={tr('motorsTip')} /></Box>{canManageGlobally && <Button size="small" startIcon={<AddIcon />} onClick={async () => { if (selected) { try { setPicker(await developmentApi.motorPicker(selected.id)); setLinkOpen(true); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } } }}>{tr('linkMotor')}</Button>}</Box>
      {canEditProject && !!selected.grant_removal_candidates?.length && <Tooltip title={<Stack spacing={0.4}><Typography sx={{ fontSize: 11 }}>{tr('grantReviewTip')}</Typography>{selected.grant_removal_candidates.map((candidate) => <Typography key={`${candidate.email}/${candidate.die}/${candidate.config}`} sx={{ fontSize: 11 }}>{candidate.email} · {candidate.die}/{candidate.config} · {tr(`grantReason.${candidate.reason}`)}</Typography>)}</Stack>}><Alert severity="info" sx={{ mb: 1, py: 0 }}>{tr('grantReviewCount', { count: selected.grant_removal_candidates.length })}</Alert></Tooltip>}
      {(() => {
        // ONE group per geometry variant (name + step note once in its header, a colour accent per die), its duties as compact sub-rows;
        // history snapshots sit under their variant and show only in "All versions" (owner 2026-10-26)
        const versions = visibleProjectVersions(motors, snapshots, showAllVersions);
        const visibleMotors = showAllVersions ? orderByGroup(versions) : latestAndRecommended(versions);
        const rows = developmentComparisonRows(visibleMotors);
        const rowMotors = rows.map((r) => r.motor);
        const stepNote = (m: ProjectMotor) => m.note ? <Typography data-testid="step-note" sx={{ fontSize: 13, color: 'var(--text-2)', whiteSpace: 'normal', maxWidth: 640 }}>{m.note}</Typography> : null;
        const headerRow = (colSpan: number, accent: string, indent: boolean, content: React.ReactNode, strong = false) => <Box component="tr"><Box component="td" colSpan={colSpan} data-recommended={strong ? 'true' : undefined} sx={{ pl: indent ? 3.2 : 1.6, pr: 1.1, py: 0.55, bgcolor: strong ? 'rgba(245,158,11,0.10)' : 'var(--panel-2)', borderTop: indent ? undefined : '1px solid var(--line-soft)', borderBottom: '1px solid var(--line-soft)', boxShadow: strong ? `inset 9px 0 0 ${accent}` : `inset 4px 0 0 ${accent}`, whiteSpace: 'normal' }}><Box sx={{ position: 'sticky', left: 0, width: motorsViewW ? Math.max(motorsViewW - 48, 280) : '100%', maxWidth: motorsViewW ? Math.max(motorsViewW - 48, 280) : undefined, boxSizing: 'border-box' }}>{content}</Box></Box></Box>;
        const groupHeader = (r: (typeof rows)[number], colSpan: number) => {
          const live = motors.find((m) => variantKey(m) === variantKey(r.motor)) ?? null;
          const note = live ?? r.motor;
          return headerRow(colSpan, motorColor(r.motor), false, <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap>
            <Box component="span" aria-hidden sx={{ width: 9, height: 9, borderRadius: '50%', flex: '0 0 9px', bgcolor: motorColor(r.motor) }} />
            <Stack spacing={0.1} sx={{ minWidth: 0, flex: '1 1 260px' }}>
              <Typography sx={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-0)', overflowWrap: 'anywhere' }} data-testid="variant-name">{r.variant}<MotorRevisionText die={r.motor.die} config={r.motor.config} canonicalDie={r.motor.canonical_die} /></Typography>
              {stepNote(note)}
              {live && <VariantStageBar data={activity} motor={live} t={tr} />}
            </Stack>
            {live && selected && <VariantControls projectId={selected.id} motor={live} staff={canManageGlobally} busy={busy} t={tr} showAll={showAllVersions}
              onChanged={async () => { await loadProjectMotors(selected.id, projectToken.current); }} />}
            {live && selected && <ProjectMotorTests projectId={selected.id} motor={live} showAll={showAllVersions} t={tr} />}
            {live && <ActivityChip rows={activityRows} motor={live} t={tr} />}
            {live && <Stack direction="row" spacing={0.5} alignItems="center">
              <span>{canManageProjectMotors ? <Select size="small" value={live.status === 'trial' ? 'trial' : 'current'} disabled={busy} onChange={(e) => void changeMotorStatus(live, e.target.value as 'current' | 'trial')} aria-label={tr('motorStatus.label')} sx={{ minWidth: 90, '& .MuiSelect-select': { py: 0.35, fontSize: 11 } }}><MenuItem value="current">{tr('motorStatus.current')}</MenuItem><MenuItem value="trial">{tr('motorStatus.trial')}</MenuItem></Select> : <Chip size="small" label={tr(`motorStatus.${live.status === 'trial' ? 'trial' : 'current'}`)} />}</span>
              <HelpTip title={tr('motorStatus.tip')} />
              {canManageProjectMotors && <Tooltip title={tr('unlink')}><span><IconButton size="small" aria-label={tr('unlink')} disabled={busy} onClick={() => setUnlinkTarget(live)} sx={{ color: 'var(--text-3)' }}><DeleteOutlineIcon sx={{ fontSize: 18 }} /></IconButton></span></Tooltip>}
            </Stack>}
          </Stack>, isRecommended(live));
        };
        const historyHeader = (r: (typeof rows)[number], colSpan: number) => headerRow(colSpan, motorColor(r.motor), true, <Stack direction="row" alignItems="center" spacing={0.8} flexWrap="wrap" useFlexGap data-testid="history-header">
          <Chip size="small" variant="outlined" icon={<HistoryIcon sx={{ fontSize: 14 }} />} label={tr('snapshots.history')} sx={{ height: 20, fontSize: 10.5 }} />
          <Typography sx={{ fontSize: 12, color: 'var(--text-1)' }}>{[r.motor.created_at ? new Date(r.motor.created_at).toLocaleString() : '', r.motor.author_display_name, r.motor.source ? snapshotSourceLabel(r.motor.source, tr) : ''].filter(Boolean).join(' · ')}</Typography>
          {r.estimate && <Chip size="small" label={tr('comparison.estimate')} />}
          <Tooltip title={tr('snapshots.details')}><IconButton size="small" aria-label={tr('snapshots.details')} onClick={() => setHistoryDetail(r.motor)} sx={{ color: 'var(--text-3)' }}><InfoOutlinedIcon sx={{ fontSize: 16 }} /></IconButton></Tooltip>
          {stepNote(r.motor)}
        </Stack>);
        return <Box ref={motorsViewRef} sx={{ minWidth: 0 }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.7 }}><ActivityChip rows={activityRows} t={tr} /><Box sx={{ flex: 1 }} />
          <Tooltip title={tr('versionView.tip')}><ToggleButtonGroup exclusive size="small" value={showAllVersions ? 'all' : 'latest'} onChange={(_, next: 'latest' | 'all' | null) => { if (next) changeShowAllVersions(next === 'all'); }} aria-label={tr('versionView.label')}>
            <ToggleButton value="latest" sx={{ textTransform: 'none', px: 1.2, py: 0.2, fontSize: 11 }}>{tr('versionView.latest')}</ToggleButton>
            <ToggleButton value="all" sx={{ textTransform: 'none', px: 1.2, py: 0.2, fontSize: 11 }}>{tr('versionView.all')}</ToggleButton>
          </ToggleButtonGroup></Tooltip>
        </Stack>
        {visibleMotors.length === 0 ? <Typography sx={{ color: 'var(--text-3)', fontSize: 12 }}>{tr('noMotors')}</Typography> : <SharedComparisonTable rows={rows} columns={developmentComparisonColumns(tr, rows)} rowKey={(r) => r.id} rowLabel={() => <span />} labelHeader="" rowAccent={(r) => motorColor(r.motor)} onRowClick={(r) => { if (!r.history) void openProjectMotor({ id: selected.id, title: selected.title }, r.motor, r.result.duty).catch((e) => setError(e instanceof Error ? e.message : String(e))); }} renderGroupHeader={(r, index, _all, colSpan) => { const flags = headerFlags(rowMotors, index); return flags.group || flags.history ? <>{flags.group && groupHeader(r, colSpan)}{flags.history && historyHeader(r, colSpan)}</> : null; }} showAllInputs inputHeaderColor="var(--text-2)" resultHeaderColor="var(--text-2)" enableColumnPicker columnPickerTitle={tr('comparison.columns')} columnPickerLabel={tr('comparison.chooseColumns')} empty={<Typography>{tr('noResults')}</Typography>} renderHeadEnd={() => <Box component="th" sx={{ px: 1, py: 0.65, fontSize: 10, color: 'var(--text-3)', borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)', textAlign: 'left' }}>{tr('comparison.actions')}</Box>} renderRowEnd={(r) => <Box component="td" onClick={(e) => e.stopPropagation()} sx={{ px: 0.5, borderBottom: '1px solid var(--app-bg)', whiteSpace: 'nowrap', cursor: 'default' }}>{r.history ? null : <MotorOpenActions motor={r.motor} duty={r.result.duty} engineer={canManageGlobally} t={tr} projectId={selected.id} projectTitle={selected.title} showAll={showAllVersions} />}</Box>} />}
      </Box>; })()}
    </Box>}
    {view === 'messages' && <Box><Stack spacing={0.8} sx={{ mb: 1 }}>{messages.map((m, i) => <Box key={`${m.created_at}-${i}`}>{i === firstUnreadIndex(messages, shownUnread) && <Divider sx={{ mb: 0.8, fontSize: 10.5 }}><Chip size="small" color="primary" label={tr('unread.new')} sx={{ height: 16, fontSize: 10 }} /></Divider>}<Paper sx={{ p: 1, bgcolor: 'var(--panel-2)', ...(m.id && shownUnread.has(m.id) ? { borderLeft: '3px solid', borderLeftColor: 'primary.main' } : {}) }}><Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{m.author_display_name || tr('projectMember')} · {m.created_at}</Typography><Typography sx={{ fontSize: 12, whiteSpace: 'pre-wrap', color: 'var(--text-1)' }}>{m.text}</Typography>{m.field_ref && <Chip size="small" label={m.field_ref} />}</Paper></Box>)}</Stack><Box sx={{ display: 'flex', gap: 1 }}><TextField size="small" fullWidth multiline maxRows={4} value={message} onChange={(e) => setMessage(e.target.value)} placeholder={tr('writeMessage')} /><Select size="small" displayEmpty value={messageFieldRef} onChange={(e) => setMessageFieldRef(e.target.value)}><MenuItem value="">{tr('noFieldReference')}</MenuItem>{sections.flatMap((section) => (section.fields ?? []).map((f) => <MenuItem key={`${section.id}:${f.id}`} value={`${section.id}:${f.id}`}>{label(f.short_label ?? f.label)}</MenuItem>))}</Select><Button disabled={busy || !message.trim()} onClick={() => void postMessage()}>{tr('send')}</Button></Box><Typography sx={{ mt: 0.5, fontSize: 10.5, color: 'var(--text-4)' }}>{tr('noEmail')}</Typography></Box>}
    {view === 'notes' && canEditProject && <NotesPanel project={selected} onSave={async (notes) => { try { const p = await developmentApi.update(selected.id, { notes }); setSelected(p); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } }} t={tr} />}
    <InviteDialog open={inviteOpen} busy={busy} email={email} role={memberRole} onEmail={setEmail} onRole={setMemberRole} onClose={() => setInviteOpen(false)} onSubmit={() => void submitInvite()} t={tr} />
    <LinkDialog open={linkOpen} busy={busy} picker={picker} onClose={() => setLinkOpen(false)} onSubmit={(item: ProjectMotorPickerItem) => void submitLink(item)} onRestore={(item: ProjectMotorPickerItem) => void restoreMotor(item)} t={tr} />
    <Dialog open={!!unlinkTarget} onClose={() => !busy && setUnlinkTarget(null)}><DialogTitle>{tr('unlink')} {unlinkTarget?.die} / {unlinkTarget?.config}?</DialogTitle><DialogContent>{tr('unlinkConfirm')}</DialogContent><DialogActions><Button disabled={busy} onClick={() => setUnlinkTarget(null)}>{tr('cancel')}</Button><Button color="error" disabled={busy} onClick={() => void confirmUnlink()}>{tr('unlink')}</Button></DialogActions></Dialog>
    <Dialog open={!!pendingStatusForProject(pendingProjectStatus, selected.id)} onClose={() => !busy && setPendingProjectStatus((value) => cancelProjectStatus(value))}><DialogTitle>{tr('confirmProjectStatus.title')}</DialogTitle><DialogContent>{tr('confirmProjectStatus.body', { status: pendingProjectStatus ? tr(`status.${pendingProjectStatus.proposed}`) : '' })}</DialogContent><DialogActions><Button disabled={busy} onClick={() => setPendingProjectStatus((value) => cancelProjectStatus(value))}>{tr('cancel')}</Button><Button disabled={busy} variant="contained" onClick={() => void confirmProjectStatus()}>{tr('confirmProjectStatus.confirm')}</Button></DialogActions></Dialog>
        <Dialog open={fieldReviewOpen} onClose={() => setFieldReviewOpen(false)} fullWidth maxWidth="sm"><DialogTitle>{reviewField.startsWith('section:') ? tr('sectionReview') : tr('fieldReview')}: {reviewField.startsWith('mission_profile:') ? tr('field.missionProfileRow', { row: reviewField.slice('mission_profile:'.length) }) : (() => { const id = reviewField.split('.').pop(); const field = sections.flatMap((section) => section.fields).find((item) => item.id === id); if (field) return label(field.short_label ?? field.label); if (reviewField.startsWith('section:')) { const section = sections.find((item) => item.id === reviewField.slice('section:'.length)); return section ? sectionTitle(section.title) : tr('sectionReview'); } return reviewField; })()}</DialogTitle><DialogContent><Stack spacing={0.8} sx={{ pt: 0.5 }}>{messages.filter((message) => reviewRefs(reviewField).some((ref) => matchesFieldReference(message.field_ref, ref))).map((message, index) => <Paper key={message.id ?? `${message.created_at}-${index}`} variant="outlined" sx={{ p: 1 }}><Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{message.author_display_name || tr('projectMember')} · {message.version ? `v${message.version}` : tr('currentDraft')}</Typography><Typography sx={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{message.text}</Typography>{message.resolved && <Chip size="small" label={tr('resolved')} />}{canEditSpec && message.id && <Button size="small" onClick={() => void resolveReviewMessage(message.id!, !message.resolved)}>{message.resolved ? tr('reopen') : tr('resolve')}</Button>}</Paper>)}{!messages.some((message) => reviewRefs(reviewField).some((ref) => matchesFieldReference(message.field_ref, ref))) && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tr('noFieldComments')}</Typography>}{canEditSpec && <><TextField size="small" multiline minRows={3} value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} placeholder={tr('writeFieldReview')} /><Button disabled={busy || !reviewComment.trim()} onClick={() => void postFieldReview()}>{tr('addFieldReview')}</Button></>}</Stack></DialogContent><DialogActions><Button onClick={() => setFieldReviewOpen(false)}>{tr('close')}</Button></DialogActions></Dialog>    <Dialog open={!!historyDetail} onClose={() => setHistoryDetail(null)} fullWidth maxWidth="sm"><DialogTitle>{tr('snapshots.history')} · {historyDetail?.die} / {historyDetail?.config}</DialogTitle><DialogContent><Stack spacing={0.7} sx={{ pt: 0.5 }}><Typography variant="body2">{historyDetail?.author_display_name} · {historyDetail?.created_at ? new Date(historyDetail.created_at).toLocaleString() : ''}</Typography>{historyDetail?.source && <Typography variant="body2">{tr('snapshots.source')}: {snapshotSourceLabel(historyDetail.source, tr)}</Typography>}{historyDetail?.note && <Tooltip title={historyDetail.note}><Typography variant="body2" noWrap sx={{ maxWidth: '100%' }}>{historyDetail.note}</Typography></Tooltip>}</Stack></DialogContent><DialogActions><Button onClick={() => setHistoryDetail(null)}>{tr('close')}</Button></DialogActions></Dialog>
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
    {picker.this_project.length ? picker.this_project.map((item) => <Box key={`${item.die}/${item.config}`} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.35 }}><Box sx={{ flex: 1 }}><Typography sx={{ fontSize: 12 }}>{pickerDisplayLabel(item, t)}</Typography><Tooltip title={item.unlinked_by ? `${item.unlinked_by} · ${item.unlinked_at ?? ''}` : item.unlinked_at ?? ''}><Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{item.die} / {item.config}</Typography></Tooltip></Box><Tooltip title={item.disabled ? t(pickerReasonKey(item.reason)) : ''}><span><Button size="small" disabled={busy || item.disabled} onClick={() => onRestore(item)}>{t('picker.restore')}</Button></span></Tooltip></Box>) : null}
    <Divider sx={{ my: 1 }} /><Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{t('picker.catalog')}</Typography>
    <TextField size="small" fullWidth placeholder={t('picker.search')} value={query} onChange={(e) => setQuery(e.target.value)} sx={{ mb: 0.7 }} />
    <Box sx={{ maxHeight: 300, overflowY: 'auto' }}>{groups.map(([die, items]) => <Box key={die}><Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', py: 0.45 }}>{die}</Typography>{items.map((item) => <Button key={`${die}/${item.config}`} fullWidth disabled={busy || item.disabled} onClick={() => setSelected(item)} sx={{ justifyContent: 'space-between', textAlign: 'left', color: selected === item ? 'primary.main' : 'var(--text-1)', textTransform: 'none', borderBottom: '1px solid var(--line-soft)' }}><span>{item.config} · {pickerDisplayLabel(item, t)}</span>{item.disabled && <Tooltip title={t(pickerReasonKey(item.reason))}><span>{t('picker.unavailable')}</span></Tooltip>}</Button>)}</Box>)}</Box>
  </DialogContent><DialogActions><Button disabled={busy} onClick={onClose}>{t('cancel')}</Button><Button disabled={busy || !selected || selected.disabled} onClick={() => selected && onSubmit(selected)}>{t('link')}</Button></DialogActions></Dialog>;
}
function NotesPanel({ project, onSave, t }: { project: ProjectSummary; onSave: (s: string) => Promise<void>; t: (key: string) => string }) { const [value, setValue] = useState(project.notes ?? ''); return <Stack spacing={1}><Box><HelpTip title={t('internalNotesTip')} /></Box><TextField multiline minRows={5} value={value} onChange={(e) => setValue(e.target.value)} /><Button sx={{ alignSelf: 'start' }} onClick={() => void onSave(value)}>{t('save')}</Button></Stack>; }
function VersionHistory({ versions, onCompare, t }: { versions: SpecSnapshot[]; onCompare: (a: number, b: number) => void; t: (key: string, args?: Record<string, unknown>) => string }) { const [a, setA] = useState('');
  const [b, setB] = useState(''); return <Box><Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{t('history')}</Typography>{versions.length === 0 ? <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{t('noVersions')}</Typography> : <Box sx={{ display: 'flex', gap: 0.7, alignItems: 'center', flexWrap: 'wrap' }}><Select size="small" value={a} displayEmpty onChange={(e) => setA(e.target.value)}><MenuItem value="">{t('versionA')}</MenuItem>{versions.map((v) => <MenuItem key={v.version} value={String(v.version)}>v{v.version}</MenuItem>)}</Select><Select size="small" value={b} displayEmpty onChange={(e) => setB(e.target.value)}><MenuItem value="">{t('versionB')}</MenuItem>{versions.map((v) => <MenuItem key={v.version} value={String(v.version)}>v{v.version}</MenuItem>)}</Select><Button size="small" disabled={!a || !b || a === b} onClick={() => onCompare(Number(a), Number(b))}>{t('compare')}</Button>{versions.map((v) => <Chip key={v.version} size="small" label={`v${v.version} · ${v.author_display_name || t('projectMember')}`} title={v.created_at ?? ''} />)}</Box>}</Box>; }
function VersionDiffDialog({ open, onClose, a, b, t, schema, lang }: { open: boolean; onClose: () => void; a: SpecSnapshot | null; b: SpecSnapshot | null; t: (key: string, args?: Record<string, unknown>) => string; schema: DevelopmentSchema | null; lang: string }) {
  const before = a?.spec ?? a?.draft;  const after = b?.spec ?? b?.draft;  const diff = useMemo(() => semanticSpecificationDiff(before, after, {    before: a?.schema ?? schema ?? undefined,    after: b?.schema ?? schema ?? undefined,  }), [a, b, schema]);
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="md"><DialogTitle>{t('versionDiff')} · {t('versionFrom', { version: a?.version ?? '?' })} → {t('versionTo', { version: b?.version ?? '?' })}</DialogTitle><DialogContent>{diff.length ? diff.map((entry, index) => {    const fieldLabel = formatDiffLabel(entry, b?.schema ?? a?.schema ?? schema ?? undefined, lang, (key) => t(key.replace('versionDiff.property.', 'diffProperty.')));    const row = entry.rowId ? [...(Array.isArray(after?.mission_profile) ? after.mission_profile : []), ...(Array.isArray(before?.mission_profile) ? before.mission_profile : [])].find((item: any) => String(item.row_id ?? '') === entry.rowId) : undefined;    const label = row?.phase ? `${String(row.phase)} · ${fieldLabel}` : fieldLabel;    return <Box key={`${entry.namespace}:${entry.fieldId}:${entry.rowId ?? ''}:${entry.path}:${index}`} sx={{ py: 0.8, borderBottom: '1px solid var(--line-soft)' }}><Typography sx={{ fontWeight: 700, fontSize: 12 }}>{label}</Typography><Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>{t(`versionChange.${entry.change}`)}</Typography><Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>{t('versionBefore')}: {formatDiffValue(entry.before, entry, a?.schema ?? schema ?? undefined, lang, t)}</Typography><Typography sx={{ fontSize: 11, color: 'var(--text-1)' }}>{t('versionAfter')}: {formatDiffValue(entry.after, entry, b?.schema ?? schema ?? undefined, lang, t)}</Typography></Box>;  }) : <Typography sx={{ fontSize: 12 }}>{t('noDiff')}</Typography>}</DialogContent><DialogActions><Button onClick={onClose}>{t('close')}</Button></DialogActions></Dialog>;
}
