/** Discussion: a general thread and one thread per part; messages carry attachments and the original/translation toggle. */
import React, { useMemo, useRef, useState } from 'react';
import { Box, Button, MenuItem, Paper, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import HelpTip from '../../../components/common/HelpTip';
import { downloadFile, productionApi } from '../api';
import { makeBilingual } from '../helpers';
import type { Lang, Part, ProdCtx, Thread } from '../types';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

const DiscussionPanel: React.FC<{ threads: Thread[]; parts: Part[]; ctx: ProdCtx }> = ({ threads, parts, ctx }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [sel, setSel] = useState<string>('');           // '' = general, else part id
  const [text, setText] = useState('');
  const [lang, setLang] = useState<Lang>(ui);
  const [files, setFiles] = useState<File[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const thread = useMemo(() => threads.find((x) => (x.part_id ?? '') === sel) ?? null, [threads, sel]);
  const sorted = useMemo(() => [...(thread?.messages ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at)), [thread]);

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
          <MenuItem value="">{t('discussion.general')}</MenuItem>
          {parts.map((p) => <MenuItem key={p.id} value={p.id}>{p.name[p.name.src_lang]}</MenuItem>)}
        </TextField>
        <HelpTip title={t('discussion.tip')} />
      </Box>
      {sorted.length === 0 && <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('discussion.none')}</Typography>}
      {sorted.map((m) => (
        <Paper key={m.id} sx={{ ...PANEL, p: 1.25, borderLeft: m.author_email.toLowerCase() === ctx.email.toLowerCase() ? '3px solid #2563eb' : undefined }}>
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
