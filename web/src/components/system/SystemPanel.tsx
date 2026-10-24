/** Schemas tab - software modules and their contracts (Simulations > Schemas, owner 2026-10-14).  VIEW ONLY.
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
  Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, Link, MenuItem, Paper, Stack, Switch, TextField,
  ThemeProvider, ToggleButton, ToggleButtonGroup, Tooltip, Typography, createTheme,
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
import { MODULE_NODE_TOKENS, NODE_BORDER_WIDTH_PX, STATE_COLORS, formatTime, loopBadge, splitLoops, typeColor } from '../../lib/systemGraph';
import type { SgDoc, SgEdge, SgLoop, SgNode } from '../../lib/systemGraph';
import { badgeOf, buildView, chipCounts, conditionText, exitGroups, laneCounts, loopHead, loopText, neighbours } from '../../lib/systemGraphView';
import type { Badge, Chip, ExitGroup, LoopText, View as GraphView, Wire } from '../../lib/systemGraphView';
import { NODE_W, blockHeight } from '../../lib/systemGraphLayout';
import type { Layout } from '../../lib/systemGraphLayout';
import { buildWiring, roundedPath, summarize, validateWiring } from '../../lib/systemGraphRoute';
import type { Pt, Route, Violation } from '../../lib/systemGraphRoute';
import SystemComposer from './SystemComposer';
import { NARROW } from '../../lib/narrowLayout';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const VIEW_KEY = 'system.view';
const MODE_KEY = 'system.mode';
const ALL_KEY = 'system.showAll';
const MOTOR_KEY = 'system.motor';
type ViewName = 'modules' | 'propeller_s1';
// text-safe on white (>= 4.5:1)
const LOOP_COLORS = ['#b45309', '#0369a1', '#7e22ce', '#047857'];
const TONE_COLORS = { ok: '#047857', warn: '#b45309', fail: '#b91c1c' } as const;
// the graph is ALWAYS drawn in the light style, whatever the app theme is (owner 2026-10-14)
const LIGHT = createTheme({ palette: { mode: 'light', background: { default: '#ffffff', paper: '#ffffff' } } });

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
  node: SgNode; title: string; moduleLabel: string; stubLabel: string; tip: ReactNode; badge: Badge; badgeText: string; noRunTip: string;
  onSelect: (node: SgNode) => void;
  chips: ChipView[]; dim: boolean; rings: string[]; dots: string[]; condition: string;
}
interface LoopData extends Record<string, unknown> {
  text: LoopText; color: string; tip: ReactNode; exits: Array<{ tone: ExitGroup['tone']; label: string; tip: string }>;
}

const PICK_SX = { '& .MuiInputBase-root': { fontSize: 13, height: 32 }, '& .MuiInputLabel-root': { fontSize: 12 } } as const;
const hidden = { opacity: 0, pointerEvents: 'none' } as const;

function BlockNode({ data }: NodeProps<Node<BlockData>>) {
  const n = data.node;
  const token = MODULE_NODE_TOKENS[n.module_id] ?? MODULE_NODE_TOKENS.M11;
  const ring = data.rings.map((c, i) => `0 0 0 ${2 + i * 3}px ${c}`).join(', ');
  const compact = !!n.context;
  const b = data.badge;
  return (
    <Tooltip arrow placement="top" title={data.tip} enterDelay={250} slotProps={{ tooltip: { sx: { maxWidth: 420 } } }}>
      <Box sx={{
        width: NODE_W, height: '100%', boxSizing: 'border-box', borderRadius: 1, overflow: 'hidden',
        bgcolor: '#ffffff', border: `${NODE_BORDER_WIDTH_PX}px ${n.roadmap ? 'dashed' : 'solid'} ${token.border}`,
        borderLeft: `6px ${n.roadmap ? 'dashed' : 'solid'} ${token.accent}`, boxShadow: ring || undefined,
        display: 'flex', flexDirection: 'column',
      }} role="button" tabIndex={0} aria-label={`${data.moduleLabel}: ${data.title}`}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); data.onSelect(n); }
        }}>
        <Handle type="target" position={Position.Left} style={hidden} />
        <Box sx={{ minHeight: 18, px: 0.7, bgcolor: token.background, color: token.foreground, display: 'flex', alignItems: 'center' }}>
          <Typography variant="caption" noWrap sx={{ fontWeight: 700, fontSize: 9.5, lineHeight: 1.2, flex: 1 }}>{data.moduleLabel}</Typography>
          {n.roadmap && <Typography variant="caption" sx={{ fontWeight: 700, fontSize: 9, color: token.foreground }}>{data.stubLabel}</Typography>}
        </Box>
        <Box sx={{ px: 0.8, py: 0.35, flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 0.2 }}>
          <Stack direction="row" alignItems="center" spacing={0.6}>
            <Typography variant="body2" noWrap sx={{ color: '#111827', fontWeight: 700, lineHeight: 1.25, flex: 1 }}>{data.title}</Typography>
            {compact && data.dots.map((c) => <Box key={c} sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: c }} />)}
          </Stack>
        {!compact && (
          <Typography variant="caption" noWrap sx={{ color: '#334155', fontFamily: 'monospace', fontSize: 10.5 }}>
            {n.source?.function ?? n.capability ?? n.id}
          </Typography>
        )}
        {!compact && data.condition && (
          <Typography variant="caption" noWrap sx={{ fontSize: 10.5, fontWeight: 600, color: 'error.dark' }}>{data.condition}</Typography>
        )}
        {!compact && b.kind === 'norun' && (
          <Tooltip title={data.noRunTip} placement="bottom">
            <Stack direction="row" spacing={0.6} alignItems="center" sx={{ cursor: 'help' }}>
              <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: STATE_COLORS.unknown }} />
              <Typography variant="caption" sx={{ fontSize: 10.5, color: '#334155' }}>{data.badgeText}</Typography>
            </Stack>
          </Tooltip>
        )}
        {!compact && b.kind === 'state' && (
          <Stack direction="row" spacing={0.6} alignItems="center">
            <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: STATE_COLORS[b.state] }} />
            <Typography variant="caption" sx={{ fontSize: 10.5, color: '#334155' }}>{data.badgeText}</Typography>
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
        </Box>
        <Handle type="source" position={Position.Right} style={hidden} />
      </Box>
    </Tooltip>
  );
}

function LoopNode({ data, width, height }: NodeProps<Node<LoopData>>) {
  const t = data.text;
  return (
    <Box sx={{
      width: width ?? '100%', height: height ?? '100%', boxSizing: 'border-box', borderRadius: 2,
      border: `2px dashed ${data.color}`, bgcolor: `${data.color}10`, pointerEvents: 'none',
    }}>
      <Tooltip arrow placement="top" title={data.tip} slotProps={{ tooltip: { sx: { maxWidth: 520 } } }}>
        <Box sx={{ pointerEvents: 'auto', px: 1, pt: 0.4, cursor: 'help', color: data.color, lineHeight: 1.35 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Box sx={{ fontSize: 12, fontWeight: 700, whiteSpace: 'nowrap' }}>{'\u21bb'} {t.title}</Box>
            <Box sx={{ flex: 1 }} />
            {data.exits.map((x, i) => (
              <Tooltip key={i} title={x.tip} placement="bottom">
                <Box sx={{ fontSize: 10.5, fontWeight: 600, px: 0.8, borderRadius: 8, whiteSpace: 'nowrap', color: '#fff',
                  bgcolor: TONE_COLORS[x.tone] }}>{x.label}</Box>
              </Tooltip>
            ))}
          </Stack>
          {t.rule && <Box sx={{ fontSize: 11, color: 'text.primary', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.rule}</Box>}
          {t.steps && <Box sx={{ fontSize: 11, color: 'text.primary', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.steps}</Box>}
          {t.last && <Box sx={{ fontSize: 11, fontWeight: 600, color: 'text.primary', whiteSpace: 'nowrap' }}>{t.last}</Box>}
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
      <EdgeLabelRenderer>
        <div style={{
          position: 'absolute', transform: `translate(-50%, -50%) translate(${d.label[0]}px, ${d.label[1]}px)`,
          pointerEvents: 'none', background: '#ffffff', color: d.color, border: `1px solid ${d.color}`, borderRadius: 6,
          height: 14, fontSize: 9.5, fontWeight: 700, lineHeight: '12px', padding: '0 4px', whiteSpace: 'nowrap',
          opacity: d.dim ? 0.15 : 1,
        }}>{wireTag(d.wire)}</div>
      </EdgeLabelRenderer>
    </>
  );
}

/** The text drawn ON the wire: its data type(s), and the number of merged items when there are several. */
function wireTag(w: Wire): string {
  const types = [...new Set(w.items.map((e) => e.type))];
  const name = types.length ? types.join(' · ') : w.type;
  return w.count > 1 ? `${name} ×${w.count}` : name;
}

