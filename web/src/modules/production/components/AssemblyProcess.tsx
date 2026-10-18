/**
 * The Assembly tab: the assembly PROCESS as a graph (owner 2026-10-17: "это не процесс сборки").
 * Steps run left to right; the stator and rotor branches run in parallel and meet at the motor assembly.
 * Operations are blue, checks are amber.  A click on a step opens it below (parts, note); editors add, change and
 * delete steps (PUT /assembly_process, audited).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import ProcessView from './ProcessView';
import {
  Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Paper, TextField, Typography,
} from '@mui/material';
import { Background, Handle, Position, ReactFlow, ReactFlowProvider } from '@xyflow/react';
import type { Edge, Node, NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from '@dagrejs/dagre';
import { useTranslation } from 'react-i18next';
import { useContentLang } from './BilingualText';
import { displayText } from '../helpers';
import { downloadFile, openFileInNewWindow, productionApi } from '../api';
import type { AssemblyStep, Bilingual, DocumentRec, Part, ProcessSection, ProdCtx } from '../types';

const W = 230;
const H = 74;
const COLORS = { operation: { bg: 'rgba(59,130,246,0.08)', border: '#3b82f6' }, check: { bg: 'rgba(245,158,11,0.10)', border: '#f59e0b' } };

interface StepNodeData { step: AssemblyStep; title: string; selected: boolean; [key: string]: unknown }

const StepNode: React.FC<NodeProps<Node<StepNodeData>>> = ({ data }) => {
  const c = COLORS[data.step.kind];
  return (
    <Box sx={{ width: W, height: H, boxSizing: 'border-box', px: 1.2, py: 0.8, borderRadius: 1.5, cursor: 'pointer',
               bgcolor: c.bg, border: `${data.selected ? 2.5 : 1.5}px solid ${c.border}`,
               boxShadow: data.selected ? `0 0 0 3px ${c.border}33` : 'none', display: 'flex', gap: 1 }}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Box sx={{ minWidth: 26, height: 26, borderRadius: '50%', bgcolor: c.border, color: '#fff', fontSize: 12.5,
                 fontWeight: 800, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{data.step.n}</Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-0)', lineHeight: 1.25,
                          display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
          {data.title}
        </Typography>
        <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', mt: 0.3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {(data.step.parts_resolved ?? []).map((p) => p.name.en || p.name.zh).join(' · ')}
        </Typography>
      </Box>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </Box>
  );
};
const nodeTypes = { step: StepNode };

function layout(steps: AssemblyStep[], titleOf: (s: AssemblyStep) => string, selected: string | null) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 26, ranksep: 46 });
  g.setDefaultEdgeLabel(() => ({}));
  steps.forEach((s) => g.setNode(s.id, { width: W, height: H }));
  const edges: Edge[] = [];
  steps.forEach((s) => s.after.forEach((a) => {
    g.setEdge(a, s.id);
    edges.push({ id: `${a}-${s.id}`, source: a, target: s.id, type: 'smoothstep', style: { stroke: '#94a3b8', strokeWidth: 1.5 } });
  }));
  dagre.layout(g);
  const nodes: Array<Node<StepNodeData>> = steps.map((s) => {
    const p = g.node(s.id);
    return { id: s.id, type: 'step', position: { x: p.x - W / 2, y: p.y - H / 2 },
             data: { step: s, title: titleOf(s), selected: s.id === selected } };
  });
  return { nodes, edges };
}

const emptyBi: Bilingual = { en: '', zh: '', src_lang: 'en', status: { en: 'confirmed', zh: 'confirmed' } } as Bilingual;

const StepDialog: React.FC<{ open: boolean; step: AssemblyStep | null; steps: AssemblyStep[]; parts: Part[]; sections: ProcessSection[];
  onClose: () => void; onSave: (s: AssemblyStep) => void }> = ({ open, step, steps, parts, sections, onClose, onSave }) => {
  const { t } = useTranslation('production');
  const [d, setD] = useState<AssemblyStep | null>(step);
  useEffect(() => setD(step), [step]);
  if (!d) return null;
  const set = (patch: Partial<AssemblyStep>) => setD({ ...d, ...patch });
  const leaf = parts.filter((p) => p.kind !== 'assembly');
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: 15 }}>{t('process.editStep', 'Step')} {d.n}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: '8px !important' }}>
        <TextField size="small" label="English" value={d.title.en} onChange={(e) => set({ title: { ...d.title, en: e.target.value } })} />
        <TextField size="small" label="中文" value={d.title.zh} onChange={(e) => set({ title: { ...d.title, zh: e.target.value } })} />
        <TextField select size="small" label={t('process.kind', 'Kind')} value={d.kind} onChange={(e) => set({ kind: e.target.value as AssemblyStep['kind'] })}>
          <MenuItem value="operation">{t('process.operation', 'Operation')}</MenuItem>
          <MenuItem value="check">{t('process.check', 'Check')}</MenuItem>
        </TextField>
        <TextField select size="small" label={t('process.after', 'After steps')} SelectProps={{ multiple: true }} value={d.after}
          onChange={(e) => set({ after: (typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value) as string[] })}>
          {steps.filter((s) => s.id !== d.id).map((s) => <MenuItem key={s.id} value={s.id}>{s.n}. {s.title.en || s.title.zh}</MenuItem>)}
        </TextField>
        <TextField select size="small" label={t('process.parts', 'Parts')} SelectProps={{ multiple: true }} value={d.parts.filter((x) => leaf.some((p) => p.id === x))}
          onChange={(e) => set({ parts: (typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value) as string[] })}>
          {leaf.map((p) => <MenuItem key={p.id} value={p.id}>{p.name.en || p.name.zh}</MenuItem>)}
        </TextField>
        {/* the process sections this step shows and the process document prints under it (empty = by title keywords) */}
        <TextField select size="small" label={t('process.stepSectionsPick', 'Process sections')} SelectProps={{ multiple: true }}
          value={(d.sections ?? []).filter((x) => sections.some((s) => s.id === x))}
          onChange={(e) => set({ sections: (typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value) as string[] })}>
          {sections.map((s) => <MenuItem key={s.id} value={s.id}>{s.title.en || s.title.zh}</MenuItem>)}
        </TextField>
        <TextField size="small" multiline minRows={2} label={t('process.noteEn', 'Note (English)')} value={d.note?.en ?? ''}
          onChange={(e) => set({ note: { ...(d.note ?? emptyBi), en: e.target.value } })} />
        <TextField size="small" multiline minRows={2} label={t('process.noteZh', '备注（中文）')} value={d.note?.zh ?? ''}
          onChange={(e) => set({ note: { ...(d.note ?? emptyBi), zh: e.target.value } })} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ textTransform: 'none' }}>{t('cancel')}</Button>
        <Button variant="contained" onClick={() => onSave(d)} sx={{ textTransform: 'none' }}>{t('save')}</Button>
      </DialogActions>
    </Dialog>
  );
};

