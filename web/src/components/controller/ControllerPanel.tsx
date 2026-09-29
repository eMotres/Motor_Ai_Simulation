/**
 * ControllerPanel — the Controller tab.
 *
 * Owner, 2026-09-22: the inverter as its own menu — the device, how the bridges
 * are combined with the motor's coils ("один контроллер на один мотор, два
 * контроллера на один мотор … один мост на каждую катушку отдельно"), the
 * losses with the MOSFET cooling, and a power schematic that follows whatever
 * topology is chosen.
 *
 * Layout: settings on the left, results on the right, the schematic and one
 * electrical period of the waveform underneath, the device catalogue at the
 * bottom.  One short line + HelpTip per control — no text walls.
 *
 * Every default comes from the DUTY (the backend resolves them from the stored
 * coupled record and sends a `sources` map back); a field left blank here is a
 * field the duty answers.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box, Paper, Typography, Button, TextField, MenuItem, Divider,
         CircularProgress, Alert, Chip, Tooltip, IconButton } from '@mui/material';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import SectionLabel from '../common/SectionLabel';
import HelpTip from '../common/HelpTip';
import DeviceCatalog from './DeviceCatalog';
import LocalCompareTable from '../common/LocalCompareTable';
import { MAX_LOCAL_ROWS, normalizeLocalRows } from '../compare/resultRows';
import type { LocalRow } from '../compare/resultRows';
import { CONTROLLER_COMPARE_COLUMNS, localControllerRow,
         controllerRowName } from './compareRows';
import { useDieContext } from '../common/useDieContext';
import { listDevices, getTopologies, solveController, getLast, postSchematic,
         polyline, fmt, pct, statusLine, getControllerSettings,
         saveControllerSettings, formStateFromSettings, settingsForSave,
         controllerSolveBody, getResolvedPoint, DEFAULT_CONTROLLER_FORM,
         getThermalCooling, carrierPrefill, carrierOriginLine,
         staleResultFields, staleResultLine,
         standaloneProblems, standaloneSolveBody, DEFAULT_STANDALONE,
         type StandaloneForm,
         DEFAULT_RIPPLE, rippleFromSettings, rippleForSave, rippleSolveFields,
         rippleProblems, type RippleForm,
         type DeviceRow, type CoilRow, type ControllerResult,
         type ControllerFormState, type ResolvedPoint,
         type ThermalCoolingByMode } from './controllerApi';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';

// UI strings: locales/<lng>/controller.json (docs/I18N.md).
const tx = nsT('controller');

const CARD = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 2 } as const;
const NUM = { width: 120, '& input': { fontSize: 12, py: 0.5 } } as const;
const TH = { fontSize: 11, color: 'var(--text-3)' } as const;
const TD = { fontSize: 12, color: 'var(--text-1)', fontFamily: 'monospace', textAlign: 'right' } as const;

type Nullable = number | '' ;

/** Where this tab keeps its stacked comparison between visits. */
const COMPARE_KEY = 'controller.compareRows';

