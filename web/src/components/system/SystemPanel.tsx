/** System tab - how the simulations are chained (Simulations > System, owner 2026-10-14).  VIEW ONLY.
 *
 *  Blocks = modules (or, in the "Propeller S1" view, the steps of the S1 rating).  Parameter sources (motor model,
 *  limits, ...) stand in an Inputs column and appear as chips on the blocks that use them; one wire per block pair
 *  (a count badge when several data items share it), right-angle routing, feedback loops as a dashed group with
 *  their lane at its bottom.  Hovering a block lights its wires and dims the rest.  The graph is the backend's
 *  (`GET /api/system/graph`, motor_ai_sim.system_graph); this file only draws it.  Pan and zoom only: nothing here
 *  edits or runs a model (stage 2 will compose graphs, stage 3 hands the JSON to agents - docs/SYSTEM_GRAPH.md).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Alert, Box, CircularProgress, FormControlLabel, IconButton, MenuItem, Paper, Stack, Switch, TextField,
  ToggleButton, ToggleButtonGroup, Tooltip, Typography, useTheme,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import {
  BaseEdge, Background, Controls, EdgeLabelRenderer, Handle, MarkerType, MiniMap, Position, ReactFlow,
} from '@xyflow/react';
import type { Edge, EdgeProps, Node, NodeProps, ReactFlowInstance } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTranslation } from 'react-i18next';

import { formatApiErrorDetail } from '../../lib/apiErrorDetail';
import { activeDuty } from '../../lib/dutySettings';
import { fetchFamilyTree } from '../../lib/familyTree';
import { STATE_COLORS, formatTime, kindColor, loopBadge, typeColor } from '../../lib/systemGraph';
import type { SgDoc, SgEdge, SgLoop, SgNode } from '../../lib/systemGraph';
import { badgeOf, buildView, chipCounts, laneCounts, neighbours } from '../../lib/systemGraphView';
import type { Badge, Chip, View as GraphView, Wire } from '../../lib/systemGraphView';
import { NODE_W, blockHeight, layoutGraph, roundedPath, routeWires } from '../../lib/systemGraphLayout';
import type { Layout, Pt } from '../../lib/systemGraphLayout';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const VIEW_KEY = 'system.view';
const ALL_KEY = 'system.showAll';
const MOTOR_KEY = 'system.motor';
type ViewName = 'modules' | 'propeller_s1';
const LOOP_COLORS = ['#f59e0b', '#0ea5e9', '#a855f7', '#10b981'];

const read = (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* a convenience */ } };

interface Motor { die: string; config: string; duty: string }
function savedMotor(): Motor | null {
  try {
    const m = JSON.parse(read(MOTOR_KEY) || 'null');
    if (m?.die && m?.config) return { die: String(m.die), config: String(m.config), duty: String(m.duty ?? '') };
  } catch { /* unreadable */ }
  const a = activeDuty();
  return a ? { die: a.die, config: a.config, duty: a.duty } : null;
}

type Tr = (k: string, o?: Record<string, unknown>) => string;

// -- custom nodes -------------------------------------------------------------------------------------------

interface ChipView { chip: Chip; text: string; tip: string }
interface BlockData extends Record<string, unknown> {
  node: SgNode; title: string; tip: ReactNode; badge: Badge; badgeText: string; noRunTip: string;
  chips: ChipView[]; dim: boolean; rings: string[]; dots: string[];
}
interface LoopData extends Record<string, unknown> { title: string; badge: string; color: string; tip: ReactNode }

const PICK_SX = { '& .MuiInputBase-root': { fontSize: 13, height: 32 }, '& .MuiInputLabel-root': { fontSize: 12 } } as const;
const hidden = { opacity: 0, pointerEvents: 'none' } as const;

