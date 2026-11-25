/** Overview -> Decisions: the owner's queue as a carousel (one card at a time, queue order, Prev / Next / Skip, keys), a History
 *  sub-view with search, and the agent tokens.  The app only RECORDS an answer; the asking session reads it back. */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Box, Button, Chip, IconButton, MenuItem, Select, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { useTranslation } from 'react-i18next';
import { useRememberedView } from '../../../lib/useRememberedView';
import { oneOf, shape } from '../../../lib/viewMemory';
import { appLinkPath } from '../../../lib/appLink';
import DecisionCard from './DecisionCard';
import { OqHttpError, ownerQueueApi, type OqTokenRec } from './ownerQueueApi';
import { NO_FILTER, NO_PROJECT, answerBody, applyFilters, clampIndex, hasUnassigned, historyOrder, keyAction, orderCards, projectChoices,
  sessionChoices, skipCard, type Filters, type OqItem } from './model';

const SUBS = ['queue', 'history'] as const;
type Sub = typeof SUBS[number];
const str = (raw: unknown): string | undefined => (typeof raw === 'string' ? raw : undefined);
const validFilters = shape<Filters>({ project: str, session: str });

const isTyping = (el: EventTarget | null): boolean => {
  const e = el as HTMLElement | null;
  return !!e && (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName) || e.isContentEditable === true);
};

interface Props { projects: Record<string, { title: string }>; onChanged?: () => void }

