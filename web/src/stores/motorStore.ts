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
  SweepConfig,
  VariationConfig,
  OperatingPoint,
  ParameterVariation,
} from '../types/motor';
import {
  defaultGeometryParams,
  defaultMaterialAssignments,
  defaultMeshSettings,
} from '../types/motor';

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ─── Material config types ────────────────────────────────────────────────────

export interface SteelMaterial {
  preset: string;
  name: string;
  mu_r: number;
  sigma: number;
  B_sat: number;
  density: number;
  lamination_mm: number;
}

export interface MagnetMaterial {
  preset: string;
  name: string;
  Br: number;
  Hc: number;
  mu_rec: number;
  density: number;
  max_temp_c: number;
}

export interface WindingMaterial {
  preset: string;
  name: string;
  sigma: number;
  fill_factor: number;
  density: number;
  alpha: number;
}

export interface ShaftMaterial {
  preset: string;
  name: string;
  density: number;
  yield_strength_mpa: number;
  tensile_mpa: number;
}

export interface MaterialConfig {
  stator:   SteelMaterial;
  rotor:    SteelMaterial;
  magnets:  MagnetMaterial;
  windings: WindingMaterial;
  shaft:    ShaftMaterial;
}

export type ComponentMaterialKey = keyof MaterialConfig;

const defaultMaterialConfig: MaterialConfig = {
  stator: {
    preset: 'm27_silicon_steel', name: 'M27 Silicon Steel',
    mu_r: 4000, sigma: 2.5e6, B_sat: 1.70, density: 7700, lamination_mm: 0.457,
  },
  rotor: {
    preset: 'm27_silicon_steel', name: 'M27 Silicon Steel',
    mu_r: 4000, sigma: 2.5e6, B_sat: 1.70, density: 7700, lamination_mm: 0.457,
  },
  magnets: {
    preset: 'ndfeb_n42', name: 'NdFeB N42',
    Br: 1.28, Hc: 979, mu_rec: 1.05, density: 7500, max_temp_c: 150,
  },
  windings: {
    preset: 'copper', name: 'Copper',
    sigma: 5.96e7, fill_factor: 0.55, density: 8960, alpha: 0.00393,
  },
  shaft: {
    preset: 'carbon_steel_1045', name: 'Carbon Steel 1045',
    density: 7850, yield_strength_mpa: 530, tensile_mpa: 625,
  },
};

// View mode for 3D visualization
type ViewMode = 'solid' | 'pointcloud' | 'hybrid' | 'stl';

interface MotorState {
  // State
  geometry: MotorGeometryParams;
  materials: MaterialAssignments;
  materialConfig: MaterialConfig;
  meshSettings: MeshSettings;
  parameterSchema: ParameterSchema[];
  parameterGroups: ParameterGroup[];
  isLoading: boolean;
  isGeometryUpdating: boolean;
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
  setGeometryUpdating: (v: boolean) => void;
  updateGeometry: (params: Partial<MotorGeometryParams>) => void;
  updateComponentMaterial: (comp: ComponentMaterialKey, patch: Record<string, number | string>) => void;
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
  runPipelineStream: (onProgress: (stage: string, progress: number) => void) => Promise<void>;
  clearStlCache: () => Promise<void>;
  loadStlMesh: (component: string) => Promise<void>;
  validateGeometry: () => Promise<void>;

  // Sweep / Optimization
  sweepConfig: SweepConfig;
  updateVariation: (paramName: string, variation: Partial<ParameterVariation>) => void;
  updateOperatingPoint: (index: 0 | 1, point: Partial<OperatingPoint>) => void;
  updateRippleThreshold: (threshold: number) => void;
  initVariationsFromSchema: () => void;
}

