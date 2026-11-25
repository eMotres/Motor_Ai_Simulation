// The Motors catalog's filters (owner 2026-11-17): ONE state shared by the controls in the Motors title line
// (components/catalog/CatalogFilters) and the table that applies them (CatalogByDie), which writes back what the controls
// list (the stage and project choices, the counts).  The filter and the saved sets are remembered per account in this
// browser (lib/viewMemory; the account preferences hold only the language today).
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';
import { NO_FILTER, parseFilter, parseSets, upsertSet, type CatalogFilter, type SavedFilterSet, type StateFilter } from '../lib/catalogByDie.ts';
import { readView, writeView } from '../lib/viewMemory.ts';

export interface FilterOptions { stages: string[]; projects: Array<{ id: string; title: string }>;
  counts: Record<StateFilter, number>; shown: number; total: number;
  /** the old motors the Active view leaves out (the number on "Show old") */
  old: number }

interface CatalogFilterState {
  account: string;
  filter: CatalogFilter;
  sets: SavedFilterSet[];
  activeSet: string | null;
  options: FilterOptions;
  /** load the account's remembered filter and sets (the Motors tab calls it when the account is known) */
  bindAccount: (account: string) => void;
  set: (patch: Partial<CatalogFilter>) => void;
  clear: () => void;
  saveSet: (name: string) => void;
  applySet: (name: string) => void;
  deleteSet: (name: string) => void;
  setOptions: (o: FilterOptions) => void;
}

const scope = (account: string, what: string) => `motors.filters.${what}.${(account || 'anon').toLowerCase()}`;

/** The vanilla store (a server-side render in a test reads its server snapshot, `getInitialState`). */
export const catalogFilterApi = createStore<CatalogFilterState>()((set, get) => {
  const remember = () => {
    const { account, filter, sets, activeSet } = get();
    writeView(scope(account, 'current'), filter);
    writeView(scope(account, 'sets'), sets);
    writeView(scope(account, 'active'), activeSet ?? '');
  };
  return {
    account: '', filter: NO_FILTER, sets: [], activeSet: null,
    options: { stages: [], projects: [], counts: { active: 0, parked: 0, archived: 0, all: 0 }, shown: 0, total: 0, old: 0 },
    bindAccount: (account) => {
      if (account === get().account) return;
      set({ account, filter: readView(scope(account, 'current'), parseFilter) ?? NO_FILTER,
        sets: readView(scope(account, 'sets'), parseSets) ?? [],
        activeSet: readView(scope(account, 'active'), (r) => (typeof r === 'string' && r ? r : null)) });
    },
    set: (patch) => { set({ filter: { ...get().filter, ...patch }, activeSet: null }); remember(); },
    // Clear empties the narrowing filters; the state tab and "Show old" are views, they stay
    clear: () => { set({ filter: { ...NO_FILTER, state: get().filter.state, showOld: get().filter.showOld }, activeSet: null }); remember(); },
    saveSet: (name) => { set({ sets: upsertSet(get().sets, name, get().filter), activeSet: name.trim() || null }); remember(); },
    applySet: (name) => {
      const s = get().sets.find((x) => x.name === name);
      if (s) { set({ filter: { ...s.filter }, activeSet: s.name }); remember(); }
    },
    deleteSet: (name) => { set({ sets: get().sets.filter((x) => x.name !== name), activeSet: get().activeSet === name ? null : get().activeSet }); remember(); },
    setOptions: (o) => {
      const cur = get().options;
      if (JSON.stringify(cur) !== JSON.stringify(o)) set({ options: o });
    },
  };
});

type Hook = {
  (): CatalogFilterState;
  <T>(selector: (s: CatalogFilterState) => T): T;
} & typeof catalogFilterApi;
export const useCatalogFilterStore: Hook = Object.assign(
  <T,>(selector?: (s: CatalogFilterState) => T) => useStore(catalogFilterApi, (selector ?? ((x: CatalogFilterState) => x)) as (s: CatalogFilterState) => T),
  catalogFilterApi) as Hook;
