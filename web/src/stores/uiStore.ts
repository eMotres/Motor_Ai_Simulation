// UI state (active tab, viewer toggles, component visibility), split out of motorStore.ts so the
// signed-out landing (cookie bar, support widget) can read `activeTab` without pulling the
// whole motor store (and everything it imports) into the first-load bundle.  motorStore.ts
// re-exports both names, so every existing import keeps working.
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// Component visibility keys
export type CompKey = 'stator' | 'rotor' | 'magnets' | 'coils' | 'shaft' | 'sleeve' | 'in_band' | 'out_band' | 'wire_insulation' | 'slot_insulation';

// UI State
interface UIState {
  sidebarOpen: boolean;
  activeTab: 'motors' | 'geometry' | 'materials' | 'mesh' | 'simulation' | 'static3d' | 'cost' | 'sweep' | 'compare' | 'model3d' | 'admin';
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
  setActiveTab: (tab: 'motors' | 'geometry' | 'materials' | 'mesh' | 'simulation' | 'static3d' | 'cost' | 'sweep' | 'compare' | 'admin') => void;
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
      componentVisibility: { stator: true, rotor: true, magnets: true, coils: true, shaft: true, sleeve: true, in_band: false, out_band: false, wire_insulation: false, slot_insulation: false },
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
          componentVisibility: { stator: false, rotor: false, magnets: false, coils: false, shaft: false, sleeve: false, in_band: false, out_band: false, wire_insulation: false, slot_insulation: false, [key]: true },
          coilVisibility: {},
          magnetVisibility: {},
        }),

      showAllComponents: () =>
        set({
          componentVisibility: { stator: true, rotor: true, magnets: true, coils: true, shaft: true, sleeve: true, in_band: true, out_band: true, wire_insulation: true, slot_insulation: true },
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
        // A key added AFTER a browser persisted this slice comes back
        // `undefined`, which reads as "hidden" — so a machine that grew a
        // retaining sleeve would draw everything but the sleeve, silently, for
        // every existing user.  Default it visible like every other real part.
        if (persisted?.componentVisibility
            && persisted.componentVisibility.sleeve === undefined) {
          persisted.componentVisibility.sleeve = true;
        }
        return persisted;
      },
    }
  )
);
