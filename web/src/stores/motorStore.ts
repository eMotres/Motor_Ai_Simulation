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
  OptimizationResult,
  OptDesignPoint,
  SavedRunMeta,
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
  updateGammaSweep: (patch: Partial<import('../types/motor').GammaSweep>) => void;
  initVariationsFromSchema: () => void;

  // Design optimization (FEM Pareto scan)
  optimizationResult: OptimizationResult | null;
  optimizationRunning: boolean;
  optimizationProgress: { done: number; total: number } | null;
  optimizationError: string | null;
  runOptimization: (stepsPerPeriod?: number, maxGeometries?: number) => Promise<void>;
  cancelOptimization: () => Promise<void>;

  // FEM refinement of selected (front) designs
  refineRunning: boolean;
  refineProgress: { done: number; total: number } | null;
  refineResults: OptDesignPoint[] | null;
  refineError: string | null;
  refineFront: (stepsPerPeriod?: number) => Promise<void>;

  // Saved scan results (persisted to disk)
  savedRuns: SavedRunMeta[];
  refreshSaved: () => Promise<void>;
  saveCurrentResult: (name: string) => Promise<void>;
  loadSaved: (id: string) => Promise<void>;
  deleteSaved: (id: string) => Promise<void>;
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

      // Sweep config initial state — two operating points ~10 % apart in
      // current (local load sensitivity), at the rated speed.
      sweepConfig: {
        variations: {},
        operatingPoints: [
          { current_a: 80, rpm: 3950 },
          { current_a: 88, rpm: 3950 },
        ],
        rippleThreshold: 0.05,
        gammaSweep: { enabled: false, min: 0, max: 30, step: 5 },
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
            // GET-only: geometry не перестраивается, таймер не нужен.
            // isGeometryUpdating устанавливается только через updateGeometryViaApi (PUT).
            isGeometryUpdating: false,
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

      updateGammaSweep: (patch) =>
        set((state) => ({
          sweepConfig: {
            ...state.sweepConfig,
            gammaSweep: { ...(state.sweepConfig.gammaSweep ?? { enabled: false, min: 0, max: 30, step: 5 }), ...patch },
          },
        })),

      initVariationsFromSchema: () => {
        const { parameterSchema, geometry, sweepConfig } = get();
        const existing = sweepConfig.variations;
        const variations: VariationConfig = {};
        for (const param of parameterSchema) {
          if (param.type === 'string') continue;
          const current = Number(geometry[param.name] ?? 0);
          // Default a freshly-seen variable's range to its CURRENT value in
          // both Min and Max (the user widens it after selecting).
          variations[param.name] = existing[param.name] ?? {
            mode: 'fixed',
            min: current,
            max: current,
            step: param.step ?? (current !== 0 ? Math.abs(current) * 0.1 : 1),
          };
        }
        set((state) => ({
          sweepConfig: { ...state.sweepConfig, variations },
        }));
      },

      // ── Design optimization ─────────────────────────────────────────────────
      optimizationResult: null,
      optimizationRunning: false,
      optimizationProgress: null,
      optimizationError: null,
      runOptimization: async (stepsPerPeriod = 6, maxGeometries = 24) => {
        const { sweepConfig } = get();
        // Design variables = GEOMETRY params marked sweep/optimize (sweep →
        // grid, optimize → spread). current/rpm come from the operating points.
        const variables = Object.entries(sweepConfig.variations)
          .filter(([, v]) => v.mode !== 'fixed')
          .map(([name, v]) => ({ name, min: Number(v.min), max: Number(v.max),
                                 mode: v.mode, step: Number(v.step) }));

        // Load-angle γ as a swept operating variable (its own section).
        const gs = sweepConfig.gammaSweep;
        if (gs?.enabled && gs.max > gs.min) {
          variables.push({ name: 'gamma_deg', min: Number(gs.min), max: Number(gs.max),
                           mode: 'sweep' as any, step: Number(gs.step) });
        }

        // Each geometry evaluated at BOTH operating currents → two FEM points
        // joined by a load-line segment.
        const [op0, op1] = sweepConfig.operatingPoints;
        const operating_points = [
          { gamma_deg: 0, current_a: op0.current_a, rpm: op0.rpm },
          { gamma_deg: 0, current_a: op1.current_a, rpm: op1.rpm },
        ];
        const ripple_max_pct = sweepConfig.rippleThreshold * 100;

        set({ optimizationRunning: true, optimizationError: null,
              optimizationProgress: { done: 0, total: 0 }, refineResults: null });
        try {
          // Every scan point is a REAL sliding-band transient (geometry + mesh
          // rebuilt per candidate) at stepsPerPeriod frames — background + poll.
          const res = await fetch(`${API_BASE_URL}/api/optimization/scan`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              variables, operating_points, ripple_max_pct,
              steps_per_period: stepsPerPeriod, max_geometries: maxGeometries,
            }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
          // eslint-disable-next-line no-constant-condition
          while (true) {
            await new Promise(r => setTimeout(r, 2000));
            const pr = await fetch(`${API_BASE_URL}/api/optimization/scan/progress`);
            const st = await pr.json();
            set({ optimizationProgress: { done: st.done, total: st.total } });
            if (!st.running) {
              if (st.error) throw new Error(st.error);
              if (st.result) set({ optimizationResult: st.result as OptimizationResult });
              break;
            }
          }
          set({ optimizationRunning: false });
        } catch (e: any) {
          set({ optimizationError: String(e?.message ?? e), optimizationRunning: false });
        }
      },

      // Stop the running scan — the backend returns whatever it computed so far,
      // and the polling loop in runOptimization picks up that partial result.
      cancelOptimization: async () => {
        try {
          await fetch(`${API_BASE_URL}/api/optimization/scan/cancel`, { method: 'POST' });
        } catch { /* ignore */ }
      },

      // ── Saved scan results (disk) ───────────────────────────────────────────
      savedRuns: [],
      refreshSaved: async () => {
        try {
          const r = await fetch(`${API_BASE_URL}/api/optimization/saved`);
          const d = await r.json();
          set({ savedRuns: d.saved || [] });
        } catch { /* ignore */ }
      },
      saveCurrentResult: async (name: string) => {
        const { optimizationResult } = get();
        if (!optimizationResult) return;
        const config = {
          steps_per_period: optimizationResult.steps_per_period,
          operating_points: optimizationResult.operating_points,
          variables: optimizationResult.variables,
          ripple_max_pct: optimizationResult.ripple_max_pct,
        };
        try {
          await fetch(`${API_BASE_URL}/api/optimization/saved`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, result: optimizationResult, config }),
          });
          await get().refreshSaved();
        } catch { /* ignore */ }
      },
      loadSaved: async (id: string) => {
        try {
          const r = await fetch(`${API_BASE_URL}/api/optimization/saved/${id}`);
          if (!r.ok) return;
          const d = await r.json();
          set({ optimizationResult: d.result as OptimizationResult, refineResults: null });
        } catch { /* ignore */ }
      },
      deleteSaved: async (id: string) => {
        try {
          await fetch(`${API_BASE_URL}/api/optimization/saved/${id}`, { method: 'DELETE' });
          await get().refreshSaved();
        } catch { /* ignore */ }
      },

      // ── FEM refinement of the Pareto-front designs ──────────────────────────
      refineRunning: false,
      refineProgress: null,
      refineResults: null,
      refineError: null,
      refineFront: async (stepsPerPeriod = 40) => {
        const { optimizationResult } = get();
        if (!optimizationResult) return;
        const OPK = new Set(['gamma_deg', 'current_a', 'rpm']);
        // The front designs (geometry overrides + their operating current).
        const designs = optimizationResult.pareto_indices.map(i => {
          const d = optimizationResult.points[i];
          const overrides: Record<string, number> = {};
          Object.entries(d.overrides || {}).forEach(([k, v]) => {
            if (!OPK.has(k)) overrides[k] = v as number;
          });
          return { overrides, current_a: d.current_a, rpm: d.rpm || 3950 };
        });
        if (!designs.length) return;
        set({ refineRunning: true, refineError: null, refineResults: null,
              refineProgress: { done: 0, total: designs.length } });
        try {
          const res = await fetch(`${API_BASE_URL}/api/optimization/refine`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ designs, steps_per_period: stepsPerPeriod, coil_temp_c: 120 }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
          // poll progress until done
          // eslint-disable-next-line no-constant-condition
          while (true) {
            await new Promise(r => setTimeout(r, 2000));
            const pr = await fetch(`${API_BASE_URL}/api/optimization/refine/progress`);
            const st = await pr.json();
            set({ refineProgress: { done: st.done, total: st.total },
                  refineResults: (st.results || []).filter((x: any) => !x.error) });
            if (!st.running) break;
          }
          set({ refineRunning: false });
        } catch (e: any) {
          set({ refineError: String(e?.message ?? e), refineRunning: false });
        }
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
export type CompKey = 'stator' | 'rotor' | 'magnets' | 'coils' | 'shaft' | 'in_band' | 'out_band';

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
  renderMode: 'extruded' | '2d';
  toggleRenderMode: () => void;
  /** @deprecated use renderMode === '2d' */
  view2d: boolean;
  toggleComponentVisibility: (key: CompKey) => void;
  toggleCoilVisibility: (index: number) => void;
  toggleMagnetVisibility: (index: number) => void;
  isolateComponent: (key: CompKey) => void;
  showAllComponents: () => void;

  // Part selection (Fusion360-style)
  selectedPart: CompKey | null;
  setSelectedPart: (key: CompKey | null) => void;
}

