/** System tab - how the simulations are chained (Simulations > System, owner 2026-10-14).  VIEW ONLY.
 *
 *  Blocks = modules (or, in the "Propeller S1" view, the steps of the S1 rating), wires coloured by the data
 *  type they carry, feedback loops drawn as a dashed group with their iteration limit and tolerance, a status
 *  badge and time per block when a run record exists.  The graph is the backend's (`GET /api/system/graph`,
 *  motor_ai_sim.system_graph); this file only draws it.  Pan and zoom only: nothing here edits or runs a model
 *  (stage 2 will compose graphs, stage 3 hands the JSON to agents - docs/SYSTEM_GRAPH.md).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Alert, Box, Chip, CircularProgress, IconButton, Paper, Stack, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
  useTheme,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow } from '@xyflow/react';
import type { Edge, Node, NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTranslation } from 'react-i18next';

import { formatApiErrorDetail } from '../../lib/apiErrorDetail';
import { activeDuty } from '../../lib/dutySettings';
import { STATE_COLORS, formatTime, kindColor, loopBadge, typeColor } from '../../lib/systemGraph';
import type { SgDoc, SgEdge, SgLoop, SgNode } from '../../lib/systemGraph';
import { NODE_H, NODE_W, layoutGraph } from '../../lib/systemGraphLayout';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const VIEW_KEY = 'system.view';
type View = 'modules' | 'propeller_s1';
const LOOP_COLORS = ['#f59e0b', '#0ea5e9', '#a855f7', '#10b981'];

function savedView(): View {
  try { return localStorage.getItem(VIEW_KEY) === 'propeller_s1' ? 'propeller_s1' : 'modules'; } catch { return 'modules'; }
}

// -- custom nodes -------------------------------------------------------------------------------------

interface BlockData extends Record<string, unknown> {
  node: SgNode; title: string; tip: ReactNode; stateLabel: string; rings: string[];
}
interface LoopData extends Record<string, unknown> { title: string; badge: string; color: string; tip: ReactNode }

function BlockNode({ data }: NodeProps<Node<BlockData>>) {
  const n = data.node;
  const st = n.status;
  const ring = data.rings.map((c, i) => `0 0 0 ${2 + i * 3}px ${c}`).join(', ');
  return (
    <Tooltip arrow placement="top" title={data.tip} enterDelay={250} slotProps={{ tooltip: { sx: { maxWidth: 420 } } }}>
      <Box sx={{
        width: NODE_W, height: NODE_H, boxSizing: 'border-box', px: 1, py: 0.5, borderRadius: 1,
        bgcolor: 'background.paper', border: '1px solid', borderColor: 'divider',
        borderLeft: `5px solid ${kindColor(n.kind)}`, boxShadow: ring || undefined,
        opacity: n.roadmap ? 0.6 : 1, display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
      }}>
        <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
        <Typography variant="body2" noWrap sx={{ fontWeight: 600, lineHeight: 1.25 }}>{data.title}</Typography>
        <Typography variant="caption" noWrap sx={{ color: 'text.secondary', fontFamily: 'monospace', fontSize: 10.5 }}>
          {n.source?.function ?? n.capability ?? n.id}
        </Typography>
        <Stack direction="row" spacing={0.6} alignItems="center">
          <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: STATE_COLORS[st.state] }} />
          <Typography variant="caption" sx={{ fontSize: 10.5, color: 'text.secondary' }}>{data.stateLabel}</Typography>
          {formatTime(st.time_s) && <Typography variant="caption" sx={{ fontSize: 10.5 }}>{formatTime(st.time_s)}</Typography>}
        </Stack>
        <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
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

const NODE_TYPES = { block: BlockNode, loop: LoopNode };

// -- the panel ----------------------------------------------------------------------------------------

export default function SystemPanel() {
  const { t: tr } = useTranslation('common');
  const theme = useTheme();
  const [view, setView] = useState<View>(savedView);
  const [doc, setDoc] = useState<SgDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<SgEdge | null>(null);
  const [tick, setTick] = useState(0);
  const duty = useMemo(() => activeDuty(), [tick]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const ctl = new AbortController();
    setLoading(true);
    const qs = new URLSearchParams({ view });
    if (duty) { qs.set('die', duty.die); qs.set('config', duty.config); qs.set('duty', duty.duty); }
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
  }, [view, duty, tick]);

  const onView = useCallback((_: unknown, v: View | null) => {
    if (!v) return;
    setView(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* a convenience */ }
  }, []);

  const flow = useMemo(() => (doc ? build(doc, tr) : null), [doc, tr]);
  const types = useMemo(() => (doc ? [...new Set(doc.edges.map((e) => e.type))] : []), [doc]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ px: 2, py: 1, borderBottom: '1px solid', borderColor: 'divider', flexWrap: 'wrap' }}>
        <ToggleButtonGroup size="small" exclusive value={view} onChange={onView}>
          <ToggleButton value="modules">{tr('system.view.modules')}</ToggleButton>
          <ToggleButton value="propeller_s1">{tr('system.view.propellerS1')}</ToggleButton>
        </ToggleButtonGroup>
        <Tooltip title={tr('system.hintTip')}>
          <Typography variant="body2" sx={{ color: 'text.secondary', cursor: 'help' }}>{tr('system.hint')}</Typography>
        </Tooltip>
        <Box sx={{ flexGrow: 1 }} />
        {doc && (
          <Tooltip title={tr(duty ? 'system.statusForTip' : 'system.noDutyTip')}>
            <Chip size="small" variant="outlined"
              label={duty ? tr('system.statusFor', { die: duty.die, config: duty.config, duty: duty.duty }) : tr('system.noDuty')} />
          </Tooltip>
        )}
        {loading && <CircularProgress size={16} />}
        <Tooltip title={tr('system.refresh')}>
          <IconButton size="small" onClick={() => setTick((x) => x + 1)}><RefreshIcon fontSize="small" /></IconButton>
        </Tooltip>
      </Stack>

      {error && <Alert severity="error" sx={{ m: 2 }}>{tr('system.failed')}: {error}</Alert>}

      <Box sx={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {flow && (
          <ReactFlow
            key={doc?.view}
            nodes={flow.nodes} edges={flow.edges} nodeTypes={NODE_TYPES}
            colorMode={theme.palette.mode === 'dark' ? 'dark' : 'light'}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
            fitView fitViewOptions={{ padding: 0.08 }} minZoom={0.05} maxZoom={2}
            onEdgeMouseEnter={(_, e) => setHover((e.data as { edge?: SgEdge } | undefined)?.edge ?? null)}
            onEdgeMouseLeave={() => setHover(null)}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={24} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeStrokeWidth={2} />
          </ReactFlow>
        )}
        {doc && (
          <Paper elevation={2} sx={{ position: 'absolute', left: 12, top: 10, zIndex: 5, px: 1.25, py: 0.75, maxWidth: 'min(560px, 70%)' }}>
            <Stack direction="row" spacing={1.25} useFlexGap flexWrap="wrap" alignItems="center">
              {types.map((ty) => (
                <Stack key={ty} direction="row" spacing={0.5} alignItems="center">
                  <Box sx={{ width: 14, height: 3, bgcolor: typeColor(ty), borderRadius: 1 }} />
                  <Typography variant="caption" sx={{ fontSize: 10.5 }}>{ty}</Typography>
                </Stack>
              ))}
            </Stack>
            <Typography variant="caption" sx={{ display: 'block', mt: 0.5, minHeight: 16, color: hover ? 'text.primary' : 'text.secondary' }}>
              {hover ? edgeLine(hover, tr) : tr('system.hoverWire')}
            </Typography>
          </Paper>
        )}
      </Box>
    </Box>
  );
}

