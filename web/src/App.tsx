import React, { useEffect } from 'react';
import {
  ThemeProvider,
  createTheme,
  CssBaseline,
  AppBar,
  Toolbar,
  Typography,
  Drawer,
  Box,
  Tabs,
  Tab,
  IconButton,
  Tooltip,
  useMediaQuery,
  Chip,
  CircularProgress,
  ToggleButtonGroup,
  ToggleButton,
} from '@mui/material';
import {
  Menu as MenuIcon,
  Settings as SettingsIcon,
  Visibility as VisibilityIcon,
  AutoFixHigh as AutoFixHighIcon,
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
import GeometryForm from './components/parameters/GeometryForm';
import MaterialControls from './components/parameters/MaterialControls';
import { useMotorStore, useUIStore } from './stores/motorStore';

const darkTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#3b82f6',
    },
    secondary: {
      main: '#10b981',
    },
    background: {
      default: '#0f172a',
      paper: '#1e293b',
    },
  },
  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
  },
  components: {
    MuiTextField: {
      defaultProps: {
        variant: 'outlined',
        size: 'small',
      },
    },
    MuiSlider: {
      styleOverrides: {
        root: {
          color: '#3b82f6',
        },
      },
    },
  },
});

const drawerWidth = 320;

function App() {
  const isMobile = useMediaQuery('(max-width:768px)');
  const { sidebarOpen, activeTab, toggleSidebar, setActiveTab, showGrid, showAxes, autoRotate, toggleGrid, toggleAxes, toggleAutoRotate } = useUIStore();
  const { resetToDefaults, fetchGeometryFromApi, connectedToApi, isLoading, viewMode, setViewMode, geometry, runPipeline, stlMeshes, clearStlCache } = useMotorStore();
  
  // Fetch geometry from Python API on mount
  useEffect(() => {
    fetchGeometryFromApi();
  }, [fetchGeometryFromApi]);
  
  // Note: Removed the useEffect that forced STL mode - users can now freely switch between view modes
  
  const renderTabContent = () => {
    switch (activeTab) {
      case 'geometry':
        return <GeometryForm />;
      case 'materials':
        return <Typography>Materials configuration coming soon...</Typography>;
      case 'mesh':
        return <Typography>Mesh settings coming soon...</Typography>;
      case 'simulation':
        return <Typography>Simulation controls coming soon...</Typography>;
      default:
        return <GeometryForm />;
    }
  };
  
  return (
    <ThemeProvider theme={darkTheme}>
      <CssBaseline />
      <Box sx={{ display: 'flex', height: '100vh' }}>
        {/* App Bar */}
        <AppBar
          position="fixed"
          sx={{
            zIndex: (theme) => theme.zIndex.drawer + 1,
            backgroundColor: 'background.paper',
            borderBottom: '1px solid',
            borderColor: 'divider',
          }}
        >
          <Toolbar>
            <IconButton
              edge="start"
              color="inherit"
              aria-label="menu"
              onClick={toggleSidebar}
              sx={{ mr: 2 }}
            >
              <MenuIcon />
            </IconButton>
            
            <Typography variant="h6" component="div" sx={{ flexGrow: 1 }}>
              Motor AI Simulator
            </Typography>
            
            {/* API Connection Status */}
            {isLoading && <CircularProgress size={24} sx={{ mr: 2 }} />}
            {connectedToApi ? (
              <Chip
                icon={<CloudSyncIcon />}
                label="Connected to API"
                color="success"
                size="small"
                sx={{ mr: 2 }}
              />
            ) : (
              <Chip
                icon={<CloudOffIcon />}
                label="Local Mode"
                color="warning"
                size="small"
                sx={{ mr: 2 }}
              />
            )}
            
            {/* View Controls */}
            <Box sx={{ display: 'flex', gap: 0.5 }}>
              <Tooltip title={showGrid ? 'Hide Grid' : 'Show Grid'}>
                <IconButton 
                  color={showGrid ? 'primary' : 'default'} 
                  onClick={toggleGrid}
                >
                  <GridOnIcon />
                </IconButton>
              </Tooltip>
              
              <Tooltip title={showAxes ? 'Hide Axes' : 'Show Axes'}>
                <IconButton 
                  color={showAxes ? 'primary' : 'default'} 
                  onClick={toggleAxes}
                >
                  <ThreeDRotationIcon />
                </IconButton>
              </Tooltip>
              
              <Tooltip title={autoRotate ? 'Stop Rotation' : 'Auto Rotate'}>
                <IconButton 
                  color={autoRotate ? 'primary' : 'default'} 
                  onClick={toggleAutoRotate}
                >
                  <AutoFixHighIcon />
                </IconButton>
              </Tooltip>
              
              {/* View Mode Selector */}
              <ToggleButtonGroup
                value={viewMode}
                exclusive
                onChange={(_, newMode) => newMode && setViewMode(newMode)}
                size="small"
                sx={{ ml: 1, mr: 1 }}
              >
                <ToggleButton value="solid" sx={{ px: 1.5 }}>
                  <Tooltip title="Solid Mesh">
                    <SquareIcon fontSize="small" />
                  </Tooltip>
                </ToggleButton>
                <ToggleButton value="pointcloud" sx={{ px: 1.5 }}>
                  <Tooltip title="Point Cloud (Modulus)">
                    <BubbleChartIcon fontSize="small" />
                  </Tooltip>
                </ToggleButton>
                <ToggleButton value="stl" sx={{ px: 1.5 }}>
                  <Tooltip title="STL (CadQuery)">
                    <ViewInArIcon fontSize="small" />
                  </Tooltip>
                </ToggleButton>
                <ToggleButton value="hybrid" sx={{ px: 1.5 }}>
                  <Tooltip title="Hybrid (All)">
                    <LayersIcon fontSize="small" />
                  </Tooltip>
                </ToggleButton>
              </ToggleButtonGroup>
              
              <Tooltip title="Generate STL from CadQuery">
                <IconButton 
                  color={viewMode === 'stl' ? 'secondary' : 'default'} 
                  onClick={() => runPipeline(geometry)}
                >
                  <BuildIcon />
                </IconButton>
              </Tooltip>
              
              <Tooltip title="Clear Cache & Rebuild (force regeneration)">
                <IconButton 
                  color="default" 
                  onClick={async () => {
                    await clearStlCache();
                    runPipeline(geometry);
                  }}
                >
                  <DeleteSweepIcon />
                </IconButton>
              </Tooltip>
              
              <Tooltip title="Reset to Defaults">
                <IconButton color="default" onClick={resetToDefaults}>
                  <RefreshIcon />
                </IconButton>
              </Tooltip>
            </Box>
          </Toolbar>
        </AppBar>
        
        {/* Material Controls - Top Bar */}
        <MaterialControls />
        
        {/* Sidebar */}
        <Drawer
          variant={isMobile ? 'temporary' : 'persistent'}
          open={sidebarOpen}
          sx={{
            width: sidebarOpen ? drawerWidth : 0,
            flexShrink: 0,
            '& .MuiDrawer-paper': {
              width: drawerWidth,
              boxSizing: 'border-box',
              top: '64px',
              height: 'calc(100% - 64px)',
              backgroundColor: 'background.paper',
              borderRight: '1px solid',
              borderColor: 'divider',
            },
          }}
        >
          {/* Tabs */}
          <Tabs
            value={activeTab}
            onChange={(_, newValue) => setActiveTab(newValue)}
            variant="fullWidth"
            sx={{
              borderBottom: '1px solid',
              borderColor: 'divider',
            }}
          >
            <Tab label="Geometry" value="geometry" />
            <Tab label="Materials" value="materials" />
            <Tab label="Mesh" value="mesh" />
            <Tab label="Sim" value="simulation" />
          </Tabs>
          
          {/* Tab Content */}
          <Box sx={{ p: 2, overflow: 'auto', flex: 1 }}>
            {renderTabContent()}
          </Box>
        </Drawer>
        
        {/* Main Content - 3D Viewer */}
        <Box
          component="main"
          sx={{
            flexGrow: 1,
            marginLeft: sidebarOpen && !isMobile ? 0 : `-${drawerWidth}px`,
            transition: (theme) => theme.transitions.create('margin', {
              easing: theme.transitions.easing.sharp,
              duration: theme.transitions.duration.leavingScreen,
            }),
            height: '100vh',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <Toolbar />
          <Box sx={{ flex: 1, position: 'relative' }}>
            <MotorScene />
          </Box>
        </Box>
      </Box>
    </ThemeProvider>
  );
}

export default App;
