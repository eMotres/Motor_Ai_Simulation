import React, { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import {
  ThemeProvider,
  CssBaseline,
  AppBar,
  Toolbar,
  Typography,
  Box,
  Tabs,
  Tab,
  IconButton,
  Tooltip,
  Chip,
  Badge,
  CircularProgress,
  useMediaQuery,
} from '@mui/material';
import {
  Refresh as RefreshIcon,
  CloudSync as CloudSyncIcon,
  CloudOff as CloudOffIcon,
  LightMode as LightModeIcon,
  DarkMode as DarkModeIcon,
} from '@mui/icons-material';
import { buildAppTheme, loadThemeMode, saveThemeMode, type AppMode } from './theme';
import BrandLogo from './components/common/BrandLogo';
import MotorsCatalog from './components/catalog/MotorsCatalog';
import ActiveFamilyStrip from './components/common/ActiveFamilyStrip';
import LoadGuardDialog from './components/common/LoadGuardDialog';
import FamilyLoadBanner from './components/common/FamilyLoadBanner';
import ComputeNodesHint from './components/common/ComputeNodesHint';
import SubTabs from './components/common/SubTabs';
import AuthButton, { preloadAuthMenu } from './components/auth/AuthButton';
import Landing from './components/landing/Landing';
import { VersionBadge } from './components/VersionBadge';
import { backendReachable } from './lib/version';
import { useAuth } from './contexts/AuthContext';
import SupportWidget from './components/support/SupportWidget';
import AnalyticsConsent from './components/common/AnalyticsConsent';
import ActivityBeat from './components/common/ActivityBeat';
import MaterialOverrideSync from './components/materials/MaterialOverrideSync';
import SaveToMotorButton from './components/common/SaveToMotorButton';
import ErrorBoundary from './components/ErrorBoundary';
import { useMaterialsLibrary } from './components/materials/useMaterialsLibrary';
import type { SelectedMaterial, MaterialCategory } from './components/materials/useMaterialsLibrary';
import { saveGlobal, blankMaterial, type Cat } from './lib/materialsActions';
import { useMotorStore, useUIStore } from './stores/motorStore';
import { installDiag } from './lib/diag';
import { newRequestCount } from './lib/visitorRequests';
import { pageVisible } from './lib/pageVisible';
import { unreadLabel } from './lib/unreadMessages';
import { useUnreadCounts, useUnreadPolling } from './lib/useUnreadMessages';
import { syncMeshConfigFromServer } from './lib/meshConfigSync';
import CompareTab from './components/compare/CompareTab';
import UpdateBanner from './components/common/UpdateBanner';
import { ensureActiveMotor } from './components/common/motorSettings';
import { useModulePanels } from './modules/moduleTabs';
import { useTranslation } from 'react-i18next';
import { useApiReady } from './contexts/AuthContext';
import { parseAdminLink } from './lib/adminLink';
import { parseAppLink } from './lib/appLink';
import { computeHintApplies, gateOpen, hasSimulationUI, isStandardUser, landingTabForUser, productionVisible, tabAllowed, TAB_GATES } from './lib/accessUi';
import { barEntries, isSimulationTab, barValue, simulationsTarget, SIMULATIONS_BAR_ID, SIMULATION_TAB_IDS } from './lib/simulationsMenu';
import { oneOf } from './lib/viewMemory';
import { useRememberedView } from './lib/useRememberedView';
import { resolveSchemasRoute } from './lib/schemasRoute';
import RefusedNotice from './components/common/RefusedNotice';
import { hasOwnConfigureChoice } from './components/compare/configureChoice';
import { consumeOpenInConfigure } from './lib/landingMotors';
import { useServerLocale } from './i18n/persist';
import { clearLiveDutyContext, invalidateLastMotorOperations, restoreLastMotor, setLastMotorState } from './lib/lastMotor';
import { watchChunkImport } from './lib/chunkReload';
import { narrowSplitColumn, narrowSplitOuter } from './lib/narrowLayout';

// Every tab except Motors and Configure (the two a standard account has) is its own chunk: three.js, recharts, the
// FEM viewers and the admin tools are fetched when their tab is first opened, not with the workspace.
const UnifiedActiveMotorScene = lazy(watchChunkImport(() => import('./components/viewer3d/UnifiedActiveMotorScene')));
const ParameterVariationTable = lazy(watchChunkImport(() => import('./components/sweep/ParameterVariationTable')));
const GeometryTab = lazy(watchChunkImport(() => import('./components/geometry/GeometryTab')));
const MaterialsCatalogPanel = lazy(watchChunkImport(() => import('./components/materials/MaterialsCatalogPanel')));
const MaterialsLibraryTree = lazy(watchChunkImport(() => import('./components/materials/MaterialsLibraryTree')));
const MaterialDetailView = lazy(watchChunkImport(() => import('./components/materials/MaterialDetailView')));
const WireStockTable = lazy(watchChunkImport(() => import('./components/materials/WireStockTable')));
const MeshPanel = lazy(watchChunkImport(() => import('./components/mesh/MeshPanel')));
const SimulationPanel = lazy(watchChunkImport(() => import('./components/simulation/SimulationPanel')));
const Static3DPanel = lazy(watchChunkImport(() => import('./components/static3d/Static3DPanel')));
const MechanicalPanel = lazy(watchChunkImport(() => import('./components/mechanical/MechanicalPanel')));
const ThermalPanel = lazy(watchChunkImport(() => import('./components/thermal/ThermalPanel')));
const ControllerPanel = lazy(watchChunkImport(() => import('./components/controller/ControllerPanel')));
const SystemPanel = lazy(watchChunkImport(() => import('./components/system/SystemPanel')));
const SweepConfigPanel = lazy(watchChunkImport(() => import('./components/sweep/SweepConfigPanel')));
const ComparePanel = lazy(watchChunkImport(() => import('./components/compare/ComparePanel')));
const CostPanel = lazy(watchChunkImport(() => import('./components/cost/CostPanel')));
const Model3DPanel = lazy(watchChunkImport(() => import('./components/model3d/Model3DPanel')));
// Production is its own chunk (owner 2026-10-18): its updates never touch the Configure / Motors bundle
const ProductionTab = lazy(watchChunkImport(() => import('./modules/production/components/ProductionPage').then((m) => ({ default: m.ProductionTab }))));
// Development is a customer-facing project area; keep the wizard out of the initial engineering bundle.
const DevelopmentTab = lazy(watchChunkImport(() => import('./modules/development/components/DevelopmentPage')));
const DesignerTab = lazy(watchChunkImport(() => import('./modules/designer/DesignerTab')));
const AdminPanel = lazy(watchChunkImport(() => import('./components/admin/AdminPanel')));

/** What a tab shows while its chunk is on the way. */
const TabLoading: React.FC = () => <CircularProgress size={18} sx={{ m: 2 }} />;

// The signed-in account menu is its own chunk (AuthButton lazy-loads it); start fetching it
// together with the workspace so the avatar does not pop in late.
preloadAuthMenu();

// Theme is built from the shared eMotres/aerostator design tokens — see
// src/theme.ts.  Light is the default (matches the marketing site); dark
// stays available via the AppBar toggle.

const API_BASE = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

/** How many visitors are waiting for an answer — the badge on the Admin tab.
 *
 *  A visitor who asks for access on the landing page leaves a request the admin
 *  has no other reason to go looking for, so the tab itself says there is one.
 *  Admin-only and deliberately silent: a 401/403 (or a signed-out moment) is not
 *  an error here, it is "no badge", and nothing is polled while the tab is
 *  hidden or the user is not an admin. */
function useNewRequestCount(isAdmin: boolean): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!isAdmin) { setN(0); return; }
    let alive = true;
    const read = () => {
      if (!pageVisible()) return;
      fetch(`${API_BASE}/api/admin/support/requests`, { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => { if (alive) setN(newRequestCount(j?.requests)); })
        .catch(() => { /* signed out, offline, or not an admin — no badge */ });
    };
    read();
    const id = setInterval(read, 60_000);
    window.addEventListener('focus', read);
    return () => { alive = false; clearInterval(id); window.removeEventListener('focus', read); };
  }, [isAdmin]);
  return n;
}

