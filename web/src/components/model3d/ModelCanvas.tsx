// The 3-D canvas of the CAD motor model - ONE viewer component for the "3D model" tab (role user) and for Geometry > Full
// (engineer / admin): the server-built GLB of /api/model3d/{die}/{config}/model.glb in a three.js scene, with the tools' state
// (hidden families, exploded view, section, measure) and the highlight of the part selected in THE part tree passed in.
//
// The model reloads when its file changes (GLB sha256 in the model list), so a rebuilt model replaces the old one without a
// reload of the page.  Everything the viewer shows is decided on the server; this component only draws.
import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Alert, Box, CircularProgress } from '@mui/material';
import { ModelViewer } from './modelViewer';
import { effectiveSettings, type Axis, type ModelInfo, type PartGroup } from '../../lib/model3d';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');

export const modelBase = (m: Pick<ModelInfo, 'die' | 'config'>): string =>
  `${API}/api/model3d/${encodeURIComponent(m.die)}/${encodeURIComponent(m.config)}`;

export async function responseDetail(r: Response): Promise<string> {
  try { const j = await r.json(); return typeof j?.detail === 'string' ? j.detail : JSON.stringify(j?.detail ?? j); } catch { return String(r.status); }
}

export interface ModelCanvasHandle {
  fit(): void;
  sectionRange(axis: Axis): { min: number; max: number };
}

export interface CanvasTools {
  hidden: ReadonlySet<string>;
  explode: number;
  section: { on: boolean; axis: Axis; position: number };
  measure: boolean;
}

interface Props {
  model: ModelInfo | null;
  /** the tools' state; applied through `effectiveSettings` (without the NDA the plain model is shown) */
  tools?: CanvasTools;
  canUseTools?: boolean;
  /** GLB node names to highlight (the part selected in the tree) */
  highlight?: ReadonlySet<string>;
  /** GLB node names hidden one by one (parts switched off in the part tree; needs the tools, i.e. the NDA) */
  hiddenNodes?: ReadonlySet<string>;
  contextLostText: string;
  onLoaded?: (r: { groups: PartGroup[]; range: { min: number; max: number } }) => void;
  onMeasure?: (mm: number | null) => void;
  onError?: (message: string | null) => void;
  /** rendered over the canvas (badges, notices) */
  children?: React.ReactNode;
}

const NO_TOOLS: CanvasTools = { hidden: new Set(), explode: 0, section: { on: false, axis: 'x', position: 0 }, measure: false };

const ModelCanvas = forwardRef<ModelCanvasHandle, Props>(({
  model, tools = NO_TOOLS, canUseTools = true, highlight, hiddenNodes, contextLostText, onLoaded, onMeasure, onError, children,
}, ref) => {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<ModelViewer | null>(null);
  const cb = useRef({ onLoaded, onMeasure, onError, contextLostText });
  cb.current = { onLoaded, onMeasure, onError, contextLostText };
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadedTick, setLoadedTick] = useState(0);

  const report = (m: string | null) => { setErr(m); cb.current.onError?.(m); };

  useImperativeHandle(ref, () => ({
    fit: () => viewer.current?.fit(),
    sectionRange: (axis: Axis) => viewer.current?.sectionRange(axis) ?? { min: -1, max: 1 },
  }), []);

  // the viewer lives as long as the canvas
  useEffect(() => {
    if (!host.current) return undefined;
    const v = new ModelViewer(host.current);
    v.onMeasure = (mm) => cb.current.onMeasure?.(mm);
    v.onContextLost = () => report(cb.current.contextLostText);
    viewer.current = v;
    return () => { v.onContextLost = () => {}; v.onMeasure = () => {}; v.dispose(); viewer.current = null; };   // a disposed viewer reports nothing
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // load the chosen model; a rebuilt file (new sha256) loads again
  const fileKey = model ? `${model.die}\u0000${model.config}\u0000${model.glb_sha256 ?? ''}` : '';
  useEffect(() => {
    const v = viewer.current;
    if (!model || !v) return undefined;
    let live = true;
    setLoading(true); report(null);
    (async () => {
      try {
        const r = await fetch(`${modelBase(model)}/model.glb`);
        if (!r.ok) { if (live) report(await responseDetail(r)); return; }
        const { groups } = await v.load(await r.arrayBuffer());
        if (!live) return;
        setLoadedTick((n) => n + 1);
        cb.current.onLoaded?.({ groups, range: v.sectionRange('x') });
      } catch (e: any) { if (live) report(e?.message ?? String(e)); } finally { if (live) setLoading(false); }
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileKey]);

  // push the controls into the scene
  useEffect(() => {
    const s = canUseTools ? effectiveSettings(true, tools) : effectiveSettings(false, tools);
    viewer.current?.apply({ ...s, highlight, hiddenNodes: canUseTools ? hiddenNodes : undefined });
  }, [tools, canUseTools, highlight, hiddenNodes, loadedTick]);

  return (
    <Box sx={{ position: 'relative', width: '100%', height: '100%' }} data-testid="model-canvas">
      <Box ref={host} sx={{ position: 'absolute', inset: 0 }} />
      {loading && <Box sx={{ position: 'absolute', top: 12, left: 12 }}><CircularProgress size={22} /></Box>}
      {err && <Alert severity="error" sx={{ position: 'absolute', top: 8, left: 8, right: 8, fontSize: 12 }} onClose={() => report(null)}>{err}</Alert>}
      {children}
    </Box>
  );
});
ModelCanvas.displayName = 'ModelCanvas';

export default ModelCanvas;
