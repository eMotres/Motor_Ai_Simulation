// The project the user last opened a motor from (or chose in the Motors tab): the Motors tab highlights its motors
// (modules/development/projectFocus.ts).  Remembered per account in browser storage (lib/viewMemory.ts) so it survives a
// reload; a project that is gone or no longer visible is dropped by the Motors tab (pickFocusProject), never trusted.
import { create } from 'zustand';
import { getStoredUser } from '../lib/localAuth';
import { readFocus, writeFocus, type FocusProject } from '../modules/development/projectFocus.ts';

interface ProjectFocusState {
  focus: FocusProject | null;
  /** re-read the remembered focus (after a sign-in / account switch) */
  reload: () => void;
  set: (focus: FocusProject | null) => void;
}

const email = () => getStoredUser()?.email ?? null;

export const useProjectFocusStore = create<ProjectFocusState>()((set) => ({
  focus: readFocus(email()),
  reload: () => set({ focus: readFocus(email()) }),
  set: (focus) => { writeFocus(email(), focus); set({ focus }); },
}));
