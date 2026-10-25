// What the tabs that work with OTHER motors tell the strip under the tab bar (lib/stripContext.ts decides what is
// shown).  The Development page publishes its open project, the Production tab its open production; each clears it
// when nothing is open.  In memory only: the pages restore their own view (lib/viewMemory.ts) and republish.
import { create } from 'zustand';
import type { StripProduction, StripProject } from '../lib/stripContext.ts';

interface TabContextState {
  project: StripProject | null;
  production: StripProduction | null;
  setProject: (project: StripProject | null) => void;
  setProduction: (production: StripProduction | null) => void;
}

export const useTabContextStore = create<TabContextState>()((set) => ({
  project: null,
  production: null,
  setProject: (project) => set({ project }),
  setProduction: (production) => set({ production }),
}));
