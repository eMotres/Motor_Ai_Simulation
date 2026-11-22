// "How it works" (owner 2026-11-22): who signs with whom, how the money goes and how the goods go - in the SAME visual
// language as the assembly process in Production (web/src/modules/production/components/AssemblyProcess.tsx): React Flow,
// equal cards on a dot grid, orthogonal (smoothstep) connectors with small arrowheads, one label chip per connector, a legend
// of chips.  Money, goods and documents keep their colour and line style.  Under it the line of project steps with the current
// one highlighted.  The layout is automatic (schemeLayout.ts): cards on a grid, ports spread along the facing sides.
import React, { useEffect, useMemo } from 'react';
import { Box, Button, Chip, Paper, Typography } from '@mui/material';
import {
  Background, BaseEdge, EdgeLabelRenderer, Handle, MarkerType, Panel, Position, ReactFlow, ReactFlowProvider,
  getSmoothStepPath, useNodesInitialized, useReactFlow, useStore,
} from '@xyflow/react';
import type { Edge, EdgeProps, Node, NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTranslation } from 'react-i18next';
import type { ContractStep, SchemeData, SchemeEdge, SchemeText } from './contractModel';
import { CARD_H, CARD_W, CHIP_MAX_W, route, type Port } from './schemeLayout';

export const ARROW = { money: '#16a34a', goods: '#ea580c', documents: '#64748b' } as const;
const LINE: Record<SchemeEdge['kind'], { width: number; dash?: string }> = {
  money: { width: 2 }, goods: { width: 2 }, documents: { width: 1.5, dash: '6 4' },
};
const POSITION: Record<Port['side'], Position> = { left: Position.Left, right: Position.Right, top: Position.Top, bottom: Position.Bottom };

interface CardData { title: string; sub: string; ports: Array<Port & { type: 'source' | 'target' }>; [key: string]: unknown }

/** A party: the card of the assembly process (equal size, tinted border, a two-line title, a grey subtitle). */
const Card: React.FC<NodeProps<Node<CardData>>> = ({ data }) => (
  <Box sx={{ width: CARD_W, height: CARD_H, boxSizing: 'border-box', px: 1.2, py: 0.9, borderRadius: 1.5,
             bgcolor: 'rgba(59,130,246,0.08)', border: '1.5px solid #3b82f6', display: 'flex', flexDirection: 'column', gap: 0.4 }}>
    {data.ports.map((p) => (
      <Handle key={p.handle} id={p.handle} type={p.type} position={POSITION[p.side]} isConnectable={false}
        style={{ opacity: 0, ...(p.side === 'left' || p.side === 'right' ? { top: `${p.frac * 100}%` } : { left: `${p.frac * 100}%` }) }} />
    ))}
    <Typography sx={{ fontSize: 12.5, fontWeight: 800, color: 'var(--text-0)', lineHeight: 1.25,
                      display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
      {data.title}
    </Typography>
    <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', lineHeight: 1.3,
                      display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
      {data.sub}
    </Typography>
  </Box>
);

interface LinkData { kind: SchemeEdge['kind']; label: string; centerX: number; centerY: number; labelX: number; labelY: number; [key: string]: unknown }

/** A relation: an orthogonal connector in its kind's colour and line style, a small arrowhead, its label on a chip. */
const Link: React.FC<EdgeProps<Edge<LinkData>>> = ({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }) => {
  const d = data as LinkData;
  const [path] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
                                     centerX: d.centerX, centerY: d.centerY, borderRadius: 6, offset: 14 });
  const colour = ARROW[d.kind];
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd}
        style={{ stroke: colour, strokeWidth: LINE[d.kind].width, strokeDasharray: LINE[d.kind].dash }} />
      {d.label && (
        <EdgeLabelRenderer>
          <Box className="nodrag nopan" data-kind={d.kind}
            sx={{ position: 'absolute', transform: `translate(-50%, -50%) translate(${d.labelX}px, ${d.labelY}px)`, maxWidth: CHIP_MAX_W,
                  px: 0.8, py: 0.3, borderRadius: 1, bgcolor: 'var(--panel)', border: `1px solid ${colour}`, color: colour,
                  fontSize: 11, fontWeight: d.kind === 'documents' ? 500 : 700, lineHeight: 1.25, textAlign: 'center',
                  whiteSpace: 'normal', pointerEvents: 'none' }}>
            {d.label}
          </Box>
        </EdgeLabelRenderer>
      )}
    </>
  );
};

const nodeTypes = { card: Card };
const edgeTypes = { link: Link };

/** Fit by default and again whenever the pane gets a real size (the same rule as the assembly process). */
const FitToScreen: React.FC<{ count: number; label: string }> = ({ count, label }) => {
  const rf = useReactFlow();
  const w = useStore((st) => st.width);
  const h = useStore((st) => st.height);
  const measured = useNodesInitialized();
  useEffect(() => {
    if (!w || !h || !measured) return undefined;
    const t = window.setTimeout(() => { void rf.fitView({ padding: 0.08 }); }, 30);
    return () => window.clearTimeout(t);
  }, [count, w, h, measured, rf]);
  return (
    <Panel position="top-right">
      <Button size="small" variant="outlined" onClick={() => void rf.fitView({ padding: 0.08, duration: 250 })}
        sx={{ textTransform: 'none', bgcolor: 'var(--panel)', fontSize: 12 }}>{label}</Button>
    </Panel>
  );
};

