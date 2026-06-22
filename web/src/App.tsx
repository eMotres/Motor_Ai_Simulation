import React, { useEffect, useRef, useState } from 'react';
import {
  ThemeProvider,
  createTheme,
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
  BubbleChart as BubbleChartIcon,
  Layers as LayersIcon,
  DeleteSweep as DeleteSweepIcon,
} from '@mui/icons-material';
import MotorScene from './components/viewer3d/MotorScene';
import ParameterVariationTable from './components/sweep/ParameterVariationTable';
import MotorsCatalog from './components/catalog/MotorsCatalog';
import AuthButton from './components/auth/AuthButton';
import { useAuth } from './contexts/AuthContext';
import AdminPanel from './components/admin/AdminPanel';
import SupportWidget from './components/support/SupportWidget';
import MaterialControls from './components/parameters/MaterialControls';
import SaveToMotorButton from './components/common/SaveToMotorButton';
import SweepConfigPanel from './components/sweep/SweepConfigPanel';
import MaterialsLibraryTree from './components/materials/MaterialsLibraryTree';
import MaterialDetailView from './components/materials/MaterialDetailView';
import { useMaterialsLibrary } from './components/materials/useMaterialsLibrary';
import type { SelectedMaterial } from './components/materials/useMaterialsLibrary';
import { useMotorStore, useUIStore } from './stores/motorStore';
import SimulationPanel from './components/simulation/SimulationPanel';
import CompareTab from './components/compare/CompareTab';
import MeshPanel from './components/mesh/MeshPanel';
import CostPanel from './components/cost/CostPanel';
import { ensureActiveMotor } from './components/common/motorSettings';
import { useModulePanels } from './modules/moduleTabs';

const darkTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#3b82f6' },
    secondary: { main: '#10b981' },
    background: { default: '#0f172a', paper: '#1e293b' },
  },
  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
  },
  components: {
    MuiTextField: { defaultProps: { variant: 'outlined', size: 'small' } },
    MuiSlider: { styleOverrides: { root: { color: '#3b82f6' } } },
  },
});

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
  bgcolor: 'rgba(0,0,0,0.75)',
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
  const { activeTab, setActiveTab, showGrid, showAxes, toggleGrid, toggleAxes } = useUIStore();
  const { isAdmin, tier, enforced } = useAuth();
  // Engineering UI (FEM/mesh/optimization) is for paid + admin accounts. Free /
  // anonymous users get the Motors storefront + the analytical Configurator only.
  // Gated ONLY when the backend enforces auth — so the open beta (enforcement off)
  // still shows everything to everyone, and no current visitor loses access.
  const fullUI = !enforced || isAdmin || tier === 'pro' || tier === 'team';
  const [panelWidth, setPanelWidth] = React.useState(300);
  const [selectedMaterial, setSelectedMaterial] = useState<SelectedMaterial | null>(null);
  const { library: matLibrary, loading: matLoading, error: matError } = useMaterialsLibrary();
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

  useEffect(() => {
    fetchGeometryFromApi();
    fetchSchemaFromApi();
    loadServerSweepConfig();
  }, [fetchGeometryFromApi, fetchSchemaFromApi, loadServerSweepConfig]);

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
    if (!fullUI && activeTab !== 'motors' && activeTab !== 'compare') setActiveTab('compare');
  }, [activeTab, isAdmin, fullUI, setActiveTab]);

  // ── Tab registry — the bar AND the content are GENERATED from this list.
  // Module-backed tabs take their title + order from the /api/modules manifests
  // (web-as-module); the static values here are the fallback when manifests are
  // unavailable, so the shell never breaks. ──
  const panels = useModulePanels();
  const tabDefs: Array<{
    id: string; label: string; order: number; panelId?: string;
    gate: 'always' | 'fullUI' | 'admin'; showViewer: boolean; keepMounted?: boolean;
    render: () => React.ReactNode;
  }> = [
    { id: 'motors', label: 'Motors', order: 0, gate: 'always', showViewer: false,
      render: () => <MotorsCatalog /> },
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
            <MaterialControls /><MotorScene /><GeometryBuildTimer />
          </Box>
        </Box>
      ) },
    { id: 'materials', label: 'Materials', order: 30, gate: 'fullUI', showViewer: true,
      render: () => (
        <Box sx={{ display: 'flex', height: '100%' }}>
          <Box sx={{ width: '50%', display: 'flex', borderRight: '1px solid', borderColor: 'divider', overflow: 'hidden' }}>
            <Box sx={{ width: panelWidth, flexShrink: 0, overflowY: 'auto', borderRight: '1px solid', borderColor: 'divider' }}>
              <MaterialsLibraryTree library={matLibrary} loading={matLoading} error={matError}
                selected={selectedMaterial} onSelect={setSelectedMaterial} />
            </Box>
            <Box onMouseDown={onDividerMouseDown} sx={{ width: 5, flexShrink: 0, cursor: 'col-resize',
              bgcolor: 'divider', transition: 'background-color 0.15s', '&:hover': { bgcolor: 'primary.main' }, userSelect: 'none' }} />
            <Box sx={{ flex: 1, overflow: 'hidden', bgcolor: '#0a1120' }}>
              <MaterialDetailView library={matLibrary} selected={selectedMaterial} />
            </Box>
          </Box>
          <Box sx={{ width: '50%', overflow: 'hidden', position: 'relative', bgcolor: '#060d17' }}>
            <MotorScene />
          </Box>
        </Box>
      ) },
    { id: 'mesh', label: 'Mesh', order: 40, panelId: 'mesh', gate: 'fullUI', showViewer: true,
      render: () => <MeshPanel /> },
    { id: 'simulation', label: 'Simulation', order: 50, panelId: 'simulation', gate: 'fullUI', showViewer: true, keepMounted: true,
      render: () => <SimulationPanel active={activeTab === 'simulation'} /> },
    { id: 'sweep', label: 'Optimization', order: 60, panelId: 'optimization', gate: 'fullUI', showViewer: false,
      render: () => <SweepConfigPanel /> },
    { id: 'cost', label: 'Cost', order: 70, panelId: 'cost', gate: 'fullUI', showViewer: false,
      render: () => <CostPanel /> },
    { id: 'compare', label: 'Configure', order: 75, gate: 'always', showViewer: false,
      render: () => <CompareTab /> },
    { id: 'admin', label: 'Admin', order: 90, panelId: 'admin', gate: 'admin', showViewer: false,
      render: () => <AdminPanel /> },
  ];
  const tabs = tabDefs
    .map((t) => ({
      ...t,
      label: (t.panelId && panels[t.panelId]?.title) || t.label,
      order: (t.panelId && panels[t.panelId]?.order) ?? t.order,
    }))
    .filter((t) => t.gate === 'always' || (t.gate === 'fullUI' && fullUI) || (t.gate === 'admin' && isAdmin))
    .sort((a, b) => a.order - b.order);
  const showViewer = !!tabs.find((t) => t.id === activeTab)?.showViewer;

  return (
    <ThemeProvider theme={darkTheme}>
      <CssBaseline />
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>

        {/* ── AppBar ── */}
        <AppBar
          position="static"
          elevation={0}
          sx={{ backgroundColor: 'background.paper', borderBottom: '1px solid', borderColor: 'divider', flexShrink: 0 }}
        >
          <Toolbar variant="dense" sx={{ gap: 1 }}>
            <Typography variant="h6" sx={{ mr: 1 }}>Motor AI Simulator</Typography>

            {isLoading && <CircularProgress size={18} sx={{ mr: 1 }} />}
            {connectedToApi ? (
              <Chip icon={<CloudSyncIcon />} label="Connected" color="success" size="small" />
            ) : (
              <Chip icon={<CloudOffIcon />} label="Local Mode" color="warning" size="small" />
            )}

            <Box sx={{ flexGrow: 1 }} />

            <AuthButton />

            {/* 3D view controls — only relevant when viewer is visible */}
            {showViewer && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
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
                  <ToggleButton value="pointcloud" sx={{ px: 1 }}>
                    <Tooltip title="Point Cloud"><BubbleChartIcon fontSize="small" /></Tooltip>
                  </ToggleButton>
                  <ToggleButton value="stl" sx={{ px: 1 }}>
                    <Tooltip title="STL (CadQuery)"><ViewInArIcon fontSize="small" /></Tooltip>
                  </ToggleButton>
                  <ToggleButton value="hybrid" sx={{ px: 1 }}>
                    <Tooltip title="Hybrid"><LayersIcon fontSize="small" /></Tooltip>
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
            )}

            <Tooltip title="Reset to Defaults">
              <IconButton size="small" onClick={resetToDefaults}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Toolbar>
        </AppBar>

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
                sx={{ minHeight: 40, fontSize: '0.8rem',
                  ...(t.id === 'motors' ? { fontWeight: 700 } : {}),
                  ...(t.id === 'admin' ? { fontWeight: 700, color: '#fbbf24' } : {}) }} />
            ))}
          </Tabs>
        </Box>

        {/* ── Main Content ── */}
        <Box sx={{ flex: 1, overflow: 'hidden' }}>

          {/* Tabs are GENERATED from the registry (manifest-driven). Each tab's
              content is rendered when active; keep-mounted tabs (Simulation) stay
              mounted and are just display-toggled so the computed dashboard +
              field animation survive navigating away and back. */}
          {tabs.map((t) => (
            t.keepMounted ? (
              <Box key={t.id} sx={{ height: '100%', display: activeTab === t.id ? 'block' : 'none' }}>
                {t.render()}
              </Box>
            ) : (activeTab === t.id ? <React.Fragment key={t.id}>{t.render()}</React.Fragment> : null)
          ))}
        </Box>
      </Box>
      {/* Floating help/feedback — available to every user, on every tab */}
      <SupportWidget />
    </ThemeProvider>
  );
}

export default App;
