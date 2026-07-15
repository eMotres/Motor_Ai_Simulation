import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { syncActiveMotor } from '../components/common/motorSettings';
import { setGeoGetter } from '../lib/apiAuth';
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

// Restore the eval parameters a descent run used into the Simulation tab's sources,
// so re-running the Simulation on an applied design REPRODUCES it (the optimizer eval
// is byte-identical to Simulation, so identical params => identical numbers). Mesh
// params are read fresh from localStorage at request time; the persisted-state Sim
// fields (steps, coil temp, …) are nudged live via the 'descent-eval-params' event.
function restoreDescentEvalParams(st: any): void {
  const ep = st?.eval_params;
  if (!ep || typeof ep !== 'object') return;
  const put = (k: string, v: unknown) => { if (v != null) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* quota */ } } };
  put('sim.stepsPP', ep.steps_per_period);  put('mesh.nSectors', ep.n_sectors);
  put('mesh.gapLayers', ep.gap_layers);     put('sim.coilTemp', ep.coil_temp_c);
  put('mesh.poleCopy', ep.pole_copy);       put('sim.torqueFilter', ep.torque_filter);
  put('sim.fieldLosses', ep.rotor_eddy);    put('sim.endWinding', ep.end_winding_factor);
  put('mesh.meshSize', ep.mesh_size_mm);    put('mesh.minSize', ep.min_size_mm);
  try { window.dispatchEvent(new CustomEvent('descent-eval-params', { detail: ep })); } catch { /* SSR */ }
}

