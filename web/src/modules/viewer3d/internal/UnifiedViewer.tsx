import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box } from '@mui/material';
import { useTranslation } from 'react-i18next';
import type { Axis, PartGroup, StandardView, Vec3 } from './model3d';
import { formatMm } from './model3d';
import PartTree, { type PartTreeProps } from './PartTree';
import ModelCanvas, { type ModelCanvasHandle, type ObjectSceneAdapter } from './ModelCanvas';
import ViewerToolbar from './ViewerToolbar';
import NavigationCube from './NavigationCube';
import { controlsFor, treeStartsCollapsed, type ViewerToolbarSpec } from './toolbarSpec';
import { buildActiveScene } from './activeScene';
import { hiddenKeysByDefault, hiddenSceneNames, selectedKeyForScene, selectedSceneNames } from './treeState';

export interface UnifiedViewerProps {
  sourceKey: string;
  resetKey: string;
  glbUrl?: string | null;
  activeParts?: Array<{ key: string; family: string; colour: string; vertices: number[] | number[][]; faces?: number[][]; opacity?: number }> | null;
  toolbarSpec: ViewerToolbarSpec;
  tree?: PartTreeProps['nodes'] | null;
  selectedKey?: string | null;
  onSelectedKeyChange?: (key: string | null) => void;
  contextLostText: string;
  simplifiedText?: string;
  simplifiedTip?: string;
  noViewText?: string;
  onLoaded?: (r: { groups: PartGroup[]; range: { min: number; max: number } }) => void;
  onMeasure?: (mm: number | null) => void;
  onError?: (message: string | null) => void;
  hostExtras?: React.ReactNode;
}

// the projection the viewer was last left in, kept for this tab's session (owner 2026-10-17)
let lastProjection: 'perspective'|'orthographic' = 'orthographic';

