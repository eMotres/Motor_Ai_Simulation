import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { syncActiveMotor } from '../components/common/motorSettings';
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
  updateSweepConstraints: (patch: Partial<SweepConfig>) => void;
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

  // Gradient / coordinate descent (fixed current+rpm, vary whitelisted vars)
  descentRunning: boolean;
  descentState: any | null;           // raw /descent/progress payload
  descentError: string | null;
  runDescent: (opts: { rippleMax: number; maxIters: number; wEff: number;
                       wTd: number; steps: number;
                       algorithm: string; nSectors: number;
                       targetTorque?: number; vPeakLimit?: number;
                       optimizeGamma?: boolean }) => Promise<void>;
  cancelDescent: () => Promise<void>;
  applyDescentBest: () => Promise<void>;
  loadLastDescent: () => Promise<void>;   // re-hydrate the last run's charts from the backend

  // Saved scan results (persisted to disk)
  savedRuns: SavedRunMeta[];
  refreshSaved: () => Promise<void>;
  saveCurrentResult: (name: string) => Promise<void>;
  loadSaved: (id: string) => Promise<void>;
  deleteSaved: (id: string) => Promise<void>;
  /** Hydrate sweepConfig from the backend so the selected variables follow the
   *  user across browsers; seeds the server if it has none yet but this browser does. */
  loadServerSweepConfig: () => Promise<void>;
}

