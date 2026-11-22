// "How it works" (owner 2026-11-22): who signs with whom, how the money goes and how the goods go, in one simple picture.
// Money, goods and documents are three arrows of their own colour with a legend; under it the line of project steps with the current
// one highlighted.  The customer view is fixed (contractModel.CUSTOMER_SCHEME); the staff view of the full chain comes from the server.
import React, { useMemo } from 'react';
import { Box, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import type { ContractStep, SchemeData, SchemeEdge, SchemeNode, SchemeText } from './contractModel';

const W = 1000;
const H = 420;
const NODE_W = 250;
const NODE_H = 110;
export const ARROW = { money: '#16a34a', goods: '#ea580c', documents: '#64748b' } as const;

/** Break a label into lines of at most ``n`` characters (CJK text has no spaces: it is cut by length). */
function wrap(text: string, n: number): string[] {
  if (!text) return [];
  if (!text.includes(' ')) return text.match(new RegExp(`.{1,${Math.max(4, Math.round(n / 2))}}`, 'gu')) ?? [text];
  const out: string[] = [];
  let line = '';
  for (const word of text.split(/\s+/)) {
    if (line && (line + ' ' + word).length > n) { out.push(line); line = word; } else line = line ? `${line} ${word}` : word;
  }
  if (line) out.push(line);
  return out;
}

/** Where an arrow leaves box ``a`` towards box ``b``: the middle of the facing side, moved ``shift`` along that side (two
 *  arrows between one pair of boxes get separate ends; the point always stays on the side, inside its corners). */
export function anchor(a: SchemeNode, b: SchemeNode, shift = 0): [number, number] {
  const aw = a.w ?? NODE_W; const ah = a.h ?? NODE_H; const bw = b.w ?? NODE_W; const bh = b.h ?? NODE_H;
  const ax = a.x + aw / 2; const ay = a.y + ah / 2; const bx = b.x + bw / 2; const by = b.y + bh / 2;
  const clamp = (v: number, lo: number, hi: number) => (hi < lo ? (lo + hi) / 2 : Math.min(Math.max(v, lo), hi));   // a side under 24 px: its middle
  if (Math.abs(bx - ax) * ah > Math.abs(by - ay) * aw) return [bx > ax ? a.x + aw : a.x, clamp(ay + shift, a.y + 12, a.y + ah - 12)];
  return [clamp(ax + shift, a.x + 12, a.x + aw - 12), by > ay ? a.y + ah : a.y];
}

/** The along-the-side shift of every edge: edges between the same two boxes are spread 26 px apart, in a fixed order. */
export function edgeShifts(edges: readonly SchemeEdge[]): number[] {
  const groups = new Map<string, number[]>();
  edges.forEach((e, i) => { const k = [e.from, e.to].sort().join('\u0000'); groups.set(k, [...(groups.get(k) ?? []), i]); });
  const out = edges.map(() => 0);
  for (const idx of groups.values()) idx.forEach((i, j) => { out[i] = (j - (idx.length - 1) / 2) * 26; });
  return out;
}

/** The width a label line takes at 12.5 px (CJK about one em, Latin about 0.55 em): for its background. */
const lineWidth = (l: string) => [...l].reduce((w, ch) => w + (/[\u2E80-\u9FFF\uFF00-\uFFEF]/.test(ch) ? 12.5 : 6.9), 0);

const Edge: React.FC<{ e: SchemeEdge; nodes: Map<string, SchemeNode>; text: (t?: SchemeText) => string; id: string; shift: number; part: 'line' | 'label' }> = ({ e, nodes, text, id, shift, part }) => {
  const a = nodes.get(e.from); const b = nodes.get(e.to);
  if (!a || !b) return null;
  const [x1, y1] = anchor(a, b, shift); const [x2, y2] = anchor(b, a, shift);
  const mx = (x1 + x2) / 2; const my = (y1 + y2) / 2;
  const len = Math.hypot(x2 - x1, y2 - y1) || 1;
  const bend = e.bend ?? 0;
  const cx = mx - ((y2 - y1) / len) * bend; const cy = my + ((x2 - x1) / len) * bend;
  const colour = ARROW[e.kind];
  const lx = (mx + cx) / 2; const ly = (my + cy) / 2;
  const lines = wrap(text(e.label), 30);
  if (part === 'line') {
    return (
      <path data-kind={e.kind} d={`M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`} fill="none" stroke={colour} strokeWidth={e.kind === 'documents' ? 1.6 : 4}
        strokeDasharray={e.kind === 'documents' ? '6 5' : undefined} markerEnd={`url(#${id}-${e.kind})`} />
    );
  }
  return (
    <g data-kind={e.kind} data-part="label">
      {lines.length > 0 && (() => {                  // a background behind the label: the line never runs through its text
        const w = Math.max(...lines.map(lineWidth)) + 10; const h = lines.length * 15 + 6;
        return <rect x={lx - w / 2} y={ly - h / 2} width={w} height={h} rx={4} fill="var(--panel)" opacity={0.94} />;
      })()}
      {lines.map((l, i) => (
        <text key={i} x={lx} y={ly + (i - (lines.length - 1) / 2) * 15} textAnchor="middle" dominantBaseline="middle" fontSize={12.5}
          fontWeight={e.kind === 'documents' ? 400 : 700} fill={colour}>{l}</text>
      ))}
    </g>
  );
};

const ContractScheme: React.FC<{ data: SchemeData; step: ContractStep | null; steps: readonly ContractStep[]; translate: boolean }> = ({ data, step, steps, translate }) => {
  const { t, i18n } = useTranslation('contract');
  const zh = i18n.language.toLowerCase().startsWith('zh');
  // the customer view carries translation keys, the staff view EN / ZH pairs
  const text = (v?: SchemeText): string => (v === undefined ? '' : typeof v === 'string' ? (translate ? t(v) : v) : (zh ? v.zh || v.en : v.en || v.zh) || '');
  const nodes = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data]);
  const shifts = useMemo(() => edgeShifts(data.edges), [data]);
  const id = useMemo(() => `scheme${Math.random().toString(36).slice(2, 8)}`, []);
  return (
    <Box data-testid="contract-scheme" sx={{ border: '1px solid var(--line-soft)', borderRadius: 1.5, bgcolor: 'var(--panel)', p: 1.25 }}>
      <Box component="svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t('scheme.title')} sx={{ width: '100%', height: 'auto', display: 'block' }}>
        <defs>
          {(Object.keys(ARROW) as Array<keyof typeof ARROW>).map((k) => (
            <marker key={k} id={`${id}-${k}`} viewBox="0 0 10 10" refX={9} refY={5} markerWidth={k === 'documents' ? 9 : 6} markerHeight={k === 'documents' ? 9 : 6} orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={ARROW[k]} />
            </marker>
          ))}
        </defs>
        {data.nodes.map((n) => {
          const w = n.w ?? NODE_W; const h = n.h ?? NODE_H;
          const sub = wrap(text(n.sub), 34);
          return (
            <g key={n.id} data-node={n.id}>
              <rect x={n.x} y={n.y} width={w} height={h} rx={10} fill="var(--panel-2)" stroke="var(--line)" strokeWidth={1.2} />
              <text x={n.x + w / 2} y={n.y + 26} textAnchor="middle" fontSize={16} fontWeight={800} fill="var(--text-0)">{text(n.label)}</text>
              {sub.slice(0, 4).map((l, i) => <text key={i} x={n.x + w / 2} y={n.y + 50 + i * 16} textAnchor="middle" fontSize={12.5} fill="var(--text-2)">{l}</text>)}
            </g>
          );
        })}
        {/* every line first, then every label on top: no line ever runs through a label */}
        {data.edges.map((e, i) => <Edge key={`l${i}`} e={e} nodes={nodes} text={text} id={id} shift={shifts[i]} part="line" />)}
        {data.edges.map((e, i) => <Edge key={`t${i}`} e={e} nodes={nodes} text={text} id={id} shift={shifts[i]} part="label" />)}
      </Box>
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center', mt: 0.75 }} data-testid="contract-scheme-legend">
        {(Object.keys(ARROW) as Array<keyof typeof ARROW>).map((k) => (
          <Box key={k} sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <Box sx={{ width: 28, height: 0, borderTop: `${k === 'documents' ? 2 : 4}px ${k === 'documents' ? 'dashed' : 'solid'} ${ARROW[k]}` }} />
            <Typography sx={{ fontSize: 12.5, color: 'var(--text-1)' }}>{t(`scheme.legend.${k}`)}</Typography>
          </Box>
        ))}
      </Box>
      {!!data.notes?.length && (
        <Box component="ul" sx={{ m: 0, mt: 0.75, pl: 2.5 }}>
          {data.notes.map((n, i) => <Typography component="li" key={i} sx={{ fontSize: 13, color: 'var(--text-1)' }}>{text(n)}</Typography>)}
        </Box>
      )}
      <Box component="ol" data-testid="contract-steps" aria-label={t('steps.title')}
        sx={{ listStyle: 'none', display: 'flex', flexWrap: 'wrap', gap: 0.5, m: 0, mt: 1.25, p: 0, alignItems: 'center' }}>
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