const UnifiedViewer: React.FC<UnifiedViewerProps> = (p) => {
  const { t } = useTranslation('model3d');
  const full = p.toolbarSpec === 'full';
  const controlSet = useMemo(() => new Set(controlsFor(p.toolbarSpec)), [p.toolbarSpec]);
  const canvas = useRef<ModelCanvasHandle>(null);
  const viewport = useViewportWidth();
  const [explode, setExplode] = useState(0);
  const [section, setSection] = useState<{ on: boolean; axis: Axis; position: number }>({ on:false, axis:'x', position:0 });
  const [range, setRange] = useState({ min:-1, max:1 });
  const [measure, setMeasure] = useState(false);
  const [measuredMm, setMeasuredMm] = useState<number | null>(null);
  const [sceneReady, setSceneReady] = useState(false);
  const [projection, setProjectionState] = useState<'perspective'|'orthographic'>(() => lastProjection);
  const setProjection = useCallback((m: 'perspective'|'orthographic') => { lastProjection = m; setProjectionState(m); }, []);
  const [cameraDirection, setCameraDirection] = useState<Vec3>([1,-1,1]);
  const [cameraUp, setCameraUp] = useState<Vec3>([0,0,1]);
  const [hiddenKeys, setHiddenKeys] = useState<ReadonlySet<string>>(new Set());
  const [localSelected, setLocalSelected] = useState<string | null>(null);
  const initialized = useRef('');
  const initializedWithRows = useRef(false);
  const resetKey = `${p.resetKey}\u0000${p.toolbarSpec}`;
  const selectedKey = p.selectedKey === undefined ? localSelected : p.selectedKey;
  const tree = p.tree ?? [];
  const activeScene = useMemo(() => p.activeParts ? buildActiveScene(p.activeParts, p.sourceKey) : null, [p.activeParts, p.sourceKey]);
  const objectScene: ObjectSceneAdapter | null = activeScene;
  useEffect(() => {
    if (initialized.current !== resetKey) {
      initialized.current = resetKey; initializedWithRows.current = false;
      setHiddenKeys(new Set()); setExplode(0); setMeasure(false); setMeasuredMm(null); setSection({ on:false, axis:'x', position:0 }); setLocalSelected(null);
      p.onMeasure?.(null); p.onSelectedKeyChange?.(null);
    }
    if (!initializedWithRows.current && tree.length > 0) {
      initializedWithRows.current = true; setHiddenKeys(hiddenKeysByDefault(tree));
    }
  }, [resetKey, tree, p.onMeasure, p.onSelectedKeyChange]);
  useEffect(() => { canvas.current?.setProjection(projection); }, [projection]);
  useEffect(() => { if (!full) { setExplode(0); setSection((s) => ({ ...s, on:false })); setMeasure(false); setMeasuredMm(null); } }, [full]);
  const hiddenNodes = useMemo(() => hiddenSceneNames(tree, hiddenKeys), [tree, hiddenKeys]);
  const highlight = useMemo(() => selectedSceneNames(tree, selectedKey ?? null), [tree, selectedKey]);
  const tools = useMemo(() => ({ hidden:new Set<string>(), explode, section, measure }), [explode, section, measure]);
  const loaded = useCallback((r: { groups: PartGroup[]; range: { min:number; max:number } }) => {
    setExplode(0); setMeasure(false); setMeasuredMm(null); p.onMeasure?.(null);
    setRange(r.range); setSection({ on:false, axis:'x', position:(r.range.min+r.range.max)/2 }); p.onLoaded?.(r);
  }, [p.onLoaded, p.onMeasure]);
  const changeSection = useCallback((s: { on:boolean; axis:Axis; position:number }) => {
    if (s.axis !== section.axis) { const r = canvas.current?.sectionRange(s.axis) ?? range; setRange(r); setSection({ ...s, position:(r.min+r.max)/2 }); }
    else setSection(s);
  }, [section.axis, range]);
  const onView = useCallback((v: StandardView) => canvas.current?.view(v), []);
  const onHome = useCallback(() => canvas.current?.home(), []);
  const cubeLabel = useCallback((v: StandardView) => t(`cube.${v}`), [t]);
  const onSelectName = (name: string | null) => {
    const key = name ? selectedKeyForScene(tree, name) : null;
    if (p.selectedKey === undefined) setLocalSelected(key);
    p.onSelectedKeyChange?.(key);
  };
  const onSelectRow = (key: string) => {
    const next = key === selectedKey ? null : key;
    if (p.selectedKey === undefined) setLocalSelected(next);
    p.onSelectedKeyChange?.(next);
  };
  return <Box data-testid="unified-viewer" sx={{ display:'flex', flexDirection:'column', width:'100%', height:'100%', minHeight:0, minWidth:0, position:'relative' }}>
    <ViewerToolbar full={full} sceneReady={sceneReady} controls={controlSet} section={section} sectionRange={range} explode={explode} measure={measure} projection={projection} t={t}
      onView={onView} onFit={() => canvas.current?.fit()} onHome={onHome} onProjection={setProjection} onSection={changeSection} onExplode={setExplode}
      onMeasure={(v) => { setMeasure(v); if (!v) setMeasuredMm(null); }} />
    {p.hostExtras}
    <Box sx={{ flex:1, minHeight:0, minWidth:0, position:'relative' }}>
      <ModelCanvas ref={canvas} sourceKey={p.sourceKey} glbUrl={p.glbUrl} objectScene={objectScene} tools={tools} fullAccess={full} highlight={highlight} hiddenNodes={hiddenNodes}
        contextLostText={p.contextLostText} simplifiedText={p.simplifiedText} simplifiedTip={p.simplifiedTip} noViewText={p.noViewText} onLoaded={loaded} onReady={setSceneReady}
        onMeasure={(mm) => { setMeasuredMm(mm); p.onMeasure?.(mm); }} onCameraDirection={setCameraDirection} onCameraUp={setCameraUp} onSelect={onSelectName} onError={p.onError} />
      {sceneReady && <Box sx={{ position:'absolute', top:8, right:8, zIndex:6 }}><NavigationCube cameraDirection={cameraDirection} cameraUp={cameraUp} onView={onView} onHome={onHome} label={cubeLabel} title={t('cube.title')} homeLabel={t('home')} /></Box>}
      {full && sceneReady && tree.length > 0 && <PartTree nodes={tree} hiddenKeys={hiddenKeys} onToggle={(key) => setHiddenKeys((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n; })}
        selectedKey={selectedKey} onSelect={onSelectRow} defaultCollapsed={treeStartsCollapsed(viewport)} style={{ maxWidth:'calc(100% - 136px)' }} rowsMaxHeight="calc(100% - 40px)" />}
      {full && measure && <Box aria-live="polite" sx={{ position:'absolute', bottom:8, right:10, zIndex:5, px:1, py:.35, bgcolor:'var(--overlay)', border:'1px solid var(--line)', borderRadius:1, fontSize:11 }}>{measuredMm == null ? t('measureHint') : formatMm(measuredMm)}</Box>}
    </Box>
  </Box>;
};

function useViewportWidth(): number {
  const [width, setWidth] = useState(() => typeof window === 'undefined' ? 1200 : window.innerWidth);
  useEffect(() => { const onResize = () => setWidth(window.innerWidth); window.addEventListener('resize', onResize); return () => window.removeEventListener('resize', onResize); }, []);
  return width;
}
export default UnifiedViewer;