function BlockNode({ data }: NodeProps<Node<BlockData>>) {
  const n = data.node;
  const ring = data.rings.map((c, i) => `0 0 0 ${2 + i * 3}px ${c}`).join(', ');
  const compact = !!n.context;
  const b = data.badge;
  return (
    <Tooltip arrow placement="top" title={data.tip} enterDelay={250} slotProps={{ tooltip: { sx: { maxWidth: 420 } } }}>
      <Box sx={{
        width: NODE_W, height: '100%', boxSizing: 'border-box', px: 1, py: 0.5, borderRadius: 1,
        bgcolor: 'background.paper', border: '1px solid', borderColor: 'divider',
        borderLeft: `5px solid ${kindColor(n.kind)}`, boxShadow: ring || undefined,
        opacity: data.dim ? 0.3 : n.roadmap ? 0.6 : 1, transition: 'opacity 120ms',
        display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 0.2,
      }}>
        <Handle type="target" position={Position.Left} style={hidden} />
        <Stack direction="row" alignItems="center" spacing={0.6}>
          <Typography variant="body2" noWrap sx={{ fontWeight: 600, lineHeight: 1.25, flex: 1 }}>{data.title}</Typography>
          {compact && data.dots.map((c) => <Box key={c} sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: c }} />)}
        </Stack>
        {!compact && (
          <Typography variant="caption" noWrap sx={{ color: 'text.secondary', fontFamily: 'monospace', fontSize: 10.5 }}>
            {n.source?.function ?? n.capability ?? n.id}
          </Typography>
        )}
        {!compact && b.kind === 'norun' && (
          <Tooltip title={data.noRunTip} placement="bottom">
            <Stack direction="row" spacing={0.6} alignItems="center" sx={{ cursor: 'help' }}>
              <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: STATE_COLORS.unknown }} />
              <Typography variant="caption" sx={{ fontSize: 10.5, color: 'text.secondary' }}>{data.badgeText}</Typography>
            </Stack>
          </Tooltip>
        )}
        {!compact && b.kind === 'state' && (
          <Stack direction="row" spacing={0.6} alignItems="center">
            <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: STATE_COLORS[b.state] }} />
            <Typography variant="caption" sx={{ fontSize: 10.5, color: 'text.secondary' }}>{data.badgeText}</Typography>
            {formatTime(n.status.time_s) && <Typography variant="caption" sx={{ fontSize: 10.5 }}>{formatTime(n.status.time_s)}</Typography>}
          </Stack>
        )}
        {!compact && data.chips.length > 0 && (
          <Stack direction="row" useFlexGap flexWrap="wrap" spacing={0.4} sx={{ mt: 0.2, '& > *': { mb: 0.3 } }}>
            {data.chips.map((c) => (
              <Tooltip key={c.chip.id} title={c.tip} placement="bottom">
                <Box sx={{
                  display: 'inline-flex', alignItems: 'center', gap: 0.4, px: 0.6, height: 16, maxWidth: 96, borderRadius: 8,
                  border: `1px solid ${typeColor(c.chip.type)}`, fontSize: 10, lineHeight: 1, cursor: 'help',
                }}>
                  <Box sx={{ width: 5, height: 5, borderRadius: '50%', bgcolor: typeColor(c.chip.type), flexShrink: 0 }} />
                  <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.text}</Box>
                </Box>
              </Tooltip>
            ))}
          </Stack>
        )}
        <Handle type="source" position={Position.Right} style={hidden} />
      </Box>
    </Tooltip>
  );
}

function LoopNode({ data, width, height }: NodeProps<Node<LoopData>>) {
  return (
    <Box sx={{
      width: width ?? '100%', height: height ?? '100%', boxSizing: 'border-box', borderRadius: 2,
      border: `2px dashed ${data.color}`, bgcolor: `${data.color}12`, pointerEvents: 'none',
    }}>
      <Tooltip arrow placement="top" title={data.tip}>
        <Box sx={{ pointerEvents: 'auto', display: 'inline-flex', gap: 0.75, alignItems: 'center', px: 1, py: 0.2,
          color: data.color, fontSize: 11.5, fontWeight: 600, cursor: 'help' }}>
          <span>{'↻'} {data.title}</span>
          {data.badge && <span style={{ fontWeight: 400 }}>{data.badge}</span>}
        </Box>
      </Tooltip>
    </Box>
  );
}

function LabelNode({ data }: NodeProps<Node<{ text: string }>>) {
  return <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: 0.6, color: 'text.secondary', textTransform: 'uppercase' }}>{data.text}</Typography>;
}

