// "With motor": pick a die and configuration, give output load rows, choose the gearbox source, ask the route.  Saved points only.
import { useEffect, useMemo, useState } from 'react';
import { Box, Button, CircularProgress, IconButton, MenuItem, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import { useTranslation } from 'react-i18next';
import { useRememberedView } from '../../../lib/useRememberedView';
import { fetchFamilyTree } from '../../../lib/familyTree';
import { GearsApiError, drivetrainApi } from '../api';
import { MAX_ROWS, buildDrivetrainRequest, defaultRows, drivetrainErrorKey, motorOptions, validateMotorMemory, type DrivetrainResponse, type LoadRow, type MotorMemory } from '../drivetrain';
import { errorMap } from '../plain';
import type { RequestRecord } from '../types';
import DrivetrainResults from './DrivetrainResults';
import { NumField } from './parts';

export interface SavedRefLite { id: string; project: string; name: string }

export default function MotorPanel({ buildInline, saved }: { buildInline: () => Promise<RequestRecord>; saved: SavedRefLite | null }) {
  const { t } = useTranslation('gears');
  const [mem, setMem] = useRememberedView<MotorMemory>('gears.motor', { die: '', configuration: '', rows: defaultRows(), source: 'inline' }, validateMotorMemory);
  const [tree, setTree] = useState<unknown>(null);
  const [result, setResult] = useState<DrivetrainResponse | null>(null);
  const [errors, setErrors] = useState<Array<{ path: string; message: string }>>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    fetchFamilyTree().then((v) => { if (live) setTree(v); }).catch((e) => { if (live) setFailure(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, []);
  const options = useMemo(() => motorOptions(tree), [tree]);
  const die = options.dies.find((d) => d.name === mem.die);
  const cfg = die?.configs.find((x) => x.name === mem.configuration);
  const fieldErr = useMemo(() => errorMap(errors.map((e) => ({ path: drivetrainErrorKey(e.path), code: '', message: e.message }))), [errors]);

  const setRow = (i: number, patch: Partial<LoadRow>) => setMem({ ...mem, rows: mem.rows.map((r, k) => (k === i ? { ...r, ...patch } : r)) });
  const useRecord = mem.source === 'record' && !!saved;

  const run = async () => {
    setBusy(true); setErrors([]); setFailure(null);
    try {
      const source = useRecord ? { kind: 'record' as const, record_id: saved!.id, project: saved!.project } : { kind: 'inline' as const, request: await buildInline() };
      setResult(await drivetrainApi.run(buildDrivetrainRequest({ die: mem.die, configuration: mem.configuration }, mem.rows, source)));
    } catch (e) {
      setResult(null);
      if (e instanceof GearsApiError && e.errors.length) setErrors(e.errors.map((x) => ({ path: x.path, message: x.message })));
      else setFailure(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };
  const ready = !!die && !!cfg && mem.rows.length > 0;
  const rowErr = (i: number, f: string) => fieldErr[`/load/${i}/${f}`];
  const unplaced = errors.filter((e) => !/^\/(motor\/(die|configuration)|load\/\d+\/)/.test(drivetrainErrorKey(e.path)));

  return (
    <Box data-testid="gears-motor" sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <Tooltip title={fieldErr['/motor/die'] ?? t('tip.motorDie')}>
          <TextField select size="small" label={t('l.motorDie')} value={die ? mem.die : ''} sx={{ width: 190 }} error={!!fieldErr['/motor/die']} data-testid="gears-motor-die"
            onChange={(e) => setMem({ ...mem, die: e.target.value, configuration: '' })} slotProps={{ inputLabel: { shrink: true } }}>
            {options.dies.map((d) => <MenuItem key={d.name} value={d.name}>{d.name}</MenuItem>)}
          </TextField>
        </Tooltip>
        <Tooltip title={fieldErr['/motor/configuration'] ?? t('tip.motorConfig')}>
          <TextField select size="small" label={t('l.motorConfig')} value={cfg ? mem.configuration : ''} sx={{ width: 190 }} error={!!fieldErr['/motor/configuration']} data-testid="gears-motor-config" disabled={!die}
            onChange={(e) => setMem({ ...mem, configuration: e.target.value })} slotProps={{ inputLabel: { shrink: true } }}>
            {(die?.configs ?? []).map((x) => <MenuItem key={x.name} value={x.name}>{x.name}</MenuItem>)}
          </TextField>
        </Tooltip>
        <Tooltip title={t('tip.gearboxSource')}>
          <ToggleButtonGroup size="small" exclusive value={useRecord ? 'record' : 'inline'} onChange={(_, v) => v && setMem({ ...mem, source: v })} aria-label={t('l.gearboxSource')}>
            <ToggleButton value="inline" sx={{ textTransform: 'none', py: 0.4 }}>{t('l.sourceInline')}</ToggleButton>
            <ToggleButton value="record" disabled={!saved} sx={{ textTransform: 'none', py: 0.4 }} data-testid="gears-source-record">{saved ? saved.name : t('l.sourceRecord')}</ToggleButton>
          </ToggleButtonGroup>
        </Tooltip>
        {fieldErr['/gearbox'] && <Typography role="alert" sx={{ color: 'error.main', fontSize: 12 }}>{fieldErr['/gearbox']}</Typography>}
      </Box>

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }} data-testid="gears-load-rows">
        <Typography sx={{ fontWeight: 600, fontSize: 13 }}><Tooltip title={t('tip.loadRows')}><span>{t('l.loadRows')}</span></Tooltip></Typography>
        {mem.rows.map((r, i) => (
          <Box key={i} data-testid={`gears-load-${i}`} sx={{ display: 'flex', gap: 0.75, alignItems: 'center', flexWrap: 'wrap' }}>
            <Tooltip title={rowErr(i, 'name') ?? t('tip.loadName')}>
              <TextField size="small" label={t('l.loadName')} value={r.name} error={!!rowErr(i, 'name') || !!rowErr(i, 'mode')} sx={{ width: 120 }}
                onChange={(e) => setRow(i, { name: e.target.value })} slotProps={{ inputLabel: { shrink: true } }} />
            </Tooltip>
            <NumField label={t('l.loadTorque')} tip={t('tip.loadTorque')} value={r.torque_out_Nm} error={rowErr(i, 'torque_out_Nm')} width={104} onChange={(v) => setRow(i, { torque_out_Nm: v })} />
            <NumField label={t('l.loadSpeed')} tip={t('tip.loadSpeed')} value={r.speed_out_rpm} error={rowErr(i, 'speed_out_rpm')} width={104} onChange={(v) => setRow(i, { speed_out_rpm: v })} />
            <Tooltip title={t('tip.loadDuty')}>
              <TextField select size="small" label={t('l.loadDuty')} value={cfg?.duties.some((d) => d.name === r.duty) ? r.duty : ''} sx={{ width: 150 }} disabled={!cfg}
                onChange={(e) => setRow(i, { duty: e.target.value })} slotProps={{ inputLabel: { shrink: true } }}>
                <MenuItem value="">{t('l.dutyAuto')}</MenuItem>
                {(cfg?.duties ?? []).map((d) => <MenuItem key={d.name} value={d.name}>{d.name}{d.kind ? ` (${d.kind})` : ''}</MenuItem>)}
              </TextField>
            </Tooltip>
            <Tooltip title={t('l.remove')}><span><IconButton size="small" disabled={mem.rows.length <= 1} aria-label={t('l.remove')} onClick={() => setMem({ ...mem, rows: mem.rows.filter((_, k) => k !== i) })}><DeleteOutlineIcon fontSize="small" /></IconButton></span></Tooltip>
            {[...Object.entries(fieldErr)].filter(([p]) => p.startsWith(`/load/${i}/`) && !/\/(torque_out_Nm|speed_out_rpm|name)$/.test(p)).map(([p, m]) => <Typography key={p} role="alert" sx={{ color: 'error.main', fontSize: 12 }}>{m}</Typography>)}
          </Box>
        ))}
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <Tooltip title={t('tip.addLoad')}><span><Button size="small" disabled={mem.rows.length >= MAX_ROWS} data-testid="gears-add-load"
            onClick={() => setMem({ ...mem, rows: [...mem.rows, { name: `load${mem.rows.length + 1}`, torque_out_Nm: 5, speed_out_rpm: 100, duty: '' }] })}>+ {t('l.addLoad')}</Button></span></Tooltip>
          <Button size="small" variant="contained" disabled={busy || !ready} data-testid="gears-dt-run" onClick={run}>{t('l.calculate')}</Button>
          {busy && <CircularProgress size={16} />}
        </Box>
      </Box>

      {failure && <Typography role="alert" data-testid="gears-dt-failure" sx={{ color: 'error.main', fontSize: 13 }}>{failure}</Typography>}
      {unplaced.length > 0 && (
        <Box role="alert" data-testid="gears-dt-errors" sx={{ border: 1, borderColor: 'error.main', borderRadius: 1, p: 1 }}>
          {unplaced.map((e, i) => <Typography key={i} sx={{ color: 'error.main', fontSize: 13 }}>{e.path || '/'}: {e.message}</Typography>)}
        </Box>
      )}
      {result ? <DrivetrainResults result={result} /> : <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('noResult')}</Typography>}
    </Box>
  );
}
