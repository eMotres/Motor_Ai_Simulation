/** "Updates by e-mail" - the compact settings block of a development project (owner / admin) and of a production (admin).
 *
 *  Three modes (owner decision 2026-10-28): "auto" (default - the daily 06:00 run sends the digest by itself, only when something
 *  changed), "approve" (the digest is drafted and waits in Admin -> Digests until the owner sends or discards it) and "off".
 *  Nothing is sent from here: this block only stores the setting. */
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Checkbox, Chip, FormControlLabel, MenuItem, Paper, Select, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { digestApi } from './digestApi';
import { isListed, settingsChange, toggleChosen, type DigestSettingsView, type DigestTarget } from './digestModel';

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.25, mb: 1.5 } as const;

const DigestSettings: React.FC<{ target: DigestTarget }> = ({ target }) => {
  const { t } = useTranslation('common');
  const key = target.kind === 'project' ? target.id : `${target.die}|${target.config}`;
  const [view, setView] = useState<DigestSettingsView | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    setView(null); setErr('');
    digestApi.settings(target).then((v) => { if (alive) setView(v); }).catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const save = useCallback(async (change: Parameters<typeof settingsChange>[1]) => {
    if (!view) return;
    const body = settingsChange(view, change);
    if (!body) return;
    setBusy(true); setErr('');
    try { setView(await digestApi.saveSettings(target, body)); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, key]);

  if (!view) return err ? <Alert severity="warning" sx={{ mb: 1.5, py: 0 }}>{t('digest.loadFailed', { reason: err })}</Alert> : null;
  const roleLabel = (r: string) => t(`digest.role.${r}`, { defaultValue: r });

  return (
    <Paper variant="outlined" sx={PANEL} data-testid="digest-settings">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-1)' }}>{t('digest.title')}</Typography>
        <HelpTip title={t('digest.tip')} />
        <Chip size="small" label={t(`digest.mode.${view.mode}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700 }} />
        {view.mode !== 'off' && <Chip size="small" label={t(`digest.frequency.${view.frequency}`)} sx={{ height: 18, fontSize: 10.5 }} />}
        {view.mode !== 'off' && <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{t('digest.recipientsCount', { count: view.will_receive.length })}</Typography>}
        {view.pending && <Chip size="small" color="warning" label={t('digest.pendingChip')} sx={{ height: 18, fontSize: 10.5 }} />}
        <Box sx={{ flex: 1 }} />
        <Button size="small" sx={{ textTransform: 'none', fontSize: 12 }} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? t('digest.hide') : t('digest.change')}
        </Button>
      </Box>
      {err && <Alert severity="error" sx={{ mt: 1, py: 0 }}>{err}</Alert>}
      {open && (
        <Box sx={{ mt: 1.25, display: 'grid', gap: 1.25 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
            <Select size="small" value={view.mode} disabled={busy} onChange={(e) => void save({ mode: String(e.target.value) })} inputProps={{ 'aria-label': t('digest.modeLabel') }}>
              <MenuItem value="auto">{t('digest.mode.auto')}</MenuItem>
              <MenuItem value="approve">{t('digest.mode.approve')}</MenuItem>
              <MenuItem value="off">{t('digest.mode.off')}</MenuItem>
            </Select>
            <Select size="small" value={view.frequency} disabled={busy || view.mode === 'off'} onChange={(e) => void save({ frequency: String(e.target.value) })} inputProps={{ 'aria-label': t('digest.frequencyLabel') }}>
              <MenuItem value="daily">{t('digest.frequency.daily')}</MenuItem>
              <MenuItem value="weekly">{t('digest.frequency.weekly')}</MenuItem>
            </Select>
            <Select size="small" value={view.recipients} disabled={busy || view.mode === 'off'} onChange={(e) => void save({ recipients: String(e.target.value) })} inputProps={{ 'aria-label': t('digest.recipientsLabel') }}>
              <MenuItem value="all">{t('digest.recipients.all')}</MenuItem>
              <MenuItem value="chosen">{t('digest.recipients.chosen')}</MenuItem>
            </Select>
          </Box>
          {view.mode !== 'off' && (
            <Box sx={{ display: 'grid', gap: 0.25 }}>
              {view.members.map((m) => {
                const listed = isListed(view, m.email);
                const skipped = view.not_receiving.find((s) => s.email === m.email);
                return (
                  <Box key={m.email} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <FormControlLabel
                      control={<Checkbox size="small" checked={listed} disabled={busy || view.recipients === 'all'}
                        onChange={(e) => void save({ chosen: toggleChosen(view.chosen, m.email, e.target.checked) })} />}
                      label={<Typography sx={{ fontSize: 12 }}>{m.email} · {roleLabel(m.role)}</Typography>} />
                    {m.opted_out && <Chip size="small" label={t('digest.leftByMember')} sx={{ height: 16, fontSize: 10 }} />}
                    {!m.opted_out && skipped && skipped.reason !== 'not_chosen' && (
                      <Chip size="small" label={t(`digest.skip.${skipped.reason}`, { defaultValue: skipped.reason })} sx={{ height: 16, fontSize: 10 }} />
                    )}
                  </Box>
                );
              })}
            </Box>
          )}
          {view.updated_by && <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{t('digest.changedBy', { who: view.updated_by, when: (view.updated_at ?? '').slice(0, 16).replace('T', ' ') })}</Typography>}
        </Box>
      )}
    </Paper>
  );
};

export default DigestSettings;
