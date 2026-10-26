/**
 * PointsConfigure - Configure in its POINTS-LIMITED mode (owner 2026-10-26).
 *
 * A motor without a full passport but with computed duty points opens here - whether it was loaded from the Motors tab or
 * from a project (ONE load path: lib/openProjectMotor -> applyCatalogDuty).  It shows only what the stores keep:
 *
 *   - a discrete mode switch (one button per stored duty point; no interpolation, nothing between the points),
 *   - the result tiles Configure shows, wherever the point stores the value ("-" otherwise),
 *   - THERMAL and MECHANICAL results with their provenance (StoredPointResults),
 *   - the stored field maps in the read-only viewer (StoredFieldViewer: no settings, no run buttons),
 *   - when the motor belongs to the project in focus: the design steps (opt1 -> opt2 -> opt3), the step table and the
 *     tile colouring against another step,
 *   - the continuous knobs (speed, current, angle) shown disabled with one tooltip line.
 *
 * Nothing is interpolated or invented; a missing value stays "-", a missing kind says "not computed".
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, Chip, Slider, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { MetricTile, PANEL, LABEL, TileRow, fmt } from '../common/ResultTiles';
import StoredFieldViewer from './StoredFieldViewer';
import StoredPointResults from './StoredPointResults';
import StepsChart from './StepsChart';
import { developmentApi } from '../../modules/development/api';
import type { FieldKind, MotorPointVariant, MotorView, MotorViewDuty, StoredField } from '../../modules/development/types';
import {
  POINT_TILES, TILE_GROUPS, busHeadroom, findVariantIndex, pointOperating, tileValue, visibleTiles,
} from '../../modules/development/pointsOnly';
import { FIELD_KIND_ORDER, configureTileKey, isNotComputed, massKind, pickPointDuty, pointDuties, provenanceLine, storedFieldKinds } from '../../modules/development/pointsLimited';
import { parametersLine } from '../../modules/development/storedFieldPayload';
import { motorColor } from '../../modules/development/motorGroups';
import { fetchMotorField } from '../../lib/motorViewApi';
import { openProjectMotor } from '../../lib/openProjectMotor';
import { nsT } from '../../i18n/nsT';
import { stepLabels } from '../../modules/development/stepSeries';
import { useProjectFocusStore } from '../../stores/projectFocusStore';
import { useUIStore } from '../../stores/uiStore';

// the tile names are the ordinary Configure's (controller namespace), so a point reads the same in both (owner 2026-10-26)
const tc = nsT('controller');

const TH = { px: 1, py: 0.5, fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textAlign: 'right', whiteSpace: 'nowrap', borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)' } as const;
const TD = { px: 1, py: 0.4, fontSize: 12, textAlign: 'right', whiteSpace: 'nowrap', borderBottom: '1px solid var(--app-bg)', fontFamily: 'monospace', color: 'var(--text-1)' } as const;

interface Props {
  view: MotorView;
  /** the duty the load made current (the mode switch opens on it when it has a stored point) */
  loadedDuty: string | null;
}