const ContractScheme: React.FC<{ data: SchemeData; step: ContractStep | null; steps: readonly ContractStep[]; translate: boolean }> = ({ data, step, steps, translate }) => {
  const { t, i18n } = useTranslation('contract');
  const zh = i18n.language.toLowerCase().startsWith('zh');
  // the built-in customer view carries translation keys, a stored scheme EN / ZH pairs
  const text = (v?: SchemeText): string => (v === undefined ? '' : typeof v === 'string' ? (translate ? t(v) : v) : (zh ? v.zh || v.en : v.en || v.zh) || '');
  const { nodes, edges } = useMemo(() => {
    const { placed, routed } = route(data);
    const portsOf = new Map<string, CardData['ports']>();
    routed.forEach((r) => {
      portsOf.set(r.edge.from, [...(portsOf.get(r.edge.from) ?? []), { ...r.source, type: 'source' as const }]);
      portsOf.set(r.edge.to, [...(portsOf.get(r.edge.to) ?? []), { ...r.target, type: 'target' as const }]);
    });
    const byId = new Map(data.nodes.map((n) => [n.id, n]));
    const ns: Array<Node<CardData>> = placed.map((p) => ({
      id: p.id, type: 'card', position: { x: p.x, y: p.y }, draggable: false, selectable: false,
      data: { title: text(byId.get(p.id)?.label), sub: text(byId.get(p.id)?.sub), ports: portsOf.get(p.id) ?? [] },
    }));
    const es: Array<Edge<LinkData>> = routed.map((r, i) => ({
      id: `e${i}`, type: 'link', source: r.edge.from, target: r.edge.to, sourceHandle: r.source.handle, targetHandle: r.target.handle,
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: ARROW[r.edge.kind] },
      data: { kind: r.edge.kind, label: text(r.edge.label), centerX: r.centerX, centerY: r.centerY, labelX: r.labelX, labelY: r.labelY },
    }));
    return { nodes: ns, edges: es };
  }, [data, zh, translate]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Box data-testid="contract-scheme" sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }} data-testid="contract-scheme-legend">
        {(Object.keys(ARROW) as Array<keyof typeof ARROW>).map((k) => (
          <Chip key={k} size="small" label={t(`scheme.legend.${k}`)}
            icon={<Box component="span" sx={{ width: 22, height: 0, ml: '8px !important', borderTop: `${LINE[k].width}px ${LINE[k].dash ? 'dashed' : 'solid'} ${ARROW[k]}` }} />}
            sx={{ bgcolor: 'var(--panel)', border: `1px solid ${ARROW[k]}`, color: 'var(--text-1)', fontSize: 12 }} />
        ))}
      </Box>
      <Paper role="img" aria-label={t('scheme.title')}
        sx={{ height: { xs: 360, md: 420 }, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'hidden' }}>
        <ReactFlowProvider>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} fitView minZoom={0.2} maxZoom={1.6}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}>
            <Background gap={18} size={1} />
            <FitToScreen count={nodes.length} label={t('scheme.fit')} />
          </ReactFlow>
        </ReactFlowProvider>
      </Paper>
      {!!data.notes?.length && (
        <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
          {data.notes.map((n, i) => <Typography component="li" key={i} sx={{ fontSize: 13, color: 'var(--text-1)' }}>{text(n)}</Typography>)}
        </Box>
      )}
      <Box component="ol" data-testid="contract-steps" aria-label={t('steps.title')}
        sx={{ listStyle: 'none', display: 'flex', flexWrap: 'wrap', gap: 0.5, m: 0, mt: 0.25, p: 0, alignItems: 'center' }}>
        {steps.map((s, i) => {
          const on = s === step;
          const done = step !== null && steps.indexOf(step) > i;
          return (
            <Box component="li" key={s} aria-current={on ? 'step' : undefined} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Box sx={{ px: 1.1, py: 0.4, borderRadius: 10, fontSize: 12.5, fontWeight: on ? 800 : 500, border: '1px solid',
                borderColor: on ? 'primary.main' : 'var(--line)', bgcolor: on ? 'primary.main' : done ? 'var(--panel-2)' : 'transparent',
                color: on ? 'primary.contrastText' : done ? 'var(--text-1)' : 'var(--text-2)' }}>{t(`steps.${s}`)}</Box>
              {i < steps.length - 1 && <Typography aria-hidden sx={{ color: 'var(--text-3)', fontSize: 13 }}>→</Typography>}
            </Box>
          );
        })}
      </Box>
    </Box>
  );
};

export default ContractScheme;