const DecisionsView: React.FC<Props> = ({ projects, onChanged }) => {
  const { t } = useTranslation('common');
  const [sub, setSub] = useRememberedView<Sub>('overview.decisions.sub', 'queue', oneOf(SUBS));
  const [filters, setFilters] = useRememberedView<Filters>('overview.decisions.filter', NO_FILTER, validFilters);
  const [open, setOpen] = useState<OqItem[]>([]);
  const [history, setHistory] = useState<OqItem[]>([]);
  const [q, setQ] = useState('');
  const [histProject, setHistProject] = useState('');
  const [skipped, setSkipped] = useState<string[]>([]);
  const [index, setIndex] = useState(0);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try { setOpen(await ownerQueueApi.items({ state: 'open' })); setNow(Date.now()); }
    catch (e) { setErr(t('ownerQueue.loadError', { why: e instanceof Error ? e.message : String(e) })); }
  }, [t]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (sub !== 'history') return;
    ownerQueueApi.items({ q, project_id: histProject }).then((r) => setHistory(historyOrder(r)))
      .catch((e: unknown) => setErr(t('ownerQueue.loadError', { why: e instanceof Error ? e.message : String(e) })));
  }, [sub, q, histProject, t]);

  const shown = useMemo(() => orderCards(applyFilters(open, filters), skipped), [open, filters, skipped]);
  const at = clampIndex(index, shown.length);
  const card = shown[at];
  useEffect(() => { setText(''); }, [card?.id]);

  const send = useCallback(async (body: { option?: string; text?: string }) => {
    if (!card) return;
    setBusy(true); setErr('');
    try { await ownerQueueApi.answer(card.id, body); setText(''); await load(); onChanged?.(); }
    catch (e) {
      if (e instanceof OqHttpError && e.status === 409) { setErr(t('ownerQueue.conflict')); await load(); onChanged?.(); }
      else setErr(t('ownerQueue.answerError', { why: e instanceof Error ? e.message : String(e) }));
    } finally { setBusy(false); }
  }, [card, load, onChanged, t]);
  const pick = (label: string) => { void send(answerBody(label, text)); };

  useEffect(() => {
    if (sub !== 'queue') return undefined;
    const onKey = (ev: KeyboardEvent) => {
      const a = keyAction(ev.key, { typing: isTyping(ev.target) || isTyping(document.activeElement), optionCount: card?.options.length ?? 0,
        modifier: ev.ctrlKey || ev.metaKey || ev.altKey });
      if (!a) return;
      ev.preventDefault();
      if (a.type === 'prev') setIndex(clampIndex(at - 1, shown.length));
      else if (a.type === 'next') setIndex(clampIndex(at + 1, shown.length));
      else if (card && !busy) pick(card.options[a.index].label);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });                                                              // re-bound every render: it reads the current card and text

  const title = (id: string | null) => (id ? projects[id]?.title : undefined);
  const sessions = sessionChoices(open);
  const sessionOn = filters.session;

  return (
    <Box data-testid="oq-view">
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1.5 }}>
        <ToggleButtonGroup size="small" exclusive value={sub} onChange={(_e, v: Sub | null) => { if (v) setSub(v); }}
          sx={{ '& .MuiToggleButton-root': { py: 0.2, px: 1, fontSize: 11.5, textTransform: 'none' } }}>
          <ToggleButton value="queue">{t('ownerQueue.viewQueue')}</ToggleButton>
          <ToggleButton value="history">{t('ownerQueue.viewHistory')}</ToggleButton>
        </ToggleButtonGroup>
        {sub === 'queue' && (
          <>
            <Select size="small" displayEmpty value={filters.project} onChange={(e) => { setFilters({ ...filters, project: String(e.target.value) }); setIndex(0); }}
              inputProps={{ 'aria-label': t('ownerQueue.project') }} sx={{ fontSize: 12, minWidth: 150 }}>
              <MenuItem value="">{t('ownerQueue.allProjects')}</MenuItem>
              {hasUnassigned(open) && <MenuItem value={NO_PROJECT}>{t('ownerQueue.noProject')}</MenuItem>}
              {projectChoices(open).map((p) => <MenuItem key={p} value={p}>{title(p) || p}</MenuItem>)}
            </Select>
            {sessionOn
              ? <Chip size="small" color="primary" label={t('ownerQueue.sessionOnly', { session: sessionOn })} onDelete={() => { setFilters({ ...filters, session: '' }); setIndex(0); }}
                  deleteIcon={<Tooltip title={t('ownerQueue.clearSession')}><span>x</span></Tooltip>} />
              : card && sessions.length > 0 && <Chip size="small" clickable variant="outlined" data-testid="oq-session-filter" label={t('ownerQueue.allFromSession', { session: card.asked_by })}
                  onClick={() => { setFilters({ ...filters, session: card.asked_by }); setIndex(0); }} />}
          </>
        )}
        {sub === 'history' && (
          <>
            <TextField size="small" label={t('ownerQueue.search')} value={q} onChange={(e) => setQ(e.target.value)} inputProps={{ 'data-testid': 'oq-search' }} />
            <Select size="small" displayEmpty value={histProject} onChange={(e) => setHistProject(String(e.target.value))} inputProps={{ 'aria-label': t('ownerQueue.project') }} sx={{ fontSize: 12, minWidth: 150 }}>
              <MenuItem value="">{t('ownerQueue.allProjects')}</MenuItem>
              {Object.keys(projects).map((p) => <MenuItem key={p} value={p}>{projects[p].title}</MenuItem>)}
            </Select>
          </>
        )}
        <Box sx={{ flex: 1 }} />
        {sub === 'queue' && (
          <Tooltip title={t('ownerQueue.keysTip')}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <IconButton size="small" aria-label={t('ownerQueue.prev')} disabled={at <= 0} onClick={() => setIndex(at - 1)}><ChevronLeftIcon /></IconButton>
              <Typography data-testid="oq-counter" sx={{ fontSize: 13, minWidth: 70, textAlign: 'center' }}>{shown.length ? t('ownerQueue.counter', { n: at + 1, total: shown.length }) : ''}</Typography>
              <IconButton size="small" aria-label={t('ownerQueue.next')} disabled={at >= shown.length - 1} onClick={() => setIndex(at + 1)}><ChevronRightIcon /></IconButton>
              <Tooltip title={t('ownerQueue.skipTip')}><span>
                <Button size="small" disabled={shown.length < 2} onClick={() => { if (card) setSkipped(skipCard(skipped, card.id)); }} sx={{ textTransform: 'none' }}>{t('ownerQueue.skip')}</Button>
              </span></Tooltip>
            </Box>
          </Tooltip>
        )}
      </Box>
      {err && <Typography data-testid="oq-view-error" sx={{ fontSize: 12, color: '#f87171', mb: 1 }}>{err}</Typography>}
      {sub === 'queue' && !card && <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t(open.length ? 'ownerQueue.emptyFiltered' : 'ownerQueue.empty')}</Typography>}
      {sub === 'queue' && card && (
        <DecisionCard key={card.id} item={card} now={now} projectTitle={title(card.project_id)}
          projectHref={card.project_id ? appLinkPath({ kind: 'project', id: card.project_id }) : null}
          text={text} onText={setText} busy={busy} onOption={pick} onSend={() => { void send(answerBody(null, text)); }}
          onRead={() => { void send({ option: 'read' }); }} />
      )}
      {sub === 'history' && (
        history.length === 0 ? <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('ownerQueue.historyEmpty')}</Typography> : (
          <Box data-testid="oq-history">
            {history.map((h) => (
              <Box key={h.id} sx={{ py: 0.75, borderBottom: '1px solid var(--line-soft)' }}>
                <Typography sx={{ fontSize: 13, fontWeight: 600 }}>
                  {h.question}
                  <Chip size="small" label={t(`ownerQueue.state.${h.state}`)} sx={{ ml: 1, height: 18, fontSize: 10 }} />
                </Typography>
                <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>
                  {[title(h.project_id) || h.project_id, h.asked_by,
                    h.answer?.option ? t('ownerQueue.answerOption', { option: h.answer.option }) : '',
                    h.answer?.text ? t('ownerQueue.answerText', { text: h.answer.text }) : '',
                    h.answer ? t('ownerQueue.answeredBy', { by: h.answer.by, at: h.answer.at.slice(0, 16).replace('T', ' ') }) : ''].filter(Boolean).join(' · ')}
                </Typography>
              </Box>
            ))}
          </Box>
        )
      )}
      <TokensPanel />
    </Box>
  );
};

