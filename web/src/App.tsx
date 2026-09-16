import React, { useEffect, useMemo, useRef, useState } from 'react';
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
  CircularProgress,
  ToggleButtonGroup,
  ToggleButton,
} from '@mui/material';
import {
  Visibility as VisibilityIcon,
  GridOn as GridOnIcon,
  ThreeDRotation as ThreeDRotationIcon,
  Refresh as RefreshIcon,
  CloudSync as CloudSyncIcon,
  CloudOff as CloudOffIcon,
  ViewInAr as ViewInArIcon,
  Build as BuildIcon,
  Square as SquareIcon,
  DeleteSweep as DeleteSweepIcon,
  LightMode as LightModeIcon,
  DarkMode as DarkModeIcon,
} from '@mui/icons-material';
import { buildAppTheme, loadThemeMode, saveThemeMode, type AppMode } from './theme';
import MotorScene from './components/viewer3d/MotorScene';
import ParameterVariationTable from './components/sweep/ParameterVariationTable';
import MotorsCatalog from './components/catalog/MotorsCatalog';
import ActiveFamilyStrip from './components/common/ActiveFamilyStrip';
import AuthButton from './components/auth/AuthButton';
import Landing from './components/landing/Landing';
import { VersionBadge } from './components/VersionBadge';
import { backendReachable } from './lib/version';
import { useAuth } from './contexts/AuthContext';
import AdminPanel from './components/admin/AdminPanel';
import SupportWidget from './components/support/SupportWidget';
import MaterialOverrideSync from './components/materials/MaterialOverrideSync';
import MaterialControls from './components/parameters/MaterialControls';
import SaveToMotorButton from './components/common/SaveToMotorButton';
import SweepConfigPanel from './components/sweep/SweepConfigPanel';
import MaterialsLibraryTree from './components/materials/MaterialsLibraryTree';
import MaterialDetailView from './components/materials/MaterialDetailView';
import ErrorBoundary from './components/ErrorBoundary';
import { useMaterialsLibrary } from './components/materials/useMaterialsLibrary';
import type { SelectedMaterial, MaterialCategory } from './components/materials/useMaterialsLibrary';
import { saveGlobal, blankMaterial, type Cat } from './lib/materialsActions';
import { useMotorStore, useUIStore } from './stores/motorStore';
import { installDiag } from './lib/diag';
import SimulationPanel from './components/simulation/SimulationPanel';
import Static3DPanel from './components/static3d/Static3DPanel';
import MechanicalPanel from './components/mechanical/MechanicalPanel';
import ThermalPanel from './components/thermal/ThermalPanel';
import { syncMeshConfigFromServer } from './lib/meshConfigSync';
import CompareTab from './components/compare/CompareTab';
import ComparePanel from './components/compare/ComparePanel';
import MeshPanel from './components/mesh/MeshPanel';
import CostPanel from './components/cost/CostPanel';
import { ensureActiveMotor } from './components/common/motorSettings';
import { useModulePanels } from './modules/moduleTabs';

