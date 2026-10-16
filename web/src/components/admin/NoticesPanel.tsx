/**
 * Admin · In-app notices — post a product notice (maintenance, new feature) to everyone, a role or named users, see how many
 * read it, withdraw it. Notices are not marketing and do not depend on any e-mail consent. (Moved here from the retired
 * Newsletter section; backend: routes/newsletter.py notices_router.)
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Paper, Typography, TextField, Button, Table, TableHead, TableRow, TableCell, TableBody, MenuItem, Select,
} from '@mui/material';
import HelpTip from '../common/HelpTip';
import { nlAdmin, type AdminNotice } from '../../lib/newsletterApi';

const ROLES = ['user', 'engineer', 'admin'];
const card = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5, mb: 2 };
const head = { fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.04em' };
const tbl = { '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 } };
const btn = { textTransform: 'none' as const, fontSize: 12 };
const ts = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : '');

const RolePick: React.FC<{ value: string[]; onChange: (v: string[]) => void }> = ({ value, onChange }) => (
  <Select multiple size="small" displayEmpty value={value}
    onChange={(e) => onChange(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)}
    renderValue={(v) => ((v as string[]).length ? (v as string[]).join(', ') : 'all roles')}
    sx={{ fontSize: 12.5, minWidth: 140 }}>
    {ROLES.map((t) => <MenuItem key={t} value={t} sx={{ fontSize: 12.5 }}>{t}</MenuItem>)}
  </Select>
);

const NoticesPanel: React.FC = () => {
  const [rows, setRows] = useState<AdminNotice[]>([]);
  const [n, setN] = useState({ title: '', body: '', level: 'info', emails: '', roles: [] as string[] });
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => { void nlAdmin.notices().then((j) => setRows(j.notices)).catch((e: Error) => setErr(e.message)); }, []);
  useEffect(load, [load]);
  const post = async () => {
    setErr(null);
    try {
      await nlAdmin.postNotice({ ...n, emails: n.emails.split(/[\s,;]+/).filter(Boolean) });
      setN({ title: '', body: '', level: 'info', emails: '', roles: [] }); load();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Paper sx={card} data-testid="notices-panel">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1 }}>
        <Typography sx={head}>In-app notices</Typography>
        <HelpTip title="Product notices shown in the bell and the Notifications list (maintenance, a new feature). Not marketing: no e-mail is sent and no consent is needed." />
      </Box>
      <Box sx={{ display: 'flex', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <TextField size="small" label="Title" value={n.title} onChange={(e) => setN({ ...n, title: e.target.value })}
          inputProps={{ maxLength: 140 }} sx={{ flex: 2, minWidth: 220 }} />
        <Select size="small" value={n.level} onChange={(e) => setN({ ...n, level: e.target.value })} sx={{ fontSize: 12.5 }}>
          {['info', 'warning', 'important'].map((l) => <MenuItem key={l} value={l} sx={{ fontSize: 12.5 }}>{l}</MenuItem>)}
        </Select>
        <RolePick value={n.roles} onChange={(roles) => setN({ ...n, roles })} />
      </Box>
      <TextField size="small" label="Text (optional)" value={n.body} multiline minRows={2} fullWidth
        onChange={(e) => setN({ ...n, body: e.target.value })} sx={{ mb: 1 }} />
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
        <TextField size="small" label="Only these e-mails (optional)" value={n.emails} fullWidth
          onChange={(e) => setN({ ...n, emails: e.target.value })} />
        <Button size="small" variant="contained" sx={btn} disabled={!n.title.trim()} onClick={() => void post()}>Post</Button>
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171', mt: 0.5 }}>{err}</Typography>}
      <Table size="small" sx={{ ...tbl, mt: 1.5 }}>
        <TableHead><TableRow>
          <TableCell>Title</TableCell><TableCell>Level</TableCell><TableCell>Audience</TableCell>
          <TableCell>Posted</TableCell><TableCell align="right">Read</TableCell><TableCell />
        </TableRow></TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id} sx={{ opacity: r.active ? 1 : 0.5 }}>
              <TableCell>{r.title}</TableCell><TableCell>{r.level}</TableCell>
              <TableCell>{r.emails.length ? `${r.emails.length} user(s)` : r.roles.length ? r.roles.join(',') : 'all'}</TableCell>
              <TableCell>{ts(r.created)}</TableCell><TableCell align="right">{r.read_count}</TableCell>
              <TableCell align="right">
                {r.active && <Button size="small" color="error" sx={btn}
                  onClick={() => void nlAdmin.withdrawNotice(r.id).then(load)}>Withdraw</Button>}
              </TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && <TableRow><TableCell colSpan={6} sx={{ color: 'var(--text-4)' }}>No notices yet.</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Paper>
  );
};

export default NoticesPanel;
