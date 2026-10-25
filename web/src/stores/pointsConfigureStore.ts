// Which project motor the Configure tab shows as a POINTS-ONLY card (project motors that have stored
// duty results but no full passport yet).  Remembered across a browser reload like every other view
// (lib/viewMemory.ts, scope `configure.points`): the card reopens on the same motor and mode; if the project,
// motor or access is gone the load fails and the card closes silently (PointsConfigure checks `restored`).
import { create } from 'zustand';
import { bool, nullableText, readView, shape, text, writeView } from '../lib/viewMemory.ts';

export interface PointsTarget {
  projectId: string;
  die: string;
  config: string;
  duty: string | null;
  /** the caller also sees trial variants (the project page's "all versions" switch) */
  showAll: boolean;
}

export const POINTS_VIEW_SCOPE = 'configure.points';
export const readPointsTarget = (storage?: Parameters<typeof readView>[2]): PointsTarget | null =>
  readView<PointsTarget>(POINTS_VIEW_SCOPE, shape<PointsTarget>({ projectId: text, die: text, config: text, duty: nullableText, showAll: bool }), storage);

interface PointsConfigureState {
  target: PointsTarget | null;
  /** the motor the card shows NOW (the opened one, then whichever step the user switched to) - the strip under the tabs names it */
  current: { projectId: string; die: string; config: string } | null;
  /** the target came from browser storage, not from a click of this session */
  restored: boolean;
  open: (target: PointsTarget) => void;
  /** remember the step / mode the user switched to, so a reload reopens on it (does not reload the card) */
  remember: (change: Partial<Pick<PointsTarget, 'die' | 'config' | 'duty'>>) => void;
  close: () => void;
}

const initial = readPointsTarget();
const motorOf = (t: PointsTarget | null) => (t ? { projectId: t.projectId, die: t.die, config: t.config } : null);

export const usePointsConfigureStore = create<PointsConfigureState>()((set, get) => ({
  target: initial,
  current: motorOf(initial),
  restored: initial !== null,
  open: (target) => { writeView(POINTS_VIEW_SCOPE, target); set({ target, current: motorOf(target), restored: false }); },
  remember: (change) => {
    const target = get().target;
    if (!target) return;
    writeView(POINTS_VIEW_SCOPE, { ...target, ...change });   // storage only: the open request itself is unchanged
    set({ current: motorOf({ ...target, ...change }) });
  },
  close: () => { writeView(POINTS_VIEW_SCOPE, null); set({ target: null, current: null, restored: false }); },
}));
