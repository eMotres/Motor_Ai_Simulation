import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  MotorGeometryParams,
  MaterialAssignments,
  MeshSettings,
  MotorConfig,
  ParameterSchema,
  ParameterGroup,
  GeometrySchemaResponse,
} from '../types/motor';
import {
  defaultGeometryParams,
  defaultMaterialAssignments,
  defaultMeshSettings,
} from '../types/motor';

// API base URL (Python FastAPI server)
const API_BASE_URL = 'http://localhost:8013';

// View mode for 3D visualization
type ViewMode = 'solid' | 'pointcloud' | 'hybrid' | 'stl';

interface MotorState {
  // State
  geometry: MotorGeometryParams;
  materials: MaterialAssignments;
  meshSettings: MeshSettings;
  parameterSchema: ParameterSchema[];
  parameterGroups: ParameterGroup[];
  isLoading: boolean;
  error: string | null;
  connectedToApi: boolean;
  viewMode: ViewMode;
  pointCloudData: any | null;
  
  // Pipeline state
  pipelineStatus: {
    fusion360: boolean;
    modulus: boolean;
  } | null;
  stlMeshes: Record<string, { vertices: number[]; faces: number[] }>;
  validationData: any | null;
  geometryMismatch: boolean;
  
  // Actions
  updateGeometry: (params: Partial<MotorGeometryParams>) => void;
  updateMaterials: (materials: Partial<MaterialAssignments>) => void;
  updateMeshSettings: (settings: Partial<MeshSettings>) => void;
  setViewMode: (mode: ViewMode) => void;
  fetchPointCloudFromApi: (nPoints?: number) => Promise<void>;
  resetToDefaults: () => void;
  loadConfig: (config: MotorConfig) => void;
  getConfig: () => MotorConfig;
  
  // API Actions
  fetchGeometryFromApi: () => Promise<void>;
  fetchSchemaFromApi: () => Promise<void>;
  updateGeometryViaApi: (params: Partial<MotorGeometryParams>) => Promise<void>;
  resetGeometryViaApi: () => Promise<void>;
  fetchFullConfigFromApi: () => Promise<void>;
  
  // Pipeline Actions
  fetchPipelineStatus: () => Promise<void>;
  runPipeline: (params: MotorGeometryParams) => Promise<void>;
  clearStlCache: () => Promise<void>;
  loadStlMesh: (component: string) => Promise<void>;
  validateGeometry: () => Promise<void>;
}