const NODE_TYPES = { block: BlockNode, loop: LoopNode, label: LabelNode };
const EDGE_TYPES = { wire: WireEdge };

// -- the panel ----------------------------------------------------------------------------------------------

interface FamilyDie { name: string; configs?: Array<{ name: string; duties?: Array<{ name: string }> }> }

export default function SystemPanel() {
  const { t: tr } = useTranslation('common');
  const [mode, setMode] = useState<'view' | 'compose'>(() => (read(MODE_KEY) === 'compose' ? 'compose' : 'view'));
  const [view, setView] = useState<ViewName>(() => (read(VIEW_KEY) === 'propeller_s1' ? 'propeller_s1' : 'modules'));
  const [showAll, setShowAll] = useState<boolean>(() => read(ALL_KEY) === '1');
  const [motor, setMotor] = useState<Motor | null>(savedMotor);
  const [dies, setDies] = useState<FamilyDie[]>([]);
  const [doc, setDoc] = useState<SgDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<Wire | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<SgNode | null>(null);
  const [tick, setTick] = useState(0);
  const box = useRef<HTMLDivElement | null>(null);
  const card = useRef<HTMLDivElement | null>(null);
  const rf = useRef<ReactFlowInstance | null>(null);

  const onMode = useCallback((_: unknown, m: 'view' | 'compose' | null) => {
    if (!m) return;
    setMode(m);
    write(MODE_KEY, m);
  }, []);

  useEffect(() => {
    let live = true;
    fetchFamilyTree().then((t) => { if (live) setDies(((t?.dies ?? []) as FamilyDie[]).filter((d) => d?.name)); }).catch(() => { /* the picker stays empty */ });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (mode !== 'view') return;
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
  }, [mode, view, motor, tick]);

  const onView = useCallback((_: unknown, v: ViewName | null) => {
    if (!v) return;
    setView(v);
    write(VIEW_KEY, v);
  }, []);
  const pick = useCallback((m: Motor | null) => {
    setMotor(m);
    write(MOTOR_KEY, JSON.stringify(m));
  }, []);

  const graph = useMemo(() => (doc ? build(doc, showAll, tr, !!motor, setSelectedNode) : null), [doc, showAll, tr, motor, setSelectedNode]);
  // dev only: the wiring check.  With "Show all wires" on the count is only reported.
  useEffect(() => {
    if (!graph || !import.meta.env.DEV || graph.violations.length === 0) return;
    console.warn(`[system graph] ${graph.violations.length} wiring violation(s)${showAll ? ' (show-all view: reported only)' : ''}`,
      summarize(graph.violations), graph.violations.slice(0, 20));
  }, [graph, showAll]);

  // fit to width when a graph (re)appears
  const fit = useCallback(() => {
    const inst = rf.current, el = box.current;
    if (!inst || !el || !graph) return;
    const L = graph.layout;
    // fit to width; when that would push the graph out of view, fit the height as well
    const topPad = (card.current?.offsetHeight ?? 0) + 22;              // keep the graph below the legend card
    const zw = (el.clientWidth - 48) / L.width, zh = (el.clientHeight - topPad - 24) / L.height;
    const z = Math.min(1.1, Math.max(0.05, Math.min(zw, Math.max(zh, 0.3))));
    const h = L.height * z;
    const vx = 24 - L.left * z, vy = topPad + Math.max(0, (el.clientHeight - topPad - 24 - h) / 2) - L.top * z;
    inst.setViewport({ x: vx, y: vy, zoom: z });
    if (import.meta.env.DEV && card.current) {              // dev only: the legend card is an obstacle too (it is drawn over the canvas)
      const c = { id: 'legend', box: { x: (12 - vx) / z, y: (10 - vy) / z, w: card.current.offsetWidth / z, h: card.current.offsetHeight / z } };
      const hits = validateWiring(L, graph.loops, graph.view.wires, graph.routes, { extra: [c], ringed: graph.ringed0 }).filter((v) => v.kind === 'extra');
      const blocks = Object.entries(L.nodes).filter(([, b]) => b.x < c.box.x + c.box.w && b.x + b.w > c.box.x && b.y < c.box.y + c.box.h && b.y + b.h > c.box.y);
      if (hits.length || blocks.length) console.warn('[system graph] the legend card covers part of the graph', hits.length, blocks.map(([id]) => id));
    }
  }, [graph]);
  useEffect(() => { fit(); }, [fit]);

  const lit = useMemo(() => (graph && focus ? neighbours(graph.view, focus) : null), [graph, focus]);
  const nodes = useMemo(() => (graph ? graph.nodes.map((n) => (
    n.type === 'block' && lit ? { ...n, data: { ...n.data, dim: !lit.nodes.has(n.id) } } : n)) : []), [graph, lit]);
  const edges = useMemo(() => (graph ? graph.edges.map((e) => (
    lit ? { ...e, data: { ...e.data!, dim: !lit.wires.has(e.id), lit: lit.wires.has(e.id) } } : e)) : []), [graph, lit]);

  const dieObj = dies.find((d) => d.name === motor?.die);
  const cfgObj = dieObj?.configs?.find((c) => c.name === motor?.config);

  return (
    <ThemeProvider theme={LIGHT}>
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, bgcolor: '#ffffff', color: '#111827' }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ px: 2, py: 1, borderBottom: '1px solid', borderColor: 'divider', flexWrap: 'wrap', rowGap: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>{tr('tabs.schemas')}</Typography>
        <ToggleButtonGroup size="small" exclusive value={mode} onChange={onMode}>
          <ToggleButton value="view">{tr('system.mode.view')}</ToggleButton>
          <ToggleButton value="compose">{tr('system.mode.compose')}</ToggleButton>
        </ToggleButtonGroup>
        {mode === 'view' && (
          <>
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
              <Stack direction="row" spacing={1} sx={{ [NARROW]: { flexWrap: 'wrap', rowGap: 1, maxWidth: '100%', '& .MuiTextField-root': { maxWidth: '100%' } } }}>
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
          </>
        )}
      </Stack>

      {mode === 'compose' ? (
        <SystemComposer />
      ) : (
        <>
          {error && <Alert severity="error" sx={{ m: 2 }}>{tr('system.failed')}: {error}</Alert>}

      <Box ref={box} sx={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {graph && (
          <ReactFlow
            key={`${doc?.view}:${showAll}`}
            nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES}
            colorMode="light"
            nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
            onInit={(inst) => { rf.current = inst; fit(); }}
            minZoom={0.05} maxZoom={2}
            onNodeMouseEnter={(_, n) => { if (n.type === 'block') setFocus(n.id); }}
            onNodeMouseLeave={() => setFocus(null)}
            onNodeClick={(_, n) => { if (n.type === 'block') setSelectedNode((n.data as BlockData).node); }}
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
          <Paper ref={card} elevation={2} sx={{ position: 'absolute', left: 12, top: 10, zIndex: 5, px: 1.25, py: 0.75, maxWidth: 'min(620px, 72%)' }}>
            <Typography variant="caption" component="div" sx={{ minHeight: 16, color: hover ? 'text.primary' : 'text.secondary' }}>
              {hover ? wireLines(hover, tr) : tr('system.hoverWire')}
            </Typography>
            {graph && graph.ringed.length > 0 && (
              <Box sx={{ mt: 0.75, pt: 0.5, borderTop: '1px solid', borderColor: 'divider' }}>
                {graph.ringed.map((r) => (
                  <Tooltip key={r.id} title={r.tip} placement="right">
                    <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'baseline', cursor: 'help' }}>
                      <Box sx={{ width: 9, height: 9, borderRadius: '50%', border: `2px solid ${r.color}`, flexShrink: 0, alignSelf: 'center' }} />
                      <Typography variant="caption" sx={{ fontWeight: 700, color: r.color }}>{'\u21bb'} {r.text.title}</Typography>
                      <Typography variant="caption" sx={{ color: 'text.secondary' }}>{[r.text.rule, r.text.last].filter(Boolean).join(' \u00b7 ')}</Typography>
                    </Box>
                  </Tooltip>
                ))}
              </Box>
            )}
          </Paper>
        )}
      </Box>
      <Dialog open={!!selectedNode} onClose={() => setSelectedNode(null)} fullWidth maxWidth="sm">
        {selectedNode && <>
          <DialogTitle>{selectedNode.module_id} · {moduleDisplayName(selectedNode, tr)}</DialogTitle>
          <DialogContent dividers>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>{tr(`system.nodes.${selectedNode.id}`, { defaultValue: selectedNode.title })}</Typography>
            <Typography variant="body2" sx={{ mb: 1.5 }}>{selectedNode.summary}</Typography>
            <Typography variant="subtitle2">{tr('system.publicApi')}</Typography>
            {selectedNode.public_api.length ? <Box component="ul" sx={{ mt: 0.5, pl: 2.5 }}>
              {selectedNode.public_api.map((api) => <li key={api}><Typography variant="body2" component="code">{api}</Typography></li>)}
            </Box> : <Typography variant="body2" color="text.secondary">{tr('system.publicApiNone')}</Typography>}
            {selectedNode.source && <Typography variant="caption" component="div" sx={{ mt: 1, fontFamily: 'monospace' }}>
              {selectedNode.source.file}:{selectedNode.source.line}
            </Typography>}
          </DialogContent>
          <DialogActions>
            <Link href={`https://github.com/eMotres/Motor_Ai_Simulation/blob/main/${selectedNode.documentation}`}
              target="_blank" rel="noreferrer" underline="hover" sx={{ mr: 1 }}>
              {tr('system.moduleDocs')}
            </Link>
            <Button onClick={() => setSelectedNode(null)}>{tr('system.close')}</Button>
          </DialogActions>
        </>}
      </Dialog>
        </>
      )}
    </Box>
    </ThemeProvider>
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
      <Box sx={{ fontWeight: 600 }}>{n.module_id} · {moduleDisplayName(n, tr)}</Box>
      <Box>{n.summary}</Box>
      {n.source && <Box sx={{ fontFamily: 'monospace', mt: 0.5 }}>{`${n.source.function} · ${n.source.file}:${n.source.line}`}</Box>}
      {n.inputs.length > 0 && <Box>{tr('system.in')}: {ports(n.inputs)}</Box>}
      {n.outputs.length > 0 && <Box>{tr('system.out')}: {ports(n.outputs)}</Box>}
      {n.roadmap && <Box>{tr('system.roadmap')}</Box>}
      {badgeText && <Box sx={{ mt: 0.5 }}>{badgeText}{st.summary ? ` · ${st.summary}` : ''}{values ? ` · ${values}` : ''}</Box>}
    </Box>
  );
}

