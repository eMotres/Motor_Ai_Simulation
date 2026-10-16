// Data hooks of Geometry > Full: the models this account may open (+ the NDA flag), THE part tree of the open motor, and the
// editable CAD-only construction table with its save / rebuild actions.  Plain fetch, like the rest of the panels.
import { useCallback, useEffect, useRef, useState } from 'react';
import { getStoredToken } from '../../lib/localAuth';
import { useLastMotorState } from '../../lib/lastMotor';
import { useApiReady } from '../../contexts/AuthContext';
import { refusalLines, shouldPoll, type CadParamsData } from '../../lib/cadParams';
import type { ModelInfo } from '../../lib/model3d';
import type { PartTreeData } from '../../lib/partTree';
import { parseAccess, type AccessInfo } from '../../lib/verification';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');
const enc = encodeURIComponent;

export interface OpenMotor { die: string; config: string }

/** The motor open in the app: an account's remembered selection (Motors / Configure), else - for the admin and the local dev
 *  backend, who work on the shared machine - the server's active die / configuration (GET /api/family/context). */
export function useOpenMotor(): OpenMotor | null {
  const { selection } = useLastMotorState();
  const ready = useApiReady();
  const [ctx, setCtx] = useState<OpenMotor | null>(null);
  useEffect(() => {
    if (!ready || selection) return undefined;
    let live = true;
    const load = async () => {
      try {
        const token = getStoredToken();
        const r = await fetch(`${API}/api/family/context`, { cache: 'no-store', headers: token ? { Authorization: `Bearer ${token}` } : {} });
        if (!r.ok) return;
        const j = await r.json();
        if (live) setCtx(j?.active && j.die && j.config ? { die: String(j.die), config: String(j.config) } : null);
      } catch { /* the panel says "open a motor first" */ }
    };
    void load();
    window.addEventListener('family-changed', load);
    return () => { live = false; window.removeEventListener('family-changed', load); };
  }, [ready, selection]);
  return selection ? { die: selection.die, config: selection.config } : ctx;
}

/** The built models the account may open, whether it may download (the server's `can_download`) and why not (`access`: the NDA, or the
 *  applicant verification behind it). */
export function useModelList(refreshKey: string | number = 0, selected: OpenMotor | null = null, pollIfMissing = false) {
  const ready = useApiReady();
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [canDownload, setCanDownload] = useState(false);
  const [access, setAccess] = useState<AccessInfo>({ state: 'nda', nda_signed: false });
  useEffect(() => {
    if (!ready) return undefined;
    let live = true;
    (async () => {
      try {
        const r = await fetch(`${API}/api/model3d/models`, { cache: 'no-store' });
        if (!r.ok) { if (live) setModels([]); return; }
        const j = await r.json();
        if (!live) return;
        setCanDownload(j.can_download === true);
        setAccess(parseAccess(j.access, j.can_download === true));
        setModels(j.models as ModelInfo[]);
      } catch { if (live) setModels([]); }
    })();
    return () => { live = false; };
  }, [ready, refreshKey]);
  const activeModels = selected ? models?.filter((m) => m.die === selected.die && m.config === selected.config) : models;
  const poll = selected
    ? (activeModels?.length ? activeModels.some((m) => m.stale || m.rebuilding) : pollIfMissing && models !== null)
    : !!activeModels?.some((m) => m.stale || m.rebuilding);
  useEffect(() => {
    if (!ready || !poll) return undefined;
    let live = true;
    let busy = false;
    const id = window.setInterval(() => {
      if (busy) return;
      busy = true;
      void fetch(`${API}/api/model3d/models`, { cache: 'no-store' }).then(async (r) => {
        if (!r.ok) return;
        const j = await r.json();
        if (!live) return;
        setCanDownload(j.can_download === true);
        setAccess(parseAccess(j.access, j.can_download === true));
        setModels(j.models as ModelInfo[]);
      }).catch(() => { /* keep the last visible model during a brief network failure */ })
        .finally(() => { busy = false; });
    }, 4000);
    return () => { live = false; window.clearInterval(id); };
  }, [ready, poll, selected?.die, selected?.config]);
  return { models, canDownload, access };
}

