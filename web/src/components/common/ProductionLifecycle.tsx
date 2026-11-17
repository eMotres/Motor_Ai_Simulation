/** Where a production really is: prepared / in production / finished (owner 2026-11-17; motor_ai_sim.production_lifecycle).
 *  The factory sees the state; an admin sets it here, and starting a production asks for the payment line (a date or a
 *  note, never an amount) because "production starts when the money from the customer arrives". */
import React, { useEffect, useState } from 'react';
import { Chip, Menu, MenuItem, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { TextPromptDialog, type TextPromptState } from './PromptDialogs';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
export const LIFECYCLE_STATES = ['prepared', 'in_production', 'finished'] as const;
export type LifecycleState = typeof LIFECYCLE_STATES[number];
interface Answer { state: LifecycleState; explicit: boolean; payment?: string | null; by?: string; at?: string; source?: string }

const COLOR: Record<LifecycleState, string> = { prepared: 'var(--text-3)', in_production: '#34d399', finished: '#60a5fa' };

export const ProductionLifecycleChip: React.FC<{ die: string; config: string; isAdmin: boolean; answer?: Answer | null }> = ({ die, config, isAdmin, answer: given }) => {
  const { t } = useTranslation('production');
  const [a, setA] = useState<Answer | null>(given ?? null);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [ask, setAsk] = useState<TextPromptState | null>(null);
  const path = `${API}/api/production-lifecycle`;
  useEffect(() => {
    if (given !== undefined) return;
    let alive = true;
    fetch(`${path}/${encodeURIComponent(die)}/${encodeURIComponent(config)}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null)).then((j) => { if (alive) setA(j as Answer | null); }).catch(() => { /* no chip */ });
    return () => { alive = false; };
  }, [die, config, given, path]);
  if (!a) return null;
  const put = async (state: LifecycleState, payment = '') => {
    const r = await fetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ die, config, state, payment }) });
    if (r.ok) setA(await r.json() as Answer);
  };
  const pick = (state: LifecycleState) => {
    setAnchor(null);
    if (state === 'in_production') {
      setAsk({ title: t('lifecycle.startTitle'), label: t('lifecycle.paymentLabel'), initial: '', okLabel: t('lifecycle.startOk'),
        onSubmit: (line) => { if (line.trim()) void put('in_production', line); } });
    } else void put(state);
  };
  const tip = [t(`lifecycle.tip.${a.state}`), a.payment ? t('lifecycle.paymentTip', { line: a.payment }) : '',
    isAdmin && a.by ? t('lifecycle.byTip', { by: a.by, at: a.at ?? '' }) : ''].filter(Boolean).join(' · ');
  return (
    <>
      <Tooltip title={tip}>
        <Chip size="small" variant="outlined" data-testid="production-lifecycle" label={t(`lifecycle.state.${a.state}`)}
          onClick={isAdmin ? (e) => setAnchor(e.currentTarget) : undefined}
          sx={{ ml: 1, height: 20, fontSize: 11, color: COLOR[a.state], borderColor: 'currentColor', verticalAlign: 'middle' }} />
      </Tooltip>
      {isAdmin && (
        <Menu open={!!anchor} anchorEl={anchor} onClose={() => setAnchor(null)}>
          {LIFECYCLE_STATES.map((s) => (
            <MenuItem key={s} dense selected={s === a.state} onClick={() => pick(s)}>{t(`lifecycle.set.${s}`)}</MenuItem>
          ))}
        </Menu>
      )}
      <TextPromptDialog state={ask} onClose={() => setAsk(null)} />
    </>
  );
};

export default ProductionLifecycleChip;