// ─── Build timing store (not persisted) ──────────────────────────────────────
interface BuildTimingState {
  mesh3d_s:      number | null;
  mesh_ext_s:    number | null;
  mesh2d_s:      number | null;
  setMesh3dTime:   (s: number) => void;
  setMeshExtTime:  (s: number) => void;
  setMesh2dTime:   (s: number) => void;
}

export const useBuildTimingStore = create<BuildTimingState>()((set) => ({
  mesh3d_s:   null,
  mesh_ext_s: null,
  mesh2d_s:   null,
  setMesh3dTime:  (s) => set({ mesh3d_s: s }),
  setMeshExtTime: (s) => set({ mesh_ext_s: s }),
  setMesh2dTime:  (s) => set({ mesh2d_s: s }),
}));

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
      renderMode: 'extruded' as 'extruded' | '2d',
      view2d: false,

      // Component tree defaults
      componentVisibility: { stator: true, rotor: true, magnets: true, coils: true, shaft: true, in_band: true, out_band: true },
      coilVisibility: {},
      magnetVisibility: {},

      selectedPart: null,
      setSelectedPart: (key) => set({ selectedPart: key }),

      toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setActiveTab: (tab) => set({ activeTab: tab }),
      toggleWireframe: () => set((state) => ({ showWireframe: !state.showWireframe })),
      toggleAxes: () => set((state) => ({ showAxes: !state.showAxes })),
      toggleGrid: () => set((state) => ({ showGrid: !state.showGrid })),
      toggleAutoRotate: () => set((state) => ({ autoRotate: !state.autoRotate })),
      updateMaterialSettings: (settings) => set((state) => ({ ...state, ...settings })),
      setCameraMode: (mode) => set({ cameraMode: mode }),
      toggleRenderMode: () => set((s) => {
        const next = s.renderMode === 'extruded' ? '2d' : 'extruded';
        return { renderMode: next, view2d: next === '2d' };
      }),
      toggleView2d: () => set((s) => {
        const next = s.renderMode === '2d' ? 'extruded' : '2d';
        return { renderMode: next, view2d: next === '2d' };
      }),

      toggleComponentVisibility: (key) =>
        set((s) => ({ componentVisibility: { ...s.componentVisibility, [key]: !s.componentVisibility[key] } })),

      toggleCoilVisibility: (index) =>
        set((s) => ({ coilVisibility: { ...s.coilVisibility, [index]: !(s.coilVisibility[index] ?? true) } })),

      toggleMagnetVisibility: (index) =>
        set((s) => ({ magnetVisibility: { ...s.magnetVisibility, [index]: !(s.magnetVisibility[index] ?? true) } })),

      isolateComponent: (key) =>
        set({
          componentVisibility: { stator: false, rotor: false, magnets: false, coils: false, shaft: false, in_band: false, out_band: false, [key]: true },
          coilVisibility: {},
          magnetVisibility: {},
        }),

      showAllComponents: () =>
        set({
          componentVisibility: { stator: true, rotor: true, magnets: true, coils: true, shaft: true, in_band: true, out_band: true },
          coilVisibility: {},
          magnetVisibility: {},
        }),
    }),
    {
      name: 'motor-ui-storage',
    }
  )
);
