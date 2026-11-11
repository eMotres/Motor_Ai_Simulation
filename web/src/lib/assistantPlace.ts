// Where the ONE assistant is (owner 2026-11-11: «Зачем нам два разных ассистента, один должен уметь всё»).  The chat bubble
// (components/support/SupportWidget.tsx) is the only assistant UI; the page that shows a project's Specification registers itself
// here so the same chat switches to the specification mode (fill the form, Undo, submit on confirmation) and back.
import { create } from 'zustand';
import type { DevelopmentSchema } from '../modules/development/types';

export interface SpecPlace {
  projectId: string;
  title: string;
  schema: DevelopmentSchema | null;
  /** save the pending edits so the revision the server checks is current (before Apply / Undo) */
  beforeApply?: () => Promise<void>;
  /** the form must reload: the assistant filled, undid or submitted something */
  onChanged?: () => void;
}

interface PlaceState {
  spec: SpecPlace | null;
  /** asked to open the chat (the specification just opened from a hand-off or the "describe your motor" link) */
  openRequest: number;
  setSpec: (place: SpecPlace | null) => void;
  requestOpen: () => void;
}

export const useAssistantPlace = create<PlaceState>((set) => ({
  spec: null,
  openRequest: 0,
  setSpec: (spec) => set({ spec }),
  requestOpen: () => set((s) => ({ openRequest: s.openRequest + 1 })),
}));
