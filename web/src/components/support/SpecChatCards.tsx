/**
 * The specification cards of one assistant reply (owner 2026-11-11: ONE assistant, the chat bubble works inside the Specification):
 * the values it filled (with their basis and Undo) and the proposals that wait for the project owner (Apply / Dismiss).  The card
 * logic is the old side panel's (modules/development/specAssistant.ts); the calls are modules/development/specAssistantApi.ts.
 */
import { Box, Button, Chip, CircularProgress, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import type { DevelopmentSchema } from '../../modules/development/types';
import { basisOf, cardAction, cardView, filledState, langOf, type Proposal } from '../../modules/development/specAssistant';
import { fileSourceOf } from '../../modules/development/specFile';

interface Props {
  applied?: Proposal[];
  proposals?: Proposal[];
  schema: DevelopmentSchema | null;
  lang: string;
  canApply: boolean;
  busyId: string | null;
  onUndo: (p: Proposal) => void;
  onApply: (p: Proposal) => void;
  onDismiss: (p: Proposal) => void;
}

export default function SpecChatCards({ applied = [], proposals = [], schema, lang, canApply, busyId, onUndo, onApply, onDismiss }: Props) {
  const { t: td } = useTranslation('development');
  const ui = langOf(lang);
  const sources = (p: Proposal) => ((p as Proposal & { sources?: string[] }).sources ?? []).filter((u) => /^https?:\/\//.test(u));
  /** Where a value read from an attached file came from: one chip, the details in its tooltip (no caption line). */
  const fileChip = (p: Proposal) => {
    const fs = fileSourceOf(p);
    if (!fs) return null;
    const found = fs.found === true;
    const tip = [td('specAssistant.sourceFrom', { ref: fs.ref }), fs.excerpt ? td('specAssistant.sourceExcerpt', { text: fs.excerpt }) : '',
      fs.converted ? td('specAssistant.sourceConverted', { text: fs.converted }) : '',
      td(found ? 'specAssistant.sourceFound' : 'specAssistant.sourceNotFound')].filter(Boolean);
    return <Tooltip title={<Box data-testid="spec-source-tip">{tip.map((t) => <Box key={t}>{t}</Box>)}</Box>}>
      <Chip size="small" data-testid="spec-source" label={`${td(found ? 'specAssistant.sourceFoundShort' : 'specAssistant.sourceNotFoundShort')}: ${fs.ref}`} color={found ? 'default' : 'warning'} variant="outlined"
        sx={{ height: 20, fontSize: 12, mt: 0.3, mr: 0.5, maxWidth: '100%' }} />
    </Tooltip>;
  };
  return <>
    {applied.map((a) => {
      const c = cardView(a, schema, ui);
      const state = filledState(a, canApply);
      const basis = basisOf(a);
      return <Box key={a.id} data-testid="spec-applied" sx={{ mt: 0.6, border: '1px solid var(--line)', borderLeft: `3px solid ${basis === 'assumed' ? '#f59e0b' : '#16a34a'}`, borderRadius: 1, p: 0.8, bgcolor: 'var(--panel-2)' }}>
        <Typography sx={{ fontWeight: 700, fontSize: 12.5 }}>{td('specAssistant.filled')}: {c.title}</Typography>
        <Typography sx={{ fontSize: 13 }}>{(a as Proposal & { answer_status?: string }).answer_status === 'not_known_yet' ? td('unknown') : c.proposed}</Typography>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{c.reason}</Typography>
        {sources(a).map((u) => <Typography key={u} component="a" href={u} target="_blank" rel="noopener noreferrer" sx={{ display: 'block', fontSize: 12, overflowWrap: 'anywhere' }}>{u}</Typography>)}
        <Chip size="small" label={td(basis === 'stated' ? 'specAssistant.basisStated' : basis === 'derived' ? 'specAssistant.basisDerived' : 'specAssistant.basisAssumed')}
          color={basis === 'assumed' ? 'warning' : 'default'} sx={{ height: 20, fontSize: 12, mt: 0.3 }} />
        {state === 'undo' && <Button size="small" data-testid="spec-undo" disabled={busyId !== null} onClick={() => onUndo(a)} sx={{ textTransform: 'none', ml: 0.5 }}>{busyId === a.id ? <CircularProgress size={14} /> : td('specAssistant.undo')}</Button>}
        {state === 'undone' && <Typography sx={{ fontSize: 12, fontStyle: 'italic' }}>{td('specAssistant.undone')}</Typography>}
      </Box>;
    })}
    {proposals.filter((p) => p.status === 'open').map((p) => {
      const c = cardView(p, schema, ui);
      return <Box key={p.id} data-testid="spec-proposal" sx={{ mt: 0.6, border: '1px solid var(--line)', borderLeft: '3px solid #3b82f6', borderRadius: 1, p: 1, bgcolor: 'var(--panel-2)' }}>
        <Typography data-testid="spec-proposal-ai" sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)' }}>{td('specAssistant.aiSuggestion')}</Typography>
        <Typography sx={{ fontWeight: 700, fontSize: 13 }}>{c.title}</Typography>
        <Typography sx={{ fontSize: 13 }}>{c.proposed}</Typography>
        {c.current && <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{td('specAssistant.now', { value: c.current })}</Typography>}
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{c.reason}</Typography>
        <Typography sx={{ fontSize: 12, color: c.recalcNeeded ? 'warning.main' : 'var(--text-2)' }}>{td('specAssistant.recalc', { what: c.recalc })}</Typography>
        {fileChip(p)}
        {fileSourceOf(p) && basisOf(p) !== 'stated' && <Chip size="small" label={td(basisOf(p) === 'derived' ? 'specAssistant.basisDerived' : 'specAssistant.basisAssumed')}
          color={basisOf(p) === 'assumed' ? 'warning' : 'default'} sx={{ height: 20, fontSize: 12, mt: 0.3 }} />}
        {cardAction(p, canApply) === 'apply' ? <Box sx={{ display: 'flex', gap: 1, mt: 0.8 }}>
          <Button size="small" variant="contained" disabled={busyId !== null} onClick={() => onApply(p)} sx={{ textTransform: 'none' }}>{busyId === c.id ? <CircularProgress size={14} /> : td('specAssistant.apply')}</Button>
          <Button size="small" disabled={busyId !== null} onClick={() => onDismiss(p)} sx={{ textTransform: 'none' }}>{td('specAssistant.dismiss')}</Button>
        </Box> : <Typography sx={{ fontSize: 12, mt: 0.6, fontStyle: 'italic' }}>{td('specAssistant.ownerApplies')}</Typography>}
      </Box>;
    })}
  </>;
}