/** Agent tokens: the OWNER creates one here (shown once) and places it in the token file; revoking stops that session at once. */
const TokensPanel: React.FC = () => {
  const { t } = useTranslation('common');
  const [rows, setRows] = useState<OqTokenRec[]>([]);
  const [name, setName] = useState('');
  const [shown, setShown] = useState('');
  const [err, setErr] = useState('');
  const load = useCallback(() => { ownerQueueApi.tokens().then(setRows).catch(() => setRows([])); }, []);
  useEffect(() => { load(); }, [load]);
  const create = () => {
    setErr(''); setShown('');
    ownerQueueApi.createToken(name.trim()).then((r) => { setShown(t('ownerQueue.tokenShown', { token: r.token })); setName(''); load(); })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  };
  return (
    <Accordion disableGutters sx={{ mt: 3, bgcolor: 'var(--panel-2)' }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}><Typography sx={{ fontSize: 12, fontWeight: 700 }}>{t('ownerQueue.tokens')}</Typography></AccordionSummary>
      <AccordionDetails>
        {rows.map((r) => (
          <Box key={r.name} sx={{ display: 'flex', gap: 1, alignItems: 'center', py: 0.25 }}>
            <Typography sx={{ fontSize: 12.5, fontWeight: 600 }}>{r.name}</Typography>
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{r.revoked_at ? t('ownerQueue.tokenRevoked') : r.last_used_at ? t('ownerQueue.tokenLastUsed', { at: r.last_used_at.slice(0, 16).replace('T', ' ') }) : t('ownerQueue.tokenNever')}</Typography>
            {!r.revoked_at && <Button size="small" onClick={() => { ownerQueueApi.revokeToken(r.name).then(load).catch(() => undefined); }} sx={{ textTransform: 'none' }}>{t('ownerQueue.tokenRevoke')}</Button>}
          </Box>
        ))}
        <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
          <TextField size="small" label={t('ownerQueue.tokenName')} value={name} onChange={(e) => setName(e.target.value)} />
          <Button size="small" variant="outlined" disabled={!name.trim()} onClick={create} sx={{ textTransform: 'none' }}>{t('ownerQueue.tokenCreate')}</Button>
        </Box>
        {shown && <Typography data-testid="oq-token-once" sx={{ fontSize: 12, mt: 1, wordBreak: 'break-all', userSelect: 'all' }}>{shown}</Typography>}
        {err && <Typography sx={{ fontSize: 12, color: '#f87171', mt: 0.5 }}>{err}</Typography>}
      </AccordionDetails>
    </Accordion>
  );
};

export default DecisionsView;
