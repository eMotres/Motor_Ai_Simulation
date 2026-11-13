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
import React, { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, Chip, Menu, MenuItem, Slider, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { PANEL, LABEL, fmt } from '../common/ResultTiles';
import ResultsPanel from '../common/ResultsPanel';
import { panelDcBus, panelRows } from '../../lib/resultsPanel';
import { watchChunkImport } from '../../lib/chunkReload';
// the field viewer is three.js: fetched when a stored field map is opened, not with the Configure tab
const StoredFieldViewer = lazy(watchChunkImport(() => import('./StoredFieldViewer')));
import StoredPointResults from './StoredPointResults';
import StarIcon from '@mui/icons-material/Star';
import { VariantControls } from '../../modules/development/components/VariantControls';
import { isRecommended, flagWho } from '../../modules/development/projectFlags';
import { useAuth } from '../../contexts/AuthContext';
import type { ProjectMotor } from '../../modules/development/types';
import BatteryPanel, { defaultBattery, type Battery } from './BatteryPanel';
import { developmentApi } from '../../modules/development/api';
import type { FieldKind, MotorPointVariant, MotorView, MotorViewDuty, StoredField } from '../../modules/development/types';
import {
  POINT_TILES, findVariantIndex, pointOperating, tileValue,
} from '../../modules/development/pointsOnly';
import { FIELD_KIND_ORDER, configureTileKey, isNotComputed, otherPointFields, pickPointDuty, pointDuties, provenanceLine, storedFieldKinds } from '../../modules/development/pointsLimited';
import { parametersLine } from '../../modules/development/storedFieldPayload';
import { motorColor } from '../../modules/development/motorGroups';
import { fetchMotorField } from '../../lib/motorViewApi';
import { openProjectMotor } from '../../lib/openProjectMotor';
import { nsT } from '../../i18n/nsT';
import { stepLabels } from '../../modules/development/stepSeries';
import { specBatteryView } from '../../modules/development/specBattery';
import { dutyInfoOf, filterToSpecPoints, specPointRefs, visibleSteps, type SpecPointRef } from '../../modules/development/specPoints';
import { projectsOfMotor, type FocusProject } from '../../modules/development/projectFocus';
import { projectOpenEvent, projectOpenStorageKey } from '../../modules/development/projectOpenIntent';
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
  const { isAdmin, role } = useAuth();
  const staff = isAdmin || role === 'engineer';
  // the project's variants with their staff marks (recommended / report reviewed), keyed like the step variants
  const motorOf = (v: MotorPointVariant): ProjectMotor => ({ die: v.die, canonical_die: v.canonical_die, config: v.config, status: v.status, report_reviewed: v.report_reviewed, recommended: v.recommended });
  const [stepsTick, setStepsTick] = useState(0);
  const reloadMotors = async () => { setStepsTick((n) => n + 1); };
  const [steps, setSteps] = useState<MotorPointVariant[]>([]);
  const [stepIndex, setStepIndex] = useState(-1);
  // owner 2026-11-09: a project page shows only the specification's points + one cold no-load duty (see modules/development/specPoints)
  // "Back to project" without a remembered project (after a reload, or a motor opened another way): the projects this die / configuration
  // is linked to, from the project list the page already reads
  const [linkedProjects, setLinkedProjects] = useState<FocusProject[]>([]);
  const [backAnchor, setBackAnchor] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setLinkedProjects([]);
    if (focus) return;
    let cancelled = false;
    developmentApi.list()
      .then((list) => { if (!cancelled) setLinkedProjects(projectsOfMotor(list, view.die, view.config, (view as { canonical_die?: string }).canonical_die)); })
      .catch(() => { /* no list: no button */ });
    return () => { cancelled = true; };
  }, [focus?.id, view.die, view.config]);
  const openProjectPage = (project: FocusProject) => {
    setBackAnchor(null);
    try { sessionStorage.setItem(projectOpenStorageKey, project.id); } catch { /* the event still opens it while the page is mounted */ }
    setActiveTab('development' as never);
    window.dispatchEvent(new CustomEvent(projectOpenEvent, { detail: { projectId: project.id } }));
  };
  const [specPoints, setSpecPoints] = useState<SpecPointRef[]>([]);
  const shownView = useMemo(() => {
    if (!focus || !view || specPoints.length === 0) return view;
    const withPoint = (view.duties ?? []).filter((d) => d.has_result === true);
    const kept = new Set(filterToSpecPoints(withPoint, dutyInfoOf, specPoints));
    return { ...view, duties: (view.duties ?? []).filter((d) => d.has_result !== true || kept.has(d)) };
  }, [view, focus, specPoints]);
  const [duty, setDuty] = useState<string | null>(() => pickPointDuty(view, loadedDuty));
  const [kind, setKind] = useState<FieldKind | null>(null);
  const [field, setField] = useState<{ key: string; value: StoredField | null; error: string; missing?: boolean } | null>(null);
  const [openError, setOpenError] = useState('');
  // Configure's own battery block, pre-filled from the project's specification when there is one (else Configure's default)
  const [battery, setBattery] = useState<Battery>(() => defaultBattery());
  const [specBattery, setSpecBattery] = useState<Battery | null>(null);
  const cache = useRef(new Map<string, StoredField>());

  const motorKey = `${view.die}\u0000${view.config}`;
  // a new motor (or the same one reloaded) restarts the mode at the loaded duty; the user's own switch is kept otherwise
  useEffect(() => { setDuty((cur) => pickPointDuty(shownView, loadedDuty, cur)); cache.current.clear(); setField(null); }, [shownView, loadedDuty]);

  useEffect(() => {
    setSpecBattery(null); setSpecPoints([]);
    if (!focus) return;
    let cancelled = false;
    developmentApi.spec(focus.id).then((sp) => {
      if (cancelled) return;
      setSpecPoints(specPointRefs(sp.draft as never));
      const raw = ((sp.draft?.fields ?? {}) as Record<string, unknown>)['2_1'];
      const value = raw && typeof raw === 'object' && !Array.isArray(raw) && 'value' in raw ? (raw as { value?: unknown }).value : raw;
      if (value && typeof value === 'object' && ['dc_source', 'ac_mains'].includes(String((value as Record<string, unknown>).supply_type))) return;      // a DC power source or AC mains is not a battery: Configure keeps its own default
      const bat = specBatteryView(value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}).bat as Battery;
      if ([bat.cells, bat.min, bat.nom, bat.max].every((n) => Number.isFinite(n))) { setSpecBattery(bat); setBattery(bat); }
    }).catch(() => { /* no specification battery: Configure's default stays */ });
    return () => { cancelled = true; };
  }, [focus?.id]);

  // design steps: only when the loaded motor belongs to the project in focus
  useEffect(() => {
    setSteps([]); setStepIndex(-1);
    if (!focus) return;
    let cancelled = false;
    developmentApi.motorPoints(focus.id, { showAll: true })
      .then((answer) => {
        if (cancelled) return;
        // trial variants stay in storage but are not shown (owner 2026-11-09); the open one is kept
        const shown = visibleSteps(answer.variants, view.die, view.config);
        const index = findVariantIndex(shown, view.die, view.config);
        if (index >= 0) { setSteps(shown); setStepIndex(index); }
      })
      .catch(() => { /* no project steps: the motor is still shown */ });
    return () => { cancelled = true; };
  }, [focus?.id, motorKey, stepsTick]);

  const points = useMemo(() => pointDuties(shownView), [shownView]);
  const current: MotorViewDuty | null = points.find((d) => d.duty === duty) ?? null;
  const kinds = storedFieldKinds(current);
  const otherPoint = otherPointFields(current);
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
  // the information panel of the ordinary Configure (ONE component, components/common/ResultsPanel), fed with the point's FINAL stored
  // result: the coupled loop's numbers, temperatures and drive where one exists, else the saved EM run's; a tile the point does not
  // store is not drawn (owner 2026-11-01)
  const panel = current?.panel ?? null;
  const dcBus = useMemo(() => panelDcBus(panel), [panel]);
  const tileName = (id: string) => { const key = configureTileKey(id, current); return key ? tc(key) : t(`points.tile.${id}`); };
  // a block is drawn only when this point has real results for it (owner 2026-10-26): no header, no empty tiles, no placeholder
  const showTiles = useMemo(() => panelRows(panel).length > 0, [panel]);
  const operating = pointOperating(current);

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
  // a map that states no speed of its own is not described with the point's operating values (they would claim more than is known)
  const fieldLabel = field?.value
    ? (current?.fields?.[field.value.kind]?.unstamped === true
      ? provenanceLine(field.value.provenance)
      : parametersLine({ rpm: operating.rpm, currentA: operating.currentA, gammaDeg: operating.gammaDeg, provenance: provenanceLine(field.value.provenance) }))
    : '';

  return (
    <Box sx={{ height: '100%', overflow: 'auto', p: 1.5, display: 'flex', flexDirection: 'column', gap: 1.25 }} data-testid="points-configure">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>{t('points.title')}</Typography>
        <HelpTip title={t('points.pendingTip')} />
        <Chip size="small" label={t('points.pending')} sx={{ color: 'var(--text-3)', bgcolor: 'var(--panel-2)' }} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{view.die} / {view.config}</Typography>
        <Box sx={{ flex: 1 }} />
        {focus && stepIndex >= 0 && steps[stepIndex] && <VariantControls projectId={focus.id} motor={motorOf(steps[stepIndex])} staff={staff} t={t} showAll onChanged={reloadMotors} />}
        {(() => {
          const targets: FocusProject[] = focus ? (steps.length > 0 ? [focus] : []) : linkedProjects;
          if (targets.length === 0) return null;
          return <>
            <Button size="small" onClick={(e) => (targets.length === 1 ? openProjectPage(targets[0]) : setBackAnchor(e.currentTarget))}>{t('points.backToProject')}</Button>
            <Menu anchorEl={backAnchor} open={!!backAnchor && targets.length > 1} onClose={() => setBackAnchor(null)}>
              {targets.map((p) => <MenuItem key={p.id} onClick={() => openProjectPage(p)} sx={{ fontSize: 13 }}>{p.title ?? p.id}</MenuItem>)}
            </Menu>
          </>;
        })()}
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
                  {isRecommended(v) && <Tooltip title={`${t('recommended.tip')} ${flagWho(v.recommended)}`}><StarIcon data-testid="step-star" sx={{ fontSize: 14, color: '#f59e0b', mr: 0.5 }} /></Tooltip>}
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

        <Box sx={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr)', gap: 1.25 }} data-testid="tiles">
          {showTiles && panel && <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, minWidth: 0 }} data-testid="em-tiles">
            <ResultsPanel panel={panel} />
            {dcBus !== null && (
              // the same size as in the ordinary Configure (owner 2026-10-26): there the panel shares a row with the cross-section
              // and the side view (flex 1 1 300px, about a third of the page); here it sat alone in the wide column and its bar and
              // labels scaled up ~3x
              <Box sx={{ mt: 0.5, maxWidth: 520 }} data-testid="battery-panel">
                <BatteryPanel vDc={dcBus} bat={battery} onChange={setBattery} machinePack={specBattery} onReset={() => { if (specBattery) setBattery(specBattery); }}
                  stockNote={!specBattery} vDcTip={t('points.batteryMarkerTip', { duty: duty ?? '' })} />
              </Box>
            )}
          </Box>}
        </Box>

        <StoredPointResults duty={current} />

        {kinds.length > 0 && <Box sx={{ ...PANEL, p: 1, display: 'flex', flexDirection: 'column', gap: 0.75 }} data-testid="stored-fields">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography sx={LABEL}>{t('points.fields.title')}</Typography>
            <ToggleButtonGroup exclusive size="small" value={activeKind} aria-label={t('points.fields.title')}
              onChange={(_, value) => { if (value) setKind(value as FieldKind); }}>
              {FIELD_KIND_ORDER.map((k) => (
                <ToggleButton key={k} value={k} disabled={!kinds.includes(k)} sx={{ textTransform: 'none', px: 1.2, py: 0.3, fontSize: 12, '&.Mui-disabled': { pointerEvents: 'auto' } }}>
                  <Tooltip title={kinds.includes(k) ? t(`points.fields.tip.${k}`) : otherPoint[k] ? t('points.fields.otherPoint', otherPoint[k]) : t('points.notComputed')}><span>{t(`points.fields.kind.${k}`)}</span></Tooltip>
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>
          {field?.error && <Typography role="alert" sx={{ fontSize: 12, color: 'error.main' }}>{t('points.fields.loadFailed')}: {field.error}</Typography>}
          {field && !field.value && !field.error && !field.missing && <Typography role="status" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.loading')}</Typography>}
          {field?.value && (
            <Suspense fallback={<Typography role="status" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.loading')}</Typography>}>
              <StoredFieldViewer key={field.key} field={field.value} contextLabel={fieldLabel} />
            </Suspense>
          )}
        </Box>}

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
                    return <Box component="td" key={spec.id} sx={TD}>{value === null ? '' : fmt(value, spec.d)}</Box>;
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