function moduleDisplayName(n: SgNode, tr: Tr): string {
  return tr(`system.modules.${n.module_id}`, { defaultValue: n.module_name });
}

function loopTip(l: SgLoop, text: LoopText, tr: Tr): ReactNode {
  return (
    <Box sx={{ fontSize: 12 }}>
      <Box sx={{ fontWeight: 700 }}>{text.title}</Box>
      {text.rule && <Box>{text.rule}</Box>}
      {text.steps && <Box>{text.steps}</Box>}
      {text.last && <Box sx={{ fontWeight: 600 }}>{text.last}</Box>}
      {text.tip.map((x, i) => <Box key={i} sx={{ mt: 0.5 }}>{x}</Box>)}
      <Box sx={{ mt: 0.5, color: 'text.secondary' }}>{tr(`system.loopKind.${l.kind}`)}{loopBadge(l) ? ` \u00b7 ${loopBadge(l)}` : ''}</Box>
      {l.source && <Box sx={{ fontFamily: 'monospace', mt: 0.5 }}>{`${l.source.function} \u00b7 ${l.source.file}:${l.source.line}`}</Box>}
    </Box>
  );
}

/** The exit tags of a loop: the way on, the flagged ones, one red tag for everything that fails the point. */
function exitTags(l: SgLoop, tr: Tr): LoopData['exits'] {
  return exitGroups(l).map((g) => {
    const reasons = g.exits.map((e) => tr(`system.exit.${e.kind === 'warn' ? e.id + 'Warn' : e.id === 'runaway' && e.param ? 'runawayAbove' : e.id}`, { above: e.param?.above_C }));
    if (g.tone === 'fail') {
      return { tone: g.tone, label: `\u2717 ${tr('system.exit.failPoint')}: ${reasons.join(' \u00b7 ')}`, tip: reasons.map((r) => `${tr('system.exit.failPoint')}: ${r}`).join('\n') };
    }
    return { tone: g.tone, label: `${g.tone === 'ok' ? '\u2713' : '!'} ${reasons.join(' \u00b7 ')}`, tip: reasons.join('\n') };
  });
}