/** THE part tree of die/config.  `source`: 'family' (Geometry / Materials, with materials + masses) or 'model3d' (the 3-D tab, names only). */
export function usePartTreeData(die: string | null, config: string | null, source: 'family' | 'model3d', enabled = true, refreshKey: string | number = 0) {
  const ready = useApiReady();
  const [tree, setTree] = useState<PartTreeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!ready || !enabled || !die || !config) { setTree(null); return undefined; }
    let live = true;
    setLoading(true);
    const url = source === 'family'
      ? `${API}/api/family/die/${enc(die)}/part_tree?config=${enc(config)}`
      : `${API}/api/model3d/${enc(die)}/${enc(config)}/parts`;
    fetch(url, { cache: 'no-store' })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<PartTreeData>;
      })
      .then((t) => { if (live) { setTree(t); setError(null); setLoading(false); } })
      .catch((e) => { if (live) { setTree(null); setError(String(e)); setLoading(false); } });
    return () => { live = false; };
  }, [ready, enabled, die, config, source, refreshKey]);
  return { tree, loading, error };
}

export interface SaveResult { ok: boolean; lines: string[] }

/** The editable construction table of die/config; polls the rebuild status while the new model is on its way. */
export function useCadParams(die: string | null, config: string | null, enabled: boolean, onRebuilt?: () => void) {
  const ready = useApiReady();
  const [data, setData] = useState<CadParamsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const was = useRef<string | null>(null);
  const cb = useRef(onRebuilt);
  cb.current = onRebuilt;
  const url = die && config ? `${API}/api/family/config/${enc(die)}/${enc(config)}/cad_params` : null;

  const refresh = useCallback(async (quiet = false) => {
    if (!ready || !enabled || !url) return;
    if (!quiet) setLoading(true);
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as CadParamsData;
      setData(j); setError(null);
      const st = j.rebuild?.state ?? null;
      if (was.current && was.current !== 'current' && st === 'current') cb.current?.();
      was.current = st;
    } catch (e) { if (!quiet) { setData(null); setError(String(e)); } } finally { if (!quiet) setLoading(false); }
  }, [ready, enabled, url]);

  useEffect(() => { was.current = null; void refresh(); }, [refresh]);

  const poll = shouldPoll(data?.rebuild);
  useEffect(() => {
    if (!poll) return undefined;
    const id = window.setInterval(() => { void refresh(true); }, 6000);
    return () => window.clearInterval(id);
  }, [poll, refresh]);

  const send = useCallback(async (body: Record<string, unknown>): Promise<SaveResult> => {
    if (!url) return { ok: false, lines: [] };
    setSaving(true);
    try {
      const r = await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, lines: refusalLines((j as { detail?: unknown }).detail).concat(r.status === 403 ? ['403'] : []) };
      const t = (j as { table?: CadParamsData }).table;
      if (t) { setData(t); was.current = t.rebuild?.state ?? null; } else void refresh(true);
      return { ok: true, lines: [] };
    } catch (e) { return { ok: false, lines: [String(e)] }; } finally { setSaving(false); }
  }, [url, refresh]);

  const save = useCallback((values: Record<string, number | null>) => send({ values }), [send]);
  const chooseBearing = useCallback((card: string | null) => send({ bearing_card: card }), [send]);

  const requestRebuild = useCallback(async (): Promise<SaveResult> => {
    if (!die || !config) return { ok: false, lines: [] };
    try {
      const r = await fetch(`${API}/api/family/config/${enc(die)}/${enc(config)}/cad_rebuild`, { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, lines: refusalLines((j as { detail?: unknown }).detail) };
      void refresh(true);
      return { ok: true, lines: [] };
    } catch (e) { return { ok: false, lines: [String(e)] }; }
  }, [die, config, refresh]);

  /** Import a Fusion CSV / FreeCAD file: `dry` validates and lists what would change, otherwise saves. */
  const importFile = useCallback(async (file: File, dry: boolean): Promise<SaveResult & { changed: string[] }> => {
    if (!url) return { ok: false, lines: [], changed: [] };
    const fd = new FormData(); fd.append('file', file);
    try {
      const r = await fetch(`${url}/import?dry_run=${dry ? 1 : 0}`, { method: 'POST', body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, lines: refusalLines((j as { detail?: unknown }).detail), changed: [] };
      if (!dry) {
        const t = (j as { table?: CadParamsData }).table;
        if (t) { setData(t); was.current = t.rebuild?.state ?? null; } else void refresh(true);
      }
      return { ok: true, lines: [], changed: ((j as { changed?: string[] }).changed ?? []) };
    } catch (e) { return { ok: false, lines: [String(e)], changed: [] }; }
  }, [url, refresh]);

  return { data, loading, saving, error, refresh, save, chooseBearing, requestRebuild, importFile };
}
