// Which project motor the Configure tab shows as a POINTS-ONLY card (project motors that have stored
// duty results but no full passport yet).  Transient on purpose: not persisted, so a reload returns to
// the ordinary Configure.
import { create } from 'zustand';

export interface PointsTarget {
  projectId: string;
  die: string;
  config: string;
  duty: string | null;
  /** the caller also sees trial variants (the project page's "all versions" switch) */
  showAll: boolean;
}

interface PointsConfigureState {
  target: PointsTarget | null;
  open: (target: PointsTarget) => void;
  close: () => void;
}

export const usePointsConfigureStore = create<PointsConfigureState>()((set) => ({
  target: null,
  open: (target) => set({ target }),
  close: () => set({ target: null }),
}));