export const useMotorStore = create<MotorState>()(
  persist(
    (set, get) => ({
      // Initial state
      geometry: { ...defaultGeometryParams },
      materials: defaultMaterialAssignments,
      meshSettings: defaultMeshSettings,
      parameterSchema: [],
      parameterGroups: [],
      isLoading: false,
      error: null,
      connectedToApi: false,
      viewMode: 'solid',
      pointCloudData: null,
      
      // Pipeline state
      pipelineStatus: null,
      stlMeshes: {},
      validationData: null,
      geometryMismatch: false,
      
      // Local Actions
      updateGeometry: (params) => set((state) => ({
        geometry: { ...state.geometry, ...params } as MotorGeometryParams,
      })),
      
      updateMaterials: (materials) => set((state) => ({
        materials: { ...state.materials, ...materials },
      })),
      
      updateMeshSettings: (settings) => set((state) => ({
        meshSettings: { ...state.meshSettings, ...settings },
      })),
      
      setViewMode: (mode) => set({ viewMode: mode }),
      
      fetchPointCloudFromApi: async (nPoints = 10000) => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/api/geometry/pointcloud?n_points=${nPoints}`);
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          set({ 
            pointCloudData: data,
            isLoading: false,
            connectedToApi: true,
          });
        } catch (error) {
          console.error('Failed to fetch point cloud from API:', error);
          set({ 
            isLoading: false, 
            error: error instanceof Error ? error.message : 'Failed to fetch point cloud',
            connectedToApi: false,
          });
        }
      },
      
      resetToDefaults: () => set({
        geometry: { ...defaultGeometryParams },
        materials: defaultMaterialAssignments,
        meshSettings: defaultMeshSettings,
      }),
      
      loadConfig: (config) => set({
        geometry: config.geometry,
        materials: config.materials,
        meshSettings: config.mesh,
      }),
      
      getConfig: () => ({
        geometry: get().geometry,
        materials: get().materials,
        mesh: get().meshSettings,
      }),
      
      // API Actions
      fetchGeometryFromApi: async () => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/api/geometry`);
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          set({ 
            geometry: data as MotorGeometryParams, 
            isLoading: false,
            connectedToApi: true,
          });
        } catch (error) {
          console.error('Failed to fetch geometry from API:', error);
          set({ 
            isLoading: false, 
            error: error instanceof Error ? error.message : 'Failed to fetch geometry',
            connectedToApi: false,
          });
        }
      },
      
      fetchSchemaFromApi: async () => {
        try {
          const response = await fetch(`${API_BASE_URL}/api/geometry/schema`);
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data: GeometrySchemaResponse = await response.json();
          set({ 
            parameterSchema: data.parameters,
            parameterGroups: data.groups,
            connectedToApi: true,
          });
        } catch (error) {
          console.error('Failed to fetch schema from API:', error);
          set({ 
            connectedToApi: false,
          });
        }
      },
      
      updateGeometryViaApi: async (params) => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/api/geometry`, {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify(params),
          });
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          set({ 
            geometry: data as MotorGeometryParams, 
            isLoading: false,
            connectedToApi: true,
          });
        } catch (error) {
          console.error('Failed to update geometry via API:', error);
          set({ 
            isLoading: false, 
            error: error instanceof Error ? error.message : 'Failed to update geometry',
            connectedToApi: false,
          });
          // Fallback to local update
          get().updateGeometry(params);
        }
      },
      
      resetGeometryViaApi: async () => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/api/geometry/reset`, {
            method: 'POST',
          });
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          set({ 
            geometry: data as MotorGeometryParams, 
            isLoading: false,
            connectedToApi: true,
          });
        } catch (error) {
          console.error('Failed to reset geometry via API:', error);
          set({ 
            isLoading: false, 
            error: error instanceof Error ? error.message : 'Failed to reset geometry',
            connectedToApi: false,
          });
          // Fallback to local reset
          get().resetToDefaults();
        }
      },
      
      fetchFullConfigFromApi: async () => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/api/config`);
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          set({ 
            geometry: data.geometry as MotorGeometryParams,
            materials: data.materials as MaterialAssignments,
            meshSettings: data.mesh as MeshSettings,
            isLoading: false,
            connectedToApi: true,
          });
        } catch (error) {
          console.error('Failed to fetch config from API:', error);
          set({ 
            isLoading: false, 
            error: error instanceof Error ? error.message : 'Failed to fetch config',
            connectedToApi: false,
          });
        }
      },
      
      // Pipeline Actions
      fetchPipelineStatus: async () => {
        try {
          const response = await fetch(`${API_BASE_URL}/api/pipeline/status`);
          if (!response.ok) return;
          const data = await response.json();
          set({ 
            pipelineStatus: {
              fusion360: data.fusion360_available,
              modulus: data.modulus_bridge_available,
            }
          });
        } catch (error) {
          console.error('Failed to fetch pipeline status:', error);
        }
      },
      
      runPipeline: async (params) => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/api/pipeline/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
          });
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          
          // Load all STL meshes
          const components = data.components || [];
          const stlMeshes: Record<string, { vertices: number[]; faces: number[] }> = {};
          
          for (const comp of components) {
            try {
              const meshResponse = await fetch(`${API_BASE_URL}/api/pipeline/stl/${comp}`);
              if (meshResponse.ok) {
                const meshData = await meshResponse.json();
                stlMeshes[comp] = {
                  vertices: meshData.vertices,
                  faces: meshData.faces,
                };
              }
            } catch (e) {
              console.warn(`Failed to load STL for ${comp}:`, e);
            }
          }
          
          set({ 
            stlMeshes,
            validationData: data.validation,
            isLoading: false,
            viewMode: 'stl',
          });
          
          // Run validation
          get().validateGeometry();
          
        } catch (error) {
          console.error('Failed to run pipeline:', error);
          set({ 
            isLoading: false, 
            error: error instanceof Error ? error.message : 'Failed to run pipeline',
          });
        }
      },
      
      clearStlCache: async () => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/api/pipeline/clear-cache`, {
            method: 'POST',
          });
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          console.log('Cache cleared:', data);
          set({ isLoading: false });
        } catch (error) {
          console.error('Failed to clear cache:', error);
          set({ 
            isLoading: false, 
            error: error instanceof Error ? error.message : 'Failed to clear cache',
          });
        }
      },
      
      loadStlMesh: async (component) => {
        try {
          const response = await fetch(`${API_BASE_URL}/api/pipeline/stl/${component}`);
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          set((state) => ({
            stlMeshes: {
              ...state.stlMeshes,
              [component]: {
                vertices: data.vertices,
                faces: data.faces,
              },
            },
          }));
        } catch (error) {
          console.error(`Failed to load STL mesh for ${component}:`, error);
        }
      },
      
      validateGeometry: async () => {
        try {
          const response = await fetch(`${API_BASE_URL}/api/pipeline/validate?n_points=50000`);
          if (!response.ok) return;
          const data = await response.json();
          
          // Check for geometry mismatch (AI geometry vs CAD)
          const validation = data.validation;
          let mismatch = false;
          
          // Simple heuristic: check if bounding boxes differ significantly
          if (validation && validation.bounding_box) {
            const bb = validation.bounding_box;
            const size = Math.max(
              bb.max[0] - bb.min[0],
              bb.max[1] - bb.min[1],
              bb.max[2] - bb.min[2]
            );
            // If volume is near zero, something is wrong
            if (validation.approximate_volume < size * size * size * 0.01) {
              mismatch = true;
            }
          }
          
          set({ 
            validationData: data,
            geometryMismatch: mismatch,
          });
        } catch (error) {
          console.error('Failed to validate geometry:', error);
        }
      },
    }),
    {
      name: 'motor-config-storage',
      // Don't persist schema - always fetch fresh from API
      partialize: (state) => ({
        geometry: state.geometry,
        materials: state.materials,
        meshSettings: state.meshSettings,
      }),
    }
  )
);

// UI State
interface UIState {
  sidebarOpen: boolean;
  activeTab: 'geometry' | 'materials' | 'mesh' | 'simulation';
  showWireframe: boolean;
  showAxes: boolean;
  showGrid: boolean;
  autoRotate: boolean;
  
  toggleSidebar: () => void;
  setActiveTab: (tab: 'geometry' | 'materials' | 'mesh' | 'simulation') => void;
  toggleWireframe: () => void;
  toggleAxes: () => void;
  toggleGrid: () => void;
  toggleAutoRotate: () => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      activeTab: 'geometry',
      showWireframe: false,
      showAxes: true,
      showGrid: true,
      autoRotate: false,
      
      toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setActiveTab: (tab) => set({ activeTab: tab }),
      toggleWireframe: () => set((state) => ({ showWireframe: !state.showWireframe })),
      toggleAxes: () => set((state) => ({ showAxes: !state.showAxes })),
      toggleGrid: () => set((state) => ({ showGrid: !state.showGrid })),
      toggleAutoRotate: () => set((state) => ({ autoRotate: !state.autoRotate })),
    }),
    {
      name: 'motor-ui-storage',
    }
  )
);