const ControllerPanel: React.FC = () => {
  useTranslation('controller'); // re-render on language change; lazy-loads the namespace
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [device, setDevice] = useState('');
  /** the chosen device's catalogue row — which loss basis it has (2026-09-27) */
  const devRow = useMemo(() => devices.find(d => d.part === device) ?? null, [devices, device]);
  const [presets, setPresets] = useState<{ id: string; label: string; hint: string }[]>([]);
  const [coils, setCoils] = useState<CoilRow[]>([]);
  const [machine, setMachine] = useState<Record<string, any>>({});

  const [topology, setTopology] = useState(DEFAULT_CONTROLLER_FORM.topology);
  const [setSplit, setSetSplit] = useState(DEFAULT_CONTROLLER_FORM.setSplit);
  const [hbMod, setHbMod] = useState(DEFAULT_CONTROLLER_FORM.hbMod);
  const [pwmMod, setPwmMod] = useState(DEFAULT_CONTROLLER_FORM.pwmMod);
  // Owner 2026-09-24: default 1, not 4 — see DEFAULT_CONTROLLER_FORM's own doc.
  const [nPar, setNPar] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.nPar);
  const [rg, setRg] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.rg);
  const [vgsOff, setVgsOff] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.vgsOff);
  // 2026-09-27 (cloud task 10): loss basis + the SPICE table's driver/layout inputs
  const [basis, setBasis] = useState<string>(DEFAULT_CONTROLLER_FORM.basis);
  const [rgOff, setRgOff] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.rgOff);
  const [vgsOn, setVgsOn] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.vgsOn);
  const [lSigma, setLSigma] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.lSigma);
  const [dead, setDead] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.dead);
  const [fsw, setFsw] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.fsw);
  // Where the Carrier box's value came from while it is NOT yet this
  // Controller's own (2026-09-24): 'legacy' = migrated from the retired
  // Simulation-tab PWM carrier, 'default' = the stated default.  Cleared by a
  // hand edit and by Save — then the value IS the Controller's.
  const [fswOrigin, setFswOrigin] = useState<string | null>(null);
  const onFswChange = (v: Nullable) => { setFswOrigin(null); setFsw(v); };
  const [vdc, setVdc] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.vdc);
  const [coolant, setCoolant] = useState(DEFAULT_CONTROLLER_FORM.coolant);
  const [flow, setFlow] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.flow);
  const [tin, setTin] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.tin);
  const [rtim, setRtim] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.rtim);
  // Owner 2026-09-22 evening: "надо добавить воздушное охлаждение и скорость
  // ветра, как в термосимуляции" — the same liquid/forced-air/still-air
  // choice the thermal tab already offers for the housing, now for the
  // device heatsink/plate.  Defaults mirror inverter.losses' own module
  // constants (air_speed_mps 5, t_ambient_c 40, fin_efficiency 0.75,
  // emissivity 0.9); the area itself is left blank so a fresh choice falls
  // back to the backend's stated "small finned heatsink" (40 cm^2/device).
  const [coolingMode, setCoolingMode] = useState(DEFAULT_CONTROLLER_FORM.coolingMode);
  const [airSpeed, setAirSpeed] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.airSpeed);
  const [tAmbient, setTAmbient] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.tAmbient);
  const [areaBasis, setAreaBasis] = useState<'heatsink' | 'plate'>(DEFAULT_CONTROLLER_FORM.areaBasis);
  const [areaCm2, setAreaCm2] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.areaCm2);
  const [finEff, setFinEff] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.finEff);
  const [emissivity, setEmissivity] = useState<Nullable>(DEFAULT_CONTROLLER_FORM.emissivity);
  const [mapping, setMapping] = useState<Record<number, string>>(DEFAULT_CONTROLLER_FORM.mapping);
  // ── 2026-09-28: a run WITHOUT a motor, per-module plates, a stated plate R
  // with its own fluid, and R_th(j-c) for a card that prints none.  Kept in
  // this browser only (session convenience) — never saved with the motor.
  const [sa, setSaRaw] = useState<StandaloneForm>(() => {
    try { return { ...DEFAULT_STANDALONE,
                   ...JSON.parse(localStorage.getItem('ctrl.standalone') || '{}') }; }
    catch { return DEFAULT_STANDALONE; }
  });
  const setSa = (patch: Partial<StandaloneForm>) => setSaRaw(prev => {
    const next = { ...prev, ...patch };
    try { localStorage.setItem('ctrl.standalone', JSON.stringify(next)); } catch { /* private window */ }
    return next;
  });
  type Ext = { plumbing: string; nPlates: Nullable; rPlate: Nullable;
               rPlateFluid: string; rPlateFlow: Nullable; rthJc: Nullable };
  const EXT0: Ext = { plumbing: 'shared', nPlates: '', rPlate: '', rPlateFluid: '',
                      rPlateFlow: '', rthJc: '' };
  const [ext, setExtRaw] = useState<Ext>(() => {
    try { return { ...EXT0, ...JSON.parse(localStorage.getItem('ctrl.ext') || '{}') }; }
    catch { return EXT0; }
  });
  const setExt = (patch: Partial<Ext>) => setExtRaw(prev => {
    const next = { ...prev, ...patch };
    try { localStorage.setItem('ctrl.ext', JSON.stringify(next)); } catch { /* private window */ }
    return next;
  });
  // PWM ripple / THD inputs (2026-09-28) — saved WITH the controller.
  const [rip, setRipRaw] = useState<RippleForm>(DEFAULT_RIPPLE);
  const setRip = (p: Partial<RippleForm>) => setRipRaw(r => ({ ...r, ...p }));
  const [settingsErr, setSettingsErr] = useState<string | null>(null);
  const [settingsSavedAt, setSettingsSavedAt] = useState<string | null>(null);
  const [switchCurrent, setSwitchCurrent] = useState<
    { i_switch_rms_A: number | null; basis: string | null; note: string } | null>(null);

  const [res, setRes] = useState<ControllerResult | null>(null);
  const [svg, setSvg] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // The stacked comparison, the same contract every other tab's uses. Kept in
  // this browser (the Compare tab's server-side library is the permanent one);
  // whatever is on disk is normalised, never trusted.
  const [rows, setRows] = useState<LocalRow[]>(() => {
    try { return normalizeLocalRows(JSON.parse(
      localStorage.getItem(COMPARE_KEY) || '[]')); } catch { return []; }
  });
  const saveRows = (next: LocalRow[]) => {
    setRows(next);
    try { localStorage.setItem(COMPARE_KEY, JSON.stringify(next)); } catch { /* private window */ }
  };
  const addRow = () => {
    setErr(null);
    try {
      if (!res) throw new Error('nothing to compare yet — press Solve first');
      if (rows.length >= MAX_LOCAL_ROWS) {
        throw new Error(`the comparison below already holds ${MAX_LOCAL_ROWS} `
          + 'variants — drop one first');
      }
      const { inputs, results } = localControllerRow(res);
      saveRows([...rows, { id: `ctrl-${Date.now()}`, name: controllerRowName(res),
                           at: new Date().toISOString(), inputs, results }]);
    } catch (e) { setErr(String(e)); }
  };

  // ── what the machine is, and what cards exist ──────────────────────────
  const loadDevices = async (topo: string) => {
    const d = await listDevices(topo);
    setDevices(d.devices);
    setSwitchCurrent({ i_switch_rms_A: d.i_switch_rms_A,
                       basis: d.i_switch_rms_basis, note: d.suggestion_note });
    const first = d.devices.find(x => !x.error);
    if (first) setDevice(p => p || first.part);
  };

  useEffect(() => { void (async () => {
    try { await loadDevices(topology); } catch (e) { setErr(String(e)); }
    try {
      const t = await getTopologies({});
      setPresets(t.presets); setCoils(t.coils); setMachine(t.machine || {});
    } catch (e) { setErr(String(e)); }
    try {
      const last = await getLast();
      if (last && (last as ControllerResult).device) setRes(last as ControllerResult);
    } catch { /* nothing solved yet */ }
  })(); }, []);

  // ── the tab's own settings, saved WITH the active configuration ────────
  // Owner 2026-09-22: "при сохранении мотора текущий контроллер тоже должен
  // сохраняться со всеми настройками". `useDieContext` is the same "which
  // motor is loaded" the Geometry table and the Optimize/Sweep pickers
  // already poll — no new plumbing, and it answers before the first Solve,
  // so a saved controller restores the moment the tab opens.
  const dieCtx = useDieContext();

  // ── COOLING INHERITS FROM THERMAL, per field, per mode (owner 2026-09-25:
  // "Когда я ставлю air или liquid, он должен брать параметры охлаждения из
  // Thermal... но можно изменить, чтобы сделать разными") — replaces the
  // 2026-09-24 button.  `thermalByMode` is what EACH of the three cooling
  // modes would inherit right now (`GET /api/controller/thermal_cooling`);
  // a field left BLANK on screen shows its entry as a placeholder + "from
  // Thermal" chip and follows it live, exactly like Carrier/DC-link already
  // do for the duty's own record — typing a value makes that one field an
  // override (see the "MOSFET cooling" rows below for the reset control).
  const [thermalByMode, setThermalByMode] = useState<ThermalCoolingByMode | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => { void (async () => {
      if (!dieCtx.die || !dieCtx.config) { if (alive) setThermalByMode(null); return; }
      try {
        const t = await getThermalCooling(dieCtx.die, dieCtx.config);
        if (alive) setThermalByMode(t);
      } catch { if (alive) setThermalByMode(null); }
    })(); };
    load();
    // A Thermal-tab edit (thermalStore's own dispatch) or a machine switch
    // must refresh this without waiting for the next mount.
    window.addEventListener('thermal-cooling-saved', load);
    window.addEventListener('family-changed', load);
    return () => {
      alive = false;
      window.removeEventListener('thermal-cooling-saved', load);
      window.removeEventListener('family-changed', load);
    };
  }, [dieCtx.die, dieCtx.config]);

  /** What the given cooling mode would inherit for ONE field right now —
   *  ``null`` when Thermal has nothing for it. */
  const thermalField = (mode: 'air_forced' | 'air_still' | 'liquid', field: string
                        ): number | string | null => {
    const v = thermalByMode?.modes?.[mode]?.fields?.[field];
    return v === undefined ? null : v;
  };
  /** The chip + HelpTip (Thermal HAS a value) or the one-line note (it does
   *  not) for a still-blank (not overridden) field — ``null`` once the
   *  field is overridden, so an edited value never keeps showing a stale
   *  "from Thermal" claim. */
  const thermalHint = (mode: 'air_forced' | 'air_still' | 'liquid', field: string,
                       overridden: boolean, label: (v: number | string) => string) => {
    if (overridden) return null;
    const info = thermalByMode?.modes?.[mode];
    if (!info) return null;
    const v = info.fields[field];
    if (v != null) {
      return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: -0.4, mb: 0.2 }}>
          <Chip size="small" label={`from Thermal: ${label(v)}`} sx={{ fontSize: 10, height: 18 }} />
          <HelpTip title={info.source || ''} />
        </Box>);
    }
    return info.note ? (
      <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', mt: -0.4, mb: 0.2 }}>
        {info.note}</Typography>) : null;
  };
  /** The small "↺ use Thermal" reset next to an OVERRIDDEN field — clears it
   *  back to blank (inherited) rather than the caller having to retype
   *  whatever Thermal is showing. */
  const ResetToThermal: React.FC<{ onReset: () => void }> = ({ onReset }) => (
    <Tooltip title="Use Thermal's value again">
      <IconButton size="small" sx={{ p: 0.2 }} onClick={onReset}>
        <RestartAltIcon sx={{ fontSize: 14 }} /></IconButton>
    </Tooltip>);

  // Gates the debounced auto-save below: `"<die>::<config>"` once THIS
  // configuration's settings have actually landed, `null` while a load is in
  // flight or none is loaded — the same `simReady`-style gate
  // `SimulationPanel`'s own debounced config PATCH uses, so a die/config
  // switch can never auto-save before the just-loaded block has replaced the
  // previous machine's state on screen (it would otherwise PATCH one
  // machine's settings onto another's file for the ~1 s the load is async).
  const settingsLoadedFor = useRef<string | null>(null);
  useEffect(() => { void (async () => {
    settingsLoadedFor.current = null;
    if (!dieCtx.die || !dieCtx.config) return;
    try {
      const block = await getControllerSettings(dieCtx.die, dieCtx.config);
      // The fallback for a missing/partial saved block is the tab's OWN
      // stated defaults, never the panel's live state: this fires on every
      // die/config CHANGE, and building it from `device, topology, nPar, …`
      // instead used whatever a PREVIOUSLY loaded machine had left on
      // screen — a never-configured machine silently inherited another
      // machine's "Devices / switch" count (root cause of the 2026-09-24
      // "keeps resetting to 4" report; see DEFAULT_CONTROLLER_FORM's doc).
      const next = formStateFromSettings(block, DEFAULT_CONTROLLER_FORM);
      setDevice(next.device); setTopology(next.topology); setSetSplit(next.setSplit);
      setHbMod(next.hbMod); setPwmMod(next.pwmMod); setNPar(next.nPar); setRg(next.rg); setVgsOff(next.vgsOff);
      setBasis(next.basis); setRgOff(next.rgOff); setVgsOn(next.vgsOn); setLSigma(next.lSigma);
      setDead(next.dead); setFsw(next.fsw); setFswOrigin(null);
      setVdc(next.vdc); setCoolant(next.coolant);
      setFlow(next.flow); setTin(next.tin); setRtim(next.rtim);
      setCoolingMode(next.coolingMode); setAirSpeed(next.airSpeed);
      setTAmbient(next.tAmbient); setAreaBasis(next.areaBasis);
      setAreaCm2(next.areaCm2); setFinEff(next.finEff); setEmissivity(next.emissivity);
      setMapping(next.mapping);
      setRipRaw(rippleFromSettings(block as any));
      if (block && (block as any).saved_at) setSettingsSavedAt((block as any).saved_at);
    } catch { /* nothing saved yet, or the read failed — the tab's own defaults stand */
    } finally { settingsLoadedFor.current = `${dieCtx.die}::${dieCtx.config}`; }
  })(); }, [dieCtx.die, dieCtx.config]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── mirror the current form to localStorage, TAGGED with the active
  // configuration — the same "current panel state" shape ActiveFamilyStrip
  // already reads for `mesh.*`/`sim.*` on every "Save to duty".  Owner
  // 2026-09-22, second round: the controller must ride THAT save too, not
  // only this tab's own button — ActiveFamilyStrip reads this key right
  // after the duty save succeeds and PATCHes it in the same flow, folding
  // the result into ONE status line.  The tag is what stops a stale mirror
  // from an earlier motor landing on the one being saved now.
  useEffect(() => {
    if (!dieCtx.die || !dieCtx.config) return;
    try {
      const state: ControllerFormState = { device, topology, setSplit, hbMod, pwmMod,
        nPar, rg, vgsOff, basis, rgOff, vgsOn, lSigma, dead, fsw, vdc, coolant, flow, tin, rtim,
        coolingMode, airSpeed, tAmbient, areaBasis, areaCm2, finEff, emissivity,
        mapping };
      localStorage.setItem('ctrl.settings', JSON.stringify({
        die: dieCtx.die, config: dieCtx.config,
        block: { ...settingsForSave(state), ...rippleForSave(rip) } }));
    } catch { /* private window — the auto-save-with-the-motor mirror just won't work this session */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dieCtx.die, dieCtx.config, device, topology, setSplit, hbMod, pwmMod, nPar, rg,
      vgsOff, basis, rgOff, vgsOn, lSigma, dead, fsw, vdc, coolant, flow, tin, rtim,
      coolingMode, airSpeed, tAmbient, areaBasis, areaCm2, finEff, emissivity,
      mapping, rip]);

  // ── AUTO-SAVE (owner 2026-09-25, "Devices / switch keeps resetting to 4")
  // ─────────────────────────────────────────────────────────────────────
  // The form used to persist only on an explicit "Save settings" click — a
  // solve (``POST /solve``) never wrote the settings block at all, so typing
  // 1 and pressing Solve left the FILE at whatever stale default (4) an
  // earlier session had saved; the next reload or machine switch read that
  // stale file straight back.  `persistSettings` is now the ONE writer this
  // tab has (the same PATCH the button always used), called from three
  // places: the button itself, every Solve, and a ~1 s debounce after the
  // last edit of any field — so the file can never again be older than what
  // is on screen.
  const persistSettings = useCallback(async () => {
    if (!dieCtx.die || !dieCtx.config) return null;
    setSettingsErr(null);
    const state: ControllerFormState = { device, topology, setSplit, hbMod, pwmMod,
      nPar, rg, vgsOff, basis, rgOff, vgsOn, lSigma, dead, fsw, vdc, coolant, flow, tin, rtim,
      coolingMode, airSpeed, tAmbient, areaBasis, areaCm2, finEff, emissivity,
      mapping };
    try {
      const r = await saveControllerSettings(dieCtx.die, dieCtx.config,
        { ...settingsForSave(state), ...rippleForSave(rip) });
      setSettingsSavedAt(r.controller?.saved_at || null);
      // The carrier on screen is now the Controller's own — no origin line.
      if (r.controller?.f_carrier_hz != null) setFswOrigin(null);
      // Every other tab that shows the Controller's drive re-reads it.
      try { window.dispatchEvent(new CustomEvent('controller-settings-saved')); }
      catch { /* SSR/no-window */ }
      return r;
    } catch (e) { setSettingsErr(String(e)); return null; }
  }, [dieCtx.die, dieCtx.config, device, topology, setSplit, hbMod, pwmMod, nPar, rg,
      vgsOff, basis, rgOff, vgsOn, lSigma, dead, fsw, vdc, coolant, flow, tin, rtim,
      coolingMode, airSpeed, tAmbient, areaBasis, areaCm2, finEff, emissivity,
      mapping, rip]);

  const saveSettings = async () => {
    if (!dieCtx.die || !dieCtx.config) {
      setSettingsErr('no motor is loaded — load a configuration first');
      return;
    }
    await persistSettings();
  };

  // Debounced: ~1 s after the last edit of ANY Controller field, not only on
  // Solve or the button — `settingsLoadedFor` blocks this until the current
  // die/config's OWN settings have loaded (never overwrite it with whatever
  // a previous machine left on screen mid-switch).
  const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!dieCtx.die || !dieCtx.config) return;
    if (settingsLoadedFor.current !== `${dieCtx.die}::${dieCtx.config}`) return;
    if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    autoSaveTimer.current = setTimeout(() => { void persistSettings(); }, 1000);
    return () => { if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dieCtx.die, dieCtx.config, device, topology, setSplit, hbMod, pwmMod, nPar, rg,
      vgsOff, basis, rgOff, vgsOn, lSigma, dead, fsw, vdc, coolant, flow, tin, rtim,
      coolingMode, airSpeed, tAmbient, areaBasis, areaCm2, finEff, emissivity,
      mapping, rip]);

  const customRows = useMemo(() =>
    coils.map(c => ({ coil: c.index, bridge: (mapping[c.index] || 'INV1').split('/')[0],
                      leg: (mapping[c.index] || 'INV1/A').split('/')[1] || 'A' })),
    [coils, mapping]);

  // The wire body — see controllerSolveBody's own doc: every blank number
  // box is OMITTED, never sent as '' or null, so the route's own V_dc /
  // carrier / current / power resolution chain runs uncontested.
  const formNow = () => ({ device, topology, setSplit, hbMod, pwmMod, nPar, rg, vgsOff, basis, rgOff, vgsOn, lSigma, dead, fsw, vdc,
      coolant, flow, tin, rtim,
      coolingMode, airSpeed, tAmbient, areaBasis, areaCm2, finEff, emissivity,
      mapping, ...ext });
  const body = () => controllerSolveBody(formNow(), customRows);
  // how many three-phase inverters the ripple sees (L_xy needed for two)
  const nInvNow = sa.on ? Number(sa.nInv) : (topology === 'two_3ph' ? 2 : 1);

  // The per-switch current — and so the catalogue's "parallel" suggestion —
  // depends on the topology, so it is re-asked when that changes.
  useEffect(() => { void loadDevices(topology).catch(() => undefined); },
    [topology]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── the schematic follows the topology, without solving ────────────────
  const schemaKey = JSON.stringify([topology, device, nPar, vdc, hbMod,
                                    topology === 'custom' ? customRows : null,
                                    machine.num_slots, machine.num_poles]);
  useEffect(() => { void (async () => {
    if (!coils.length) return;
    try {
      // No star_delta: the route resolves the connection from the duty, the
      // same way Solve does, so the picture cannot contradict the numbers.
      const s = await postSchematic(body());
      setSvg(s.svg);
    } catch (e) { setSvg(''); }
  })(); }, [schemaKey, coils.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── the resolved point, BEFORE Solve is pressed ─────────────────────────
  // Owner 2026-09-22 audit: the tab must say where every number is about to
  // come from, not only after a failed Solve.  Built on the exact same
  // resolution `POST /solve` runs (GET /point), so this line can never name
  // a different source than the answer that follows it.
  const [point, setPoint] = useState<ResolvedPoint | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => { void (async () => {
      if (!dieCtx.die || !dieCtx.config) { if (alive) setPoint(null); return; }
      try { const p = await getResolvedPoint(dieCtx.die, dieCtx.config); if (alive) setPoint(p); }
      catch { if (alive) setPoint(null); }
    })(); };
    load();
    // Same events the rest of this tab already reacts to for a new machine
    // or a fresh electromagnetic/coupled answer (``useDieContext`` itself
    // re-polls on ``family-changed``/``sim-design-applied``; this also
    // catches a transient EM run finishing on the SAME die/config, where
    // those two never fire) — the resolved point must never lag behind a
    // Solve on the Simulation/Coupled tab.
    window.addEventListener('sim-transient-done', load);
    window.addEventListener('sim-design-applied', load);
    window.addEventListener('family-changed', load);
    return () => {
      alive = false;
      window.removeEventListener('sim-transient-done', load);
      window.removeEventListener('sim-design-applied', load);
      window.removeEventListener('family-changed', load);
    };
  }, [dieCtx.die, dieCtx.config, dieCtx.active]);

  // THE CARRIER BOX HOLDS A REAL VALUE (owner 2026-09-24).  Nothing saved in
  // it yet → the value the backend resolved (the retired Simulation-tab
  // carrier, migrated, or the stated default) is written in, with its origin
  // shown on one line until the auto-save (any edit, or the next Solve,
  // 2026-09-25) makes it this Controller's own.
  useEffect(() => {
    const next = carrierPrefill(fsw, point);
    if (next.fsw !== fsw) { setFsw(next.fsw); setFswOrigin(next.origin); }
  }, [point, fsw]);

  const solve = async (fresh = false) => {
    setBusy(true); setErr(null);
    // Every Solve persists the settings it is about to run with — the same
    // writer as the button/debounce — so a result can never outlive the file
    // it should have written (owner 2026-09-25 report's root cause: Solve
    // alone never saved anything).
    void persistSettings();
    {
      const bad = [...(sa.on ? standaloneProblems(sa, formNow()) : []),
                   ...rippleProblems(rip, nInvNow)];
      if (bad.length) { setErr(bad.join(' · ')); setBusy(false); return; }
    }
    try {
      const r = await solveController(
        { ...(sa.on ? standaloneSolveBody(body(), sa) : body()), ...rippleSolveFields(rip) },
        fresh);
      setRes(r);
      if (r.schematic_svg) setSvg(r.schematic_svg);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const L = res?.losses as any; const T = res?.thermal as any;
  const E = res?.efficiency; const P = res?.point as any;
  const wave = res?.waveforms;
  const firstCoil = wave ? Object.keys(wave.coils)[0] : null;

  // ── stale result vs. the live form (owner 2026-09-25) ───────────────────
  // A result served from history (``fresh=false``) — or simply left on
  // screen after the owner edited a field without pressing Solve again — can
  // describe settings the form no longer shows.  Never present that pair
  // silently: one short line names what disagrees, in place of a result the
  // owner would otherwise read as a plain answer to what is on screen now.
  const staleFields = useMemo(() => staleResultFields(
    { device, topology, setSplit, hbMod, pwmMod, nPar, rg, vgsOff, basis, rgOff, vgsOn, lSigma, dead, fsw, vdc,
      coolant, flow, tin, rtim, coolingMode, airSpeed, tAmbient, areaBasis,
      areaCm2, finEff, emissivity, mapping }, res),
    [device, topology, setSplit, hbMod, pwmMod, nPar, rg, vgsOff, basis, rgOff, vgsOn, lSigma, dead, fsw, vdc,
     coolant, flow, tin, rtim, coolingMode, airSpeed, tAmbient, areaBasis,
     areaCm2, finEff, emissivity, mapping, res]);
  const staleLine = staleResultLine(staleFields, res);

  return (
    <Box sx={{ height: '100%', overflowY: 'auto', p: 2.5, bgcolor: 'var(--panel-2)' }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1.5, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 20, fontWeight: 800, color: 'var(--text-0)' }}>{tx('controller')}</Typography>
        <HelpTip i18nKey="controller.theInverterThatDrivesThis" />
        {res?.context && (
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)', fontFamily: 'monospace' }}>
            {res.context.die} · {res.context.config} · {res.context.duty}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        {res?.served_from_history && (
          <Tooltip title={`Loaded from history — computed ${res.computed_at || ''}`}>
            <Chip size="small" label={tx('fromHistory')} sx={{ fontSize: 10, height: 20 }} />
          </Tooltip>
        )}
        <Button size="small" variant="contained" disabled={busy || !device}
          onClick={() => void solve(false)} sx={{ textTransform: 'none' }}>
          {busy ? 'Solving…' : 'Solve'}</Button>
        <Button size="small" variant="outlined" disabled={busy || !device}
          onClick={() => void solve(true)} sx={{ textTransform: 'none', fontSize: 11 }}>
          {tx('recompute')}</Button>
        <Button size="small" variant="outlined" disabled={!res}
          onClick={addRow} sx={{ textTransform: 'none', fontSize: 11 }}>
          + Add to comparison</Button>
        <Tooltip title={dieCtx.active
          ? 'Every edit and every Solve already saves this controller — '
            + 'device, topology, mapping, N parallel, R_G, dead time, '
            + 'carrier, DC link and cooling — with the loaded configuration. '
            + 'This button just does it right now instead of waiting ~1 s.'
          : 'Load a configuration first — the controller is saved WITH it.'}>
          <span>
            <Button size="small" variant="outlined" disabled={!dieCtx.active}
              onClick={() => void saveSettings()} sx={{ textTransform: 'none', fontSize: 11 }}>
              {tx('saveNow')}</Button>
          </span>
        </Tooltip>
        {settingsSavedAt && !settingsErr && (
          <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>
            saved {settingsSavedAt}</Typography>)}
      </Box>

      {settingsErr && <Alert severity="error" sx={{ mb: 1, fontSize: 12.5 }}
        onClose={() => setSettingsErr(null)}>{settingsErr}</Alert>}

      {/* ONE status line: the plain-English refusal when the duty has no
          electromagnetic answer yet, otherwise which point of the duty this
          solve is FOR — the S1-verified machine, a limit crossing, or the
          duty's steady point — never silent about which numbers are below. */}
      {statusLine(res, err) && (err
        ? <Alert severity="error" sx={{ mb: 1.5, fontSize: 12.5 }}>{statusLine(res, err)}</Alert>
        : <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)', mb: 1 }}>{statusLine(res, err)}</Typography>)}
      {res?.violations?.map((v, i) => (
        <Alert key={i} severity="error" sx={{ mb: 1, fontSize: 12.5 }}>{v}</Alert>))}
      {res?.warnings?.map((w, i) => (
        <Alert key={i} severity="warning" sx={{ mb: 1, fontSize: 12.5 }}>{w}</Alert>))}

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* ── settings ── */}
        <Paper sx={{ ...CARD, width: 330, flexShrink: 0 }}>
          <SectionLabel sx={{ mb: 1.5 }}>{tx('controller')}</SectionLabel>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.1 }}>
            <Row label={tx('runWithoutAMotor')} tip="Standalone: solve an inverter from a typed point (DC link, current per phase, f1, m, cos φ) — for a customer's controller with no motor in this app. Topology is one or two 3-phase inverters; nothing is taken from a duty or the Thermal tab.">
              <TextField select size="small" value={sa.on ? 'on' : 'off'}
                onChange={e => setSa({ on: e.target.value === 'on' })}
                sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                <MenuItem value="off" sx={{ fontSize: 12 }}>{tx('noTheLoadedMotor')}</MenuItem>
                <MenuItem value="on" sx={{ fontSize: 12 }}>{tx('yesTypedPoint')}</MenuItem>
              </TextField>
            </Row>
            {sa.on && (<>
              <Row label={tx('inverters')} tip="1 = one 3-phase inverter; 2 = dual 3-phase (6 phases), each inverter a full 3-phase set at the typed current.">
                <TextField select size="small" value={String(sa.nInv)}
                  onChange={e => setSa({ nInv: Number(e.target.value) })}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="1" sx={{ fontSize: 12 }}>1 × 3-phase</MenuItem>
                  <MenuItem value="2" sx={{ fontSize: 12 }}>2 × 3-phase (6 phases)</MenuItem>
                </TextField>
              </Row>
              {Number(sa.nInv) === 2 && (
                <Row label={tx('set2Shift')} tip={tx('electricalAngleOfInverter2Against')} unit="°">
                  <Num v={sa.shift} set={v => setSa({ shift: v })} /></Row>)}
              <Row label={tx('phaseCurrent')} tip={tx('rmsCurrentOfOnePhaseOne')} unit="A rms">
                <Num v={sa.iPh} set={v => setSa({ iPh: v })} /></Row>
              <Row label="f1" tip={tx('fundamentalElectricalFrequencyTheCarrierIs')} unit="Hz">
                <Num v={sa.f1} set={v => setSa({ f1: v })} /></Row>
              <Row label={tx('modulationM')} tip="m = 2·V_phase,peak / V_dc. Sets the output voltage and so the power; with synchronous rectification it does not change the device losses.">
                <Num v={sa.m} set={v => setSa({ m: v })} /></Row>
              <Row label="cos φ" tip={tx('displacementPowerFactorOfTheLoad')}>
                <Num v={sa.pf} set={v => setSa({ pf: v })} /></Row>
              <Row label={tx('modulation')} tip="SVPWM is linear to m = 2/√3 ≈ 1.155, sine-triangle to m = 1; above that the run warns as overmodulated.">
                <TextField select size="small" value={sa.scheme} onChange={e => setSa({ scheme: e.target.value })}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="svpwm" sx={{ fontSize: 12 }}>SVPWM</MenuItem>
                  <MenuItem value="spwm" sx={{ fontSize: 12 }}>{tx('sineTriangle')}</MenuItem>
                </TextField>
              </Row>
              <Row label={tx('powerDirection')} tip="Motor: DC link → machine. Generator: an active rectifier, machine → DC link — efficiency = (P_ac − P_loss)/P_ac and the link current reverses. MOSFET losses do not change with direction (synchronous rectification: the reverse current is in the channel). A motor run takes the duty's own mode.">
                <TextField select size="small" value={sa.direction || 'motor'}
                  onChange={e => setSa({ direction: e.target.value })}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="motor" sx={{ fontSize: 12 }}>{tx('motor')}</MenuItem>
                  <MenuItem value="generator" sx={{ fontSize: 12 }}>{tx('generatorActiveRectifier')}</MenuItem>
                </TextField>
              </Row>
              {standaloneProblems(sa, formNow()).length > 0 && (
                <Alert severity="warning" sx={{ fontSize: 11.5, py: 0 }}>
                  {standaloneProblems(sa, formNow()).join(' · ')}</Alert>)}
            </>)}
            <Row label={tx('device')} tip={tx('thePowerDeviceFromTheCatalogue')}>
              <TextField select size="small" value={device} onChange={e => setDevice(e.target.value)}
                sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                {devices.filter(d => !d.error).map(d =>
                  <MenuItem key={d.part} value={d.part} sx={{ fontSize: 12 }}>{d.part}</MenuItem>)}
              </TextField>
            </Row>
            <SectionLabel sx={{ mt: 0.5 }}>{tx('pwmRipple')}</SectionLabel>
            <Row label="L_d" unit="µH" tip="Phase inductance the carrier sees (d-axis). Blank = the duty's own incremental L_d (star-equivalent in delta). Typed = per inverter phase.">
              <Num v={rip.ld} set={v => setRip({ ld: v })} placeholder={tx('duty')} /></Row>
            <Row label="L_q" unit="µH" tip="q-axis inductance. Blank = L_d (or the duty's own L_q).">
              <Num v={rip.lq} set={v => setRip({ lq: v })} placeholder="= L_d" /></Row>
            <Row label={tx('lCommutating')} unit="µH" tip="Optional subtransient/commutating inductance — what the winding shows at carrier frequency when eddy currents screen the iron. Replaces L_d and L_q when typed.">
              <Num v={rip.lsub} set={v => setRip({ lsub: v })} /></Row>
            {nInvNow === 2 && (<>
              <Row label="L_xy" unit="% L_d" tip={tx('dual3PhaseTheXY')}>
                <Num v={rip.lxyPct} set={v => setRip({ lxyPct: v })} /></Row>
              <Row label={tx('neutrals')} tip="Isolated: each set's zero-sequence current is zero. Common: the two neutrals tied — the difference zero sequence circulates through L_0.">
                <TextField select size="small" value={rip.neutral} onChange={e => setRip({ neutral: e.target.value })}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="isolated" sx={{ fontSize: 12 }}>{tx('isolated2Neutrals')}</MenuItem>
                  <MenuItem value="common" sx={{ fontSize: 12 }}>{tx('commonTied')}</MenuItem>
                </TextField>
              </Row>
              {rip.neutral === 'common' && (
                <Row label="L_0" unit="µH" tip="Zero-sequence inductance of the tied neutrals' circulating path. Required with common neutrals.">
                  <Num v={rip.l0} set={v => setRip({ l0: v })} /></Row>)}
              <Row label={tx('carrierInterleave')} unit="°" tip={tx('carrierPhaseShiftOfInverter2')}>
                <Num v={rip.interleave} set={v => setRip({ interleave: v })} /></Row>
            </>)}
            <Row label={tx('thdLimit')} unit="%" tip={tx('optionalPhaseCurrentThdPwmRipple')}>
              <Num v={rip.thdLimit} set={v => setRip({ thdLimit: v })} /></Row>
            {rippleProblems(rip, nInvNow).length > 0 && (
              <Alert severity="warning" sx={{ fontSize: 11.5, py: 0 }}>
                {rippleProblems(rip, nInvNow).join(' · ')}</Alert>)}
            {!sa.on && <Row label={tx('topology')} tip={tx('howTheBridgesAreCombinedWith')}>
              <TextField select size="small" value={topology} onChange={e => setTopology(e.target.value)}
                sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                {presets.map(p => <MenuItem key={p.id} value={p.id} sx={{ fontSize: 12 }}>{p.label}</MenuItem>)}
              </TextField>
            </Row>}
            {!sa.on && topology === 'two_3ph' && (
              <Row label={tx('coilSplit')} tip="series_split: the winding is untouched, so each inverter keeps the per-coil current and supplies half the volts. power_split: the sets are reconnected so each inverter delivers half the power at the full bus, halving the device current.">
                <TextField select size="small" value={setSplit} onChange={e => setSetSplit(e.target.value)}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="series_split" sx={{ fontSize: 12 }}>{tx('seriesSplit')}</MenuItem>
                  <MenuItem value="power_split" sx={{ fontSize: 12 }}>{tx('powerSplit')}</MenuItem>
                </TextField>
              </Row>)}
            {topology !== 'h_bridge' && (
              <Row label="PWM" tip="Three-phase modulation. Sine is linear to m = 1; SVPWM (min-max zero sequence) and third-harmonic injection add the same common-mode term to all three legs, so the line voltages are unchanged and the linear range extends to m = 2/√3 (+15.5 % voltage on the same DC link). Used by the loss model and by the coupled drive=inverter run.">
                <TextField select size="small" value={pwmMod} onChange={e => setPwmMod(e.target.value)}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="sine" sx={{ fontSize: 12 }}>{tx('sine')}</MenuItem>
                  <MenuItem value="svpwm" sx={{ fontSize: 12 }}>SVPWM</MenuItem>
                  <MenuItem value="third_harmonic" sx={{ fontSize: 12 }}>{tx('thirdHarmonic')}</MenuItem>
                </TextField>
              </Row>)}
            {topology === 'h_bridge' && (
              <Row label={tx('hBridgePwm')} tip={tx('unipolarSwitchesTheTwoLegsAgainst')}>
                <TextField select size="small" value={hbMod} onChange={e => setHbMod(e.target.value)}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="unipolar" sx={{ fontSize: 12 }}>{tx('unipolar')}</MenuItem>
                  <MenuItem value="bipolar" sx={{ fontSize: 12 }}>{tx('bipolar')}</MenuItem>
                </TextField>
              </Row>)}
            <Row label={tx('devicesSwitch')} tip="How many of the chosen part sit in parallel in ONE switch position. They are assumed to share the current equally — the usual reason a real stack is derated. The same number for every bridge; per-bridge counts only via the API."><Num v={nPar} set={setNPar} /></Row>
            {(() => { const d = devices.find(x => x.part === device);
              return d && d.r_th_jc_k_w == null && d.r_th_jc_max_k_w == null; })() && (
              <Row label="R_th j-c (assumed)" tip="This device's datasheet prints no junction-to-case resistance, so the junction temperature needs a stated value per switch. It is shown as an assumption on the result." unit="K/W">
                <Num v={ext.rthJc} set={v => setExt({ rthJc: v })} /></Row>)}
            <Row label={tx('switchingLosses')} tip={"SPICE = the vendor's own device model run in the datasheet's double-pulse circuit (ngspice, the KiCad engine): switching, conduction and dead time from the same model, for every device that has one. Datasheet = the card's curves, the labelled fallback for a device whose model cannot run here"
                + (devRow?.spice?.needs ? ` — ${devRow.spice.needs}.` : '.')}>
              <TextField select size="small" value={basis || (devRow?.basis_default ?? 'spice')}
                onChange={e => setBasis(e.target.value === (devRow?.basis_default ?? 'spice') ? '' : e.target.value)}
                sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                <MenuItem value="spice" disabled={devRow != null && !devRow.spice?.has_table} sx={{ fontSize: 12 }}>
                  SPICE{devRow != null && !devRow.spice?.has_table ? ' (no model)' : ''}</MenuItem>
                <MenuItem value="datasheet" sx={{ fontSize: 12 }}>{tx('datasheet')}</MenuItem>
              </TextField>
            </Row>
            {devRow?.spice?.deviation_line && (basis || devRow.basis_default) === 'spice' && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: -0.6, ml: 0.5 }}>
                <Typography sx={{ fontSize: 10.5, color: '#fcd34d' }}>{devRow.spice.deviation_line}</Typography>
                <HelpTip i18nKey="controller.theVendorModelWasChecked" />
              </Box>)}
            <Row label="R_G,on" tip="External turn-on gate resistance per device. Default = the datasheet test circuit (2.3 Ω CoolSiC, 1.6 Ω OptiMOS). SPICE: interpolated between the simulated resistances; datasheet: a line through the datasheet point with its figure's slope." unit="Ω"><Num v={rg} set={setRg} /></Row>
            <Row label="R_G,off" tip="External turn-off gate resistance per device (blank = same as R_G,on). SPICE only; the datasheet path has one R_G." unit="Ω"><Num v={rgOff} set={setRgOff} /></Row>
            <Row label="V_GS on" tip="Gate-on voltage (blank = the datasheet's: 18 V CoolSiC, 10 V OptiMOS). Picks the R_DS(on) curve and the SPICE set." unit="V"><Num v={vgsOn} set={setVgsOn} /></Row>
            <Row label="V_GS off" tip={tx('gateOffVoltage0VOr')} unit="V"><Num v={vgsOff} set={setVgsOff} /></Row>
            <Row label="Loop L_σ" tip={`Power-loop stray inductance per commutation cell (blank = the datasheet test circuit${devRow?.spice?.l_sigma_default_nH != null ? `: ${devRow.spice.l_sigma_default_nH} nH` : ''}). SPICE only: the nearest simulated loop is used and named.`} unit="nH"><Num v={lSigma} set={setLSigma} /></Row>
            <Row label={tx('deadTime')} tip={tx('bothSwitchesOfALegOff')} unit="µs"><Num v={dead} set={setDead} /></Row>
            <Divider sx={{ borderColor: 'var(--panel)', my: 0.5 }} />
            <Row label={tx('carrier')} tip={'PWM carrier frequency — THE machine\'s carrier: the coupled '
                + 'run (drive inverter/PWM), the thermal loss map and the report\'s resonance '
                + 'checks all read it from here. Saved with the configuration. '
                + 'A configuration with none saved starts from the old Simulation-tab carrier, else 20 kHz.'}
                unit="Hz">
              <Num v={fsw} set={onFswChange} />
            </Row>
            {carrierOriginLine(fswOrigin) && (
              <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', mt: -0.6, ml: 0.5 }}>
                {carrierOriginLine(fswOrigin)}</Typography>)}
            <Row label={tx('dcLink')} tip={'Bus voltage. Blank = the configuration\'s battery, nominal'
                + (point?.v_dc_V != null
                  ? ` — ${fmt(point.v_dc_V, 0)} V now (${point.sources?.v_dc_V || 'resolved'}).`
                  : ' — this configuration has none yet.')
                + ' Type a value to override it.'} unit="V">
              <Num v={vdc} set={setVdc}
                placeholder={point?.v_dc_V != null ? `battery ${fmt(point.v_dc_V, 0)}` : undefined} />
            </Row>
            {point && (point.i_phase_rms_A != null || point.rpm != null) && (
              <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.5 }}>
                {point.i_phase_rms_A != null &&
                  <Chip size="small" label={`I_phase ${fmt(point.i_phase_rms_A, 0)} A`} sx={{ fontSize: 10, height: 20 }} />}
                {point.star_delta &&
                  <Chip size="small" label={point.star_delta} sx={{ fontSize: 10, height: 20 }} />}
                {point.rpm != null &&
                  <Chip size="small" label={`${fmt(point.rpm, 0)} rpm`} sx={{ fontSize: 10, height: 20 }} />}
                {point.p_ac_W != null &&
                  <Chip size="small" label={`P_ac ${fmt(point.p_ac_W, 0)} W`} sx={{ fontSize: 10, height: 20 }} />}
                {point.modulation_index != null &&
                  <Chip size="small" label={`m ${fmt(point.modulation_index, 3)}`} sx={{ fontSize: 10, height: 20 }} />}
                {point.power_factor != null &&
                  <Chip size="small" label={`cos φ ${fmt(point.power_factor, 3)}`} sx={{ fontSize: 10, height: 20 }} />}
                <HelpTip title={Object.entries(point.sources || {})
                  .map(([k, v]) => `${k}: ${v}`).join('\n') || 'From the loaded duty\'s electromagnetic/coupled record.'} />
              </Box>
            )}
            <Divider sx={{ borderColor: 'var(--panel)', my: 0.5 }} />
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <SectionLabel sx={{ mb: 0.5, flex: 1 }}>{tx('mosfetCooling')}</SectionLabel>
              <HelpTip i18nKey="controller.windSpeedAmbientAirAnd" />
            </Box>
            <Row label={tx('mode')} tip={tx('liquidColdplateForcedAirFanSlipstream')}>
              <TextField select size="small" value={coolingMode} onChange={e => setCoolingMode(e.target.value)}
                sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                <MenuItem value="liquid" sx={{ fontSize: 12 }}>{tx('liquidColdplate')}</MenuItem>
                <MenuItem value="air_forced" sx={{ fontSize: 12 }}>{tx('airForced')}</MenuItem>
                <MenuItem value="air_still" sx={{ fontSize: 12 }}>{tx('airStill')}</MenuItem>
              </TextField>
            </Row>
            {coolingMode === 'liquid' && (<>
              <Row label={tx('coolant')} tip="The coldplate fluid, from the same catalogue the motor jacket uses. Inherits the Thermal tab's jacket fluid by default.">
                <TextField select size="small" value={coolant} onChange={e => setCoolant(e.target.value)}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="" sx={{ fontSize: 12, fontStyle: 'italic' }}>(from Thermal)</MenuItem>
                  {['water', 'water_glycol_50', 'water_glycol_50_65c', 'ethylene_glycol', 'oil',
                    'rp3_kerosene'].map(c =>
                    <MenuItem key={c} value={c} sx={{ fontSize: 12 }}>{c}</MenuItem>)}
                </TextField>
                {coolant !== '' && <ResetToThermal onReset={() => setCoolant('')} />}
              </Row>
              {thermalHint('liquid', 'coolant', coolant !== '', v => String(v))}
              <Row label={tx('flow')} tip="Coldplate flow. It sets the film coefficient AND the coolant's own temperature rise. Inherits the Thermal tab's jacket flow by default." unit="L/min">
                <Num v={flow} set={setFlow}
                  placeholder={thermalField('liquid', 'flow_lpm') != null
                    ? fmt(thermalField('liquid', 'flow_lpm') as number, 1) : undefined} />
                {flow !== '' && <ResetToThermal onReset={() => setFlow('')} />}
              </Row>
              {thermalHint('liquid', 'flow_lpm', flow !== '', v => `${fmt(v as number, 1)} L/min`)}
              <Row label={tx('inlet')} tip="Coolant inlet temperature — the bottom of the whole thermal stack. Inherits the Thermal tab's jacket inlet by default." unit="°C">
                <Num v={tin} set={setTin}
                  placeholder={thermalField('liquid', 't_in_c') != null
                    ? fmt(thermalField('liquid', 't_in_c') as number, 1) : undefined} />
                {tin !== '' && <ResetToThermal onReset={() => setTin('')} />}
              </Row>
              {thermalHint('liquid', 't_in_c', tin !== '', v => `${fmt(v as number, 1)} °C`)}
              <Row label={tx('plumbing')} tip="One shared plate under every device; or one plate per module fed in parallel (flow ÷ plates, each at the inlet); or in series (full flow through each, the last plate sees the hottest coolant — that one is reported).">
                <TextField select size="small" value={ext.plumbing} onChange={e => setExt({ plumbing: e.target.value })}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="shared" sx={{ fontSize: 12 }}>{tx('oneSharedPlate')}</MenuItem>
                  <MenuItem value="parallel" sx={{ fontSize: 12 }}>{tx('platesInParallel')}</MenuItem>
                  <MenuItem value="series" sx={{ fontSize: 12 }}>{tx('platesInSeries')}</MenuItem>
                </TextField>
              </Row>
              {ext.plumbing !== 'shared' && (
                <Row label={tx('plates')} tip={tx('howManyPlatesUsuallyOnePer')}>
                  <Num v={ext.nPlates} set={v => setExt({ nPlates: v })} /></Row>)}
              <Row label={tx('plateRStated')} tip="Blank = the channel correlation. Or type a plate-to-coolant resistance per plate, e.g. from a coldplate datasheet; it is corrected to this coolant and flow below." unit="K/W">
                <Num v={ext.rPlate} set={v => setExt({ rPlate: v })} /></Row>
              {ext.rPlate !== '' && (<>
                <Row label={tx('measuredWith')} tip="The fluid that stated R was measured with. A different working coolant is corrected by the Dittus–Boelter film ratio; the laminar bound is shown beside it.">
                  <TextField select size="small" value={ext.rPlateFluid} onChange={e => setExt({ rPlateFluid: e.target.value })}
                    sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                    <MenuItem value="" sx={{ fontSize: 12, fontStyle: 'italic' }}>(this coolant — no correction)</MenuItem>
                    {['water', 'water_glycol_50', 'water_glycol_50_65c', 'ethylene_glycol', 'oil', 'rp3_kerosene'].map(c =>
                      <MenuItem key={c} value={c} sx={{ fontSize: 12 }}>{c}</MenuItem>)}
                  </TextField>
                </Row>
                <Row label={tx('atFlow')} tip="Per-plate flow the stated R was measured at; blank = this run's per-plate flow. The film scales with flow^0.8." unit="L/min">
                  <Num v={ext.rPlateFlow} set={v => setExt({ rPlateFlow: v })} /></Row>
              </>)}
            </>)}
            {coolingMode !== 'liquid' && (<>
              {coolingMode === 'air_forced' && (<>
                <Row label={tx('windSpeed')} tip="Air speed over the device heatsink/plate — the same 'wind speed' input as the thermal simulation. Inherits the Thermal tab's forced-air speed by default; blank with nothing to inherit = 5 m/s." unit="m/s">
                  <Num v={airSpeed} set={setAirSpeed}
                    placeholder={thermalField('air_forced', 'air_speed_mps') != null
                      ? fmt(thermalField('air_forced', 'air_speed_mps') as number, 1) : undefined} />
                  {airSpeed !== '' && <ResetToThermal onReset={() => setAirSpeed('')} />}
                </Row>
                {thermalHint('air_forced', 'air_speed_mps', airSpeed !== '', v => `${fmt(v as number, 1)} m/s`)}
              </>)}
              <Row label={tx('ambient')} tip="Ambient air temperature — the bottom of the whole thermal stack in this mode. Inherits the Thermal tab's ambient by default; blank with nothing to inherit = 40 °C." unit="°C">
                <Num v={tAmbient} set={setTAmbient}
                  placeholder={thermalField(coolingMode as 'air_forced' | 'air_still', 't_ambient_c') != null
                    ? fmt(thermalField(coolingMode as 'air_forced' | 'air_still', 't_ambient_c') as number, 1) : undefined} />
                {tAmbient !== '' && <ResetToThermal onReset={() => setTAmbient('')} />}
              </Row>
              {thermalHint(coolingMode as 'air_forced' | 'air_still', 't_ambient_c', tAmbient !== '',
                v => `${fmt(v as number, 1)} °C`)}
              <Row label={tx('areaBasis')} tip={tx('aHeatsinkBoltedToEachDevice')}>
                <TextField select size="small" value={areaBasis} onChange={e => setAreaBasis(e.target.value as 'heatsink' | 'plate')}
                  sx={{ width: 190, '& .MuiSelect-select': { fontSize: 12, py: 0.6 } }}>
                  <MenuItem value="heatsink" sx={{ fontSize: 12 }}>{tx('perDeviceHeatsink')}</MenuItem>
                  <MenuItem value="plate" sx={{ fontSize: 12 }}>{tx('sharedPcbPlate')}</MenuItem>
                </TextField>
              </Row>
              <Row label={tx('wettedArea')} tip="Blank = a small finned heatsink, 40 cm² per device (this module's stated assumption)." unit="cm²">
                <Num v={areaCm2} set={setAreaCm2} />
              </Row>
              <Row label={tx('finEfficiency')} tip="Stated constant, not fitted. Blank = 0.75 (a short aluminium pin/plate fin); a bare flat pad with no fins is 1.0.">
                <Num v={finEff} set={setFinEff} />
              </Row>
              {coolingMode === 'air_still' && (
                <Row label={tx('emissivity')} tip="Blank = 0.9 (anodised aluminium / bare FR4 solder mask); a bare polished heatsink is far lower.">
                  <Num v={emissivity} set={setEmissivity} />
                </Row>)}
            </>)}
            <Row label="R_th TIM" tip="Thermal interface between the device tab and the plate/heatsink, per device, every mode. It is comparable with R_th(j-c) itself, so it changes the answer." unit="K/W"><Num v={rtim} set={setRtim} /></Row>
          </Box>
        </Paper>

        {/* ── results ── */}
        <Paper sx={{ ...CARD, flex: 1, minWidth: 420 }}>
          {busy && !res && <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', color: 'var(--text-3)', py: 2 }}><CircularProgress size={16} /> {tx('solving')}</Box>}
          {!res && !busy && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>
                {point?.line || 'Press Solve — the operating point comes from the loaded duty.'}
              </Typography>
              {point?.line && (
                <HelpTip title={Object.entries(point.sources || {})
                  .map(([k, v]) => `${k}: ${v}`).join('\n')} />
              )}
            </Box>
          )}
          {res && (
            <>
              {/* The result must never disagree with the form silently
                  (owner 2026-09-25) — ONE line, before anything else. */}
              {staleLine && (
                <Alert severity="warning" sx={{ mb: 1, fontSize: 12.5 }}>{staleLine}</Alert>
              )}
              {/* ONE LINE for the whole model, the full text behind the ⓘ.
                  Owner 2026-09-22: «не пиши это всё, никто это не читает». */}
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  Basis: {String(L?.basis_label ?? 'datasheet curves')} · synchronous
                  rectification · {res.settings?.devices_parallel} device(s) per switch sharing equally
                </Typography>
                <HelpTip title={(res.model_notes || []).join('. ')} />
              </Box>
              {L?.spice_deviation_line && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: -0.75, mb: 1 }}>
                  <Typography sx={{ fontSize: 11, color: '#fcd34d' }}>
                    {String(L.spice_deviation_line)} (see ⓘ)</Typography>
                  <HelpTip i18nKey="controller.theVendorSpiceModelAgainst" />
                </Box>)}
              <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap', mb: 1.5 }}>
                <Tile label={tx('inverterLosses')} value={`${fmt(L?.total_W, 0)} W`} />
                <Tile label={res.power_direction === 'generator'
                    ? 'Rectifier efficiency (generator)' : 'Inverter efficiency (motor)'}
                  value={pct(E?.inverter)}
                  tip={(E as any)?.inverter_definition || ''} />
                <Tile label={tx('wallToShaftEfficiency')} value={pct(E?.wall_to_shaft)}
                  tip="inverter efficiency × the ONE shaft efficiency of this duty's coupled record" />
                <Tile label="T_j max" value={`${fmt(T?.t_j_max_c, 0)} °C`}
                  bad={(T?.margin_K ?? 1) < 0}
                  tip={`limit ${fmt(T?.t_j_limit_c, 0)} °C · margin ${fmt(T?.margin_K, 0)} K`} />
                <Tile label={tx('dcLinkRipple')} value={`${fmt(res.dc_link?.i_cap_rms_A, 0)} A rms`}
                  tip={`mean ${fmt(res.dc_link?.i_dc_mean_A, 0)} A · pk-pk ${fmt(res.dc_link?.i_dc_pp_A, 0)} A`} />
                <Tile label={tx('switches')} value={`${res.topology?.n_switches} × ${res.settings?.devices_parallel}`}
                  tip={`${res.topology?.n_devices} devices in ${res.topology?.n_bridges} bridge(s)`} />
              </Box>

              <RippleCard r={res.ripple} />

              <SectionLabel sx={{ mb: 0.75 }}>{tx('lossSplit')}</SectionLabel>
              <Box sx={{ display: 'grid', gridTemplateColumns: '1.6fr repeat(5, 1fr)', rowGap: 0.5, columnGap: 1 }}>
                <Typography sx={TH}>{tx('bridgeLeg')}</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>{tx('conduction')}</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>3rd quadrant</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>{tx('switching')}</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>{tx('perDevice')}</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>T_j</Typography>
                {res.bridges?.map(b => (
                  <React.Fragment key={b.id}>
                    <Box sx={{ gridColumn: '1 / -1', mt: 0.5, display: 'flex',
                               alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                      <Typography sx={{ fontSize: 11.5, color: 'var(--text-0)' }}>
                        {b.label} · {b.connection} · m {fmt(b.modulation_index, 3)} · {fmt(b.p_loss_W, 0)} W · ×{b.devices_parallel}
                      </Typography>
                    </Box>
                    {b.legs.map(l => (
                      <React.Fragment key={`${b.id}${l.leg}`}>
                        <Typography sx={{ fontSize: 12, color: 'var(--text-2)', fontFamily: 'monospace' }}>
                          {b.id}/{l.leg} · {fmt(l.i_leg_rms_A, 0)} A</Typography>
                        <Typography sx={TD}>{fmt(l.p_conduction_W, 0)} W</Typography>
                        <Typography sx={TD}>{fmt(l.p_third_quadrant_W, 0)} W</Typography>
                        <Typography sx={TD}>{fmt(l.p_switching_W, 0)} W</Typography>
                        <Typography sx={TD}>{fmt(l.p_device_W, 1)} W</Typography>
                        <Typography sx={{ ...TD, color: l.t_j_c > (T?.t_j_limit_c ?? 1e9) ? '#fca5a5' : 'var(--text-1)' }}>
                          {fmt(l.t_j_c, 0)} °C</Typography>
                      </React.Fragment>))}
                  </React.Fragment>))}
                <Divider sx={{ gridColumn: '1 / -1', borderColor: 'var(--panel)', my: 0.5 }} />
                {/* The totals do NOT go in the grid: its last two columns are
                    "per device" and "T_j", and a sum has neither. */}
                <Typography sx={{ gridColumn: '1 / -1', fontSize: 12.5, color: 'var(--text-0)' }}>
                  Total {fmt(L?.total_W, 0)} W — conduction {fmt(L?.conduction_W, 0)} W ·
                  3rd quadrant {fmt(L?.third_quadrant_W, 0)} W ·
                  switching {fmt(L?.switching_W, 0)} W ·
                  E_oss {fmt(L?.e_oss_W, 0)} W
                </Typography>
              </Box>
              {/* ── the datasheet limits, always, one line + the table ── */}
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mt: 1.5 }}>
                <SectionLabel sx={{ m: 0 }}>{tx('datasheetLimits')}</SectionLabel>
                <Chip size="small"
                  label={res.limits_verdict === 'fail' ? 'FAIL'
                       : res.limits_verdict === 'warn' ? 'WARNING' : 'PASS'}
                  color={res.limits_verdict === 'fail' ? 'error'
                       : res.limits_verdict === 'warn' ? 'warning' : 'success'}
                  sx={{ fontSize: 10, height: 20 }} />
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  {res.feasible === false
                    ? `${(res.limits || []).filter(r => r.verdict === 'fail').length} limit(s) exceeded`
                    : `inside every published limit of ${res.device}`}
                </Typography>
                <HelpTip title={tx('everyPublishedLimitOfTheChosen')} />
              </Box>
              <Box sx={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr 1fr 0.9fr 0.9fr', rowGap: 0.4, columnGap: 1, mt: 0.5 }}>
                <Typography sx={TH}>{tx('limit')}</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>{tx('thisDuty')}</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>{tx('rating')}</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>{tx('margin')}</Typography>
                <Typography sx={{ ...TH, textAlign: 'right' }}>{tx('verdict')}</Typography>
                {(res.limits || []).map(r => (
                  <React.Fragment key={r.name}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.4, minWidth: 0 }}>
                      <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{r.name}</Typography>
                      <HelpTip title={`${r.source}${r.note ? ` — ${r.note}` : ''}`} />
                    </Box>
                    <Typography sx={TD}>{r.value == null ? '—' : `${fmt(r.value, 1)} ${r.unit}`}</Typography>
                    <Typography sx={TD}>{r.limit == null ? '—' : `${fmt(r.limit, 1)} ${r.unit}`}</Typography>
                    <Typography sx={TD}>{r.margin == null ? '—' : `${fmt(r.margin, 1)} ${r.unit}`}</Typography>
                    <Typography sx={{ ...TD, color: r.verdict === 'fail' ? '#fca5a5'
                                        : r.verdict === 'warn' ? '#fbbf24'
                                        : r.verdict === 'pass' ? '#34d399' : 'var(--text-3)' }}>
                      {r.verdict === 'not_judged' ? 'not judged' : r.verdict.toUpperCase()}
                    </Typography>
                  </React.Fragment>))}
              </Box>

              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.75 }}>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  E_oss {L?.e_oss_policy === 'added' ? 'added' : 'in E_on'} ·
                  {' '}{T?.cooling_mode === 'air_forced' ? 'air — forced'
                       : T?.cooling_mode === 'air_still' ? 'air — still' : 'liquid coldplate'} ·
                  {T?.cooling_mode === 'liquid'
                    ? ` coolant ${fmt(T?.t_coolant_in_c, 0)} → ${fmt(T?.t_coolant_in_c + (T?.coolant_rise_K ?? 0), 0)} °C ·`
                      + (T?.plumbing ? ` ${T.plumbing.n_plates} plates ${T.plumbing.mode}, hottest plate in ${fmt(T.plumbing.t_plate_in_c, 1)} °C ·` : '')
                    : ` ambient ${fmt(T?.t_coolant_in_c, 0)} °C ·`}
                  {' '}case {fmt(T?.t_case_c, 0)} °C · R_path {fmt(T?.r_coldplate_k_w, 4)} K/W
                </Typography>
                <HelpTip title={`E_oss reference ${fmt(L?.e_oss_reference_W, 0)} W.`} />
              </Box>
            </>
          )}
        </Paper>
      </Box>

      {/* ── schematic + waveform ── */}
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mt: 2, alignItems: 'flex-start' }}>
        <Paper sx={{ ...CARD, flex: 1, minWidth: 420 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <SectionLabel sx={{ m: 0 }}>{tx('powerSchematic')}</SectionLabel>
            <HelpTip i18nKey="controller.drawnFromTheChosenTopology" />
          </Box>
          <Box sx={{ color: 'var(--text-1)', overflowX: 'auto' }}
            dangerouslySetInnerHTML={{ __html: svg }} />
        </Paper>

        <Paper sx={{ ...CARD, flex: 1, minWidth: 380 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <SectionLabel sx={{ m: 0 }}>{tx('oneElectricalPeriod')}</SectionLabel>
            <HelpTip title={tx('whatTheCoilActuallySeesThe')} />
          </Box>
          {wave && firstCoil ? (
            <>
              <svg viewBox="0 0 600 150" style={{ width: '100%' }} role="img" aria-label={tx('phaseVoltageAndCurrent')}>
                {/* Each trace on its OWN scale (the report's rule for a figure
                    pair): the PWM voltage swings the bus, the current a few
                    hundred amps, and one axis would hide the second. */}
                <polyline points={polyline(wave.coils[firstCoil].v_V, 600, 150)} fill="none"
                  stroke="var(--text-3)" strokeWidth="0.8" />
                <polyline points={polyline(wave.coils[firstCoil].i_A, 600, 150)} fill="none"
                  stroke="#0ea5e9" strokeWidth="1.6" />
              </svg>
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                coil {firstCoil} · v_rms {fmt(wave.coils[firstCoil].v_rms_V, 0)} V ·
                i_rms {fmt(wave.coils[firstCoil].i_rms_A, 0)} A ·
                f {fmt(wave.f_elec_hz, 0)} Hz · dead time {fmt(wave.dead_time_us, 2)} µs
                {wave.dead_time_error_V != null && ` · dead-time voltage error ±${fmt(wave.dead_time_error_V, 1)} V`}
              </Typography>
            </>
          ) : <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tx('solveToDrawTheWaveform')}</Typography>}
          {res && (
            <Box sx={{ mt: 1.5 }}>
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                {P?.i_leg_rms_3ph_A != null && `leg ${fmt(P.i_leg_rms_3ph_A, 0)} A rms · `}
                phase {fmt(P?.i_phase_rms_A, 0)} A · {P?.star_delta} ·
                m {fmt(P?.modulation_index, 3)} · pf {fmt(P?.power_factor, 3)} ·
                {` ${fmt(P?.f_carrier_hz, 0)} Hz carrier · ${fmt(P?.v_dc_V, 0)} V bus`}
              </Typography>
            </Box>)}
        </Paper>
      </Box>

      {/* ── custom mapping ── */}
      {topology === 'custom' && (
        <Paper sx={{ ...CARD, mt: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <SectionLabel sx={{ m: 0 }}>{tx('coilBridgeMapping')}</SectionLabel>
            <HelpTip i18nKey="controller.oneRowPerCoilWhich" />
          </Box>
          <Box sx={{ display: 'grid', gridTemplateColumns: 'auto 1fr', rowGap: 0.75, columnGap: 1.5, alignItems: 'center', maxWidth: 520 }}>
            {coils.map(c => (
              <React.Fragment key={c.index}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)', fontFamily: 'monospace' }}>{c.label}</Typography>
                <TextField size="small" value={mapping[c.index] ?? `INV1/${c.phase}`}
                  onChange={e => setMapping(m => ({ ...m, [c.index]: e.target.value }))}
                  sx={{ width: 160, '& input': { fontSize: 12, py: 0.5, fontFamily: 'monospace' } }} />
              </React.Fragment>))}
          </Box>
        </Paper>)}

      {/* ── the stacked comparison, the same table every other tab uses ── */}
      <Box sx={{ mt: 2 }}>
        <LocalCompareTable
          title={tx('controllerVariants')}
          rows={rows}
          columns={CONTROLLER_COMPARE_COLUMNS}
          onRemove={(id) => saveRows(rows.filter(r => r.id !== id))}
          onClear={() => saveRows([])}
          onRename={(id, name) => saveRows(rows.map(r => (r.id === id ? { ...r, name } : r)))}
          emptyHint={'Press "+ Add to comparison" and this solve becomes a column here — '
                     + 'two topologies, two devices or two coldplates side by side.'}
        />
      </Box>

      {/* ── catalogue ── */}
      <Box sx={{ mt: 2 }}>
        <DeviceCatalog devices={devices} selected={device} onSelect={setDevice}
          onChanged={setDevices} parallel={nPar} onParallel={setNPar}
          switchCurrent={switchCurrent} />
      </Box>

    </Box>
  );
};

const Row: React.FC<{ label: string; tip: string; unit?: string; children: React.ReactNode }> =
  ({ label, tip, unit, children }) => (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <Typography sx={{ fontSize: 12, color: 'var(--text-1)', flex: 1 }}>{label}</Typography>
      <HelpTip title={tip} />
      {children}
      {unit && <Typography sx={{ fontSize: 10, color: 'var(--text-3)', width: 32 }}>{unit}</Typography>}
    </Box>);

const Num: React.FC<{ v: Nullable; set: (v: Nullable) => void; placeholder?: string }> =
  ({ v, set, placeholder }) => (
  <TextField type="number" size="small" value={v} placeholder={placeholder}
    onChange={e => set(e.target.value === '' ? '' : parseFloat(e.target.value))}
    sx={NUM} />);

/** PWM current ripple / THD — one line of numbers + the top harmonics. */
const RippleCard: React.FC<{ r?: ControllerResult['ripple'] }> = ({ r }) => {
  if (!r) return null;
  if (r.status !== 'computed') {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1.5 }}>
        <SectionLabel sx={{ m: 0 }}>{tx('pwmRipple')}</SectionLabel>
        <Typography sx={{ fontSize: 11.5, color: r.status === 'missing_input' ? '#fca5a5' : 'var(--text-3)' }}>
          not computed — {r.reason}</Typography>
      </Box>);
  }
  const top = [...(r.spectrum || [])].sort((a, b) => b.i_rms_A - a.i_rms_A).slice(0, 6);
  return (
    <Box sx={{ mb: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.75 }}>
        <SectionLabel sx={{ m: 0 }}>{tx('pwmRipple')}</SectionLabel>
        {r.thd_verdict && (
          <Chip size="small" label={r.thd_verdict === 'fail' ? 'THD FAIL' : 'THD PASS'}
            color={r.thd_verdict === 'fail' ? 'error' : 'success'} sx={{ fontSize: 10, height: 20 }} />)}
        <HelpTip title={`${r.model || ''}. L_d/L_q ${fmt(r.l_d_uH, 1)}/${fmt(r.l_q_uH, 1)} µH`
          + (r.l_xy_uH ? `, L_xy ${fmt(r.l_xy_uH, 1)} µH` : '')
          + `. f_sw/f1 = ${fmt(r.carrier_ratio, 3)}${r.synchronous ? ' (synchronous)' : ' (asynchronous)'}, `
          + `${r.window_periods} period(s) simulated. `
          + Object.entries(r.sources || {}).map(([k, v]) => `${k}: ${v}`).join('; ')} />
      </Box>
      <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap', mb: 0.75 }}>
        <Tile label={tx('ripple')} value={`${fmt(r.ripple_rms_A, 1)} A rms`}
          tip={`pk-pk ${fmt(r.ripple_pp_A, 0)} A · alpha-beta ${fmt(r.ripple_rms_ab_A, 1)} A`
            + (r.ripple_rms_xy_A != null ? ` · x-y ${fmt(r.ripple_rms_xy_A, 1)} A` : '')} />
        <Tile label={tx('phaseCurrentThd')} value={`${fmt(r.thd_pct, 2)} %`}
          bad={r.thd_verdict === 'fail'}
          tip={r.thd_limit_pct != null ? `limit ${fmt(r.thd_limit_pct, 1)} %` : 'no limit set'} />
        <Tile label={tx('dcLinkCapWithRipple')} value={`${fmt(r.dc_link?.i_cap_rms_A, 0)} A rms`}
          tip={`bus mean ${fmt(r.dc_link?.i_dc_mean_A, 0)} A (negative = into the link)`} />
      </Box>
      {top.length > 0 && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'monospace' }}>
          {top.map(h => `${fmt(h.f_Hz / 1000, 2)} kHz ${fmt(h.i_rms_A, 1)} A`).join(' · ')}
        </Typography>)}
    </Box>);
};

const Tile: React.FC<{ label: string; value: string; tip?: string; bad?: boolean }> =
  ({ label, value, tip, bad }) => (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{label}</Typography>
        {tip && <HelpTip title={tip} />}
      </Box>
      <Typography sx={{ fontSize: 18, fontWeight: 800, color: bad ? '#fca5a5' : 'var(--text-0)' }}>
        {value}</Typography>
    </Box>);

export default ControllerPanel;
