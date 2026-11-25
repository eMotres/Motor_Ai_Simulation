import { useEffect, useRef, useState } from 'react';
import { Box, Button, Chip, MenuItem, Select, Stack, Tooltip, Typography } from '@mui/material';
import { developmentApi } from '../api';
import { STATE_COLOR, comparedMotors, requirementRows, suggestedCount } from '../specSync';
import type { ProjectMotor } from '../types';

/** The specification's requirements against each linked motor, side by side (owner 2026-11-25): requirement | result | difference and
 *  a state per requirement (no point / stale / differs / matched).  Engineers link a requirement to a duty (or confirm the suggestion);
 *  nobody here changes a requirement.  The state is the server's (development/spec_sync.py). */
export default function SpecPointsSection({ projectId, motors, canLink, onChanged, tr, nf }: {
  projectId: string; motors: ProjectMotor[]; canLink: boolean; onChanged: () => void;
  tr: (key: string, args?: Record<string, unknown>) => string; nf: (v: number | null | undefined) => string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const auto = useRef<Set<string>>(new Set());
  const shown = comparedMotors(motors);
  // engineers: the unambiguous matches are linked once per motor and page load; the rest stays "no point" for a confirm
  useEffect(() => {
    if (!canLink) return;
    for (const m of shown) {
      const key = `${projectId}|${m.die}|${m.config}`;
      if (auto.current.has(key) || suggestedCount(m) === 0) continue;
      auto.current.add(key);
      developmentApi.autoPointLinks(projectId, m.die, m.config).then((r) => { if (r.linked) onChanged(); }).catch(() => undefined);
    }
  }, [canLink, projectId, shown.map((m) => `${m.die}/${m.config}/${suggestedCount(m)}`).join(',')]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!shown.length) return null;
  const link = async (m: ProjectMotor, rowId: string, duty: string | null) => {
    setBusy(true); setError('');
    try { await developmentApi.setPointLink(projectId, m.die, m.config, rowId, duty); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const th = { textAlign: 'left' as const, fontWeight: 600, fontSize: 12, padding: '4px 8px', color: 'var(--text-2)', whiteSpace: 'nowrap' as const };
  const td = { fontSize: 12.5, padding: '4px 8px', verticalAlign: 'top' as const };
  return <Stack spacing={1.2} sx={{ mb: 2 }} data-testid="spec-points">
    {error && <Typography color="error" sx={{ fontSize: 12.5 }}>{error}</Typography>}
    {shown.map((m) => <Box key={`${m.die}/${m.config}`} sx={{ overflowX: 'auto' }}>
      <Typography sx={{ fontSize: 13, fontWeight: 700 }}>{tr('specSync.title')} · {m.config}</Typography>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <thead><tr>
          <th style={th}>{tr('specSync.point')}</th>
          {(['torque', 'speed', 'voltage', 'thermal'] as const).map((k) => <th key={k} style={th} colSpan={3}>{tr(`specSync.check.${k}`)}</th>)}
          <th style={th}>{tr('specSync.state')}</th>{canLink && <th style={th}>{tr('specSync.duty')}</th>}
        </tr><tr><th style={th} />{[0, 1, 2, 3].flatMap((i) => ['required', 'result', 'delta'].map((c) => <th key={`${i}${c}`} style={{ ...th, fontWeight: 400 }}>{tr(`specSync.col.${c}`)}</th>))}<th style={th} />{canLink && <th style={th} />}</tr></thead>
        <tbody>{requirementRows(m).map((r) => <tr key={r.id} data-state={r.state}>
          <td style={td}>{r.name || '-'}</td>
          {r.cells.flatMap((c) => [<td key={`${c.key}r`} style={td}>{nf(c.required)}</td>, <td key={`${c.key}x`} style={td}>{nf(c.result)}</td>,
            <td key={`${c.key}d`} style={{ ...td, color: c.status === 'fail' ? 'var(--error, #c62828)' : undefined }}>{c.delta}</td>])}
          <td style={td}><Tooltip title={r.reasons.length ? r.reasons.map((x) => tr(`specSync.reason.${x}`)).join('\n') : tr(`specSync.stateTip.${r.state}`)}>
            <Chip size="small" color={STATE_COLOR[r.state]} label={tr(`specSync.stateName.${r.state}`)} /></Tooltip>{r.duty && <Typography component="span" sx={{ fontSize: 12, ml: 0.8 }}>{r.duty}</Typography>}</td>
          {canLink && <td style={td}><Stack direction="row" spacing={0.6} alignItems="center">
            <Select size="small" displayEmpty disabled={busy} value={r.duty ?? ''} sx={{ minWidth: 150, fontSize: 12.5 }} inputProps={{ 'aria-label': tr('specSync.duty') }}
              onChange={(e) => void link(m, r.id, e.target.value ? String(e.target.value) : null)}>
              <MenuItem value=""><em>{tr('specSync.noDuty')}</em></MenuItem>
              {(m.results ?? []).map((x) => x.duty).filter((d): d is string => !!d).map((d) => <MenuItem key={d} value={d}>{d}</MenuItem>)}
            </Select>
            {!r.duty && r.suggested && <Tooltip title={tr('specSync.confirmTip', { duty: r.suggested })}><span><Button size="small" disabled={busy} onClick={() => void link(m, r.id, r.suggested)}>{tr('specSync.confirm')}</Button></span></Tooltip>}
          </Stack></td>}
        </tr>)}</tbody>
      </table>
    </Box>)}
  </Stack>;
}