// Material PROPERTIES (Br, μr, σ, …) are not edited in the UI — material
// selection is library-based, assigned per part on the Materials tab via
// /api/materials (see useMotorAssignments).  The old editable materialConfig
// (and its MaterialsPanel) were dead code and have been removed.

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
  setVariations: (variations: SweepConfig['variations']) => void;
  updateOperatingPoint: (index: 0 | 1, point: Partial<OperatingPoint>) => void;
  updateRippleThreshold: (threshold: number) => void;
  updateSweepConstraints: (patch: Partial<SweepConfig>) => void;
  initVariationsFromSchema: () => void;

  // Gradient / coordinate descent (fixed current+rpm, vary whitelisted vars)
  descentRunning: boolean;
  descentState: any | null;           // raw /descent/progress payload
  descentError: string | null;
  baselineLine: any | null;           // standalone current-only line (drawn before a run)
  baselineBusy: boolean;
  baselineError: string | null;
  // Snapshot of the optimization INPUTS at the last launch — used to detect what
  // changed since (materials, rpm, ripple, winding, …) and suggest re-optimizing.
  lastOptSnapshot: Record<string, any> | null;
  setLastOptSnapshot: (s: Record<string, any> | null) => void;
  runDescent: (opts: { rippleMax: number; maxIters: number; wEff: number;
                       wTd: number; steps: number;
                       algorithm: string; nSectors: number;
                       targetTorque?: number; vPeakLimit?: number;
                       optimizeGamma?: boolean; autoExpand?: boolean;
                       maxRounds?: number; surrogateSeed?: boolean;
                       objective?: string; currentBumpPct?: number;
                       ripplePenaltyLambda?: number;
                       thdPenaltyLambda?: number; thdMaxPct?: number }) => Promise<void>;
  cancelDescent: () => Promise<void>;
  applyDescentBest: () => Promise<void>;
  applyDescentPoint: (pt: any) => Promise<void>;   // apply a USER-PICKED scatter point
  loadLastDescent: () => Promise<void>;   // re-hydrate the last run's charts from the backend
  computeBaselineLine: (opts: { currentBumpPct: number; steps: number; nSectors: number }) => Promise<void>;

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

      // Replace the whole variations map at once — used to swap the per-algorithm
      // variable sets (each sub-tab keeps its own selection in localStorage).
      setVariations: (variations) =>
        set((state) => ({ sweepConfig: { ...state.sweepConfig, variations } })),

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
        // Preserve ALL non-schema variables (load angle γ, phase current, …) — they
        // aren't in parameterSchema but MUST survive re-init.  This effect runs on
        // every mount, and a tab switch remounts the panel, so keeping only γ here
        // (the old behaviour) made a phase-current (or any non-schema) variable
        // vanish when you left the Optimization tab and came back.
        for (const k of Object.keys(existing)) {
          if (!(k in variations)) variations[k] = existing[k];
        }
        set((state) => ({
          sweepConfig: { ...state.sweepConfig, variations },
        }));
      },

      // (Legacy Pareto FEM-scan + front-refine + saved-scan-runs were removed —
      //  the torque-driven descent below is now the sole optimizer.)

      // ── Gradient / coordinate descent ───────────────────────────────────────
      descentRunning: false,
      descentState: null,
      descentError: null,
      baselineLine: null,
      baselineBusy: false,
      baselineError: null,
      lastOptSnapshot: null,
      setLastOptSnapshot: (s) => set({ lastOptSnapshot: s }),
      runDescent: async ({ rippleMax, maxIters, wEff, wTd, steps, algorithm, nSectors, targetTorque, vPeakLimit, optimizeGamma, autoExpand, maxRounds, surrogateSeed, objective, currentBumpPct, ripplePenaltyLambda, thdPenaltyLambda, thdMaxPct }) => {
        const { sweepConfig } = get();
        // Fixed operating point = Sweep "Point 1" (γ/current from Simulation).
        const op0 = sweepConfig.operatingPoints[0] || ({} as any);
        // Variables = every active (non-fixed) entry; each card sets its explicit
        // [min, max] + step — the single shared variable interface used by every
        // algorithm (Optimize searches [min,max]; Sweep grids it; DOE screens it).
        const variables = Object.entries(sweepConfig.variations)
          .filter(([, v]) => v.mode !== 'fixed')
          .map(([name, v]) => ({ name, min: Number(v.min), max: Number(v.max),
                                 mode: v.mode, step: Number(v.step) }));
        // Mesh settings — all from the Mesh tab (single source), so the optimizer
        // meshes EXACTLY like Simulation (incl. the Periodic pole/slot toggle).
        let mesh_size_mm = 4.0, min_size_mm = 0.3, pole_copy = false, torque_filter = true;
        try { mesh_size_mm = Number(JSON.parse(localStorage.getItem('mesh.meshSize') ?? '4')) || 4.0; } catch { /* default */ }
        try { min_size_mm  = Number(JSON.parse(localStorage.getItem('mesh.minSize')  ?? '0.3')) || 0.3; } catch { /* default */ }
        try { pole_copy    = JSON.parse(localStorage.getItem('mesh.poleCopy') ?? 'false') === true; } catch { /* default */ }
        try { torque_filter = JSON.parse(localStorage.getItem('sim.torqueFilter') ?? 'true') !== false; } catch { /* default */ }
        // Loss model — SINGLE SOURCE: Simulation, so Optimize's efficiency matches the
        // Simulation tab (same keys Sweep reads). Without these the eval drops the
        // field magnet/shaft eddy + end-winding copper → η reads several points high.
        let rotor_eddy = true, end_winding_factor = 0;
        try { rotor_eddy = JSON.parse(localStorage.getItem('sim.fieldLosses') ?? 'true') !== false; } catch { /* default true */ }
        try { end_winding_factor = Number(JSON.parse(localStorage.getItem('sim.endWinding') ?? '0')) || 0; } catch { /* default 0 */ }
        // Air-gap mesh layers + coil temperature — SINGLE SOURCE: Mesh/Simulation tabs,
        // so the optimizer meshes the air gap (dominant torque/ripple driver) and sets
        // copper resistance EXACTLY like Simulation, else a selected design won't
        // reproduce when re-run in the Simulation tab.
        let gap_layers = 2, coil_temp_c = 120, structured_gap = false;
        try { gap_layers  = Number(JSON.parse(localStorage.getItem('mesh.gapLayers') ?? '2')) || 2; } catch { /* default */ }
        try { coil_temp_c = Number(JSON.parse(localStorage.getItem('sim.coilTemp')  ?? '120')) || 120; } catch { /* default */ }
        // Belt (mapped) gap mesh — SINGLE SOURCE: the Mesh tab "Structured" toggle.
        // Honest ripple (quarter == full disk), same build as Simulation.
        try { structured_gap = JSON.parse(localStorage.getItem('mesh.structuredGap') ?? 'false') === true; } catch { /* default */ }
        let airgap_macro = false;
        try { airgap_macro = JSON.parse(localStorage.getItem('mesh.harmonicGap') ?? 'false') === true; } catch { /* default */ }
        let iron_template = true;
        try { iron_template = JSON.parse(localStorage.getItem('mesh.ironTemplate') ?? 'true') !== false; } catch { /* default */ }
        if (iron_template) structured_gap = true;  // template needs the belt

        set({ descentRunning: true, descentError: null, descentState: null });
        try {
          const res = await fetch(`${API_BASE_URL}/api/optimization/descent/start`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              variables,
              operating_point: { gamma_deg: op0.gamma_deg ?? 0, current_a: op0.current_a, rpm: op0.rpm },
              ripple_max_pct: rippleMax, w_eff: wEff, w_td: wTd,
              max_iters: maxIters, steps_per_period: steps,
              mesh_size_mm, min_size_mm, pole_copy, torque_filter,
              rotor_eddy, end_winding_factor, gap_layers, coil_temp_c, structured_gap, airgap_macro, iron_template,
              algorithm, n_sectors: nSectors,
              target_torque_nm: targetTorque ?? 0,
              v_peak_limit: vPeakLimit ?? 1e9,
              optimize_gamma: optimizeGamma ?? true,
              auto_expand: autoExpand ?? false,
              max_rounds: maxRounds ?? 5,
              surrogate_seed: surrogateSeed ?? false,
              objective: objective ?? 'baseline_line',
              current_bump_pct: currentBumpPct ?? 10,
              // Ripple gate enforcement in the COST (0 = off, ripple only trimmed on
              // the chart): cost += λ·max(0, ripple% − limit%)/100.
              ripple_penalty_lambda: ripplePenaltyLambda ?? 0,
              // Line-to-line voltage THD gate (FOC waveform quality), same scale.
              thd_penalty_lambda: thdPenaltyLambda ?? 0,
              thd_max_pct: thdMaxPct ?? 5,
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
      computeBaselineLine: async ({ currentBumpPct, steps, nSectors }) => {
        // Draw the current-only baseline line up-front (2 sims @ I, +bump% I), WITHOUT
        // a full optimization. Same eval params (single sources) as runDescent so the
        // line matches what the optimizer will evaluate against.
        const { sweepConfig } = get();
        const op0 = sweepConfig.operatingPoints[0] || ({} as any);
        let mesh_size_mm = 4.0, min_size_mm = 0.3, pole_copy = false, torque_filter = true;
        try { mesh_size_mm = Number(JSON.parse(localStorage.getItem('mesh.meshSize') ?? '4')) || 4.0; } catch { /* default */ }
        try { min_size_mm  = Number(JSON.parse(localStorage.getItem('mesh.minSize')  ?? '0.3')) || 0.3; } catch { /* default */ }
        try { pole_copy    = JSON.parse(localStorage.getItem('mesh.poleCopy') ?? 'false') === true; } catch { /* default */ }
        try { torque_filter = JSON.parse(localStorage.getItem('sim.torqueFilter') ?? 'true') !== false; } catch { /* default */ }
        let rotor_eddy = true, end_winding_factor = 0;
        try { rotor_eddy = JSON.parse(localStorage.getItem('sim.fieldLosses') ?? 'true') !== false; } catch { /* default */ }
        try { end_winding_factor = Number(JSON.parse(localStorage.getItem('sim.endWinding') ?? '0')) || 0; } catch { /* default */ }
        let gap_layers = 2, coil_temp_c = 120, structured_gap = false;
        try { gap_layers  = Number(JSON.parse(localStorage.getItem('mesh.gapLayers') ?? '2')) || 2; } catch { /* default */ }
        try { coil_temp_c = Number(JSON.parse(localStorage.getItem('sim.coilTemp')  ?? '120')) || 120; } catch { /* default */ }
        // Belt (mapped) gap mesh — SINGLE SOURCE: the Mesh tab "Structured" toggle.
        // Honest ripple (quarter == full disk), same build as Simulation.
        try { structured_gap = JSON.parse(localStorage.getItem('mesh.structuredGap') ?? 'false') === true; } catch { /* default */ }
        let airgap_macro = false;
        try { airgap_macro = JSON.parse(localStorage.getItem('mesh.harmonicGap') ?? 'false') === true; } catch { /* default */ }
        let iron_template = true;
        try { iron_template = JSON.parse(localStorage.getItem('mesh.ironTemplate') ?? 'true') !== false; } catch { /* default */ }
        if (iron_template) structured_gap = true;  // template needs the belt
        set({ baselineBusy: true, baselineError: null });
        try {
          const res = await fetch(`${API_BASE_URL}/api/optimization/descent/baseline`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              operating_point: { gamma_deg: op0.gamma_deg ?? 0, current_a: op0.current_a, rpm: op0.rpm },
              current_bump_pct: currentBumpPct, steps_per_period: steps, n_sectors: nSectors,
              mesh_size_mm, min_size_mm, pole_copy, torque_filter,
              rotor_eddy, end_winding_factor, gap_layers, coil_temp_c, structured_gap, airgap_macro, iron_template,
            }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
          const j = await res.json();
          set({ baselineLine: j.baseline_line ?? null });
        } catch (e: any) {
          set({ baselineError: String(e?.message ?? e) });
        } finally {
          set({ baselineBusy: false });
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
        restoreDescentEvalParams(st);   // pin the run's eval params into the Simulation tab
        // Persist the OPERATING POINT the design was found at (solved current +
        // MTPA γ).  Without this, simulating the saved geometry runs at the
        // Simulation panel's idle current → low torque → inflated ripple% (the
        // 55% the user hit).  max_current is the solver's I_phase_rms — the same
        // quantity the optimizer solved as current_a.
        const m = st?.best?.metrics || st?.result?.best;
        const I = (typeof m?.current_a === 'number') ? m.current_a : undefined;
        const g = (typeof st?.mtpa_gamma_deg === 'number') ? st.mtpa_gamma_deg
                : (typeof m?.gamma_deg === 'number' ? m.gamma_deg : undefined);
        if (I === undefined && g === undefined) return;
        if (get().connectedToApi) {
          try {
            await fetch(`${API_BASE_URL}/api/simulation/config`, {
              method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                ...(I !== undefined ? { max_current: I } : {}),
                ...(g !== undefined ? { phase_offset_deg: g } : {}),
              }),
            });
          } catch { /* non-fatal: live event below still updates the panel */ }
        }
        // SimulationPanel is ALWAYS mounted (hidden when inactive), so it won't
        // re-read on a tab switch — nudge it to adopt the operating point live.
        try {
          window.dispatchEvent(new CustomEvent('sim-operating-point', { detail: { current: I, gamma: g } }));
        } catch { /* SSR/no-window */ }
      },
      // Apply a USER-PICKED scatter point (click-to-select on the descent chart) —
      // same geometry + operating-point write as applyDescentBest, but for an
      // arbitrary evaluated design instead of the optimiser's auto-best.
      applyDescentPoint: async (pt: any) => {
        const overrides = pt?.overrides;
        if (!overrides || !Object.keys(overrides).length) return;
        if (get().connectedToApi) await get().updateGeometryViaApi(overrides);
        else get().updateGeometry(overrides);
        restoreDescentEvalParams(get().descentState);   // pin the run's eval params into the Simulation tab
        const I = (typeof pt?.current_a === 'number') ? pt.current_a : undefined;
        const g = (typeof pt?.gamma_deg === 'number') ? pt.gamma_deg : undefined;
        if (I !== undefined || g !== undefined) {
          if (get().connectedToApi) {
            try {
              await fetch(`${API_BASE_URL}/api/simulation/config`, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  ...(I !== undefined ? { max_current: I } : {}),
                  ...(g !== undefined ? { phase_offset_deg: g } : {}),
                }),
              });
            } catch { /* non-fatal */ }
          }
          try {
            window.dispatchEvent(new CustomEvent('sim-operating-point', { detail: { current: I, gamma: g } }));
          } catch { /* SSR/no-window */ }
        }
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
          if (srvVars && _sweepSelected(srvVars) > 0 && _sweepSelected(local.variations) === 0) {
            // Adopt the server's config ONLY when THIS browser has no selection yet
            // (fresh load).  If this browser already has variables, they win (handled
            // below) — so a reload never clobbers your selections with a server copy
            // that may be stale (e.g. a var added <600 ms before reload hadn't synced).
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
            // THIS browser has selections → they win on reload; push them to the
            // server so they sync out (covers server-empty AND server-stale).
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
        meshSettings: state.meshSettings,
        sweepConfig: state.sweepConfig,
        lastOptSnapshot: state.lastOptSnapshot,
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
export type CompKey = 'stator' | 'rotor' | 'magnets' | 'coils' | 'shaft' | 'in_band' | 'out_band' | 'wire_insulation' | 'slot_insulation';

// UI State
interface UIState {
  sidebarOpen: boolean;
  activeTab: 'motors' | 'geometry' | 'materials' | 'mesh' | 'simulation' | 'cost' | 'sweep' | 'compare' | 'admin';
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
  setActiveTab: (tab: 'motors' | 'geometry' | 'materials' | 'mesh' | 'simulation' | 'cost' | 'sweep' | 'compare' | 'admin') => void;
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

// P2 (multi-user): feed the live geometry to the fetch interceptor so every
// COMPUTE request carries THIS client's design (?geo=) — stateless per-user
// isolation, no shared global config. See docs/MULTI_USER_PLAN.md.
setGeoGetter(() => {
  try {
    const g = useMotorStore.getState().geometry as Record<string, unknown> | undefined;
    if (!g) return null;
    const num: Record<string, number> = {};
    for (const [k, v] of Object.entries(g)) {
      if (typeof v === 'number' && Number.isFinite(v)) num[k] = v;
    }
    return Object.keys(num).length ? JSON.stringify(num) : null;
  } catch { return null; }
});

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

      // Component tree defaults. The FEM sliding-band air domains (in_band/out_band) and the
      // thin insulation layers are analysis overlays, not physical parts — and the bands are
      // drawn as big translucent disks that cover the whole stator+coils. Default them OFF so
      // the motor renders cleanly; they stay individually toggleable in the component tree.
      componentVisibility: { stator: true, rotor: true, magnets: true, coils: true, shaft: true, in_band: false, out_band: false, wire_insulation: false, slot_insulation: false },
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
          componentVisibility: { stator: false, rotor: false, magnets: false, coils: false, shaft: false, in_band: false, out_band: false, wire_insulation: false, slot_insulation: false, [key]: true },
          coilVisibility: {},
          magnetVisibility: {},
        }),

      showAllComponents: () =>
        set({
          componentVisibility: { stator: true, rotor: true, magnets: true, coils: true, shaft: true, in_band: true, out_band: true, wire_insulation: true, slot_insulation: true },
          coilVisibility: {},
          magnetVisibility: {},
        }),
    }),
    {
      name: 'motor-ui-storage',
      version: 1,
      // v1: the FEM sliding-band air domains (in_band/out_band) used to default visible and were
      // drawn as big translucent disks covering the whole motor — it looked like a geometry glitch.
      // Force them off once for users who persisted the old default; they remain toggleable.
      migrate: (persisted: any, version: number) => {
        if (version < 1 && persisted?.componentVisibility) {
          persisted.componentVisibility.in_band = false;
          persisted.componentVisibility.out_band = false;
        }
        return persisted;
      },
    }
  )
);
