/** Discussion: a general thread and one thread per part; messages carry attachments and the original/translation toggle. */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, Chip, Divider, MenuItem, Paper, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import HelpTip from '../../../components/common/HelpTip';
import { downloadFile, productionApi } from '../api';
import { activeThreads, initialThreadKey, makeBilingual } from '../helpers';
import { accumulateHighlight, firstUnreadIndex, refreshUnread, unreadLabel } from '../../../lib/unreadMessages';
import { pageVisible } from '../../../lib/pageVisible';
import type { Lang, Part, ProdCtx, Thread } from '../types';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

const badge = (n: number) => (n > 0 ? <Chip size="small" color="primary" label={unreadLabel(n)} sx={{ ml: 1, height: 16, fontSize: 10 }} /> : null);

const DiscussionPanel: React.FC<{ threads: Thread[]; parts: Part[]; ctx: ProdCtx }> = ({ threads, parts, ctx }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  // '' = general, else part id; opens on the thread with unread messages, else the newest one (owner 2026-10-28)
  const [sel, setSel] = useState<string>(() => initialThreadKey(threads));
  const [text, setText] = useState('');
  const [lang, setLang] = useState<Lang>(ui);
  const [files, setFiles] = useState<File[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  // Unread messages (owner 2026-10-28): `markedIds` = ones already reported read, `shown` = the highlight of the open thread
  const [markedIds, setMarkedIds] = useState<ReadonlySet<string>>(new Set());
  const [shown, setShown] = useState<ReadonlySet<string>>(new Set());
  const shownThread = useRef('');
  const [visibleTick, setVisibleTick] = useState(0);

  const thread = useMemo(() => threads.find((x) => (x.part_id ?? '') === sel) ?? null, [threads, sel]);
  const active = useMemo(() => activeThreads(threads), [threads]);
  const partName = (id: string) => { const p = parts.find((x) => x.id === id); return p ? (p.name.en || p.name.zh) : id; };
  const sorted = useMemo(() => [...(thread?.messages ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at)), [thread]);

  useEffect(() => { const on = () => setVisibleTick((n) => n + 1); document.addEventListener('visibilitychange', on); return () => document.removeEventListener('visibilitychange', on); }, []);
  const unreadOf = (th?: Thread | null) => (th?.unread_ids ?? []).filter((id) => !markedIds.has(id));
  // Showing a thread marks it read on the server (never while the page is hidden); the highlight stays until the thread is left.
  useEffect(() => {
    if (!thread) { shownThread.current = ''; setShown((s) => (s.size ? new Set() : s)); return; }
    const pending = unreadOf(thread);
    if (shownThread.current !== thread.id) { shownThread.current = thread.id; setShown(new Set(pending)); }
    else if (pending.length) setShown((s) => accumulateHighlight(s, pending));
    const last = thread.messages[thread.messages.length - 1]?.id;
    if (!pending.length || !last || !pageVisible()) return;
    void productionApi.markThreadRead(ctx.die, ctx.config, thread.id, last)
      .then(() => { setMarkedIds((m) => accumulateHighlight(m, pending)); return refreshUnread(); })
      .catch(() => { /* the next visit marks it */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread, markedIds, visibleTick, ctx.die, ctx.config]);

  const send = async () => {
    if (!text.trim()) return;
    setBusy(true); setErr(null);
    try {
      const th = thread ?? await productionApi.createThread(ctx.die, ctx.config, sel || null);
      await productionApi.postMessage(ctx.die, ctx.config, th.id, makeBilingual(text, lang), files);
      setText(''); setFiles([]); if (fileRef.current) fileRef.current.value = '';
      ctx.reload();
    } catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
    finally { setBusy(false); }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <TextField select size="small" label={t('discussion.thread')} value={sel} onChange={(e) => setSel(e.target.value)} sx={{ minWidth: 240 }}>
          <MenuItem value="">{t('discussion.general')}{badge(unreadOf(threads.find((x) => !x.part_id)).length)}</MenuItem>
          {parts.map((p) => <MenuItem key={p.id} value={p.id}>{p.name.en || p.name.zh}{badge(unreadOf(threads.find((x) => x.part_id === p.id)).length)}</MenuItem>)}
        </TextField>
        <HelpTip title={t('discussion.tip')} />
      </Box>
      {active.length > 0 && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
          <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{t('discussion.active')}</Typography>
          {active.map((a) => {
            const unread = unreadOf(threads.find((x) => (x.part_id ?? '') === a.key)).length;
            return (
              <Chip key={a.key || 'general'} size="small" clickable onClick={() => setSel(a.key)}
                variant={a.key === sel ? 'filled' : 'outlined'} color={unread > 0 ? 'error' : a.key === sel ? 'primary' : 'default'}
                title={a.last.slice(0, 16).replace('T', ' ')}
                label={`${a.key ? partName(a.key) : t('discussion.general')} · ${a.count}${unread > 0 ? ` · ${unreadLabel(unread)}` : ''}`} />
            );
          })}
        </Box>
      )}
      {sorted.length === 0 && <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('discussion.none')}</Typography>}
      {sorted.map((m, i) => (
        <React.Fragment key={m.id}>
        {i === firstUnreadIndex(sorted, shown) && <Divider sx={{ fontSize: 10.5 }}><Chip size="small" color="primary" label={t('unread.new')} sx={{ height: 16, fontSize: 10 }} /></Divider>}
        <Paper sx={{ ...PANEL, p: 1.25, borderLeft: shown.has(m.id) ? '3px solid var(--mui-palette-primary-main, #1976d2)' : m.author_email.toLowerCase() === ctx.email.toLowerCase() ? '3px solid #2563eb' : undefined }}>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 0.25 }}>{m.author_email} · {m.created_at.slice(0, 16).replace('T', ' ')}</Typography>
          <BilingualText chat value={m.text} canConfirm={ctx.canEdit} die={ctx.die} config={ctx.config}
            tref={{ entity: 'thread', entity_id: thread!.id, field: 'text', message_id: m.id }} onChanged={ctx.reload} />
          {m.attachments.length > 0 && (
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 0.5 }}>
              {m.attachments.map((a) => (
                <Button key={a.file_id} size="small" sx={{ textTransform: 'none', fontSize: 11, p: 0, minWidth: 0 }}
                  onClick={() => void downloadFile(ctx.die, ctx.config, a.file_id, a.name)}>{a.name}</Button>
              ))}
            </Box>
          )}
        </Paper>
        </React.Fragment>
      ))}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        <BilingualInput label={t('discussion.write')} value={text} lang={lang} multiline onChange={(v, l) => { setText(v); setLang(l); }} />
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
          <Button size="small" sx={{ textTransform: 'none' }} onClick={() => fileRef.current?.click()}>{t('discussion.attach')}{files.length ? ` (${files.length})` : ''}</Button>
          <Button size="small" variant="contained" disabled={busy || !text.trim()} sx={{ textTransform: 'none' }} onClick={() => void send()}>{t('discussion.send')}</Button>
          {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
        </Box>
      </Box>
    </Box>
  );
};

export default DiscussionPanel;