export const AssemblyProcess: React.FC<{ ctx: ProdCtx; parts: Part[]; sections?: ProcessSection[]; documents?: DocumentRec[] }> = ({ ctx, parts, sections = [], documents = [] }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [steps, setSteps] = useState<AssemblyStep[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [edit, setEdit] = useState<AssemblyStep | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    productionApi.assemblyProcess(ctx.die, ctx.config).then((v) => setSteps(v.steps)).catch((e) => setErr(String(e)));
  }, [ctx.die, ctx.config]);
  useEffect(load, [load]);

  const titleOf = useCallback((s: AssemblyStep) => displayText(s.title, ui).text, [ui]);
  const { nodes, edges } = useMemo(() => layout(steps, titleOf, sel), [steps, titleOf, sel]);
  const cur = steps.find((s) => s.id === sel) ?? null;

  const save = async (next: AssemblyStep[]) => {
    try {
      const strip = next.map(({ parts_resolved: _r, ...s }) => s);    // eslint-disable-line @typescript-eslint/no-unused-vars
      const v = await productionApi.saveAssemblyProcess(ctx.die, ctx.config, strip as AssemblyStep[]);
      setSteps(v.steps); setErr(null);
    } catch (e) { setErr(String(e)); }
  };
  const addAfter = () => {
    const n = steps.length + 1;
    let k = n; while (steps.some((s) => s.id === `s${k}`)) k += 1;
    setEdit({ id: `s${k}`, n, title: { ...emptyBi }, kind: 'operation', parts: [], after: sel ? [sel] : [], note: null });
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <Chip size="small" label={t('process.operation', 'Operation')} sx={{ bgcolor: COLORS.operation.bg, border: `1px solid ${COLORS.operation.border}` }} />
        <Chip size="small" label={t('process.check', 'Check')} sx={{ bgcolor: COLORS.check.bg, border: `1px solid ${COLORS.check.border}` }} />
        {ctx.canEdit && <Button size="small" variant="outlined" onClick={addAfter} sx={{ textTransform: 'none' }}>+ {t('process.addStep', 'Add step')}</Button>}
        {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      </Box>
      <Paper sx={{ height: 420, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'hidden' }}>
        <ReactFlowProvider>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.2} maxZoom={1.6}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
            onNodeClick={(_, n) => setSel(n.id === sel ? null : n.id)}>
            <Background gap={18} size={1} />
          </ReactFlow>
        </ReactFlowProvider>
      </Paper>
      {cur && (
        <Paper sx={{ p: 2, bgcolor: 'var(--panel)', border: `1px solid ${COLORS[cur.kind].border}`, borderRadius: 1.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <Typography sx={{ fontSize: 15, fontWeight: 800 }}>{cur.n}. {titleOf(cur)}</Typography>
            <Chip size="small" label={t(`process.${cur.kind}`, cur.kind)} sx={{ bgcolor: COLORS[cur.kind].bg }} />
            {ctx.canEdit && (
              <>
                <Button size="small" onClick={() => setEdit(cur)} sx={{ textTransform: 'none', ml: 'auto' }}>{t('process.edit', 'Edit')}</Button>
                <Button size="small" color="error" sx={{ textTransform: 'none' }}
                  onClick={() => { const rest = steps.filter((s) => s.id !== cur.id).map((s) => ({ ...s, after: s.after.filter((a) => a !== cur.id) }));
                                   setSel(null); void save(rest); }}>{t('process.delete', 'Delete')}</Button>
              </>
            )}
          </Box>
          {cur.note && displayText(cur.note, ui).text && (
            <Typography sx={{ fontSize: 13, color: 'var(--text-1)', mb: 1, whiteSpace: 'pre-wrap' }}>{displayText(cur.note, ui).text}</Typography>
          )}
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mb: 0.5 }}>{t('process.parts', 'Parts')}</Typography>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {(cur.parts_resolved ?? []).length === 0
              ? <Typography sx={{ fontSize: 12.5, color: 'var(--text-4)' }}>—</Typography>
              : (cur.parts_resolved ?? []).map((p) => <Chip key={p.id} size="small" label={p.name.en || p.name.zh} />)}
          </Box>
          {/* the drawings and documents of this step's parts (owner 2026-10-18: the magnet layout drawing belongs here); a click opens it */}
          {(() => {
            const ids = new Set((cur.parts_resolved ?? []).map((p) => p.id));
            const docs = documents.filter((d) => d.part_id && ids.has(d.part_id) && !d.name.startsWith('[superseded'));
            return docs.length > 0 && (
              <Box sx={{ mt: 1.25 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mb: 0.5 }}>{t('process.stepDocs', 'Drawings and documents')}</Typography>
                <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                  {docs.map((d) => {
                    const ext = (d.name.split('.').pop() || '').toLowerCase();
                    const viewable = ['pdf', 'png', 'jpg', 'jpeg'].includes(ext);
                    return <Chip key={d.id} size="small" variant="outlined" label={d.name} clickable
                      onClick={() => void (viewable ? openFileInNewWindow(ctx.die, ctx.config, d.file_id, ext === 'jpeg' ? 'jpg' : ext)
                        : downloadFile(ctx.die, ctx.config, d.file_id, d.name)).catch((e) => setErr(String(e)))} />;
                  })}
                </Box>
              </Box>
            );
          })()}
          {relatedSections(cur, sections).length > 0 && (
            <Box sx={{ mt: 1.5 }}>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mb: 0.5 }}>{t('process.stepSections', 'Process for this step')}</Typography>
              <ProcessView sections={relatedSections(cur, sections)} ctx={ctx} readOnly numberPrefix={String(cur.n || (steps.findIndex((s) => s.id === cur.id) + 1))} />
            </Box>
          )}
        </Paper>
      )}
      {/* the whole process document is generated from these steps and lives in Documents (owner 2026-10-18) */}
      <StepDialog open={!!edit} step={edit} steps={steps} parts={parts} sections={sections} onClose={() => setEdit(null)}
        onSave={(s) => {
          const exists = steps.some((x) => x.id === s.id);
          const next = exists ? steps.map((x) => (x.id === s.id ? s : x)) : [...steps, s];
          setEdit(null); setSel(s.id); void save(next);
        }} />
    </Box>
  );
};

