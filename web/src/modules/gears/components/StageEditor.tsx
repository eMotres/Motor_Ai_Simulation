// The stage list: planetary / compound planetary / pair, each with its gears and (optional) catalogue bearings.
import { Box, Button, IconButton, MenuItem, Paper, TextField, Tooltip, Typography } from '@mui/material';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import { useTranslation } from 'react-i18next';
import { BEARING_SLOTS, FIXED_MEMBERS, GEAR_FIELDS, ROLES, defaultStage, gearPath, humanoidJoint, type EditorState, type GearField } from '../plain';
import type { CataloguesAnswer, GearRole, PlainStage, StageKind } from '../types';
import { Lbl, NumField } from './parts';

interface Props {
  state: EditorState; onChange: (next: EditorState) => void; errors: Record<string, string>; catalogues: CataloguesAnswer | null;
}

const GEAR_STEP: Partial<Record<GearField, number>> = { z: 1 };

export default function StageEditor({ state, onChange, errors, catalogues }: Props) {
  const { t } = useTranslation('gears');
  const setStage = (i: number, patch: Partial<PlainStage>) =>
    onChange({ ...state, stages: state.stages.map((s, k) => (k === i ? { ...s, ...patch } : s)) });
  const setGear = (i: number, role: GearRole, field: GearField, v: number) => {
    const s = state.stages[i];
    setStage(i, { gears: { ...s.gears, [role]: { ...s.gears[role]!, [field]: v } } });
  };
  const move = (i: number, d: number) => {
    const to = i + d;
    if (to < 0 || to >= state.stages.length) return;
    const stages = [...state.stages];
    [stages[i], stages[to]] = [stages[to], stages[i]];
    onChange({ ...state, stages });
  };
  const add = (kind: StageKind) => {
    if (state.stages.length >= 6) return;
    const n = state.stages.length + 1;
    onChange({ ...state, stages: [...state.stages, defaultStage(kind, `s${n}`)] });
  };
  const bearings = (catalogues?.bearings ?? []).filter((b) => b.core_supported);

  return (
    <Box data-testid="gears-stage-editor" sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <Typography sx={{ fontWeight: 600, fontSize: 14 }}><Lbl k="stages" /></Typography>
        <Tooltip title={t('tip.presetJoint')}><Button size="small" variant="outlined" data-testid="gears-preset-joint" onClick={() => onChange(humanoidJoint())}>{t('l.presetJoint')}</Button></Tooltip>
        {(['planetary', 'compound_planetary', 'pair'] as StageKind[]).map((k) => (
          <Tooltip key={k} title={t(`tip.add_${k}`)}><span><Button size="small" disabled={state.stages.length >= 6} data-testid={`gears-add-${k}`} onClick={() => add(k)}>+ {t(`kind.${k}`)}</Button></span></Tooltip>
        ))}
      </Box>
      {state.stages.map((s, i) => (
        <Paper key={s.uid} variant="outlined" sx={{ p: 1, borderColor: 'var(--line)' }} data-testid={`gears-stage-${i}`}>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 600, minWidth: 18 }}>{i + 1}</Typography>
            <Tooltip title={errors[`/stages/${i}/name`] ?? t('tip.stageName')}>
              <TextField size="small" label={t('l.stageName')} value={s.name} error={!!errors[`/stages/${i}/name`]}
                onChange={(e) => setStage(i, { name: e.target.value })} sx={{ width: 110 }} slotProps={{ inputLabel: { shrink: true } }} />
            </Tooltip>
            <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t(`kind.${s.kind}`)}</Typography>
            {s.kind !== 'pair' && (<>
              <NumField label={t('l.nPlanets')} tip={t('tip.nPlanets')} value={s.n_planets ?? 3} width={72}
                error={errors[`/stages/${i}/n_planets`]} onChange={(v) => setStage(i, { n_planets: Math.round(v) })} />
              <Tooltip title={t('tip.fixedMember')}>
                <TextField select size="small" label={t('l.fixedMember')} value={s.fixed_member ?? 'ring'} sx={{ width: 96 }}
                  onChange={(e) => setStage(i, { fixed_member: e.target.value })} slotProps={{ inputLabel: { shrink: true } }}>
                  {FIXED_MEMBERS.map((m) => <MenuItem key={m} value={m}>{t(`member.${m}`)}</MenuItem>)}
                </TextField>
              </Tooltip>
            </>)}
            <Box sx={{ flex: 1 }} />
            <Tooltip title={t('l.up')}><span><IconButton size="small" disabled={i === 0} aria-label={t('l.up')} onClick={() => move(i, -1)}><ArrowUpwardIcon fontSize="small" /></IconButton></span></Tooltip>
            <Tooltip title={t('l.down')}><span><IconButton size="small" disabled={i === state.stages.length - 1} aria-label={t('l.down')} onClick={() => move(i, 1)}><ArrowDownwardIcon fontSize="small" /></IconButton></span></Tooltip>
            <Tooltip title={t('l.remove')}><span><IconButton size="small" disabled={state.stages.length <= 1} aria-label={t('l.remove')} data-testid={`gears-remove-${i}`}
              onClick={() => onChange({ ...state, stages: state.stages.filter((_, k) => k !== i) })}><DeleteOutlineIcon fontSize="small" /></IconButton></span></Tooltip>
          </Box>
          {ROLES[s.kind].map((role) => (
            <Box key={role} sx={{ display: 'flex', gap: 0.75, alignItems: 'center', flexWrap: 'wrap', mb: 0.75 }}>
              <Typography sx={{ width: 62, fontSize: 12 }}>{t(`role.${role}`)}</Typography>
              {GEAR_FIELDS.map((f) => (
                <NumField key={f} label={t(`gear.${f}`)} tip={t(`gearTip.${f}`)} value={s.gears[role]?.[f] ?? (f === 'z' ? 0 : 0)}
                  step={GEAR_STEP[f]} width={f === 'z' ? 64 : 84} testId={`gears-${i}-${role}-${f}`}
                  error={errors[gearPath(i, role, f)]} onChange={(v) => setGear(i, role, f, v)} />
              ))}
              {Object.keys(errors).filter((p) => p.startsWith(`/stages/${i}/gears/${role}`) && !GEAR_FIELDS.some((f) => p === gearPath(i, role, f))).map((p) => (
                <Typography key={p} role="alert" sx={{ color: 'error.main', fontSize: 12 }}>{errors[p]}</Typography>
              ))}
            </Box>
          ))}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {BEARING_SLOTS[s.kind].map((slot) => (
              <Tooltip key={slot} title={errors[`/stages/${i}/bearings/${slot}`] ?? t('tip.bearingSlot')}>
                <TextField select size="small" label={t(`slot.${slot}`)} value={s.bearings[slot] ?? ''} sx={{ width: 150 }}
                  error={!!errors[`/stages/${i}/bearings/${slot}`]} slotProps={{ inputLabel: { shrink: true } }}
                  onChange={(e) => setStage(i, { bearings: { ...s.bearings, [slot]: e.target.value } })}>
                  <MenuItem value="">{t('l.none')}</MenuItem>
                  {bearings.map((b) => <MenuItem key={b.name} value={b.name}>{b.name}</MenuItem>)}
                </TextField>
              </Tooltip>
            ))}
          </Box>
          {Object.keys(errors).filter((p) => p.startsWith(`/stages/${i}`) && !p.includes('/gears/') && !p.includes('/bearings/') && p !== `/stages/${i}/name`).map((p) => (
            <Typography key={p} role="alert" sx={{ color: 'error.main', fontSize: 12, mt: 0.5 }}>{p}: {errors[p]}</Typography>
          ))}
        </Paper>
      ))}
    </Box>
  );
}