interface Built {
  nodes: Node[]; edges: Edge[]; layout: Layout; view: GraphView; violations: Violation[]; widened: number;
  routes: Record<string, Route>; loops: SgLoop[]; ringed0: string[];
  /** loops drawn as a ring (no box): described in the legend card */
  ringed: Array<{ id: string; color: string; text: LoopText; tip: ReactNode }>;
}

function build(doc: SgDoc, showAll: boolean, tr: Tr, hasMotor: boolean, onSelect: (node: SgNode) => void): Built {
  const view = buildView(doc, showAll);
  const counts = chipCounts(doc, view);
  const heights: Record<string, number> = {};
  for (const n of doc.nodes) heights[n.id] = blockHeight(n, counts[n.id] ?? 0);
  const ltext: Record<string, LoopText> = {};
  const heads: Record<string, number> = {};
  for (const l of doc.loops) { ltext[l.id] = loopText(l, tr); heads[l.id] = loopHead(ltext[l.id].lines); }
  // lay out, route and check: nothing is drawn through a block, a loop header or another wire's line
  const wiring = buildWiring(doc, view, { heights, lanes: laneCounts(doc.loops, view.wires), heads },
    { ringed: splitLoops(doc.loops).unboxed.map((l) => l.id) }, showAll ? 1 : 4);
  const lay = wiring.layout;
  const routes = wiring.routes;
  const layout = lay;

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
      data: { text: ltext[l.id], color: color[l.id], tip: loopTip(l, ltext[l.id], tr), exits: exitTags(l, tr) } satisfies LoopData,
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
        node: n, title: t, moduleLabel: `${n.module_id} · ${moduleDisplayName(n, tr)}`, stubLabel: tr('system.stub'),
        tip: nodeTip(n, t, badgeText, tr), badge, badgeText, noRunTip: tr('system.noRunTip'), onSelect,
        chips: chips.map((c) => ({ ...c, tip: c.tip })), dim: false, rings: rings[n.id] ?? [],
        dots: n.context ? [...new Set(n.outputs.map((p) => p.type))].map(typeColor) : [],
        condition: conditionText(n, tr),
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
  const ringed = lay.unboxed.map((id) => {
    const l = doc.loops.find((x) => x.id === id)!;
    return { id, color: color[id], text: ltext[id], tip: loopTip(l, ltext[id], tr) };
  });
  return {
    nodes, edges, layout, view, ringed, violations: wiring.violations, widened: wiring.widened,
    routes, loops: doc.loops, ringed0: lay.unboxed,
  };
}
