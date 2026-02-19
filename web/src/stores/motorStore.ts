import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  MotorGeometryParams,
  MaterialAssignments,
  MeshSettings,
  MotorConfig,
} from '../types/motor';
import {
  defaultGeometryParams,
  defaultMaterialAssignments,
  defaultMeshSettings,
  computeDerivedParams,
} from '../types/motor';

interface MotorState {
  // State
  geometry: MotorGeometryParams;
  materials: MaterialAssignments;
  meshSettings: MeshSettings;
  
  // Actions
  updateGeometry: (params: Partial<MotorGeometryParams>) => void;
  updateMaterials: (materials: Partial<MaterialAssignments>) => void;
  updateMeshSettings: (settings: Partial<MeshSettings>) => void;
  resetToDefaults: () => void;
  loadConfig: (config: MotorConfig) => void;
  getConfig: () => MotorConfig;
}

export const useMotorStore = create<MotorState>()(
  persist(
    (set, get) => ({
      // Initial state
      geometry: computeDerivedParams(defaultGeometryParams),
      materials: defaultMaterialAssignments,
      meshSettings: defaultMeshSettings,
      
      // Actions
      updateGeometry: (params) => set((state) => ({
        geometry: computeDerivedParams({ ...state.geometry, ...params }),
      })),
      
      updateMaterials: (materials) => set((state) => ({
        materials: { ...state.materials, ...materials },
      })),
      
      updateMeshSettings: (settings) => set((state) => ({
        meshSettings: { ...state.meshSettings, ...settings },
      })),
      
      resetToDefaults: () => set({
        geometry: computeDerivedParams(defaultGeometryParams),
        materials: defaultMaterialAssignments,
        meshSettings: defaultMeshSettings,
      }),
      
      loadConfig: (config) => set({
        geometry: computeDerivedParams(config.geometry),
        materials: config.materials,
        meshSettings: config.mesh,
      }),
      
      getConfig: () => ({
        geometry: get().geometry,
        materials: get().materials,
        mesh: get().meshSettings,
      }),
    }),
    {
      name: 'motor-config-storage',
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