// Sweep-config ↔ backend sync state (see loadServerSweepConfig + the subscription
// after the store): _sweepHydrating suppresses echoing a server-driven hydrate
// back to the server; _sweepSaveTimer debounces saves while the user edits.
let _sweepHydrating = false;
let _sweepSaveTimer: ReturnType<typeof setTimeout> | undefined;
// A config counts as "real" only if ≥1 variable is actually selected
// (mode !== 'fixed').  A fresh browser carries all schema params as 'fixed', so
// guarding on this prevents an empty/just-loaded browser from seeding or saving
// an all-'fixed' config that would clobber another browser's real selections.
const _sweepSelected = (vars?: Record<string, { mode?: string }>) =>
  vars ? Object.values(vars).filter((v) => v && v.mode && v.mode !== 'fixed').length : 0;

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
          { current_a: 80, rpm: 3950, gamma_deg: 0 },
          { current_a: 88, rpm: 3950, gamma_deg: 0 },
        ],
        rippleThreshold: 0.05,
        ratedTorqueNm: 30.5,
        vBusV: 140,
        modulation: 'svpwm',
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
          syncActiveMotor();   // auto-save the geometry edit into "my" motor
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

      updateSweepConstraints: (patch) =>
        set((state) => ({
          sweepConfig: { ...state.sweepConfig, ...patch },
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
        // Preserve non-schema variables (e.g. the load angle γ, selected from the
        // Simulation tab) — they aren't in parameterSchema but must survive re-init.
        if (existing['gamma_deg']) variations['gamma_deg'] = existing['gamma_deg'];
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
        // Design variables = every variation marked sweep/optimize.  This
        // includes geometry params (selected via the Geometry chart icon) AND
        // the load angle gamma_deg (selected via the Simulation tab checkbox);
        // the backend splits gamma_deg out of the geometry override and threads
        // it to the FEM current vector, so the mesh is unchanged.
        const variables = Object.entries(sweepConfig.variations)
          .filter(([, v]) => v.mode !== 'fixed')
          .map(([name, v]) => ({ name, min: Number(v.min), max: Number(v.max),
                                 mode: v.mode, step: Number(v.step) }));

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
          // Honor the Mesh-tab resolution so tuning it (coarser = fewer
          // elements) directly speeds up the scan.
          let mesh_size_mm = 4.0, min_size_mm = 0.3;
          try { mesh_size_mm = Number(JSON.parse(localStorage.getItem('mesh.meshSize') ?? '4')) || 4.0; } catch { /* default */ }
          try { min_size_mm  = Number(JSON.parse(localStorage.getItem('mesh.minSize')  ?? '0.3')) || 0.3; } catch { /* default */ }
          const res = await fetch(`${API_BASE_URL}/api/optimization/scan`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              variables, operating_points, ripple_max_pct,
              steps_per_period: stepsPerPeriod, max_geometries: maxGeometries,
              mesh_size_mm, min_size_mm,
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
          steps_per_period: (optimizationResult as any).steps_per_period,
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

      // ── Gradient / coordinate descent ───────────────────────────────────────
      descentRunning: false,
      descentState: null,
      descentError: null,
      runDescent: async ({ rippleMax, maxIters, wEff, wTd, steps, algorithm, nSectors, targetTorque, vPeakLimit, optimizeGamma }) => {
        const { sweepConfig, geometry } = get();
        // Variables = every active (non-fixed) entry.  OPTIMIZE vars search a
        // SYMMETRIC ± deviation around the CURRENT geometry value (range tracks the
        // live design, matching the UI's "value ± deviation"); SWEEP keeps its grid.
        const variables = Object.entries(sweepConfig.variations)
          .filter(([, v]) => v.mode !== 'fixed')
          .map(([name, v]) => {
            const cur = Number((geometry as Record<string, any>)[name]);
            const dlt = (Number(v.max) - Number(v.min)) / 2;
            const sym = v.mode === 'optimize' && Number.isFinite(cur) && dlt > 0;
            return { name, min: sym ? cur - dlt : Number(v.min),
                     max: sym ? cur + dlt : Number(v.max),
                     mode: v.mode, step: Number(v.step) };
          });
        // Fixed operating point = Sweep "Point 1" (current + rpm).
        const op0 = sweepConfig.operatingPoints[0];
        let mesh_size_mm = 4.0, min_size_mm = 0.3;
        try { mesh_size_mm = Number(JSON.parse(localStorage.getItem('mesh.meshSize') ?? '4')) || 4.0; } catch { /* default */ }
        try { min_size_mm  = Number(JSON.parse(localStorage.getItem('mesh.minSize')  ?? '0.3')) || 0.3; } catch { /* default */ }

        set({ descentRunning: true, descentError: null, descentState: null });
        try {
          const res = await fetch(`${API_BASE_URL}/api/optimization/descent/start`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              variables,
              operating_point: { gamma_deg: op0.gamma_deg ?? 0, current_a: op0.current_a, rpm: op0.rpm },
              ripple_max_pct: rippleMax, w_eff: wEff, w_td: wTd,
              max_iters: maxIters, steps_per_period: steps,
              mesh_size_mm, min_size_mm,
              algorithm, n_sectors: nSectors,
              target_torque_nm: targetTorque ?? 0,
              v_peak_limit: vPeakLimit ?? 1e9,
              optimize_gamma: optimizeGamma ?? true,
            }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
          // eslint-disable-next-line no-constant-condition
          while (true) {
            await new Promise(r => setTimeout(r, 2000));
            const pr = await fetch(`${API_BASE_URL}/api/optimization/descent/progress`);
            const st = await pr.json();
            set({ descentState: st });
            if (!st.running) { if (st.error) set({ descentError: st.error }); break; }
          }
          set({ descentRunning: false });
        } catch (e: any) {
          set({ descentError: String(e?.message ?? e), descentRunning: false });
        }
      },
      cancelDescent: async () => {
        try { await fetch(`${API_BASE_URL}/api/optimization/descent/cancel`, { method: 'POST' }); }
        catch { /* ignore */ }
      },
      applyDescentBest: async () => {
        const st: any = get().descentState;
        const overrides = st?.best?.x || st?.result?.best?.overrides;
        if (!overrides || !Object.keys(overrides).length) return;
        if (get().connectedToApi) await get().updateGeometryViaApi(overrides);
        else get().updateGeometry(overrides);
      },
      loadLastDescent: async () => {
        // The backend keeps the last descent in memory — re-hydrate it so the
        // charts survive a page reload (without re-running the optimization).
        try {
          const r = await fetch(`${API_BASE_URL}/api/optimization/descent/progress`);
          if (!r.ok) return;
          const st = await r.json();
          if (st && (st.running || ((st.history?.length ?? 0) > 0) || ((st.points?.length ?? 0) > 0))) {
            set({ descentState: st, descentRunning: !!st.running });
          }
        } catch { /* ignore */ }
      },
      loadServerSweepConfig: async () => {
        // Server-side sweep config so the selected variables follow the user
        // across browsers (not trapped in one browser's localStorage).  The
        // server wins on load; if the server has none yet but THIS browser has a
        // config, seed the server so it then shows everywhere.
        try {
          const r = await fetch(`${API_BASE_URL}/api/sweep/config`);
          if (!r.ok) return;
          const { config } = await r.json();
          const local = get().sweepConfig;
          const srvVars = config?.variations && typeof config.variations === 'object'
            ? config.variations : null;
          if (srvVars && _sweepSelected(srvVars) > 0) {
            // Server has a real config → it wins (this is what makes it
            // browser-independent).
            const ops = Array.isArray(config.operatingPoints) && config.operatingPoints.length === 2
              ? config.operatingPoints : local.operatingPoints;
            _sweepHydrating = true;
            set({ sweepConfig: {
              ...local,                       // keep any local-only fields
              variations: srvVars,
              operatingPoints: ops as [OperatingPoint, OperatingPoint],
              rippleThreshold: typeof config.rippleThreshold === 'number'
                ? config.rippleThreshold : local.rippleThreshold,
              // rated-duty constraints follow the server too (cross-browser)
              ratedTorqueNm: typeof config.ratedTorqueNm === 'number' ? config.ratedTorqueNm : local.ratedTorqueNm,
              vBusV: typeof config.vBusV === 'number' ? config.vBusV : local.vBusV,
              modulation: config.modulation ?? local.modulation,
            } });
            _sweepHydrating = false;
          } else if (_sweepSelected(local.variations) > 0) {
            // Server has nothing real yet, but THIS browser does → seed it so the
            // selection then shows in every browser.
            fetch(`${API_BASE_URL}/api/sweep/config`, {
              method: 'PUT', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(local),
            }).catch(() => {});
          }
        } catch { /* ignore */ }
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

// Mirror sweepConfig to the backend on every change (debounced) so the selected
// variables / operating points / ripple limit follow the user across browsers,
// not just the one that holds localStorage.  Hydrate is suppressed via the flag
// so a server-driven load isn't echoed straight back.
let _prevSweepConfig = useMotorStore.getState().sweepConfig;
useMotorStore.subscribe((state) => {
  if (state.sweepConfig === _prevSweepConfig) return;
  _prevSweepConfig = state.sweepConfig;
  if (_sweepHydrating) return;
  // Never persist an all-'fixed' config — that's a fresh/just-loaded browser
  // (e.g. right after the schema populates every param as 'fixed'), and saving
  // it would wipe another browser's real selections off the server.
  if (_sweepSelected(state.sweepConfig.variations) === 0) return;
  clearTimeout(_sweepSaveTimer);
  _sweepSaveTimer = setTimeout(() => {
    fetch(`${API_BASE_URL}/api/sweep/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(useMotorStore.getState().sweepConfig),
    }).catch(() => { /* offline / backend down — localStorage still holds it */ });
  }, 600);
});

// Component visibility keys
export type CompKey = 'stator' | 'rotor' | 'magnets' | 'coils' | 'shaft' | 'in_band' | 'out_band';

// UI State
interface UIState {
  sidebarOpen: boolean;
  activeTab: 'motors' | 'geometry' | 'materials' | 'mesh' | 'simulation' | 'sweep' | 'compare';
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
  setActiveTab: (tab: 'motors' | 'geometry' | 'materials' | 'mesh' | 'simulation' | 'sweep' | 'compare') => void;
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

// Dev affordance: expose the motor store so the Sweep/Optimize flow can be
// driven through the app's OWN actions (same ones the UI buttons call) rather
// than bypassing the frontend. Dev build only.
if (typeof window !== 'undefined' && import.meta.env?.DEV) {
  (window as unknown as { __motorStore?: typeof useMotorStore }).__motorStore = useMotorStore;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      activeTab: 'motors',
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