/** Is this account a member of at least one production?  One read when asked (`check`); a failure is "no". */
function useProductionMember(check: boolean): boolean {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (!check) { setOk(false); return; }
    let alive = true;
    fetch(`${API_BASE}/api/production`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (alive) setOk(Array.isArray(j?.productions) && j.productions.length > 0); })
      .catch(() => { /* signed out or offline - no tab */ });
    return () => { alive = false; };
  }, [check]);
  return ok;
}

/** Designer tab (pilot): drawn only when the server says this account may use the design assistant
 *  (DESIGN_AGENT_ACCESS) - one read per sign-in, a failure is "not allowed". */
function useDesignerAllowed(signedIn: boolean): boolean {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (!signedIn) { setOk(false); return; }
    let alive = true;
    fetch(`${API_BASE}/api/design-agent/status`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (alive) setOk(!!j?.allowed); })
      .catch(() => { /* offline or signed out - no tab */ });
    return () => { alive = false; };
  }, [signedIn]);
  return ok;
}

/** Customer Development visibility comes from the cheap membership index; reset when auth identity changes. */
function useDevelopmentMember(check: boolean, identity?: string): boolean {
  const [member, setMember] = useState(false);
  useEffect(() => {
    let alive = true;
    setMember(false);
    if (!check) return () => { alive = false; };
    fetch(`${API_BASE}/api/projects/membership`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (alive) setMember(j?.has_projects === true); })
      .catch(() => { /* no membership grant on a failed request */ });
    return () => { alive = false; };
  }, [check, identity]);
  return member;
}

// ─── Geometry build timer ───────────────────────────────────────────────────
const indicatorBoxSx = {
  position: 'absolute' as const,
  bottom: 16,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 999,
  display: 'flex',
  alignItems: 'center',
  gap: 1,
  bgcolor: 'var(--overlay)',
  backdropFilter: 'blur(4px)',
  px: 2,
  py: 0.75,
  borderRadius: 2,
  border: '1px solid rgba(59,130,246,0.4)',
};

