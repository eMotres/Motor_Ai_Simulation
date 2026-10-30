// The Geometry table's per-row "optimizable" switch and the Optimization variable list share ONE source: the server's
// set (GET/PUT /api/geometry/optimizable).  This store loads it, sends one switch at a time (a queue, so two quick clicks
// cannot overtake each other) and writes the answer into the motor store's schema, which both panels already read.
// A run that is already going keeps the list it started with — nothing here touches a run.
import { create } from 'zustand';
import { useMotorStore } from './motorStore';
import {
  afterToggle, refusalText, toggleBody, withOptimizable,
  type OptimizableAnswer,
} from '../lib/optimizableSet';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const URL_ = `${API}/api/geometry/optimizable`;

interface OptimizableState {
  /** the server let this account read the set (customers never get it) */
  available: boolean;
  /** this account may change it */
  canEdit: boolean;
  allAllowed: boolean;
  /** one short line when the last switch was refused */
  error: string | null;
  load: () => Promise<void>;
  toggle: (name: string, enable: boolean) => Promise<void>;
  clearError: () => void;
}

let queue: Promise<void> = Promise.resolve();

const applyToSchema = (a: OptimizableAnswer) =>
  useMotorStore.setState((s) => ({
    parameterSchema: withOptimizable(s.parameterSchema, a.optimizable, a.all_allowed),
  }));

export const useOptimizableStore = create<OptimizableState>((set, get) => ({
  available: false,
  canEdit: false,
  allAllowed: false,
  error: null,

  clearError: () => set({ error: null }),

  load: async () => {
    try {
      const r = await fetch(URL_);
      if (!r.ok) { set({ available: false, canEdit: false }); return; }
      const a = (await r.json()) as OptimizableAnswer;
      set({ available: true, canEdit: !!a.can_edit, allAllowed: !!a.all_allowed, error: null });
      applyToSchema(a);
    } catch {
      set({ available: false, canEdit: false });
    }
  },

  toggle: (name, enable) => {
    // optimistic: the switch moves at once; the server's answer is the one that stays
    const schema = useMotorStore.getState().parameterSchema;
    applyToSchema({ optimizable: afterToggle(schema, name, enable), all_allowed: false } as OptimizableAnswer);
    set({ allAllowed: false, error: null });
    queue = queue.then(async () => {
      try {
        const r = await fetch(URL_, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(toggleBody(name, enable)),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          set({ error: refusalText((j as { detail?: unknown }).detail, `HTTP ${r.status}`) });
          await get().load();                       // put the switches back to what the server holds
          return;
        }
        const a = j as OptimizableAnswer;
        set({ allAllowed: !!a.all_allowed, canEdit: a.can_edit !== false });
        applyToSchema(a);
      } catch (e) {
        set({ error: (e as Error).message || 'network error' });
        await get().load();
      }
    });
    return queue;
  },
}));