/** Which process-document sections belong to a step: the step's own `sections` ids when it names them, else the
 *  default steps' keywords matched against the section titles (owner 2026-10-18: a click on a step shows its process). */
const STEP_KEYWORDS: Record<string, string[]> = {
  s1: ['winding', 'coil'], s2: ['winding', 'insulation'], s3: ['pre-power', 'inspection'], s4: ['ring'],
  s5: ['magnet'], s6: ['magnet'], s7: ['shaft', 'fits'], s8: ['balance'], s9: ['assembly sequence', 'bearing', 'fits', 'bracket'],
  s10: ['phase plate', 'wiring'], s11: ['phase plate', 'wiring'], s12: ['pre-power', 'winding'], s13: ['pre-power'],
};

export function relatedSections(step: AssemblyStep, sections: ProcessSection[]): ProcessSection[] {
  const own = step.sections;
  if (own && own.length) return sections.filter((x) => own.includes(x.id));
  const keys = STEP_KEYWORDS[step.id] ?? [];
  if (!keys.length) return [];
  return sections.filter((x) => {
    const title = `${x.title?.en ?? ''} ${x.title?.zh ?? ''}`.toLowerCase();
    // whole words, like the server: "ring" is not in "bearing" or "wiring"
    return keys.some((k) => new RegExp(`\\b${k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(title));
  });
}

export default AssemblyProcess;
