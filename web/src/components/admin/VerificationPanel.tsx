/**
 * Admin · Verification — the queue of applicants who signed the model NDA (docs/APPLICANT_VERIFICATION.md): list by status, the case
 * with every check (source, raw answer, time), the signals counted, the correspondence, the uploaded documents, the timeline and the
 * downloads, and the actions: approve (with a reason), ask for more (the text goes out as a templated mail), deny (with a reason),
 * re-run the checks.  Every action is audited on the server.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, Chip, Link, Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { CASE_STATUSES, adminActions, reasonOk, sortQueue, statusTone, type QueueRow } from '../../lib/verification';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5, mb: 2 } as const;
const HEAD = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', py: 0.5 } as const;
const CELL = { fontSize: 12, color: 'var(--text-1)', py: 0.5, verticalAlign: 'top' } as const;
const TONE: Record<string, 'success' | 'warning' | 'error' | 'default'> = { ok: 'success', warn: 'warning', bad: 'error', info: 'default' };

interface Row extends QueueRow { status_reason: string; title: string; website: string; created_iso: string; rounds: number; open_replies: number; signals: number; company_confirmed: boolean; downloads: number }
interface Evidence { source: string; check: string; kind: string; result: string; strength: string; value: string; summary: string; source_url: string; registry_grade: boolean; at: number; detail?: unknown; raw?: unknown }
interface CaseState {
  id: string; user: string; status: string; status_actor: string; status_reason: string; application: Record<string, string>; nda: Record<string, string>;
  reasons: Array<{ code: string; text: string }>; requests: Array<{ code: string; params?: Record<string, unknown> }>; evidence: Evidence[];
  decision: { signals?: Array<{ family: string; strength: string; summary: string }>; company?: { confirmed: boolean; by: string[] } };
  correspondence: Array<{ dir: 'in' | 'out'; iso: string; to?: string; subject?: string; body?: string; urls?: string[]; note?: string; delivered?: boolean; codes?: string[] }>;
  documents: Array<{ id: string; name: string; ext: string; bytes: number; sha256: string }>; emails: { verified: Array<{ email: string; method: string }> };
  access: { granted: boolean; by: string; at: number };
}
interface EventRow { iso: string; type: string; actor: string; data: Record<string, unknown> }
interface Detail { case: CaseState; events: EventRow[]; downloads: Array<{ iso: string; motor_id: string; kind: string; download_id: string; sha256: string; file: string }>; reply_url: string }

const Chipped: React.FC<{ status: string }> = ({ status }) => {
  const { t } = useTranslation('admin');
  return <Chip size="small" color={TONE[statusTone(status)]} label={t(`verification.status.${status}`, { defaultValue: status })} sx={{ fontSize: 11, height: 20 }} />;
};

const post = async (url: string, body?: unknown): Promise<{ ok: boolean; text: string }> => {
  try {
    const r = await fetch(`${API}${url}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) });
    if (r.ok) return { ok: true, text: '' };
    const j = (await r.json().catch(() => ({}))) as { detail?: unknown };
    return { ok: false, text: typeof j.detail === 'string' ? j.detail : `HTTP ${r.status}` };
  } catch (e) { return { ok: false, text: (e as Error).message }; }
};

const CaseView: React.FC<{ id: string; onBack: () => void; onChanged: () => void }> = ({ id, onBack, onChanged }) => {
  const { t } = useTranslation('admin');
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [note, setNote] = useState('');          // the applicant-facing sentence of a denial mail (optional)
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/admin/verification/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setD(await r.json() as Detail); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    const r = await post(`/api/admin/verification/${encodeURIComponent(id)}/${path}`, body);
    setBusy(false);
    if (!r.ok) { setErr(r.text); return; }
    setText(''); setErr(null); await load(); onChanged();
  };

  const getDoc = async (docId: string, name: string) => {
    const r = await fetch(`${API}/api/admin/verification/${encodeURIComponent(id)}/documents/${encodeURIComponent(docId)}`);
    if (!r.ok) { setErr(`HTTP ${r.status}`); return; }
    const href = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 10_000);
  };

  if (!d) return <Box>{err ? <Alert severity="error">{err}</Alert> : <Typography sx={{ fontSize: 12 }}>…</Typography>}</Box>;
  const c = d.case;
  const a = c.application;
  const acts = adminActions(c.status);
  const ok = reasonOk(text);
  const kv = (k: string, v?: string) => (v ? <Typography key={k} sx={{ fontSize: 12 }}><b>{t(`verification.field.${k}`, { defaultValue: k })}:</b> {v}</Typography> : null);

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
        <Button size="small" onClick={onBack} sx={{ textTransform: 'none' }}>← {t('verification.back')}</Button>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>{a.legal_name}</Typography>
        <Chipped status={c.status} />
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)', fontFamily: 'monospace' }}>{c.id}</Typography>
      </Box>
      {err && <Alert severity="error" sx={{ mb: 1, fontSize: 12 }} onClose={() => setErr(null)}>{err}</Alert>}

      <Paper sx={PANEL}>
        <Typography sx={HEAD}>{t('verification.application')}</Typography>
        {['applicant_name', 'title', 'work_email', 'country', 'reg_no', 'vat_no', 'website', 'purpose', 'address'].map((k) => kv(k, a[k]))}
        {kv('account', c.user)}
        {c.nda?.version && <Typography sx={{ fontSize: 12 }}><b>NDA:</b> {c.nda.version} · {c.nda.id} · {c.nda.iso}</Typography>}
        {c.emails.verified.length > 0 && <Typography sx={{ fontSize: 12 }}><b>{t('verification.verifiedEmails')}:</b> {c.emails.verified.map((v) => `${v.email} (${v.method})`).join(', ')}</Typography>}
      </Paper>

      <Paper sx={PANEL}>
        <Typography sx={HEAD}>{t('verification.decision')}</Typography>
        <Typography sx={{ fontSize: 12 }}>{c.status_actor} · {c.status_reason || '—'}</Typography>
        {c.reasons.map((r, i) => <Typography key={i} sx={{ fontSize: 12, color: 'var(--text-2)' }}>• [{r.code}] {r.text}</Typography>)}
        {c.requests.length > 0 && <Typography sx={{ fontSize: 12, mt: 0.5 }}><b>{t('verification.asked')}:</b> {c.requests.map((r) => r.code).join(', ')}</Typography>}
        <Typography sx={{ fontSize: 12, mt: 0.5 }}><b>{t('verification.company')}:</b> {c.decision.company?.confirmed ? `${t('verification.confirmedBy')} ${c.decision.company.by.join(', ')}` : t('verification.notConfirmed')}</Typography>
        {(c.decision.signals ?? []).map((s, i) => <Typography key={i} sx={{ fontSize: 12 }}>• {s.strength} · {s.family}: {s.summary}</Typography>)}
        <Typography sx={{ fontSize: 12, mt: 0.5 }}><b>{t('verification.access')}:</b> {c.access.granted ? `${t('verification.granted')} (${c.access.by})` : t('verification.closed')}</Typography>
      </Paper>

      <Paper sx={PANEL}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1 }}>
          <Typography sx={HEAD}>{t('verification.actions')}</Typography>
          <HelpTip title={t('verification.actionsHelp')} />
        </Box>
        <TextField size="small" fullWidth multiline minRows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder={t('verification.reasonPlaceholder')} />
        {acts.includes('deny') && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mt: 1 }}>
            <TextField size="small" fullWidth value={note} onChange={(e) => setNote(e.target.value)} placeholder={t('verification.publicNote')} inputProps={{ maxLength: 500 }} />
            <HelpTip title={t('verification.publicNoteTip')} />
          </Box>
        )}
        <Box sx={{ display: 'flex', gap: 1, mt: 1, flexWrap: 'wrap' }}>
          {acts.includes('approve') && <Button size="small" variant="contained" color="success" disabled={busy || !ok} onClick={() => void act('approve', { reason: text })} sx={{ textTransform: 'none' }}>{t('verification.approve')}</Button>}
          {acts.includes('requestInfo') && <Button size="small" variant="outlined" disabled={busy || !ok} onClick={() => void act('request-info', { text })} sx={{ textTransform: 'none' }}>{t('verification.requestInfo')}</Button>}
          {acts.includes('deny') && <Button size="small" variant="outlined" color="error" disabled={busy || !ok} onClick={() => void act('deny', { reason: text, public_note: note })} sx={{ textTransform: 'none' }}>{t('verification.deny')}</Button>}
          {acts.includes('rerun') && <Button size="small" disabled={busy} onClick={() => void act('rerun')} sx={{ textTransform: 'none' }}>{t('verification.rerun')}</Button>}
          <Link href={d.reply_url} target="_blank" rel="noopener noreferrer" sx={{ fontSize: 12, alignSelf: 'center' }}>{t('verification.replyPage')}</Link>
        </Box>
      </Paper>

      <Paper sx={PANEL}>
        <Typography sx={HEAD}>{t('verification.evidence')} ({c.evidence.length})</Typography>
        <Table size="small">
          <TableHead><TableRow>{['source', 'check', 'result', 'strength', 'summary', 'when'].map((h) => <TableCell key={h} sx={HEAD}>{t(`verification.col.${h}`, { defaultValue: h })}</TableCell>)}</TableRow></TableHead>
          <TableBody>
            {c.evidence.map((e, i) => (
              <TableRow key={i}>
                <TableCell sx={CELL}>{e.source}{e.registry_grade ? ' ★' : ''}</TableCell>
                <TableCell sx={{ ...CELL, fontFamily: 'monospace' }}>{e.check}{e.value ? ` · ${e.value}` : ''}</TableCell>
                <TableCell sx={{ ...CELL, color: e.result === 'pass' ? 'success.main' : e.result === 'conflict' ? 'error.main' : undefined }}>{e.result}</TableCell>
                <TableCell sx={CELL}>{e.strength}</TableCell>
                <TableCell sx={CELL}>
                  {e.summary}
                  {e.source_url && <> · <Link href={e.source_url} target="_blank" rel="noopener noreferrer" sx={{ fontSize: 12 }}>{t('verification.source')}</Link></>}
                  {e.raw != null && <details><summary style={{ cursor: 'pointer', fontSize: 11 }}>{t('verification.raw')}</summary><pre style={{ fontSize: 10.5, whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(e.raw, null, 1)}</pre></details>}
                </TableCell>
                <TableCell sx={CELL}>{new Date(e.at * 1000).toISOString().replace('T', ' ').slice(0, 19)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>

      <Paper sx={PANEL}>
        <Typography sx={HEAD}>{t('verification.correspondence')}</Typography>
        {c.correspondence.length === 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>—</Typography>}
        {c.correspondence.map((m, i) => (
          <Box key={i} sx={{ borderTop: i ? '1px solid var(--line-soft)' : 'none', py: 0.75 }}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{m.iso} · {m.dir === 'out' ? `→ ${m.to}${m.delivered === false ? ` (${t('verification.notSent')})` : ''}` : `← ${t('verification.applicant')}`}</Typography>
            {m.subject && <Typography sx={{ fontSize: 12, fontWeight: 700 }}>{m.subject}</Typography>}
            {m.body && <Typography sx={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{m.body}</Typography>}
            {(m.urls ?? []).map((u) => <Typography key={u} sx={{ fontSize: 12 }}>{u}</Typography>)}
            {m.note && <Typography sx={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{m.note}</Typography>}
          </Box>
        ))}
        {c.documents.map((doc) => (
          <Typography key={doc.id} sx={{ fontSize: 12, mt: 0.5 }}>
            📎 <Link component="button" underline="hover" sx={{ fontSize: 12 }} onClick={() => void getDoc(doc.id, `${doc.id}.${doc.ext}`)}>{doc.name}</Link> · {doc.bytes} B · <span style={{ fontFamily: 'monospace' }}>{doc.sha256.slice(0, 12)}</span>
          </Typography>
        ))}
      </Paper>

      <Paper sx={PANEL}>
        <Typography sx={HEAD}>{t('verification.downloads')} ({d.downloads.length})</Typography>
        {d.downloads.map((x) => <Typography key={x.download_id} sx={{ fontSize: 12, fontFamily: 'monospace' }}>{x.iso} · {x.kind} · {x.motor_id} · {x.download_id} · {x.sha256.slice(0, 12)}</Typography>)}
      </Paper>

      <Paper sx={PANEL}>
        <Typography sx={HEAD}>{t('verification.timeline')} ({d.events.length})</Typography>
        {d.events.map((e, i) => (
          <Typography key={i} sx={{ fontSize: 11.5, fontFamily: 'monospace', color: 'var(--text-2)' }}>
            {e.iso} · {e.actor} · {e.type}{typeof e.data.status === 'string' ? ` → ${e.data.status}` : ''}{typeof e.data.action === 'string' ? ` ${e.data.action}` : ''}
          </Typography>
        ))}
      </Paper>
    </Box>
  );
};

const VerificationPanel: React.FC = () => {
  const { t } = useTranslation('admin');
  const [rows, setRows] = useState<Row[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/admin/verification${filter ? `?status=${filter}` : ''}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json() as { cases: Row[]; counts: Record<string, number> };
      setRows(j.cases); setCounts(j.counts); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [filter]);
  useEffect(() => { void load(); }, [load]);

  const sorted = useMemo(() => sortQueue(rows ?? []), [rows]);
  if (open) return <CaseView id={open} onBack={() => { setOpen(null); void load(); }} onChanged={() => void load()} />;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1.25, flexWrap: 'wrap' }}>
        <Chip size="small" label={`${t('verification.all')} ${Object.values(counts).reduce((a, b) => a + b, 0)}`} color={filter === '' ? 'primary' : 'default'} onClick={() => setFilter('')} sx={{ fontSize: 11 }} />
        {CASE_STATUSES.map((s) => (
          <Chip key={s} size="small" label={`${t(`verification.status.${s}`)} ${counts[s] ?? 0}`} color={filter === s ? 'primary' : 'default'} onClick={() => setFilter(s)} sx={{ fontSize: 11 }} />
        ))}
        <Button size="small" onClick={() => void load()} sx={{ textTransform: 'none' }}>{t('verification.refresh')}</Button>
        <HelpTip title={t('verification.help')} />
      </Box>
      {err && <Alert severity="error" sx={{ mb: 1, fontSize: 12 }}>{err}</Alert>}
      <Paper sx={PANEL}>
        <Table size="small">
          <TableHead><TableRow>{['updated', 'company', 'country', 'applicant', 'account', 'status', 'signals'].map((h) => <TableCell key={h} sx={HEAD}>{t(`verification.col.${h}`, { defaultValue: h })}</TableCell>)}</TableRow></TableHead>
          <TableBody>
            {sorted.map((r) => (
              <TableRow key={r.id} hover sx={{ cursor: 'pointer' }} onClick={() => setOpen(r.id)}>
                <TableCell sx={CELL}>{r.updated_iso.replace('T', ' ').replace('Z', '')}</TableCell>
                <TableCell sx={CELL}>{r.company}{r.company_confirmed ? ' ✓' : ''}</TableCell>
                <TableCell sx={CELL}>{r.country}</TableCell>
                <TableCell sx={CELL}>{r.applicant}{r.title ? ` · ${r.title}` : ''}</TableCell>
                <TableCell sx={CELL}>{r.user}</TableCell>
                <TableCell sx={CELL}><Chipped status={r.status} /></TableCell>
                <TableCell sx={CELL}>{r.signals}</TableCell>
              </TableRow>
            ))}
            {rows && rows.length === 0 && <TableRow><TableCell colSpan={7} sx={CELL}>{t('verification.empty')}</TableCell></TableRow>}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
};

export default VerificationPanel;