// Theme is built from the shared eMotres/aerostator design tokens — see
// src/theme.ts.  Light is the default (matches the marketing site); dark
// stays available via the AppBar toggle.

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
  useEffect(() => { saveThemeMode(themeMode); }, [themeMode]);
  // Adopt the server's mesh block into the browser's mesh.* keys once per
  // boot — see lib/meshConfigSync for the full-ring sweep this prevents.
  // HERE, at the root: it used to sit in GeometryBuildTimer, which mounts
  // only with the Geometry tab, so a browser that opened on Thermal or
  // Electromagnetic never synced at all — the user's F5 kept "4 sectors"
  // from the Ø200 on a 12/14 machine (2026-09-09, "нажимаю, но то же самое").
  useEffect(() => { void syncMeshConfigFromServer(); }, []);
  const appTheme = useMemo(() => buildAppTheme(themeMode), [themeMode]);
  const { activeTab, setActiveTab, showGrid, showAxes, toggleGrid, toggleAxes } = useUIStore();
  const { user, isAdmin, tier, enforced, resolved: authResolved } = useAuth();
  // Access tiers (only enforced when the backend has AUTH_ENFORCE on; with it off,
  // dev shows everything):
  //   • Anonymous       → the Motors catalog ONLY (browse, can't work with a motor).
  //   • Signed in (free) → + the analytical Configurator.
  //   • Pro / team / admin → + the full engineering UI (geometry/mesh/FEM/optimize).
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
  const fullUI   = !enforced || isAdmin || tier === 'pro' || tier === 'team';
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
    viewMode,
    setViewMode,
    geometry,
    runPipeline,
    clearStlCache,
    loadServerSweepConfig,
  } = useMotorStore();

  // Point-cloud and hybrid render modes were retired from the toolbar (user
  // decision) — a stored selection of either would leave the viewer in a mode
  // with no button, so coerce it to solid once on load.
  useEffect(() => {
    if (viewMode === 'pointcloud' || viewMode === 'hybrid') setViewMode('solid');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Not before somebody is signed in: on a server that publishes nothing
  // (PUBLIC_EXHIBIT=0) these three answer 401 to a visitor, and the retry loop
  // below would then poll the door every 5 s for as long as the login screen is
  // open.  `signedIn` is true on an unenforced backend, so local dev boots
  // exactly as it always did, and flipping it (the sign-in dialog) re-runs this.
  useEffect(() => {
    if (authPending || !signedIn) return;
    fetchGeometryFromApi();
    fetchSchemaFromApi();
    loadServerSweepConfig();
  }, [authPending, signedIn, fetchGeometryFromApi, fetchSchemaFromApi, loadServerSweepConfig]);

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
    if (connectedToApi || authPending || !signedIn) return;
    const t = setInterval(() => {
      fetchGeometryFromApi();
      fetchSchemaFromApi();
    }, 5000);
    return () => clearInterval(t);
  }, [connectedToApi, authPending, signedIn, fetchGeometryFromApi, fetchSchemaFromApi]);

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

  // There's always a working motor ("my copy"): a brand-new user with none gets
  // one created from the current state, so every later edit has somewhere to
  // auto-save.  A short delay lets the panels seed localStorage first.
  useEffect(() => { const t = setTimeout(() => { ensureActiveMotor(); }, 1200); return () => clearTimeout(t); }, []);

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
  useEffect(() => {
    if (activeTab === 'admin' && !isAdmin) { setActiveTab('motors'); return; }
    // Anonymous visitors get the catalog only — Configure + FEM require sign-in.
    if (!signedIn && activeTab !== 'motors') { setActiveTab('motors'); return; }
    // The DEFAULT client set (user's spec 2026-08-24): Motors, Configure,
    // Compare, Materials.  The old two-tab whitelist here silently bounced
    // every Materials/Compare click back to Configure ("эти два меню не
    // работают", 2026-08-25) — the gate list on the tabs and this redirect
    // must name the same set.
    const clientTabs = ['motors', 'compare', 'materials'];
    if (!fullUI && !clientTabs.includes(activeTab)) setActiveTab('compare');
  }, [activeTab, isAdmin, fullUI, signedIn, setActiveTab]);

  // ── Tab registry — the bar AND the content are GENERATED from this list.
  // Module-backed tabs take their title + order from the /api/modules manifests
  // (web-as-module); the static values here are the fallback when manifests are
  // unavailable, so the shell never breaks. ──
  const panels = useModulePanels();
  const tabDefs: Array<{
    id: string; label: string; order: number; panelId?: string;
    gate: 'always' | 'signedIn' | 'fullUI' | 'admin'; showViewer: boolean; keepMounted?: boolean;
    render: () => React.ReactNode;
  }> = [
    { id: 'motors', label: 'Motors', order: 0, gate: 'always', showViewer: false,
      render: () => (
        <Box sx={{ p: 3, overflowY: 'auto', height: '100%' }}>
          <MotorsCatalog />
        </Box>
      ) },
    { id: 'geometry', label: 'Geometry', order: 20, panelId: 'geometry', gate: 'fullUI', showViewer: true,
      render: () => (
        <Box sx={{ display: 'flex', height: '100%' }}>
          <Box sx={{ width: panelWidth, flexShrink: 0, overflowY: 'auto', p: 1.5 }}>
            <Box sx={{ mb: 1.5 }}><SaveToMotorButton /></Box>
            <ParameterVariationTable />
          </Box>
          <Box onMouseDown={onDividerMouseDown} sx={{ width: 5, flexShrink: 0, cursor: 'col-resize',
            bgcolor: 'divider', transition: 'background-color 0.15s', '&:hover': { bgcolor: 'primary.main' }, userSelect: 'none' }} />
          <Box sx={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
            {/* Viewer controls live WITH the viewer they drive (moved out of
                the AppBar — user request): grid, axes, render mode, STL
                build, cache rebuild. */}
            <Box sx={{ position: 'absolute', top: 40, right: 8, zIndex: 12,
                       display: 'flex', alignItems: 'center', gap: 0.5,
                       bgcolor: 'background.paper', border: '1px solid',
                       borderColor: 'divider', borderRadius: 1, px: 0.5, py: 0.25 }}>
              <Tooltip title={showGrid ? 'Hide Grid' : 'Show Grid'}>
                <IconButton size="small" color={showGrid ? 'primary' : 'default'} onClick={toggleGrid}>
                  <GridOnIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={showAxes ? 'Hide Axes' : 'Show Axes'}>
                <IconButton size="small" color={showAxes ? 'primary' : 'default'} onClick={toggleAxes}>
                  <ThreeDRotationIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <ToggleButtonGroup
                value={viewMode}
                exclusive
                onChange={(_, m) => m && setViewMode(m)}
                size="small"
                sx={{ mx: 0.5 }}
              >
                <ToggleButton value="solid" sx={{ px: 1 }}>
                  <Tooltip title="Solid Mesh"><SquareIcon fontSize="small" /></Tooltip>
                </ToggleButton>
                <ToggleButton value="stl" sx={{ px: 1 }}>
                  <Tooltip title="STL (CadQuery)"><ViewInArIcon fontSize="small" /></Tooltip>
                </ToggleButton>
              </ToggleButtonGroup>
              <Tooltip title="Generate STL from CadQuery">
                <IconButton size="small" onClick={() => runPipeline(geometry)}>
                  <BuildIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title="Clear Cache & Rebuild">
                <IconButton size="small" onClick={async () => { await clearStlCache(); runPipeline(geometry); }}>
                  <DeleteSweepIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
            <MaterialControls /><MotorScene /><GeometryBuildTimer />
          </Box>
        </Box>
      ) },
    { id: 'materials', label: 'Materials', order: 30, gate: 'signedIn', showViewer: true,
      render: () => (
        <Box sx={{ display: 'flex', height: '100%' }}>
          {/* Library tree — full-height column on the left */}
          <Box sx={{ width: panelWidth, flexShrink: 0, overflowY: 'auto', borderRight: '1px solid', borderColor: 'divider' }}>
            <MaterialsLibraryTree library={matLibrary} loading={matLoading} error={matError}
              selected={selectedMaterial} onSelect={setSelectedMaterial}
              canAdd={isAdmin} onAdd={handleAddGlobal} />
          </Box>
          <Box onMouseDown={onDividerMouseDown} sx={{ width: 5, flexShrink: 0, cursor: 'col-resize',
            bgcolor: 'divider', transition: 'background-color 0.15s', '&:hover': { bgcolor: 'primary.main' }, userSelect: 'none' }} />
          {/* Right area — HORIZONTAL split: geometry on top, material props below */}
          <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <Box sx={{ height: '50%', overflow: 'hidden', position: 'relative',
              borderBottom: '1px solid', borderColor: 'divider', bgcolor: 'var(--panel-2)' }}>
              <MotorScene force3d />
            </Box>
            <Box sx={{ flex: 1, overflow: 'hidden', bgcolor: 'var(--panel-2)' }}>
              <MaterialDetailView library={matLibrary} selected={selectedMaterial}
                onChanged={matReload} onSelect={setSelectedMaterial} />
            </Box>
          </Box>
        </Box>
      ) },
    { id: 'mesh', label: 'Mesh', order: 40, panelId: 'mesh', gate: 'fullUI', showViewer: true,
      render: () => <MeshPanel /> },
    { id: 'simulation', label: 'Electromagnetic', order: 50, panelId: 'simulation', gate: 'fullUI', showViewer: true, keepMounted: true,
      render: () => <SimulationPanel active={activeTab === 'simulation'} /> },
    // The 3D end-effect model gets its own scene (its own camera fit, its own
    // cut planes), so it does NOT take the AppBar's viewer cluster — those
    // controls drive MotorScene, and wiring them to a second canvas would make
    // the grid/axes toggles lie about which scene they act on.
    // 3D static solves are the owner's passport tool (admin-only server-side).
    { id: 'static3d', label: '3D', order: 55, gate: 'admin', showViewer: false,
      render: () => <Static3DPanel /> },
    // Rotor centrifugal stress / retaining-sleeve sizing.  Like the 3D tab it
    // draws its own picture (the deformed rotor cross-section), so it does not
    // take the AppBar's viewer cluster — those controls drive MotorScene.
    { id: 'mechanical', label: 'Mechanical', order: 57, gate: 'admin', showViewer: false,
      render: () => <MechanicalPanel /> },
    // The steady-state temperature map and the coupled EM↔thermal loop.  Split
    // out of the Simulation tab's field viewer on 2026-09-07: a conduction
    // solve has its own mesh, its own boundary conditions and its own minute of
    // CPU, and it drew its own picture inside an electromagnetic output menu.
    // Like Mechanical it does NOT take the AppBar's viewer cluster — those
    // controls drive MotorScene.
    { id: 'thermal', label: 'Thermal', order: 58, gate: 'admin', showViewer: false,
      render: () => <ThermalPanel /> },
    // The optimizer/sweep burns the whole machine on the shared config —
    // server-side it is admin-only since the deploy hardening, so showing the
    // tab to pro users would only offer buttons that 403.
    { id: 'sweep', label: 'Optimization', order: 60, panelId: 'optimization', gate: 'admin', showViewer: false,
      render: () => <SweepConfigPanel /> },
    // Compare writes the SHARED saved-sims store and Cost studies run on the
    // shared config (kernel/study carries no per-user geometry yet) — both are
    // the owner's tools until they learn to work on the client-side copy.
    // Compare is the ENGINEER'S tab again (user 2026-08-25: the client
    // compares saved configurations inside Configure instead — one place,
    // no second menu).
    { id: 'comparePoints', label: 'Compare', order: 65, gate: 'fullUI', showViewer: false,
      render: () => <ComparePanel /> },
    { id: 'cost', label: 'Cost', order: 70, panelId: 'cost', gate: 'admin', showViewer: false,
      render: () => <CostPanel /> },
    { id: 'compare', label: 'Configure', order: 75, gate: 'signedIn', showViewer: false,
      render: () => <CompareTab /> },
    { id: 'admin', label: 'Admin', order: 90, panelId: 'admin', gate: 'admin', showViewer: false,
      render: () => <AdminPanel /> },
  ];
  const tabs = tabDefs
    .map((t) => ({
      ...t,
      label: (t.panelId && panels[t.panelId]?.title) || t.label,
      // Ternary, not `&&`: the falsy branch of `t.panelId && …` keeps the string
      // type in the union, so `order` became string|number and the numeric sort
      // below failed to type-check (TS2362/2363).
      order: (t.panelId ? panels[t.panelId]?.order : undefined) ?? t.order,
    }))
    .filter((t) => t.gate === 'always' || (t.gate === 'signedIn' && signedIn) || (t.gate === 'fullUI' && fullUI) || (t.gate === 'admin' && isAdmin))
    .sort((a, b) => a.order - b.order);
  const showViewer = !!tabs.find((t) => t.id === activeTab)?.showViewer;

  return (
    <ThemeProvider theme={appTheme}>
      <CssBaseline />
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>

        {/* ── AppBar ── */}
        <AppBar
          position="static"
          elevation={0}
          sx={{ backgroundColor: 'background.paper', borderBottom: '1px solid', borderColor: 'divider', flexShrink: 0 }}
        >
          <Toolbar variant="dense" sx={{ gap: 1 }}>
            <Typography variant="h6" sx={{ mr: 1, letterSpacing: 0.3 }}>
              AeroStator{' '}
              <Box component="span" sx={{ fontWeight: 300, opacity: 0.7 }}>Core</Box>
            </Typography>
            <VersionBadge />

            {isLoading && <CircularProgress size={18} sx={{ mr: 1 }} />}
            {(connectedToApi || (!signedIn && backendUp)) ? (
              <Chip icon={<CloudSyncIcon />} label="Connected" color="success" size="small" />
            ) : (
              <Chip icon={<CloudOffIcon />} label="Local Mode" color="warning" size="small" />
            )}

            <Box sx={{ flexGrow: 1 }} />

            <AuthButton />

            <Tooltip title={themeMode === 'light' ? 'Dark theme' : 'Light theme'}>
              <IconButton size="small"
                onClick={() => setThemeMode(m => (m === 'light' ? 'dark' : 'light'))}>
                {themeMode === 'light'
                  ? <DarkModeIcon fontSize="small" />
                  : <LightModeIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
            <Tooltip title="Reset to Defaults">
              <IconButton size="small" onClick={resetToDefaults}>
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
            value={activeTab}
            onChange={(_, v) => setActiveTab(v)}
            variant="fullWidth"
            sx={{ minHeight: 40 }}
          >
            {tabs.map((t) => (
              <Tab key={t.id} label={t.label} value={t.id}
                /* Sentence case and tight padding: "ELECTROMAGNETIC" in caps
                   did not fit a full-width tab and was clipped to "ECTROMAGNE"
                   (user 2026-09-08). */
                sx={{ minHeight: 40, fontSize: '0.8rem', textTransform: 'none',
                  px: 0.75, minWidth: 0, letterSpacing: 0,
                  ...(t.id === 'motors' ? { fontWeight: 700 } : {}),
                  ...(t.id === 'admin' ? { fontWeight: 700, color: '#fbbf24' } : {}) }} />
            ))}
          </Tabs>
        </Box>

        {/* ── What is loaded: die / configuration / duty + save-back ── */}
        <ActiveFamilyStrip />

        {/* ── Main Content ── */}
        <Box sx={{ flex: 1, overflow: 'hidden' }}>

          {/* Tabs are GENERATED from the registry (manifest-driven). Each tab's
              content is rendered when active; keep-mounted tabs (Simulation) stay
              mounted and are just display-toggled so the computed dashboard +
              field animation survive navigating away and back. */}
          {tabs.map((t) => (
            t.keepMounted ? (
              <Box key={t.id} sx={{ height: '100%', display: activeTab === t.id ? 'block' : 'none' }}>
                <ErrorBoundary label={t.label}>{t.render()}</ErrorBoundary>
              </Box>
            ) : (activeTab === t.id ? <ErrorBoundary key={t.id} label={t.label}>{t.render()}</ErrorBoundary> : null)
          ))}
        </Box>
        </>
        )}
      </Box>
      {/* Floating help/feedback — available to every user, on every tab */}
      <SupportWidget />
      {/* Publishes the per-user material override to the fetch interceptor (Stage 2b) */}
      <MaterialOverrideSync />
    </ThemeProvider>
  );
}

export default App;