// -- graph -> React Flow ------------------------------------------------------------------------------

type Tr = (k: string, o?: Record<string, unknown>) => string;

function edgeLine(e: SgEdge, tr: Tr): string {
  const a = `${e.from.node}${e.from.port ? '.' + e.from.port : ''}`;
  const b = `${e.to.node}${e.to.port ? '.' + e.to.port : ''}`;
  const vals = e.values ? ' · ' + Object.entries(e.values).map(([k, v]) => `${k} = ${String(v)}`).join(', ') : '';
  return `${a} → ${b} · ${e.type}${e.feedback ? ' · ' + tr('system.feedback') : ''}${e.label ? ' · ' + e.label : ''}${vals}`;
}

function nodeTip(n: SgNode, title: string, tr: Tr): ReactNode {
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
      <Box sx={{ mt: 0.5 }}>{tr(`system.state.${st.state}`)}{st.summary ? ` · ${st.summary}` : ''}{values ? ` · ${values}` : ''}</Box>
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

function build(doc: SgDoc, tr: Tr): { nodes: Node[]; edges: Edge[] } {
  const lay = layoutGraph(doc);
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
  for (const n of doc.nodes) {
    const b = lay.nodes[n.id];
    const title = tr(`system.nodes.${n.id}`, { defaultValue: n.title });
    nodes.push({
      id: n.id, type: 'block', position: { x: b.x, y: b.y }, width: NODE_W, height: NODE_H, draggable: false,
      data: { node: n, title, tip: nodeTip(n, title, tr), stateLabel: tr(`system.state.${n.status.state}`), rings: rings[n.id] ?? [] },
    });
  }
  const edges: Edge[] = doc.edges.map((e) => {
    const c = typeColor(e.type);
    return {
      id: e.id, source: e.from.node, target: e.to.node, type: 'default', animated: e.feedback,
      style: { stroke: c, strokeWidth: 1.8, strokeDasharray: e.feedback ? '6 4' : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color: c, width: 16, height: 16 },
      interactionWidth: 20, data: { edge: e },
    };
  });
  return { nodes, edges };
}
