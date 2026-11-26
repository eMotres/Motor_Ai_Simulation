// Save / open on the Gears page (/api/gears/boxes): a name, Save (new) / Save changes, the list of saved gearboxes
// (archived ones behind a toggle), Open, Archive / Restore, flag chips, and the loud "changed elsewhere" path.
import { useCallback, useEffect, useState } from 'react';
import { Box, Button, Chip, FormControlLabel, Paper, Switch, TextField, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useRememberedView } from '../../../lib/useRememberedView';
import { GearsApiError, boxesApi } from '../api';
import { boxChips, isStaleVersion, validateSavedRef, visibleBoxes, type SavedRef } from '../saved';
import type { BoxList, BoxRecord, BoxSummary, RequestRecord } from '../types';
import { Lbl } from './parts';

interface Props {
  name: string; onName: (name: string) => void;
  buildSave: () => Promise<{ request: RequestRecord; response: unknown | null }>;
  onOpen: (rec: BoxRecord) => void;
  /** the open record (id, scope, name), or null: the With motor tab can use it as its gearbox */
  onRef?: (ref: { id: string; project: string; name: string } | null) => void;
}

const remembered = { ref: null as SavedRef | null, project: '', name: '', showArchived: false };

export default function SavedPanel({ name, onName, buildSave, onOpen, onRef }: Props) {
  const { t } = useTranslation('gears');
  const [mem, setMem] = useRememberedView('gears.saved', remembered, validateSavedRef);
  const [projectText, setProjectText] = useState(mem.project);
  const [list, setList] = useState<BoxList | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);   // id of the record that changed elsewhere
  const project = mem.project || null;
  const current0 = mem.ref;
  useEffect(() => { onRef?.(current0 ? { id: current0.id, project: current0.project, name: current0.name } : null); }, [current0?.id, current0?.name, current0?.project]);  // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = useCallback(async () => {
    try { setList(await boxesApi.list(mem.project || null, 'all')); }
    catch (e) { setMessage(e instanceof Error ? e.message : String(e)); }
  }, [mem.project]);
  useEffect(() => { void refresh(); }, [refresh]);

  const guard = async (id: string | null, fn: () => Promise<void>) => {
    setBusy(true); setMessage(null);
    try { await fn(); await refresh(); }
    catch (e) {
      if (isStaleVersion(e)) setConflict(id);
      else setMessage(e instanceof GearsApiError && e.errors.length ? e.errors.map((x) => `${x.path || '/'}: ${x.message}`).join('; ') : e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };
  const setRef = (rec: Pick<BoxRecord, 'id' | 'version' | 'name'>) => setMem({ ...mem, ref: { id: rec.id, version: rec.version, project: mem.project, name: rec.name } });

  const save = (asNew: boolean) => guard(mem.ref?.id ?? null, async () => {
    const body = await buildSave();
    const clean = name.trim() || t('defaultName');
    if (asNew || !mem.ref) {
      const rec = await boxesApi.create(project, { name: clean, request: body.request, ...(body.response ? { response: body.response } : {}) });
      setRef(rec);
    } else {
      const rec = await boxesApi.update(project, mem.ref.id, { version: mem.ref.version, name: clean, request: body.request, response: body.response ?? null });
      setRef(rec); setConflict(null);
    }
  });
  const open = (id: string) => guard(id, async () => {
    const rec = await boxesApi.get(project, id);
    onOpen(rec); setRef(rec); setConflict(null);
  });
  const toggleState = (b: BoxSummary) => guard(b.id, async () => {
    const rec = await (b.state === 'active' ? boxesApi.archive(project, b.id, b.version) : boxesApi.restore(project, b.id, b.version));
    if (mem.ref?.id === rec.id) setRef(rec);
  });

  const shown = visibleBoxes(list, mem.showArchived);
  const current = mem.ref;
  return (
    <Paper variant="outlined" data-testid="gears-saved" sx={{ p: 1, borderColor: 'var(--line)', display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography sx={{ fontWeight: 600, fontSize: 14 }}><Lbl k="saved" /></Typography>
        <Tooltip title={t('tip.saveName')}>
          <TextField size="small" label={t('l.saveName')} value={name} onChange={(e) => onName(e.target.value)} sx={{ width: 170 }}
            slotProps={{ inputLabel: { shrink: true }, htmlInput: { maxLength: 120, 'data-testid': 'gears-save-name' } }} />
        </Tooltip>
        <Tooltip title={t('tip.project')}>
          <TextField size="small" label={t('l.project')} value={projectText} onChange={(e) => setProjectText(e.target.value)} sx={{ width: 130 }}
            onBlur={() => { if (projectText !== mem.project) setMem({ ...mem, project: projectText.trim(), ref: null }); }}
            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
            placeholder={t('workspace')} slotProps={{ inputLabel: { shrink: true } }} />
        </Tooltip>
        <Tooltip title={t('tip.saveNew')}><span><Button size="small" variant="outlined" disabled={busy} data-testid="gears-save-new" onClick={() => save(true)}>{t('l.saveNew')}</Button></span></Tooltip>
        <Tooltip title={t('tip.saveChanges')}><span><Button size="small" variant="contained" disabled={busy || !current} data-testid="gears-save-changes" onClick={() => save(false)}>{t('l.saveChanges')}</Button></span></Tooltip>
        {current && <Tooltip title={t('tip.currentRecord', { id: current.id, version: current.version })}><Chip size="small" variant="outlined" label={current.name} data-testid="gears-current" sx={{ height: 22 }} /></Tooltip>}
      </Box>
      {conflict && (
        <Box role="alert" data-testid="gears-conflict" sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
          <Typography sx={{ color: 'error.main', fontSize: 13, fontWeight: 600 }}>{t('changedElsewhere')}</Typography>
          <Button size="small" color="error" variant="outlined" data-testid="gears-reload" onClick={() => open(conflict)}>{t('l.reload')}</Button>
        </Box>
      )}
      {message && <Typography role="alert" data-testid="gears-save-error" sx={{ color: 'error.main', fontSize: 13 }}>{message}</Typography>}
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Tooltip title={t('tip.showArchived')}>
          <FormControlLabel sx={{ m: 0 }} control={<Switch size="small" checked={mem.showArchived} onChange={(_, v) => setMem({ ...mem, showArchived: v })} inputProps={{ 'aria-label': t('l.showArchived') }} />}
            label={<Typography sx={{ fontSize: 12 }}>{t('l.showArchived')}{shown.archived ? ` (${shown.archived})` : ''}</Typography>} />
        </Tooltip>
        {shown.unreadable > 0 && (
          <Tooltip title={t('tip.unreadable')}><Chip size="small" color="error" variant="outlined" data-testid="gears-unreadable" label={t('unreadable', { count: shown.unreadable })} sx={{ height: 22 }} /></Tooltip>
        )}
      </Box>
      <Box data-testid="gears-saved-list" sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        {shown.items.length === 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('noSaved')}</Typography>}
        {shown.items.map((b) => (
          <Box key={b.id} data-testid={`gears-box-${b.id}`} data-state={b.state} sx={{ display: 'flex', gap: 0.75, alignItems: 'center', flexWrap: 'wrap', opacity: b.state === 'archived' ? 0.7 : 1 }}>
            <Typography sx={{ fontSize: 13, minWidth: 120 }}>{b.name}</Typography>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>v{b.version}</Typography>
            {b.state === 'archived' && <Chip size="small" variant="outlined" label={t('archived')} sx={{ height: 20, fontSize: 11 }} />}
            {boxChips(b).map((c) => (
              <Tooltip key={c} title={t(`flagTip.${c}`, { defaultValue: c })}>
                <Chip size="small" variant="outlined" label={t(`flag.${c}`, { defaultValue: c })} data-flag={c}
                  sx={{ height: 20, fontSize: 11, color: c === 'response_stale' || c === 'catalogue_changed' || c === 'core_stale' ? 'var(--warn, #ed6c02)' : 'var(--err, #d32f2f)', borderColor: 'currentColor' }} />
              </Tooltip>
            ))}
            <Box sx={{ flex: 1 }} />
            <Button size="small" disabled={busy} data-testid={`gears-open-${b.id}`} onClick={() => open(b.id)}>{t('l.open')}</Button>
            <Button size="small" disabled={busy} data-testid={`gears-toggle-${b.id}`} onClick={() => toggleState(b)}>{t(b.state === 'active' ? 'l.archive' : 'l.restore')}</Button>
          </Box>
        ))}
      </Box>
    </Paper>
  );
}