export const useMotorStore = create<MotorState>()(
  persist(
    (set, get) => ({
      // Initial state
      geometry: { ...defaultGeometryParams },
      materials: defaultMaterialAssignments,
      materialConfig: defaultMaterialConfig,
      meshSettings: defaultMeshSettings,
      parameterSchema: [],
      parameterGroups: [],
      isLoading: false,
      isGeometryUpdating: false,
      error: null,
      connectedToApi: false,
      viewMode: 'solid',
      pointCloudData: null,
      
      // Pipeline state
      pipelineStatus: null,
      stlMeshes: {},
      validationData: null,
      geometryMismatch: false,

      // Sweep config initial state
      sweepConfig: {
        variations: {},
        operatingPoints: [
          { current_a: 10, rpm: 3000 },
          { current_a: 20, rpm: 3000 },
        ],
        rippleThreshold: 0.05,
      },

      setGeometryUpdating: (v) => set({ isGeometryUpdating: v }),

      updateComponentMaterial: (comp, patch) =>
        set((state) => ({
          materialConfig: {
            ...state.materialConfig,
            [comp]: { ...state.materialConfig[comp], ...patch },
          },
        })),

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
          const viewMode = get().viewMode;
          set({
            geometry: data as MotorGeometryParams,
            isLoading: false,
            connectedToApi: true,
            // Show indicator while the initial mesh fetch is running
            isGeometryUpdating: viewMode === 'solid' || viewMode === 'hybrid',
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
        set({ isLoading: true, isGeometryUpdating: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/api/geometry`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
          });
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data = await response.json();
          const viewMode = get().viewMode;
          set({
            geometry: data as MotorGeometryParams,
            isLoading: false,
            connectedToApi: true,
            // In STL mode the mesh won't auto-reload, so clear indicator immediately
            isGeometryUpdating: viewMode !== 'stl',
          });
          // STL mode: clear after a short acknowledgement delay
          if (viewMode === 'stl') {
            await new Promise(r => setTimeout(r, 600));
            set({ isGeometryUpdating: false });
          }
        } catch (error) {
          console.error('Failed to update geometry via API:', error);
          set({
            isLoading: false,
            isGeometryUpdating: false,
            error: error instanceof Error ? error.message : 'Failed to update geometry',
            connectedToApi: false,
          });
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
      
      runPipelineStream: (onProgress) =>
        new Promise((resolve, reject) => {
          set({ isLoading: true, error: null });
          const es = new EventSource(`${API_BASE_URL}/api/pipeline/stream`);

          es.onmessage = async (event) => {
            const data = JSON.parse(event.data);
            onProgress(data.stage, data.progress);

            if (data.stage === 'complete') {
              es.close();
              const components: string[] = data.components ?? [];
              const stlMeshes: Record<string, { vertices: number[]; faces: number[] }> = {};

              for (const comp of components) {
                try {
                  const r = await fetch(`${API_BASE_URL}/api/pipeline/stl/${comp}`);
                  if (r.ok) {
                    const m = await r.json();
                    stlMeshes[comp] = { vertices: m.vertices, faces: m.faces };
                  }
                } catch { /* skip failed component */ }
              }

              set({ stlMeshes, validationData: data.validation, isLoading: false, viewMode: 'stl' });
              resolve();
            }

            if (data.stage === 'error') {
              es.close();
              set({ isLoading: false, error: data.message ?? 'Pipeline failed' });
              reject(new Error(data.message));
            }
          };

          es.onerror = () => {
            es.close();
            set({ isLoading: false, error: 'Pipeline connection lost' });
            reject(new Error('SSE connection failed'));
          };
        }),

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

      // ── Sweep actions ────────────────────────────────────────────────────────
      updateVariation: (paramName, variation) =>
        set((state) => ({
          sweepConfig: {
            ...state.sweepConfig,
            variations: {
              ...state.sweepConfig.variations,
              [paramName]: {
                ...state.sweepConfig.variations[paramName],
                ...variation,
              },
            },
          },
        })),

      updateOperatingPoint: (index, point) =>
        set((state) => {
          const pts: [OperatingPoint, OperatingPoint] = [...state.sweepConfig.operatingPoints] as [OperatingPoint, OperatingPoint];
          pts[index] = { ...pts[index], ...point };
          return { sweepConfig: { ...state.sweepConfig, operatingPoints: pts } };
        }),

      updateRippleThreshold: (threshold) =>
        set((state) => ({
          sweepConfig: { ...state.sweepConfig, rippleThreshold: threshold },
        })),

      initVariationsFromSchema: () => {
        const { parameterSchema, geometry, sweepConfig } = get();
        const existing = sweepConfig.variations;
        const variations: VariationConfig = {};
        for (const param of parameterSchema) {
          if (param.type === 'string') continue;
          const current = Number(geometry[param.name] ?? 0);
          variations[param.name] = existing[param.name] ?? {
            mode: 'fixed',
            min: param.min ?? current * 0.5,
            max: param.max ?? current * 1.5,
            step: param.step ?? (current !== 0 ? Math.abs(current) * 0.1 : 1),
          };
        }
        set((state) => ({
          sweepConfig: { ...state.sweepConfig, variations },
        }));
      },
    }),
    {
      name: 'motor-config-storage',
      // Don't persist schema - always fetch fresh from API
      partialize: (state) => ({
        geometry: state.geometry,
        materials: state.materials,
        materialConfig: state.materialConfig,
        meshSettings: state.meshSettings,
        sweepConfig: state.sweepConfig,
      }),
    }
  )
);

// Component visibility keys
export type CompKey = 'stator' | 'rotor' | 'magnets' | 'coils' | 'shaft';

// UI State
interface UIState {
  sidebarOpen: boolean;
  activeTab: 'geometry' | 'materials' | 'mesh' | 'simulation' | 'sweep';
  showWireframe: boolean;
  showAxes: boolean;
  showGrid: boolean;
  autoRotate: boolean;

  // Material controls - single values for all parts
  metalness: number;
  roughness: number;
  envIntensity: number;

  // Camera mode
  cameraMode: 'perspective' | 'orthographic';

  // Component tree: group-level visibility
  componentVisibility: Record<CompKey, boolean>;

  // Individual item visibility (missing key = visible)
  coilVisibility: Record<number, boolean>;
  magnetVisibility: Record<number, boolean>;

  toggleSidebar: () => void;
  setActiveTab: (tab: 'geometry' | 'materials' | 'mesh' | 'simulation' | 'sweep') => void;
  toggleWireframe: () => void;
  toggleAxes: () => void;
  toggleGrid: () => void;
  toggleAutoRotate: () => void;
  updateMaterialSettings: (settings: Partial<{
    metalness: number;
    roughness: number;
    envIntensity: number;
  }>) => void;
  setCameraMode: (mode: 'perspective' | 'orthographic') => void;
  toggleComponentVisibility: (key: CompKey) => void;
  toggleCoilVisibility: (index: number) => void;
  toggleMagnetVisibility: (index: number) => void;
  isolateComponent: (key: CompKey) => void;
  showAllComponents: () => void;
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

      // Material controls
      metalness: 0.5,
      roughness: 0.5,
      envIntensity: 0.5,

      // Camera mode
      cameraMode: 'orthographic',

      // Component tree defaults
      componentVisibility: { stator: true, rotor: true, magnets: true, coils: true, shaft: true },
      coilVisibility: {},
      magnetVisibility: {},

      toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setActiveTab: (tab) => set({ activeTab: tab }),
      toggleWireframe: () => set((state) => ({ showWireframe: !state.showWireframe })),
      toggleAxes: () => set((state) => ({ showAxes: !state.showAxes })),
      toggleGrid: () => set((state) => ({ showGrid: !state.showGrid })),
      toggleAutoRotate: () => set((state) => ({ autoRotate: !state.autoRotate })),
      updateMaterialSettings: (settings) => set((state) => ({ ...state, ...settings })),
      setCameraMode: (mode) => set({ cameraMode: mode }),

      toggleComponentVisibility: (key) =>
        set((s) => ({ componentVisibility: { ...s.componentVisibility, [key]: !s.componentVisibility[key] } })),

      toggleCoilVisibility: (index) =>
        set((s) => ({ coilVisibility: { ...s.coilVisibility, [index]: !(s.coilVisibility[index] ?? true) } })),

      toggleMagnetVisibility: (index) =>
        set((s) => ({ magnetVisibility: { ...s.magnetVisibility, [index]: !(s.magnetVisibility[index] ?? true) } })),

      isolateComponent: (key) =>
        set({
          componentVisibility: { stator: false, rotor: false, magnets: false, coils: false, shaft: false, [key]: true },
          coilVisibility: {},
          magnetVisibility: {},
        }),

      showAllComponents: () =>
        set({
          componentVisibility: { stator: true, rotor: true, magnets: true, coils: true, shaft: true },
          coilVisibility: {},
          magnetVisibility: {},
        }),
    }),
    {
      name: 'motor-ui-storage',
    }
  )
);