const GeometryBuildTimer: React.FC = () => {
  const isGeometryUpdating = useMotorStore(s => s.isGeometryUpdating);
  const [elapsed, setElapsed] = useState(0);
  const [lastBuildTime, setLastBuildTime] = useState<number | null>(null);
  const [showResult, setShowResult] = useState(false);
  const startRef = useRef<number | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const hideRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // (The server mesh-block sync used to live here — see App() below for why
  // it moved: this timer mounts only with the Geometry tab.)

  useEffect(() => {
    if (isGeometryUpdating) {
      startRef.current = Date.now();
      setElapsed(0);
      setShowResult(false);
      if (hideRef.current) clearTimeout(hideRef.current);
      intervalRef.current = setInterval(() => {
        setElapsed(Date.now() - (startRef.current ?? Date.now()));
      }, 100);
    } else {
      if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; }
      if (startRef.current !== null) {
        const total = Date.now() - startRef.current;
        startRef.current = null;
        setLastBuildTime(total);
        setShowResult(true);
        hideRef.current = setTimeout(() => setShowResult(false), 5000);
      }
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [isGeometryUpdating]);

  if (isGeometryUpdating) {
    return (
      <Box sx={indicatorBoxSx}>
        <CircularProgress size={16} thickness={5} />
        <Typography variant="caption" sx={{ color: 'white', fontSize: 12 }}>
          Building… {(elapsed / 1000).toFixed(1)}s
        </Typography>
      </Box>
    );
  }

  if (showResult && lastBuildTime !== null) {
    return (
      <Box sx={{ ...indicatorBoxSx, border: '1px solid rgba(74,222,128,0.4)' }}>
        <Typography variant="caption" sx={{ color: '#4ade80', fontSize: 12 }}>
          ✓ Geometry: {(lastBuildTime / 1000).toFixed(1)}s
        </Typography>
      </Box>
    );
  }

  return null;
};

function App() {
  const [themeMode, setThemeMode] = useState<AppMode>(() => loadThemeMode());
  const narrow = useMediaQuery('(max-width:899.95px)');
  useEffect(() => { saveThemeMode(themeMode); }, [themeMode]);
  const appTheme = useMemo(() => buildAppTheme(themeMode), [themeMode]);
  const { activeTab, setActiveTab } = useUIStore();
  // tabs opened in this page session ('visited' keep-mounted tabs stay mounted after their first visit)
  const [visitedTabs, setVisitedTabs] = useState<ReadonlySet<string>>(() => new Set([activeTab]));
  useEffect(() => { setVisitedTabs((s) => (s.has(activeTab) ? s : new Set(s).add(activeTab))); }, [activeTab]);

  useEffect(() => {
    const route = resolveSchemasRoute(window.location.href);
    if (route.redirected) window.history.replaceState(null, '', route.href);
    if (route.activeTab) setActiveTab(route.activeTab);
  }, [setActiveTab]);
  const { user, isAdmin, role, enforced, defaultMotor, resolved: authResolved } = useAuth();
  // Interface language (docs/I18N.md): adopt the signed-in user's stored choice.
  const { t: tr, i18n } = useTranslation('common');
  useServerLocale(i18n, useApiReady(), user?.email);
  // Access (only enforced when the backend has AUTH_ENFORCE on; with it off,
  // dev shows everything):
  //   • Anonymous       → the Motors catalog ONLY (browse, can't work with a motor).
  //   • Signed in (any role) → the full engineering UI (geometry/mesh/FEM/optimize).
  const signedIn = !enforced || !!user;
  // UNTIL /api/me HAS ANSWERED we do not know whether this backend enforces
  // auth — `enforced` starts false, so `signedIn` reads true for the ~100 ms
  // before the answer lands.  An anonymous visitor to a closed server spent
  // that window booting the workspace: the geometry and schema probes went
  // out and came back 401, red in the console of the first page anyone sees
  // (live, 2026-09-16).  A RESTORED SESSION skips the wait entirely — `user`
  // comes back from localStorage synchronously — so a signed-in boot is
  // exactly what it was, and only an anonymous one waits.
  const authPending = !authResolved && !user;
  // THE FULL ENGINEERING UI is the admin's, an ENGINEER's (role "engineer", owner 2026-10-14: Configure only, or the whole
  // simulation) and the local, unenforced dev server's.  A standard account (role "user", owner 2026-10-05) has exactly
  // two menus - Motors and Configure - because both only READ the catalogue; the
  // writer routes stay admin-only in the gate.  Until /api/me has answered we do
  // not know the role, so the answer is the NARROW one: a hidden tab is never
  // mounted, and a mounted Simulation panel is what fired the 403s on
  // /api/simulation/caches for a regular account.
  const fullUI   = authResolved && hasSimulationUI(enforced, isAdmin, role);
  const standardUser = isStandardUser(enforced, isAdmin, role);
  // Production tab: the roles that work on productions, or ANY account that is a member of one (owner 2026-10-18: a standard
  // account added to a production as engineer saw nothing - the server grants by membership, the tab gated by role).
  const productionMember = useProductionMember(authResolved && signedIn && !productionVisible(enforced, isAdmin, role));
  const producer = authResolved && (productionVisible(enforced, isAdmin, role) || productionMember);
  const developmentMember = useDevelopmentMember(authResolved && signedIn && !isAdmin && role !== 'engineer', user?.email);
  const developmentAllowed = authResolved && (isAdmin || role === 'engineer' || developmentMember || !enforced);
  // A link to a page of the app (activity e-mails, shared documents links): once the account is known to reach the tab it names -
  // Production for /production/..., Development for /projects/... - open that tab; the page itself reads the link from the address
  // bar (ProductionTab, DevelopmentPage).  Root has already checked on the server that the caller is a production member.
  const deepLink = useRef(parseAppLink(window.location.pathname, window.location.search));
  useEffect(() => {
    const link = deepLink.current;
    if (!link || !authResolved || !signedIn) return;
    if (link.kind === 'production' ? !producer : !developmentAllowed) return;
    deepLink.current = null;
    setActiveTab((link.kind === 'production' ? 'production' : 'development') as never);
  }, [authResolved, signedIn, producer, developmentAllowed, setActiveTab]);
  // The owner's daily report links to Admin sections (/?tab=admin&section=digests&draft=...): open the Admin tab once the account is
  // known to be an admin; AdminPanel reads the section and DigestsSection the draft from the address bar.
  const adminLink = useRef(parseAdminLink(window.location.search));
  useEffect(() => {
    if (!adminLink.current || !authResolved || !signedIn || !isAdmin) return;
    adminLink.current = null;
    setActiveTab('admin' as never);
  }, [authResolved, signedIn, isAdmin, setActiveTab]);
  const newRequests = useNewRequestCount(isAdmin);
  // Unread project / production messages (owner 2026-10-28): polled once a minute while the page is visible; the Development and
  // Production entries show the totals, the lists and pages read the same counts.
  useUnreadPolling(authResolved && signedIn && (producer || developmentAllowed), user?.email);
  const unreadMessages = useUnreadCounts();
  const designerAllowed = useDesignerAllowed(signedIn);
  const [panelWidth, setPanelWidth] = React.useState(300);
  const [selectedMaterial, setSelectedMaterial] = useState<SelectedMaterial | null>(null);
  const { library: matLibrary, loading: matLoading, error: matError, reload: matReload } = useMaterialsLibrary();

  // Admin: create a new blank material in the shared (global) library, then select it to edit.
  const handleAddGlobal = async (category: MaterialCategory) => {
    const name = `New ${category} ${Date.now().toString(36).slice(-4)}`;
    try {
      await saveGlobal(category as Cat, name, blankMaterial(category as Cat));
      matReload();
      setSelectedMaterial({ category, name });
    } catch { /* error surfaces in the detail view on the next action */ }
  };
  // Remember the last-viewed material so the detail pane restores it next session.
  useEffect(() => {
    try { if (selectedMaterial) localStorage.setItem('materials.selected', JSON.stringify(selectedMaterial)); } catch { /* quota */ }
  }, [selectedMaterial]);
  // Once the library loads, ensure a material is selected — the last-viewed one (if it
  // still exists) or the first available — so the detail pane is never empty.
  useEffect(() => {
    if (!matLibrary) return;
    const lib = matLibrary as unknown as Record<string, Record<string, unknown>>;
    const has = (s: SelectedMaterial | null): boolean =>
      !!s && !!lib[s.category] && Object.prototype.hasOwnProperty.call(lib[s.category], s.name);
    if (has(selectedMaterial)) return;
    let restore: SelectedMaterial | null = null;
    try { const raw = localStorage.getItem('materials.selected'); if (raw) restore = JSON.parse(raw); } catch { /* ignore */ }
    if (has(restore)) { setSelectedMaterial(restore); return; }
    for (const cat of ['steel', 'magnet', 'conductor', 'insulator', 'coolant'] as const) {
      const names = Object.keys(lib[cat] ?? {});
      if (names.length) { setSelectedMaterial({ category: cat, name: names[0] }); return; }
    }
  }, [matLibrary, selectedMaterial]);
  const isDragging = React.useRef(false);

  const onDividerMouseDown = React.useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    const startX = e.clientX;
    const startW = panelWidth;
    const onMove = (me: MouseEvent) => {
      if (!isDragging.current) return;
      const next = Math.max(160, Math.min(520, startW + me.clientX - startX));
      setPanelWidth(next);
    };
    const onUp = () => {
      isDragging.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [panelWidth]);
  const {
    resetToDefaults,
    fetchGeometryFromApi,
    fetchSchemaFromApi,
    connectedToApi,
    isLoading,
    loadServerSweepConfig,
  } = useMotorStore();


  // Not before somebody is signed in: on a server that publishes nothing
  // (PUBLIC_EXHIBIT=0) these three answer 401 to a visitor, and the retry loop
  // below would then poll the door every 5 s for as long as the login screen is
  // open.  `signedIn` is true on an unenforced backend, so local dev boots
  // exactly as it always did, and flipping it (the sign-in dialog) re-runs this.
  useEffect(() => {
    if (!authResolved) return;
    if (!signedIn) {
      invalidateLastMotorOperations();
      setLastMotorState({ email: null, status: 'idle', selection: null });
      clearLiveDutyContext();
      useMotorStore.setState({ geometry: null as never });
      return;
    }
    let alive = true;
    const email = user?.email?.trim().toLowerCase() ?? null;
    const perAccount = enforced && !!email && !isAdmin;
    const ordinary = perAccount;
    void (async () => {
      fetchSchemaFromApi();
      loadServerSweepConfig();
      if (perAccount) {
        // `/api/geometry` describes the shared owner workspace. Never let it
        // impersonate a signed-in account while its saved choice is resolving.
        useMotorStore.setState({ geometry: null as never });
        try {
          const restored = await restoreLastMotor(email!);
          if (!alive || user?.email?.trim().toLowerCase() !== email) return;
          if (restored.status === 'loaded' || ordinary) return;
        } catch {
          if (!alive || user?.email?.trim().toLowerCase() !== email) return;
          if (ordinary) return;
        }
      }
      if (!alive) return;
      await fetchGeometryFromApi();
    })();
    return () => { alive = false; };
  }, [authResolved, authPending, signedIn, enforced, isAdmin, user?.email,
      fetchGeometryFromApi, fetchSchemaFromApi, loadServerSweepConfig]);

  // Adopt the server's mesh block into the browser's mesh.* keys once per
  // boot — see lib/meshConfigSync for the full-ring sweep this prevents.
  // HERE, at the root: it used to sit in GeometryBuildTimer, which mounts
  // only with the Geometry tab, so a browser that opened on Thermal or
  // Electromagnetic never synced at all — the user's F5 kept "4 sectors"
  // from the Ø200 on a 12/14 machine (2026-09-09, "I click, but it's the same thing").
  // Behind the same gate as the probes above: /api/mesh/config is closed to an
  // anonymous caller, and the sync has nothing to adopt until there is a
  // session whose machine it belongs to (401 measured live, 2026-09-16).
  useEffect(() => {
    if (authPending || !signedIn) return;
    void syncMeshConfigFromServer();
  }, [authPending, signedIn]);

  // RECONNECT: the boot probe above runs once, and a backend that was merely
  // slow to wake (first request after a restart imports the whole solver
  // stack) left the app stuck in "Local Mode" until a manual reload — a
  // one-shot connectivity check is a silent-failure trap. While disconnected,
  // retry every 5 s; the effect re-arms whenever connectivity flips.
  // Edits typed during the outage are safe from this loop: fetchGeometryFromApi
  // itself carries the reconnect barrier (motorStore) that re-PUTs
  // pendingGeometryEdits BEFORE adopting the server's geometry — the first
  // successful tick syncs the queue instead of clobbering it.
  useEffect(() => {
    if (connectedToApi || !authResolved || authPending || !signedIn) return;
    const ordinary = enforced && !!user && !isAdmin;
    const t = setInterval(() => {
      if (!ordinary) fetchGeometryFromApi();
      fetchSchemaFromApi();
    }, 5000);
    return () => clearInterval(t);
  }, [connectedToApi, authResolved, authPending, signedIn, enforced, user?.email,
      isAdmin, fetchGeometryFromApi, fetchSchemaFromApi]);

  // THE HEADER CHIP WHILE SIGNED OUT.  `connectedToApi` is set by the geometry
  // and schema fetches, and those answer 401 to a visitor on a server that
  // publishes nothing (PUBLIC_EXHIBIT=0) — so the login screen called a healthy
  // backend "Local Mode" (live, 2026-09-16).  Ask /api/version instead: open to
  // anonymous callers by design, and it carries no machine.  Once signed in the
  // store's own answer takes over again.
  const [backendUp, setBackendUp] = useState(false);
  useEffect(() => {
    if (signedIn) return;
    let alive = true;
    const probe = () => { void backendReachable().then(ok => { if (alive) setBackendUp(ok); }); };
    probe();
    const t = setInterval(probe, 30000);
    return () => { alive = false; clearInterval(t); };
  }, [signedIn]);

  // The in-page flight recorder (lib/diag): heap once a minute, main-thread
  // stalls, WebGL context loss — read with `__diag()` after a freeze.
  useEffect(() => { installDiag(); }, []);

  // MCP Stage 3: an agent hands the engineer …/?tab=configure&design=d-… —
  // open the Configure tab; the panel shows the draft and applies it to the
  // tuner only when he clicks Open (the open machine is never replaced).
  // Re-run once signed in: the guard below bounces a signed-out visitor to Motors.
  useEffect(() => {
    if (!signedIn) return;
    try {
      const q = new URLSearchParams(window.location.search);
      if (resolveSchemasRoute(window.location.href).activeTab === 'schemas') return;
      if (q.get('tab') === 'configure' || q.get('design')) setActiveTab('compare');
    } catch { /* ignore */ }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn]);

  // There's always a working motor ("my copy"): a brand-new user with none gets
  // one created from the current state, so every later edit has somewhere to
  // auto-save.  A short delay lets the panels seed localStorage first.
  // "A brand-new USER" is the point: with nobody signed in there is no account
  // to own the motor, and the POST /api/presets went out 1.2 s into an
  // anonymous visit and came back 401 (live, 2026-09-16).  Same gate as the
  // probes; signing in re-arms the timer, so the first working motor is still
  // created the moment there is somebody to create it for.
  useEffect(() => {
    if (authPending || !signedIn) return;
    // The working-motor auto-save exists for the Geometry tab's edits; a
    // standard account has no such tab and nothing to auto-save, so it is not
    // given a write it would never use.
    if (!authResolved || standardUser) return;
    const t = setTimeout(() => { ensureActiveMotor(); }, 1200);
    return () => clearTimeout(t);
  }, [authPending, signedIn, authResolved, standardUser]);

  // The Simulation panel is kept mounted but hidden via display:none while
  // another tab is active.  recharts' ResponsiveContainer measures 0×0 inside
  // a display:none box, so on returning we fire a resize tick to force it to
  // re-measure from 0 → real width (otherwise charts can come back 0-height).
  useEffect(() => {
    if (activeTab !== 'simulation') return;
    const id = setTimeout(() => window.dispatchEvent(new Event('resize')), 60);
    return () => clearTimeout(id);
  }, [activeTab]);

  // Keep the active tab within what the role allows (after sign-out / role change).
  // Not before /api/me has answered: the role is unknown until then, and a
  // provisional "anonymous" must not throw an admin off the tab he reloaded on.
  useEffect(() => {
    if (!authResolved) return;
    if (activeTab === 'admin' && !isAdmin) { setActiveTab('motors'); return; }
    // Anonymous visitors get the catalog only — Configure + FEM require sign-in.
    if (!signedIn && activeTab !== 'motors') { setActiveTab('motors'); return; }
    // A standard account has Motors and Configure and nothing else.  Anything
    // else (a stale tab from before the role was known, a ?tab= link) lands on
    // Configure when something is selected there, else on Motors.
    // ...plus Production when it is a member of one, and Designer when the server opened it for the account.
    const tab = activeTab as string;   // the store's tab type predates these two tabs
    const extraOk = (tab === 'production' && producer) || (tab === 'designer' && designerAllowed) || (tab === 'development' && developmentAllowed);
    if (standardUser && !tabAllowed(activeTab, true) && !extraOk) {
      setActiveTab(landingTabForUser(hasOwnConfigureChoice(user?.email) || !!defaultMotor));
    }
  }, [activeTab, isAdmin, standardUser, signedIn, authResolved, defaultMotor, setActiveTab, producer, designerAllowed, developmentAllowed]);

  // A standard account's first screen: Configure when it already has something
  // selected (his own last choice or the default motor an admin set), else the
  // Motors list.  Once per sign-in; never moves him afterwards.
  const landedRef = useRef(false);
  useEffect(() => {
    if (!authResolved || !signedIn || !standardUser || landedRef.current) return;
    landedRef.current = true;
    // a motor card on the landing page ("Open in Configure") counts as a choice made there
    const fromLanding = consumeOpenInConfigure();
    setActiveTab(landingTabForUser(hasOwnConfigureChoice(user?.email) || !!defaultMotor || fromLanding));
  }, [authResolved, signedIn, standardUser, defaultMotor, setActiveTab]);

  // ── Tab registry — the bar AND the content are GENERATED from this list.
  // Module-backed tabs take their title + order from the /api/modules manifests
  // (web-as-module); the static values here are the fallback when manifests are
  // unavailable, so the shell never breaks. ──
  const panels = useModulePanels();
  const geometryEmContent = <>
    <Box sx={{ mb:1.5 }}><SaveToMotorButton /></Box>
    <ParameterVariationTable />
  </>;
  // own Suspense: the panel beside it draws while the three.js chunk is still on the way
  const geometryActiveViewer = <><Suspense fallback={<TabLoading />}><UnifiedActiveMotorScene /></Suspense><GeometryBuildTimer /></>;
  const tabDefs: Array<{
    id: string; label: string; order: number; panelId?: string;
    gate: 'always' | 'signedIn' | 'standard' | 'fullUI' | 'admin' | 'producer' | 'designer' | 'development'; showViewer: boolean;
    /** true: mounted from the start and display-toggled; 'visited': mounted on the first visit, then kept */
    keepMounted?: boolean | 'visited';
    render: () => React.ReactNode;
  }> = [
    { id: 'motors', label: 'Motors', order: 0, gate: TAB_GATES.motors, showViewer: false,
      render: () => (
        <Box sx={{ p: 3, overflowY: 'auto', height: '100%' }}>
          <MotorsCatalog />
        </Box>
      ) },
    { id: 'geometry', label: 'Geometry', order: 20, panelId: 'geometry', gate: TAB_GATES.geometry, showViewer: true,
      render: () => (
        <GeometryTab emContent={geometryEmContent} activeViewer={geometryActiveViewer}
          panelWidth={panelWidth} onDividerMouseDown={onDividerMouseDown} />
      ) },
    { id: 'materials', label: 'Materials', order: 30, gate: TAB_GATES.materials, showViewer: true,
      render: () => (
        <MaterialsCatalogPanel materialView={
          <Box sx={{ display: 'flex', height: '100%', ...narrowSplitOuter }}>
          {/* Library tree — full-height column on the left */}
          <Box sx={{ width: panelWidth, flexShrink: 0, overflowY: 'auto', borderRight: '1px solid', borderColor: 'divider', ...narrowSplitColumn }}>
            <MaterialsLibraryTree library={matLibrary} loading={matLoading} error={matError}
              selected={selectedMaterial} onSelect={setSelectedMaterial}
              canAdd={isAdmin} onAdd={handleAddGlobal} />
            <WireStockTable />
          </Box>
          <Box data-split-divider onMouseDown={onDividerMouseDown} sx={{ width: 5, flexShrink: 0, cursor: 'col-resize',
            bgcolor: 'divider', transition: 'background-color 0.15s', '&:hover': { bgcolor: 'primary.main' }, userSelect: 'none' }} />
          {/* Right area — HORIZONTAL split: geometry on top, material props below */}
          <Box data-split-workspace sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <Box sx={{ height: '50%', overflow: 'hidden', position: 'relative',
              borderBottom: '1px solid', borderColor: 'divider', bgcolor: 'var(--panel-2)' }}>
              <Suspense fallback={<TabLoading />}><UnifiedActiveMotorScene force3d /></Suspense>
            </Box>
            <Box sx={{ flex: 1, overflow: 'hidden', bgcolor: 'var(--panel-2)' }}>
              <MaterialDetailView library={matLibrary} selected={selectedMaterial}
                onChanged={matReload} onSelect={setSelectedMaterial} />
            </Box>
          </Box>
          </Box>
        } />
      ) },
    { id: 'mesh', label: 'Mesh', order: 40, panelId: 'mesh', gate: TAB_GATES.mesh, showViewer: true,
      render: () => <MeshPanel /> },
    { id: 'simulation', label: 'Electromagnetic', order: 50, panelId: 'simulation', gate: TAB_GATES.simulation, showViewer: true, keepMounted: true,
      render: () => <SimulationPanel active={activeTab === 'simulation'} /> },
    { id: 'static3d', label: '3D', order: 55, gate: TAB_GATES.static3d, showViewer: false,
      render: () => <Static3DPanel /> },
    // Rotor centrifugal stress / retaining-sleeve sizing.  Like the 3D tab it
    // draws its own picture (the deformed rotor cross-section), so it does not
    // take the AppBar's viewer cluster — those controls drive MotorScene.
    { id: 'mechanical', label: 'Mechanical', order: 57, gate: TAB_GATES.mechanical, showViewer: false,
      render: () => <MechanicalPanel /> },
    // The steady-state temperature map and the coupled EM↔thermal loop.  Split
    // out of the Simulation tab's field viewer on 2026-09-07: a conduction
    // solve has its own mesh, its own boundary conditions and its own minute of
    // CPU, and it drew its own picture inside an electromagnetic output menu.
    // Like Mechanical it does NOT take the AppBar's viewer cluster — those
    // controls drive MotorScene.
    { id: 'thermal', label: 'Thermal', order: 58, gate: TAB_GATES.thermal, showViewer: false,
      render: () => <ThermalPanel /> },
    // The INVERTER (2026-09-22): the device, the coil->bridge map, the losses
    // with the MOSFET coldplate and the waveform the motor will be fed with.
    // It draws its own schematic, so like Mechanical and Thermal it does not
    // take the AppBar's viewer cluster.  Admin-only for now: it writes the
    // duty's `controller` block on the shared store, exactly as the coupled
    // loop does.
    { id: 'controller', label: 'Controller', order: 59, gate: TAB_GATES.controller, showViewer: false,
      render: () => <ControllerPanel /> },
    // The optimizer/sweep burns the whole machine on the shared config —
    // server-side it is admin-only since the deploy hardening, so showing the
    // tab to pro users would only offer buttons that 403.
    // Schemas (owner 2026-10-14): the view-only graph of how the simulations are chained (modules, data flow,
    // coupling loops; the propeller S1 chain).  Engineer + admin, in the Simulations dropdown; lazy, the graph
    // libraries are only fetched when the tab is opened.
    { id: 'schemas', label: 'Schemas', order: 61, gate: TAB_GATES.schemas, showViewer: false,
      render: () => <SystemPanel /> },
    { id: 'sweep', label: 'Optimization', order: 60, panelId: 'optimization', gate: TAB_GATES.sweep, showViewer: false,
      render: () => <SweepConfigPanel /> },
    // Compare writes the SHARED saved-sims store and Cost studies run on the
    // shared config (kernel/study carries no per-user geometry yet) — both are
    // the owner's tools until they learn to work on the client-side copy.
    // Compare is the ENGINEER'S tab again (user 2026-08-25: the client
    // compares saved configurations inside Configure instead — one place,
    // no second menu).
    { id: 'comparePoints', label: 'Compare', order: 65, gate: TAB_GATES.comparePoints, showViewer: false,
      render: () => <ComparePanel /> },
    { id: 'cost', label: 'Cost', order: 70, panelId: 'cost', gate: TAB_GATES.cost, showViewer: false,
      render: () => <CostPanel /> },
    { id: 'compare', label: 'Configure', order: 75, gate: TAB_GATES.compare, showViewer: false,
      render: () => <CompareTab /> },
    // 3D model (owner 2026-10-13): look at and rotate the motor model; tools and STEP download come with the NDA (server flag can_download).
    { id: 'model3d', label: '3D model', order: 76, gate: TAB_GATES.model3d, showViewer: false,
      render: () => <Model3DPanel /> },
    // Production (stage 1): parts, BOM, process, documents, discussion, proposals and revisions of one motor; admin, engineer and OEM producer.
    // kept mounted once opened (owner 2026-10-18): coming back shows the same motor, sub-tab and scroll, no reload
    { id: 'production', label: 'Production', order: 77, gate: TAB_GATES.production, showViewer: false, keepMounted: 'visited',
      render: () => <ProductionTab /> },
    // Development projects and the customer specification wizard.
    { id: 'development', label: 'Development', order: 78, gate: TAB_GATES.development, showViewer: false, keepMounted: 'visited',
      render: () => <DevelopmentTab /> },
    // Designer (pilot 2026-10-18): the design assistant chat; shown when the server says this account may use it.
    { id: 'designer', label: 'Designer', order: 95, gate: TAB_GATES.designer, showViewer: false,
      render: () => <DesignerTab /> },
    { id: 'admin', label: 'Admin', order: 90, panelId: 'admin', gate: TAB_GATES.admin, showViewer: false,
      render: () => <AdminPanel /> },
  ];
  const tabs = tabDefs
    .map((t) => ({
      ...t,
      // English: the manifest title (web-as-module) wins, as before.  Other
      // locales: the translated tab name; the manifest/registry text is the
      // fallback for a tab this build has no key for.
      label: (i18n.resolvedLanguage ?? 'en') === 'en'
        ? ((t.panelId && panels[t.panelId]?.title) || t.label)
        : tr(`tabs.${t.id}`, { defaultValue: (t.panelId && panels[t.panelId]?.title) || t.label }),
      // Ternary, not `&&`: the falsy branch of `t.panelId && …` keeps the string
      // type in the union, so `order` became string|number and the numeric sort
      // below failed to type-check (TS2362/2363).
      order: (t.panelId ? panels[t.panelId]?.order : undefined) ?? t.order,
    }))
    .filter((t) => gateOpen(t.gate, { signedIn, fullUI, isAdmin, producer, designer: designerAllowed, development: developmentAllowed }))
    .sort((a, b) => a.order - b.order);
  // A tab this account no longer has (a remembered "3D model" tab of an engineer: the model lives in Geometry > Full for the simulation
  // UI, owner 2026-10-15) lands on the first tab it has - never on an empty body.
  useEffect(() => {
    if (!authResolved || !signedIn || tabs.length === 0) return;
    if (!tabs.some((x) => x.id === activeTab)) setActiveTab((fullUI ? 'geometry' : 'motors') as never);
  }, [authResolved, signedIn, fullUI, activeTab, tabs.length]); // eslint-disable-line react-hooks/exhaustive-deps
  // The bar: Electromagnetic / Thermal / Mechanical collapse into ONE "Simulations"
  // dropdown (owner 2026-10-13).  Every panel keeps its own tab id, gate and mount
  // rule above; only the bar is drawn differently (lib/simulationsMenu).
  const entries = barEntries(tabs);
  // The plain "Simulations" tab opens the member the user looked at last (remembered across reloads, lib/viewMemory).
  const [lastSim, setLastSim] = useRememberedView<string>('simulations.lastTab', 'simulation', oneOf(SIMULATION_TAB_IDS));
  useEffect(() => { if (isSimulationTab(activeTab) && activeTab !== lastSim) setLastSim(activeTab); }, [activeTab, lastSim, setLastSim]);
  const simMembers = (entries.find((e) => e.kind === 'simulations') as { members: { id: string; label: string }[] } | undefined)?.members ?? [];

  return (
    <ThemeProvider theme={appTheme}>
      <CssBaseline />
      <UpdateBanner />
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>

        {/* ── AppBar ── */}
        <AppBar
          position="static"
          elevation={0}
          sx={{ backgroundColor: 'background.paper', borderBottom: '1px solid', borderColor: 'divider', flexShrink: 0 }}
        >
          <Toolbar variant="dense" sx={{ gap: 1, flexWrap: 'wrap', py: 0.5, minHeight: 'auto' }}>
            <BrandLogo size={28} />
            <Typography variant="h6" sx={{ mr: 1, letterSpacing: 0.3, whiteSpace: 'nowrap', fontSize: { xs: '1.05rem', sm: '1.25rem' } }}>
              AeroStator{' '}
              <Box component="span" sx={{ fontWeight: 300, opacity: 0.7 }}>Core</Box>
            </Typography>
            {/* Phone width: the version badge and the connection chip leave the bar so brand, account and theme fit. */}
            <Box sx={{ display: { xs: 'none', sm: 'contents' } }}>
              <VersionBadge />
            </Box>

            {isLoading && <CircularProgress size={18} sx={{ mr: 1 }} />}
            <Box sx={{ display: { xs: 'none', sm: 'contents' } }}>
              {(connectedToApi || (!signedIn && backendUp)) ? (
                <Chip icon={<CloudSyncIcon />} label={tr('status.connected')} color="success" size="small" />
              ) : (
                <Tooltip title={tr('status.reconnectTip')}>
                  {/* the chip reconnects now instead of waiting for the next 5 s tick (2026-10-25: owner stuck in Local Mode after an outage) */}
                  <Chip icon={<CloudOffIcon />} label={tr('status.localMode')} color="warning" size="small"
                        onClick={() => { fetchGeometryFromApi(); fetchSchemaFromApi(); }} />
                </Tooltip>
              )}
            </Box>

            <Box sx={{ flexGrow: 1 }} />

            <AuthButton />

            <Tooltip title={themeMode === 'light' ? tr('theme.dark') : tr('theme.light')}>
              <IconButton size="small"
                onClick={() => setThemeMode(m => (m === 'light' ? 'dark' : 'light'))}>
                {themeMode === 'light'
                  ? <DarkModeIcon fontSize="small" />
                  : <LightModeIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
            <Tooltip title={tr('actions.resetDefaults')}>
              <IconButton size="small" onClick={resetToDefaults} sx={{ display: { xs: 'none', sm: 'inline-flex' } }}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Toolbar>
        </AppBar>

        {/* ── THE FRONT DOOR ──────────────────────────────────────────────
            A visitor who is not signed in on a backend that ENFORCES auth used
            to get the header, an empty tab bar and one grey line ("Sign in to
            see the motor catalog") — an empty shell, which is what the user
            asked to replace on 2026-09-16.  The landing takes the whole body
            instead, and the tab bar, the family strip and every panel behind
            them stay unmounted: each of them calls an `/api/*` route that
            answers 401 to an anonymous caller, so not mounting them is also
            what keeps the first impression free of failed requests.

            `signedIn` is `!enforced || !!user`, so an UNENFORCED backend — the
            local dev server — never sees this branch and boots exactly as
            before.  The header (brand, version, the Sign in button, the theme
            toggle) is above this and stays on both sides. */}
        {authPending ? null : !signedIn ? <Landing /> : (
        <>
        {/* ── Full-width Navigation Tabs ── */}
        <Box sx={{ bgcolor: 'background.paper', borderBottom: '1px solid', borderColor: 'divider', flexShrink: 0 }}>
          <Tabs
            value={barValue(activeTab)}
            onChange={(_, v) => {
              if (v !== SIMULATIONS_BAR_ID) { setActiveTab(v); return; }
              const to = simulationsTarget(lastSim, simMembers);
              if (to) setActiveTab(to as never);
            }}
            /* Thirteen tabs do not fit a phone: below md they scroll sideways instead of squeezing to unreadable widths. */
            variant={narrow ? 'scrollable' : 'fullWidth'}
            scrollButtons={narrow ? 'auto' : false}
            allowScrollButtonsMobile
            sx={{ minHeight: 40 }}
          >
            {entries.map((e) => e.kind === 'simulations' ? (
              <Tab key={e.id} value={e.id}
                /* a plain tab like the others; the members are sub-tabs in the Simulations header row below the bar */
                label={tr('tabs.simulations', { defaultValue: 'Simulations' })}
                sx={{ minHeight: 40, fontSize: '0.8rem', textTransform: 'none',
                  px: 0.75, minWidth: 0, letterSpacing: 0 }} />
            ) : (() => { const t = tabs.find((x) => x.id === e.id)!; return (
              <Tab key={t.id} value={t.id}
                /* The badge rides on the RENDERED label, never on the registry
                   literal above: tests/test_support_chat_limits.py regexes the
                   id/label pairs out of this file and holds every one of them
                   to the assistant's system prompt, so the literals must stay
                   plain strings.  (Nor may this comment spell such a pair out —
                   the regex would capture it as a fourteenth tab.) */
                label={t.id === 'admin' && newRequests > 0 ? (
                  <Badge badgeContent={newRequests} color="error" invisible={newRequests === 0}
                    sx={{ '& .MuiBadge-badge': { top: -2, right: -10, height: 15, minWidth: 15, fontSize: 9.5 } }}>
                    {t.label}
                  </Badge>
                ) : (t.id === 'production' ? (unreadMessages?.totalProductions ?? 0) : t.id === 'development' ? (unreadMessages?.totalProjects ?? 0) : 0) > 0 ? (
                  <Tooltip title={tr('unreadMessagesTip', { defaultValue: '{count} unread messages', count: t.id === 'production' ? unreadMessages!.totalProductions : unreadMessages!.totalProjects })}>
                    <Badge badgeContent={unreadLabel(t.id === 'production' ? unreadMessages!.totalProductions : unreadMessages!.totalProjects)} color="error"
                      sx={{ '& .MuiBadge-badge': { top: -2, right: -12, height: 15, minWidth: 15, fontSize: 9.5 } }}>
                      {t.label}
                    </Badge>
                  </Tooltip>
                ) : t.label}
                /* Sentence case and tight padding: "ELECTROMAGNETIC" in caps
                   did not fit a full-width tab and was clipped to "ECTROMAGNE"
                   (user 2026-09-08). */
                sx={{ minHeight: 40, fontSize: '0.8rem', textTransform: 'none',
                  px: 0.75, minWidth: 0, letterSpacing: 0,
                  ...(t.id === 'motors' ? { fontWeight: 700 } : {}),
                  ...(t.id === 'admin' ? { fontWeight: 700, color: '#fbbf24' } : {}) }} />
            ); })())}
          </Tabs>
        </Box>

        {/* ── What is loaded: die / configuration / duty + save-back ── */}
        <ActiveFamilyStrip />
        {/* The one question before another machine replaces this one (lib/loadGuard) */}
        <LoadGuardDialog />
        {/* A motor loading in the background (its tab is already open) / why it failed */}
        <FamilyLoadBanner />
        {/* Heavy FEM tabs: one line pointing a user / engineer at their own server (docs/BYO_COMPUTE.md) */}
        {computeHintApplies(activeTab, enforced, isAdmin, role) && <ComputeNodesHint />}

        {/* ── Simulations header row (owner 2026-10-30): the dropdown's members as sub-tabs in the main menu's style,
            on top of every panel of the group, so Electromagnetic / Thermal / Mechanical / Schemas switch in one click ── */}
        {simMembers.length > 0 && isSimulationTab(activeTab) && (
          <Box data-testid="simulations-header" sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', px: 3, py: 0.25,
            bgcolor: 'background.paper', borderBottom: '1px solid', borderColor: 'divider', flexShrink: 0 }}>
            <Typography variant="subtitle2">{tr('tabs.simulations', { defaultValue: 'Simulations' })}</Typography>
            <SubTabs value={activeTab} items={simMembers} onChange={(id) => setActiveTab(id as never)}
              ariaLabel={tr('tabs.simulations', { defaultValue: 'Simulations' })} testId="simulations-tabs" />
          </Box>
        )}

        {/* ── Main Content ── */}
        <Box sx={{ flex: 1, overflow: 'hidden' }}>

          {/* Tabs are GENERATED from the registry (manifest-driven). Each tab's
              content is rendered when active; keep-mounted tabs (Simulation) stay
              mounted and are just display-toggled so the computed dashboard +
              field animation survive navigating away and back.  Each body has its
              own Suspense: a lazy tab still loading never blanks the others. */}
          {tabs.map((t) => (
            t.keepMounted && (t.keepMounted === true || visitedTabs.has(t.id)) ? (
              <Box key={t.id} sx={{ height: '100%', display: activeTab === t.id ? 'block' : 'none' }}>
                <ErrorBoundary label={t.label}><Suspense fallback={<TabLoading />}>{t.render()}</Suspense></ErrorBoundary>
              </Box>
            ) : (activeTab === t.id ? <ErrorBoundary key={t.id} label={t.label}><Suspense fallback={<TabLoading />}>{t.render()}</Suspense></ErrorBoundary> : null)
          ))}
        </Box>
        </>
        )}
      </Box>
      {/* Floating help/feedback — available to every user, on every tab */}
      <RefusedNotice />
      <SupportWidget />
      {/* Consent bar + GA4 (lib/analytics.ts): loads only after mount, idle and Accept; never for China */}
      <AnalyticsConsent />
      {/* Working-time heartbeat: signed-in accounts only, tab id only (lib/activityBeat.ts) */}
      {user && <ActivityBeat />}
      {/* Publishes the per-user material override to the fetch interceptor (Stage 2b) */}
      <MaterialOverrideSync />
    </ThemeProvider>
  );
}

export default App;