const PointsConfigure: React.FC<Props> = ({ view, loadedDuty }) => {
  const { t } = useTranslation('development');
  useTranslation('controller');                       // lazy-loads the Configure labels the tiles borrow
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const focus = useProjectFocusStore((s) => s.focus);
  const [steps, setSteps] = useState<MotorPointVariant[]>([]);
  const [stepIndex, setStepIndex] = useState(-1);
  const [duty, setDuty] = useState<string | null>(() => pickPointDuty(view, loadedDuty));
  const [kind, setKind] = useState<FieldKind | null>(null);
  const [field, setField] = useState<{ key: string; value: StoredField | null; error: string; missing?: boolean } | null>(null);
  const [openError, setOpenError] = useState('');
  const [busText, setBusText] = useState('');
  const cache = useRef(new Map<string, StoredField>());

  const motorKey = `${view.die}\u0000${view.config}`;
  // a new motor (or the same one reloaded) restarts the mode at the loaded duty; the user's own switch is kept otherwise
  useEffect(() => { setDuty((cur) => pickPointDuty(view, loadedDuty, cur)); cache.current.clear(); setField(null); }, [view, loadedDuty]);

  // design steps: only when the loaded motor belongs to the project in focus
  useEffect(() => {
    setSteps([]); setStepIndex(-1);
    if (!focus) return;
    let cancelled = false;
    developmentApi.motorPoints(focus.id, { showAll: true })
      .then((answer) => {
        if (cancelled) return;
        const index = findVariantIndex(answer.variants, view.die, view.config);
        if (index >= 0) { setSteps(answer.variants); setStepIndex(index); }
      })
      .catch(() => { /* no project steps: the motor is still shown */ });
    return () => { cancelled = true; };
  }, [focus?.id, motorKey]);

  const points = useMemo(() => pointDuties(view), [view]);
  const current: MotorViewDuty | null = points.find((d) => d.duty === duty) ?? null;
  const kinds = storedFieldKinds(current);
  const activeKind: FieldKind | null = kind && kinds.includes(kind) ? kind : (kinds[0] ?? null);

  // the stored map of the chosen kind (cached per motor / duty / kind; project route when the motor is a project step)
  useEffect(() => {
    if (!current || !activeKind) { setField(null); return; }
    const key = `${motorKey}\u0000${current.duty}\u0000${activeKind}`;
    const hit = cache.current.get(key);
    if (hit) { setField({ key, value: hit, error: '' }); return; }
    let cancelled = false;
    setField({ key, value: null, error: '' });
    const request = focus && stepIndex >= 0
      ? developmentApi.motorField(focus.id, view.die, view.config, current.duty, activeKind, true)
      : fetchMotorField(view.die, view.config, current.duty, activeKind);
    request
      .then((value) => { if (!cancelled) { cache.current.set(key, value); setField({ key, value, error: '' }); } })
      .catch((e) => { if (!cancelled) setField({ key, value: null, error: isNotComputed(e) ? '' : (e instanceof Error ? e.message : String(e)), missing: isNotComputed(e) }); });
    return () => { cancelled = true; };
  }, [motorKey, current?.duty, activeKind, focus?.id, stepIndex]);

  const peers = useMemo(() => steps.map((v) => v.points.find((p) => p.duty === duty) ?? null), [steps, duty]);
  const labels = useMemo(() => stepLabels(steps), [steps]);
  // temperatures are NOT in this group of tiles: they belong to the coupled loop and sit in the thermal block with their own source
  const tiles = visibleTiles(current, peers).filter((spec) => spec.group !== 'thermal');
  const tileName = (id: string) => { const key = configureTileKey(id, current); return key ? tc(key) : t(`points.tile.${id}`); };
  const tileTip = (id: string) => {
    if (id === 'mass') return massKind(current) === 'active' ? tc('configure.massPartialTip') : tc('configure.massFullTip');
    if (id === 'torqueMass') return massKind(current) === 'active' ? t('points.tileTip.torqueActiveMass') : t('points.tileTip.torqueMass');
    return t(`points.tileTip.${id}`);
  };
  const emSource = current?.sources?.em;
  const emChip = emSource?.kind === 'coupled' ? t('points.source.coupledChip')
    : emSource?.coil_temp_c !== undefined ? t('points.source.emChip', { winding: Math.round(emSource.coil_temp_c) }) : t('points.source.emChipPlain');
  const emChipTip = emSource?.kind === 'coupled' ? t('points.source.coupledTip')
    : t(emSource?.coil_temp_c !== undefined ? 'points.source.emTip' : 'points.source.emTipPlain', { winding: emSource?.coil_temp_c !== undefined ? Math.round(emSource.coil_temp_c) : '', magnet: emSource?.magnet_temp_c !== undefined ? Math.round(emSource.magnet_temp_c) : '—' });
  const showChart = steps.length > 1 && stepIndex >= 0;
  const operating = pointOperating(current);
  const busV = busText.trim() === '' ? null : Number(busText.replace(',', '.'));
  const busValid = busV !== null && Number.isFinite(busV) && busV > 0;
  const headroom = busValid ? busHeadroom(current, busV) : null;

  const openStep = (index: number) => {
    const v = steps[index];
    if (!v || !focus || index === stepIndex) return;
    setOpenError('');
    void openProjectMotor(focus, {
      die: v.die, config: v.config, configure_duty: null, default_duty: null,
      duties: v.points.map((p) => p.duty), results: v.points.map((p) => ({ duty: p.duty, has_result: true })),
    }, v.points.some((p) => p.duty === duty) ? duty : null).catch((e) => setOpenError(e instanceof Error ? e.message : String(e)));
  };

  const knobs: Array<{ id: string; value: number | null; unit: string }> = [
    { id: 'speed', value: operating.rpm, unit: 'rpm' },
    { id: 'current', value: operating.currentA, unit: 'A' },
    { id: 'gamma', value: operating.gammaDeg, unit: '°' },
  ];
  const fieldLabel = field?.value
    ? parametersLine({ rpm: operating.rpm, currentA: operating.currentA, gammaDeg: operating.gammaDeg, provenance: provenanceLine(field.value.provenance) })
    : '';

  return (
    <Box sx={{ height: '100%', overflow: 'auto', p: 1.5, display: 'flex', flexDirection: 'column', gap: 1.25 }} data-testid="points-configure">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>{t('points.title')}</Typography>
        <HelpTip title={t('points.pendingTip')} />
        <Chip size="small" label={t('points.pending')} sx={{ color: 'var(--text-3)', bgcolor: 'var(--panel-2)' }} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{view.die} / {view.config}</Typography>
        <Box sx={{ flex: 1 }} />
        {focus && steps.length > 0 && <Button size="small" onClick={() => setActiveTab('development' as never)}>{t('points.backToProject')}</Button>}
      </Box>
      {openError && <Typography role="alert" sx={{ fontSize: 12, color: 'error.main' }}>{t('points.loadFailed')}: {openError}</Typography>}

      <Box sx={{ ...PANEL, p: 1, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        {steps.length > 0 && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography sx={LABEL}>{t('points.step')}</Typography>
            <ToggleButtonGroup exclusive size="small" value={stepIndex} aria-label={t('points.step')}
              onChange={(_, value) => { if (typeof value === 'number') openStep(value); }}>
              {steps.map((v, index) => (
                <ToggleButton key={`${v.die}/${v.config}`} value={index} sx={{ textTransform: 'none', px: 1.2, py: 0.3, fontSize: 12, boxShadow: `inset 3px 0 0 ${motorColor(v)}` }}>
                  <Box component="span" aria-hidden data-testid="step-accent" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: motorColor(v), mr: 0.75, flex: '0 0 8px' }} />
                  <Tooltip title={`${v.canonical_die} / ${v.config}`}><span>{labels[index]}</span></Tooltip>
                  {v.status === 'trial' && <Chip size="small" label={t('motorStatus.trial')} sx={{ ml: 0.75, height: 16, fontSize: 9 }} />}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>
        )}

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Typography sx={LABEL}>{t('points.mode')}</Typography>
          {points.length > 0
            ? <ToggleButtonGroup exclusive size="small" value={duty} aria-label={t('points.mode')}
                onChange={(_, value) => { if (typeof value === 'string') setDuty(value); }}>
                {points.map((p) => (
                  <ToggleButton key={p.duty} value={p.duty} sx={{ textTransform: 'none', px: 1.2, py: 0.3, fontSize: 12 }}>{p.duty}</ToggleButton>
                ))}
              </ToggleButtonGroup>
            : <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.noPoints')}</Typography>}
          <HelpTip title={t('points.modeTip')} />
        </Box>
      </Box>

      {current && <>
        <Box sx={{ ...PANEL, p: 1, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {knobs.map((k) => (
              <Tooltip key={k.id} title={`${t('points.knobTip')} ${t('points.knobsDisabledTip')}`}>
                <Box sx={{ ...PANEL, px: 1, py: 0.5, width: 190, boxSizing: 'border-box', opacity: 0.75 }}>
                  <Typography sx={{ ...LABEL, fontSize: 10.5 }}>{t(`points.knob.${k.id}`)}
                    <Box component="span" sx={{ ml: 0.75, fontFamily: 'monospace', color: 'var(--text-0)' }}>{k.value === null ? '—' : `${fmt(k.value, k.id === 'gamma' ? 1 : 0)} ${k.unit}`}</Box>
                  </Typography>
                  <Slider disabled size="small" min={0} max={Math.max(1, (k.value ?? 0) * 1.5)} value={k.value ?? 0} aria-label={t(`points.knob.${k.id}`)} sx={{ py: 0.75 }} />
                </Box>
              </Tooltip>
            ))}
          </Box>
        </Box>

        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0,1fr)', lg: showChart ? 'minmax(0,1.5fr) minmax(320px,1fr)' : 'minmax(0,1fr)' }, gap: 1.25, alignItems: 'stretch' }} data-testid="tiles-and-chart">
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, minWidth: 0 }} data-testid="em-tiles">
            <Tooltip title={emChipTip}>
              <Chip size="small" label={emChip} data-testid="em-source-chip" sx={{ alignSelf: 'flex-start', height: 20, fontSize: 11, color: 'var(--text-2)', bgcolor: 'var(--panel-2)' }} />
            </Tooltip>
            {TILE_GROUPS.filter((g) => g !== 'thermal').map((group) => {
              const groupTiles = tiles.filter((spec) => spec.group === group);
              if (groupTiles.length === 0) return null;
              return (
                <Box key={group}>
                  <TileRow>
                    {groupTiles.map((spec) => {
                      const value = tileValue(current, spec);
                      return <MetricTile key={spec.id} label={tileName(spec.id)} value={value} unit={spec.unit} d={spec.d}
                        base={value ?? 0} plain blankTip={t('points.notStored')} tip={tileTip(spec.id)} />;
                    })}
                  </TileRow>
                </Box>
              );
            })}
          </Box>
          {showChart && <StepsChart variants={steps} duty={duty} current={stepIndex} onPick={openStep} />}
        </Box>

        <StoredPointResults duty={current} />

        <Box sx={{ ...PANEL, p: 1, display: 'flex', flexDirection: 'column', gap: 0.75 }} data-testid="stored-fields">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography sx={LABEL}>{t('points.fields.title')}</Typography>
            <ToggleButtonGroup exclusive size="small" value={activeKind} aria-label={t('points.fields.title')}
              onChange={(_, value) => { if (value) setKind(value as FieldKind); }}>
              {FIELD_KIND_ORDER.map((k) => (
                <ToggleButton key={k} value={k} disabled={!kinds.includes(k)} sx={{ textTransform: 'none', px: 1.2, py: 0.3, fontSize: 12 }}>
                  <Tooltip title={kinds.includes(k) ? t(`points.fields.tip.${k}`) : t('points.notComputed')}><span>{t(`points.fields.kind.${k}`)}</span></Tooltip>
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>
          {kinds.length === 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.notComputed')}</Typography>}
          {field?.error && <Typography role="alert" sx={{ fontSize: 12, color: 'error.main' }}>{t('points.fields.loadFailed')}: {field.error}</Typography>}
          {field?.missing && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.notComputed')}</Typography>}
          {field && !field.value && !field.error && !field.missing && <Typography role="status" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.loading')}</Typography>}
          {field?.value && <StoredFieldViewer key={field.key} field={field.value} contextLabel={fieldLabel} />}
        </Box>

        {stepIndex >= 0 && (
          <Box sx={{ ...PANEL, p: 1, display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography sx={LABEL}>{t('points.bus')}</Typography>
            <TextField size="small" value={busText} onChange={(e) => setBusText(e.target.value)} placeholder="V"
              inputProps={{ inputMode: 'decimal', 'aria-label': t('points.bus') }} sx={{ width: 96, '& input': { py: 0.45, fontSize: 12 } }}
              error={busText.trim() !== '' && !busValid} />
            <HelpTip title={busValid && headroom === null ? `${t('points.busTip')} ${t('points.busNoRequirement')}` : t('points.busTip')} />
            {busText.trim() !== '' && !busValid && <Typography role="alert" sx={{ fontSize: 11, color: 'error.main' }}>{t('points.busInvalid')}</Typography>}
            {headroom && <Typography role="status" sx={{ fontSize: 12, fontWeight: 700, color: headroom.ok ? '#4ade80' : '#f87171' }}>
              {headroom.ok ? t('points.busOk', { required: fmt(headroom.requiredV, 1), margin: fmt(headroom.headroomPct, 1) }) : t('points.busShort', { required: fmt(headroom.requiredV, 1), margin: fmt(-headroom.headroomPct, 1) })}
            </Typography>}
          </Box>
        )}
      </>}

      {duty && steps.length > 0 && <Box sx={{ ...PANEL, overflowX: 'auto' }}>
        <Typography sx={{ ...LABEL, p: 1, pb: 0.25 }}>{t('points.stepsTable', { duty })}</Typography>
        <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead><tr>
            <Box component="th" sx={{ ...TH, textAlign: 'left' }}>{t('points.step')}</Box>
            {POINT_TILES.filter((spec) => spec.group !== 'thermal' && (spec.core || peers.some((peer) => tileValue(peer, spec) !== null))).map((spec) => (
              <Box component="th" key={spec.id} sx={TH}>{tileName(spec.id)} <span style={{ fontWeight: 400 }}>{spec.unit}</span></Box>
            ))}
          </tr></thead>
          <tbody>
            {steps.map((v, index) => {
              const p = peers[index];
              return (
                <Box component="tr" key={`${v.die}/${v.config}`} onClick={() => openStep(index)}
                  sx={{ cursor: 'pointer', bgcolor: index === stepIndex ? 'var(--panel-3, rgba(255,255,255,0.06))' : 'transparent', '&:hover': { bgcolor: 'rgba(255,255,255,0.04)' } }}>
                  <Box component="td" sx={{ ...TD, textAlign: 'left', boxShadow: `inset 3px 0 0 ${motorColor(v)}` }}>{labels[index]}</Box>
                  {POINT_TILES.filter((spec) => spec.group !== 'thermal' && (spec.core || peers.some((peer) => tileValue(peer, spec) !== null))).map((spec) => {
                    const value = tileValue(p, spec);
                    return <Box component="td" key={spec.id} sx={TD}>{value === null ? '—' : fmt(value, spec.d)}</Box>;
                  })}
                </Box>
              );
            })}
          </tbody>
        </Box>
      </Box>}
    </Box>
  );
};

export default PointsConfigure;
