import { useState } from 'react';
import { Box, ButtonBase, Menu, MenuItem, Typography } from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { SpecDraft } from '../../wizardState';
import { stepProgress, type StepState, type WizardStep } from './steps';
import { TOUCH } from './ui';

type T = (key: string, args?: Record<string, unknown>) => string;

/** The step marker: a check when done, a half ring when partly answered, the number otherwise (dashed when optional). */
function Marker({ index, state, current }: { index: number; state: StepState; current: boolean }) {
  const base = { width: 28, height: 28, flex: '0 0 28px', borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 600, boxSizing: 'border-box' } as const;
  if (current) return <Box aria-hidden component="span" sx={{ ...base, bgcolor: 'primary.main', color: 'primary.contrastText' }}>{index + 1}</Box>;
  if (state === 'done') return <Box aria-hidden component="span" sx={{ ...base, bgcolor: 'success.main', color: '#fff' }}><CheckIcon sx={{ fontSize: 16 }} /></Box>;
  if (state === 'partial') return <Box aria-hidden component="span" sx={{ ...base, border: '2px solid', borderColor: 'primary.main', color: 'primary.main', background: 'linear-gradient(90deg, var(--panel-2) 50%, transparent 50%)' }}>{index + 1}</Box>;
  return <Box aria-hidden component="span" sx={{ ...base, border: '2px', borderStyle: state === 'optional' ? 'dashed' : 'solid', borderColor: 'var(--line)', color: 'var(--text-3)' }}>{index + 1}</Box>;
}

/** Vertical stepper (desktop): each step with one line of what it covers and its state. */
export function StepList({ steps, draft, current, onSelect, tr }: { steps: WizardStep[]; draft: SpecDraft; current: number; onSelect: (i: number) => void; tr: T }) {
  return <Box component="nav" aria-label={tr('wizard.stepsNav')} sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
    {steps.map((step, i) => {
      const p = step.kind === 'review' ? null : stepProgress(step, draft);
      const state: StepState = p?.state ?? 'empty';
      const active = i === current;
      return <ButtonBase key={step.id} onClick={() => onSelect(i)} aria-current={active ? 'step' : undefined} aria-label={`${tr(step.title)}${p ? `, ${tr(`wizard.state.${state}`)}` : ''}`}
        sx={{ display: 'flex', alignItems: 'center', gap: 1.5, textAlign: 'left', justifyContent: 'flex-start', minHeight: 56, px: 1.5, py: 1, borderRadius: '10px', bgcolor: active ? 'var(--panel)' : 'transparent', boxShadow: active ? '0 1px 2px rgba(0,0,0,.12)' : 'none', color: 'var(--text-0)', '&:hover': { bgcolor: 'var(--panel)' }, '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main' } }}>
        <Marker index={i} state={state} current={active} />
        <Box component="span" sx={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
          <Typography component="span" sx={{ fontSize: 14, fontWeight: active ? 600 : 500, lineHeight: 1.3 }}>{tr(step.title)}</Typography>
          <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-3)', lineHeight: 1.3, overflowWrap: 'anywhere' }}>{tr(step.summary)}</Typography>
        </Box>
        {p && p.required > 0 && <Typography component="span" sx={{ fontSize: 11.5, color: 'var(--text-3)', fontVariantNumeric: 'tabular-nums' }}>{p.answered}/{p.required}</Typography>}
      </ButtonBase>;
    })}
  </Box>;
}

/** The phone layout: the stepper collapses to one bar - the current step, a segment per step, a menu to jump. */
export function StepBar({ steps, draft, current, onSelect, tr }: { steps: WizardStep[]; draft: SpecDraft; current: number; onSelect: (i: number) => void; tr: T }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const step = steps[current];
  const fieldSteps = steps.filter((s) => s.kind !== 'review').length;
  const label = step.kind === 'review' ? tr(step.title) : tr('wizard.stepOf', { step: current + 1, total: fieldSteps });
  return <Box sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '12px', overflow: 'hidden' }}>
    <ButtonBase onClick={(e) => setAnchor(e.currentTarget)} aria-haspopup="menu" aria-expanded={!!anchor} aria-label={`${tr('wizard.stepsNav')}: ${label}`} sx={{ width: '100%', minHeight: 56, px: 1.5, justifyContent: 'flex-start', gap: 1.5, textAlign: 'left' }}>
      <Box component="span" sx={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
        <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{label}</Typography>
        <Typography component="span" sx={{ fontSize: 15, fontWeight: 600, color: 'var(--text-0)' }}>{tr(step.title)}</Typography>
      </Box>
      <ExpandMoreIcon sx={{ color: 'var(--text-3)' }} />
    </ButtonBase>
    <Box aria-hidden sx={{ display: 'flex', gap: '3px', px: 1.5, pb: 1 }}>
      {steps.map((s, i) => { const st = s.kind === 'review' ? 'empty' : stepProgress(s, draft).state; return <Box key={s.id} sx={{ flex: 1, height: 4, borderRadius: 2, bgcolor: i === current ? 'primary.main' : st === 'done' ? 'success.main' : st === 'partial' ? 'var(--text-4)' : 'var(--line)' }} />; })}
    </Box>
    <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}>
      {steps.map((s, i) => <MenuItem key={s.id} selected={i === current} onClick={() => { setAnchor(null); onSelect(i); }} sx={{ minHeight: TOUCH, gap: 1.5 }}>
        <Marker index={i} state={s.kind === 'review' ? 'empty' : stepProgress(s, draft).state} current={i === current} />{tr(s.title)}
      </MenuItem>)}
    </Menu>
  </Box>;
}