interface WireData extends Record<string, unknown> {
  path: string; color: string; feedback: boolean; dim: boolean; lit: boolean; count: number; label: Pt; wire: Wire;
}
function WireEdge({ id, markerEnd, data }: EdgeProps<Edge<WireData>>) {
  const d = data!;
  return (
    <>
      <BaseEdge id={id} path={d.path} markerEnd={markerEnd} interactionWidth={16}
        style={{ stroke: d.color, strokeWidth: d.lit ? 2.8 : 1.8, strokeDasharray: d.feedback ? '6 4' : undefined,
          opacity: d.dim ? 0.1 : 1, transition: 'opacity 120ms' }} />
      {d.count > 1 && (
        <EdgeLabelRenderer>
          <div style={{
            position: 'absolute', transform: `translate(-50%, -50%) translate(${d.label[0]}px, ${d.label[1]}px)`,
            pointerEvents: 'none', background: d.color, color: '#fff', borderRadius: 8, minWidth: 16, height: 16,
            fontSize: 10, fontWeight: 700, lineHeight: '16px', textAlign: 'center', padding: '0 4px', opacity: d.dim ? 0.15 : 1,
          }}>{d.count}</div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const NODE_TYPES = { block: BlockNode, loop: LoopNode, label: LabelNode };
const EDGE_TYPES = { wire: WireEdge };

// -- the panel ----------------------------------------------------------------------------------------------

interface FamilyDie { name: string; configs?: Array<{ name: string; duties?: Array<{ name: string }> }> }

export default function SystemPanel() {
  const { t: tr } = useTranslation('common');
  const theme = useTheme();
  const [view, setView] = useState<ViewName>(() => (read(VIEW_KEY) === 'propeller_s1' ? 'propeller_s1' : 'modules'));
  const [showAll, setShowAll] = useState<boolean>(() => read(ALL_KEY) === '1');
  const [motor, setMotor] = useState<Motor | null>(savedMotor);
  const [dies, setDies] = useState<FamilyDie[]>([]);
  const [doc, setDoc] = useState<SgDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<Wire | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const box = useRef<HTMLDivElement | null>(null);
  const rf = useRef<ReactFlowInstance | null>(null);

  useEffect(() => {
    let live = true;
    fetchFamilyTree().then((t) => { if (live) setDies(((t?.dies ?? []) as FamilyDie[]).filter((d) => d?.name)); }).catch(() => { /* the picker stays empty */ });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    setLoading(true);
    const qs = new URLSearchParams({ view });
    if (motor) { qs.set('die', motor.die); qs.set('config', motor.config); if (motor.duty) qs.set('duty', motor.duty); }
    fetch(`${API.replace(/\/$/, '')}/api/system/graph?${qs.toString()}`, { cache: 'no-store', signal: ctl.signal })
      .then(async (r) => {
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(formatApiErrorDetail((j as { detail?: unknown }).detail) || `HTTP ${r.status}`);
        setDoc(j as SgDoc);
        setError(null);
      })
      .catch((e: unknown) => { if ((e as Error)?.name !== 'AbortError') setError(String((e as Error)?.message ?? e)); })
      .finally(() => setLoading(false));
    return () => ctl.abort();
  }, [view, motor, tick]);

  const onView = useCallback((_: unknown, v: ViewName | null) => {
    if (!v) return;
    setView(v);
    write(VIEW_KEY, v);
  }, []);
  const pick = useCallback((m: Motor | null) => {
    setMotor(m);
    write(MOTOR_KEY, JSON.stringify(m));
  }, []);

  const graph = useMemo(() => (doc ? build(doc, showAll, tr, !!motor) : null), [doc, showAll, tr, motor]);

  // fit to width when a graph (re)appears
  const fit = useCallback(() => {
    const inst = rf.current, el = box.current;
    if (!inst || !el || !graph) return;
    const L = graph.layout;
    // fit to width; when that would push the graph out of view, fit the height as well
    const zw = (el.clientWidth - 48) / L.width, zh = (el.clientHeight - 80) / L.height;
    const z = Math.min(1.1, Math.max(0.05, Math.min(zw, Math.max(zh, 0.3))));
    const h = L.height * z;
    inst.setViewport({ x: 24 - L.left * z, y: Math.max(64, (el.clientHeight - h) / 2) - L.top * z, zoom: z });
  }, [graph]);
  useEffect(() => { fit(); }, [fit]);

  const lit = useMemo(() => (graph && focus ? neighbours(graph.view, focus) : null), [graph, focus]);
  const nodes = useMemo(() => (graph ? graph.nodes.map((n) => (
    n.type === 'block' && lit ? { ...n, data: { ...n.data, dim: !lit.nodes.has(n.id) } } : n)) : []), [graph, lit]);
  const edges = useMemo(() => (graph ? graph.edges.map((e) => (
    lit ? { ...e, data: { ...e.data!, dim: !lit.wires.has(e.id), lit: lit.wires.has(e.id) } } : e)) : []), [graph, lit]);
  const types = useMemo(() => {
    if (!graph) return [] as string[];
    const t = new Set<string>(graph.view.wires.map((w) => w.type));
    for (const cs of Object.values(graph.view.chips)) for (const c of cs) t.add(c.type);
    return [...t];
  }, [graph]);

  const dieObj = dies.find((d) => d.name === motor?.die);
  const cfgObj = dieObj?.configs?.find((c) => c.name === motor?.config);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ px: 2, py: 1, borderBottom: '1px solid', borderColor: 'divider', flexWrap: 'wrap', rowGap: 1 }}>
        <ToggleButtonGroup size="small" exclusive value={view} onChange={onView}>
          <ToggleButton value="modules">{tr('system.view.modules')}</ToggleButton>
          <ToggleButton value="propeller_s1">{tr('system.view.propellerS1')}</ToggleButton>
        </ToggleButtonGroup>
        <Tooltip title={tr('system.hintTip')}>
          <Typography variant="body2" sx={{ color: 'text.secondary', cursor: 'help' }}>{tr('system.hint')}</Typography>
        </Tooltip>
        <Box sx={{ flexGrow: 1 }} />
        <Tooltip title={tr('system.showAllTip')}>
          <FormControlLabel sx={{ mr: 0 }} control={<Switch size="small" checked={showAll}
            onChange={(_, v) => { setShowAll(v); write(ALL_KEY, v ? '1' : '0'); }} />}
            label={<Typography variant="body2">{tr('system.showAll')}</Typography>} />
        </Tooltip>
        <Tooltip title={tr('system.pickTip')}>
          <Stack direction="row" spacing={1}>
            <TextField select size="small" label={tr('system.pick.die')} sx={{ minWidth: 150, ...PICK_SX }} value={motor?.die ?? ''}
              onChange={(e) => {
                const d = dies.find((x) => x.name === e.target.value);
                const c = d?.configs?.[0];
                pick(d && c ? { die: d.name, config: c.name, duty: '' } : null);
              }}>
              <MenuItem value=""><em>{tr('system.pick.none')}</em></MenuItem>
              {dies.map((d) => <MenuItem key={d.name} value={d.name}>{d.name}</MenuItem>)}
            </TextField>
            <TextField select size="small" label={tr('system.pick.config')} sx={{ minWidth: 100, ...PICK_SX }} value={motor?.config ?? ''}
              disabled={!dieObj} onChange={(e) => motor && pick({ ...motor, config: String(e.target.value), duty: '' })}>
              {(dieObj?.configs ?? []).map((c) => <MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}
            </TextField>
            <TextField select size="small" label={tr('system.pick.duty')} sx={{ minWidth: 120, ...PICK_SX }} value={motor?.duty ?? ''}
              disabled={!cfgObj} onChange={(e) => motor && pick({ ...motor, duty: String(e.target.value) })}>
              <MenuItem value=""><em>{tr('system.pick.anyDuty')}</em></MenuItem>
              {(cfgObj?.duties ?? []).map((d) => <MenuItem key={d.name} value={d.name}>{d.name}</MenuItem>)}
            </TextField>
          </Stack>
        </Tooltip>
        {loading && <CircularProgress size={16} />}
        <Tooltip title={tr('system.refresh')}>
          <IconButton size="small" onClick={() => setTick((x) => x + 1)}><RefreshIcon fontSize="small" /></IconButton>
        </Tooltip>
      </Stack>

      {error && <Alert severity="error" sx={{ m: 2 }}>{tr('system.failed')}: {error}</Alert>}

      <Box ref={box} sx={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {graph && (
          <ReactFlow
            key={`${doc?.view}:${showAll}`}
            nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES}
            colorMode={theme.palette.mode === 'dark' ? 'dark' : 'light'}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
            onInit={(inst) => { rf.current = inst; fit(); }}
            minZoom={0.05} maxZoom={2}
            onNodeMouseEnter={(_, n) => { if (n.type === 'block') setFocus(n.id); }}
            onNodeMouseLeave={() => setFocus(null)}
            onEdgeMouseEnter={(_, e) => setHover((e.data as { wire?: Wire } | undefined)?.wire ?? null)}
            onEdgeMouseLeave={() => setHover(null)}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={24} />
            <Controls showInteractive={false} fitViewOptions={{ padding: 0.05 }} />
            <MiniMap pannable zoomable nodeStrokeWidth={2} />
          </ReactFlow>
        )}
        {doc && (
          <Paper elevation={2} sx={{ position: 'absolute', left: 12, top: 10, zIndex: 5, px: 1.25, py: 0.75, maxWidth: 'min(620px, 72%)' }}>
            <Stack direction="row" spacing={1.25} useFlexGap flexWrap="wrap" alignItems="center">
              {types.map((ty) => (
                <Stack key={ty} direction="row" spacing={0.5} alignItems="center">
                  <Box sx={{ width: 14, height: 3, bgcolor: typeColor(ty), borderRadius: 1 }} />
                  <Typography variant="caption" sx={{ fontSize: 10.5 }}>{ty}</Typography>
                </Stack>
              ))}
            </Stack>
            <Typography variant="caption" component="div" sx={{ mt: 0.5, minHeight: 16, color: hover ? 'text.primary' : 'text.secondary' }}>
              {hover ? wireLines(hover, tr) : tr('system.hoverWire')}
            </Typography>
          </Paper>
        )}
      </Box>
    </Box>
  );
}

// -- graph -> React Flow ------------------------------------------------------------------------------------

function itemLine(e: SgEdge): string {
  const vals = e.values ? ' = ' + Object.entries(e.values).map(([k, v]) => `${k} ${String(v)}`).join(', ') : '';
  const a = e.from.port ?? e.type, b = e.to.port ?? e.type;
  return `${a} → ${b} · ${e.type}${e.label ? ' · ' + e.label : ''}${vals}`;
}

function wireLines(w: Wire, tr: Tr): ReactNode {
  return (
    <>
      <div style={{ fontWeight: 600 }}>{w.from} {'→'} {w.to}{w.feedback ? ` · ${tr('system.feedback')}` : ''}</div>
      {w.items.map((e) => <div key={e.id}>{itemLine(e)}</div>)}
    </>
  );
}

function nodeTip(n: SgNode, title: string, badgeText: string, tr: Tr): ReactNode {
  const st = n.status;
  const ports = (xs: SgNode['inputs']) => xs.map((p) => p.port === p.type ? p.type : `${p.port}: ${p.type}`).join(', ');
  const values = st.values ? Object.entries(st.values).filter(([, v]) => v != null).map(([k, v]) => `${k} = ${String(v)}`).join(', ') : '';
  return (
    <Box sx={{ fontSize: 12 }}>
      <Box sx={{ fontWeight: 700 }}>{title}</Box>
      <Box>{n.summary}</Box>
      {n.source && <Box sx={{ fontFamily: 'monospace', mt: 0.5 }}>{`${n.source.function} · ${n.source.file}:${n.source.line}`}</Box>}
      {n.inputs.length > 0 && <Box>{tr('system.in')}: {ports(n.inputs)}</Box>}
      {n.outputs.length > 0 && <Box>{tr('system.out')}: {ports(n.outputs)}</Box>}
      {n.roadmap && <Box>{tr('system.roadmap')}</Box>}
      {badgeText && <Box sx={{ mt: 0.5 }}>{badgeText}{st.summary ? ` · ${st.summary}` : ''}{values ? ` · ${values}` : ''}</Box>}
    </Box>
  );
}

function loopTip(l: SgLoop, tr: Tr): ReactNode {
  const d = l.detail ?? {};
  return (
    <Box sx={{ fontSize: 12 }}>
      <Box sx={{ fontWeight: 700 }}>{l.title}</Box>
      <Box>{tr(`system.loopKind.${l.kind}`)}{loopBadge(l) ? ` · ${loopBadge(l)}` : ''}</Box>
      {typeof d.what === 'string' && <Box>{d.what}</Box>}
      {l.source && <Box sx={{ fontFamily: 'monospace', mt: 0.5 }}>{`${l.source.function} · ${l.source.file}:${l.source.line}`}</Box>}
    </Box>
  );
}

interface Built { nodes: Node[]; edges: Edge[]; layout: Layout; view: GraphView }

function build(doc: SgDoc, showAll: boolean, tr: Tr, hasMotor: boolean): Built {
  const view = buildView(doc, showAll);
  const counts = chipCounts(doc, view);
  const heights: Record<string, number> = {};
  for (const n of doc.nodes) heights[n.id] = blockHeight(n, counts[n.id] ?? 0);
  const lay = layoutGraph(doc, { heights, lanes: laneCounts(doc.loops, view.wires) });
  const { routes, top, height } = routeWires(lay, doc.loops, view.wires);
  const layout = { ...lay, top, height };

  const title = (n: SgNode) => tr(`system.nodes.${n.id}`, { defaultValue: n.title });
  const byId = Object.fromEntries(doc.nodes.map((n) => [n.id, n]));
  const color: Record<string, string> = {};
  doc.loops.forEach((l, i) => { color[l.id] = LOOP_COLORS[i % LOOP_COLORS.length]; });
  const rings: Record<string, string[]> = {};
  for (const id of lay.unboxed) {
    const l = doc.loops.find((x) => x.id === id)!;
    for (const n of l.nodes) (rings[n] ||= []).push(color[id]);
  }
  const nodes: Node[] = [];
  for (const l of doc.loops) {
    const b = lay.loops[l.id];
    if (!b) continue;
    nodes.push({
      id: `loop:${l.id}`, type: 'loop', position: { x: b.x, y: b.y }, width: b.w, height: b.h, zIndex: -1,
      selectable: false, draggable: false, focusable: false,
      data: { title: tr(`system.loops.${l.id}`, { defaultValue: l.title }), badge: loopBadge(l), color: color[l.id], tip: loopTip(l, tr) },
    });
  }
  if (lay.inputs) {
    nodes.push({ id: 'label:inputs', type: 'label', position: { x: lay.inputs.x, y: lay.inputs.y }, selectable: false, draggable: false,
      focusable: false, data: { text: tr('system.inputs') } });
  }
  for (const n of doc.nodes) {
    const b = lay.nodes[n.id];
    if (!b) continue;
    const t = title(n);
    const badge = badgeOf(n.status, hasMotor);
    const badgeText = badge.kind === 'norun' ? tr('system.noRun') : badge.kind === 'state' ? tr(`system.state.${badge.state}`) : '';
    const chips: ChipView[] = (view.chips[n.id] ?? []).map((c) => ({
      chip: c,
      text: c.source ? title(byId[c.source]) : tr(`system.chips.${(c.label ?? c.type).replace(/\s+/g, '_')}`, { defaultValue: c.label ?? c.type }),
      tip: c.items.map(itemLine).join('\n'),
    }));
    nodes.push({
      id: n.id, type: 'block', position: { x: b.x, y: b.y }, width: b.w, height: b.h, draggable: false,
      data: {
        node: n, title: t, tip: nodeTip(n, t, badgeText, tr), badge, badgeText, noRunTip: tr('system.noRunTip'),
        chips: chips.map((c) => ({ ...c, tip: c.tip })), dim: false, rings: rings[n.id] ?? [],
        dots: n.context ? [...new Set(n.outputs.map((p) => p.type))].map(typeColor) : [],
      } satisfies BlockData,
    });
  }
  const edges: Edge[] = [];
  for (const w of view.wires) {
    const r = routes[w.id];
    if (!r) continue;
    const c = typeColor(w.type);
    edges.push({
      id: w.id, source: w.from, target: w.to, type: 'wire', interactionWidth: 16,
      markerEnd: { type: MarkerType.ArrowClosed, color: c, width: 14, height: 14 },
      data: { path: roundedPath(r.points), color: c, feedback: w.feedback, dim: false, lit: false, count: w.count, label: r.label, wire: w } satisfies WireData,
    });
  }
  return { nodes, edges, layout, view };
}
