import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Alert, Box, CircularProgress, Tooltip, Typography } from '@mui/material';
import type { Object3D } from 'three';
import { ModelViewer, type ViewerState } from './modelViewer';
import { effectiveSettings, type Axis, type Box as BoundsBox, type PartGroup, type StandardView, type Vec3 } from './model3d';

export interface ObjectSceneAdapter { root: Object3D; groups: PartGroup[]; box?: BoundsBox }
export interface ModelCanvasHandle {
  fit(): void; home(): void; view(view: StandardView): void; setProjection(mode: 'perspective'|'orthographic'): void;
  sectionRange(axis: Axis): { min: number; max: number };
}
interface Props {
  sourceKey: string;
  glbUrl?: string | null;
  objectScene?: ObjectSceneAdapter | null;
  tools: ViewerState;
  fullAccess: boolean;
  highlight?: ReadonlySet<string>;
  hiddenNodes?: ReadonlySet<string>;
  contextLostText: string;
  simplifiedText?: string;
  simplifiedTip?: string;
  noViewText?: string;
  onLoaded?: (r: { groups: PartGroup[]; range: { min: number; max: number } }) => void;
  onReady?: (ready: boolean) => void;
  onMeasure?: (mm: number | null) => void;
  onCameraDirection?: (direction: Vec3) => void;
  onCameraUp?: (up: Vec3) => void;
  onSelect?: (name: string | null) => void;
  onError?: (message: string | null) => void;
  children?: React.ReactNode;
}
const ModelCanvas = forwardRef<ModelCanvasHandle, Props>((p, ref) => {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<ModelViewer | null>(null);
  const callbacks = useRef(p); callbacks.current = p;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadedTick, setLoadedTick] = useState(0);
  const report = (message: string | null) => { setError(message); callbacks.current.onError?.(message); };
  useImperativeHandle(ref, () => ({ fit: () => viewer.current?.fit(), home: () => viewer.current?.home(), view: (v) => viewer.current?.view(v),
    setProjection: (m) => viewer.current?.setProjection(m), sectionRange: (a) => viewer.current?.sectionRange(a) ?? { min:-1,max:1 } }), []);
  useEffect(() => {
    if (!host.current) return;
    const v = new ModelViewer(host.current);
    v.onMeasure = (mm) => callbacks.current.onMeasure?.(mm);
    v.onCameraDirection = (d) => callbacks.current.onCameraDirection?.(d);
    v.onCameraUp = (u) => callbacks.current.onCameraUp?.(u);
    v.onSelect = (name) => { if (callbacks.current.fullAccess) callbacks.current.onSelect?.(name); };
    v.onContextLost = () => report(callbacks.current.contextLostText);
    viewer.current = v;
    return () => { v.onContextLost = () => {}; v.onMeasure = () => {}; v.onCameraDirection = () => {}; v.onCameraUp = () => {}; v.onSelect = () => {}; v.dispose(); viewer.current = null; };
  }, []);
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    let live = true;
    setLoading(false); report(null);
    if (p.objectScene) {
      v.setObject(p.objectScene.root, p.objectScene.groups);
      callbacks.current.onReady?.(true); setLoadedTick((n) => n + 1);
      callbacks.current.onLoaded?.({ groups:p.objectScene.groups, range:v.sectionRange('x') });
      return;
    }
    v.clearModel(); callbacks.current.onReady?.(false);
    if (!p.glbUrl) return;
    setLoading(true);
    (async () => {
      try {
        const response = await fetch(p.glbUrl!);
        if (!live) return;
        if (!response.ok) {
          let detail = String(response.status);
          try { const body = await response.json(); detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail ?? body); } catch { /* status fallback */ }
          if (live) report(detail); return;
        }
        const buffer = await response.arrayBuffer(); if (!live) return;
        const { groups } = await v.load(buffer); if (!live) return;
        callbacks.current.onReady?.(true); setLoadedTick((n) => n + 1);
        callbacks.current.onLoaded?.({ groups, range:v.sectionRange('x') });
      } catch (e) { if (live) report(e instanceof Error ? e.message : String(e)); }
      finally { if (live) setLoading(false); }
    })();
    return () => { live = false; v.cancelPendingLoad(); callbacks.current.onReady?.(false); };
  }, [p.sourceKey, p.glbUrl, p.objectScene?.root]);
  useEffect(() => {
    vapply();
    function vapply() {
      const full = callbacks.current.fullAccess;
      viewer.current?.apply({ ...effectiveSettings(full, callbacks.current.tools), highlight:full ? callbacks.current.highlight : undefined,
        hiddenNodes:full ? callbacks.current.hiddenNodes : undefined });
    }
  }, [p.tools, p.fullAccess, p.highlight, p.hiddenNodes, loadedTick]);
  return <Box sx={{ position:'relative', width:'100%', height:'100%' }} data-testid="model-canvas">
    <Box ref={host} sx={{ position:'absolute', inset:0 }} />
    {loading && <Box sx={{ position:'absolute', top:12, left:12 }}><CircularProgress size={22}/></Box>}
    {error && <Alert severity="error" sx={{ position:'absolute', top:8, left:8, right:8, fontSize:12 }} onClose={() => report(null)}>{error}</Alert>}
    {!p.fullAccess && p.glbUrl && p.simplifiedText && <Tooltip title={p.simplifiedTip ?? ''}><Typography sx={{ position:'absolute', left:12, bottom:10, fontSize:11.5, color:'var(--text-3)', bgcolor:'var(--panel)', border:'1px solid var(--line-soft)', borderRadius:1, px:1, py:.25 }}>{p.simplifiedText}</Typography></Tooltip>}
    {!p.glbUrl && !p.objectScene && p.noViewText && <Typography sx={{ position:'absolute', top:'45%', left:0, right:0, textAlign:'center', fontSize:13, color:'var(--text-3)' }}>{p.noViewText}</Typography>}
    {p.children}
  </Box>;
});
ModelCanvas.displayName = 'ModelCanvas';
export default ModelCanvas;
