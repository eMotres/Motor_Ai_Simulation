// The Motors tab's project highlight (owner 2026-10-26): which dies / configurations belong to the selected project.
// Written by components/catalog/ProjectHighlightSelect (which loads the project and its motors), read by FamilyCatalog to
// mark the matching rows.  Memory only; the choice itself is remembered by stores/projectFocusStore.
import { create } from 'zustand';
import { highlightIndex, type HighlightIndex } from '../modules/development/projectFocus.ts';

interface HighlightState {
  index: HighlightIndex;
  setMotors: (motors: ReadonlyArray<{ die: string; config: string; canonical_die?: string }>) => void;
}

export const useProjectHighlightStore = create<HighlightState>()((set) => ({
  index: highlightIndex([]),
  setMotors: (motors) => set({ index: highlightIndex(motors) }),
}));
