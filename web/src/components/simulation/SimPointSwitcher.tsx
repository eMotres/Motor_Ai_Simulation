/**
 * SimPointSwitcher — the computed POINTS (duties) of the loaded motor.
 *
 * The Motors catalog lists motors only and loads each configuration's default
 * duty (`rated`).  Everything else a configuration has been computed at — its
 * other operating points — appears HERE, the moment the motor is loaded, as one
 * row of chips in the Electromagnetic Simulation tab (owner 2026-11-04: «как в
 * конфигуре подгружаешь один мотор и появляется переключатель различных точек
 * моделирования»; «все duty переходят внутрь моделирования»).
 *
 *   • A click loads that point through the SAME load as the header strip's mode
 *     list and the catalog's configuration buttons (lib/dutyApply
 *     applyDutyEverywhere): its operating point, materials, stored runs and
 *     charts come back with nothing solved, and it works after F5 because the
 *     list is read from the catalog, not from memory.
 *   • The Sine / PWM chips of StoredRunSelector then belong to the chosen
 *     point, and the excitation that was on screen stays when the new point has
 *     it stored.
 *   • Editors also create, rename, duplicate and delete points here, make one
 *     the catalog's default, save the last run into it, take the report's
 *     pictures from it and open the configuration's history — the controls
 *     that used to sit in the catalog's duty table.  Nothing stored changes by
 *     the move; these are the same requests.
 *
 * Hidden when the motor has fewer than two points (and the viewer cannot edit).
 */
import React, { useEffect, useRef, useState } from 'react';
import { Box, Tooltip, CircularProgress, Menu, MenuItem, Divider } from '@mui/material';
import { activeDuty, dutyCycleChip, dutyKey } from '../../lib/dutySettings';
import { gatedDutyCycleChip } from '../../lib/dutyCycleFlag';
import { applyDutyEverywhere } from '../../lib/dutyApply';
import { fetchFamilyTree } from '../../lib/familyTree';
import { canWriteServer } from '../../lib/localAuth';
import { uiCanWrite } from '../../lib/accessUi';
import { getSavers } from '../../lib/loadGuard';
import { dutyRuns, driveLabel, pickedRun, showStoredRun } from '../../lib/dutyRuns';
import {
  configOfTree, driveAfterSwitch, pointsOf, switcherLayout, type Point, type TreeCfg,
} from '../../lib/simPoints';
import {
  createDuty, deleteDuty, duplicateDuty, renameDuty, reportPicturesKey, setDefaultDuty,
} from '../../lib/dutyOps';
import {
  ConfirmDialog, TextPromptDialog, type ConfirmState, type TextPromptState,
} from '../common/PromptDialogs';
import ConfigHistoryDialog from '../catalog/ConfigHistoryDialog';
import { PointChips } from './SimChips';

/** The duty-cycle chip ("S3 ED 25 % · 60 s") — nothing at all while the feature is off. */
const cycleChip = (d: { duty_cycle?: Record<string, unknown> | null }): string | null =>
  gatedDutyCycleChip(dutyCycleChip(d.duty_cycle));

const readOpMode = (): 'motor' | 'generator' => {
  try { return JSON.parse(localStorage.getItem('sim.opMode') || '"motor"') === 'generator' ? 'generator' : 'motor'; }
  catch { return 'motor'; }
};

const SimPointSwitcher: React.FC = () => {
  const [duty, setDuty] = useState(() => activeDuty());
  const [cfg, setCfg] = useState<TreeCfg | null>(null);
  const [canWrite, setCanWrite] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const [askText, setAskText] = useState<TextPromptState | null>(null);
  const [askConfirm, setAskConfirm] = useState<ConfirmState | null>(null);
  const [hist, setHist] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const read = async (fresh: boolean) => {
    const a = activeDuty();
    setDuty(a);
    if (!a) { setCfg(null); return; }
    try {
      const t = await fetchFamilyTree({ fresh });
      if (!alive.current) return;
      setCanWrite(uiCanWrite(t.can_write, canWriteServer()));
      setCfg(configOfTree(t.dies as never, a.die, a.config));
    } catch { /* keep the last list; the chips stay usable */ }
  };
  useEffect(() => {
    const first = window.setTimeout(() => { void read(false); }, 0);
    // The tree moves on every catalog mutation and every save (family-changed); the
    // duty itself moves on every load (sim-settings-restored fires last there).
    const fresh = () => { void read(true); };
    const soft = () => { void read(false); };
    window.addEventListener('family-changed', fresh);
    window.addEventListener('sim-settings-restored', soft);
    return () => {
      window.clearTimeout(first);
      window.removeEventListener('family-changed', fresh);
      window.removeEventListener('sim-settings-restored', soft);
    };
  }, []);

  const points: Point[] = pointsOf(cfg, canWrite, driveLabel, cycleChip);
  const layout = switcherLayout(points, canWrite, duty?.duty ?? null);
  if (!duty || !cfg || (!layout.chips && !layout.controls)) return null;
  const here = points.find((p) => p.name === duty.duty) ?? null;

  /** Load a point, then keep the excitation that was on screen when the new point has it. */
  const load = async (name: string) => {
    if (busy) return;
    setBusy(name); setMsg(null);
    try {
      const prev = pickedRun(dutyKey(duty.die, duty.config, duty.duty));
      const done = await applyDutyEverywhere(duty.die, duty.config, name, canWrite);
      if (!done.declined) {
        const target = points.find((p) => p.name === name);
        const want = target ? driveAfterSwitch(prev, target) : null;
        const k = dutyKey(duty.die, duty.config, name);
        if (want && dutyRuns(k).some((r) => r.drive === want)) {
          await showStoredRun({ die: duty.die, config: duty.config, duty: name }, want);
        }
        window.dispatchEvent(new CustomEvent('family-changed'));
      }
    } catch (e: unknown) {
      setMsg(String((e as Error)?.message ?? e));
    }
    setBusy(null);
  };

  /** A catalog mutation: run it, say what the backend said, refresh the lists. */
  const mutate = async (label: string, fn: () => Promise<Response>, then?: () => Promise<void>) => {
    setBusy(label); setMsg(null);
    try {
      const r = await fn();
      if (!r.ok) {
        let why = `HTTP ${r.status}`;
        try { why = (await r.json()).detail ?? why; } catch { /* keep */ }
        setMsg(why);
      } else {
        window.dispatchEvent(new CustomEvent('family-changed'));
        if (then) await then();
      }
    } catch (e: unknown) { setMsg(String((e as Error)?.message ?? e)); }
    setBusy(null);
  };

  const closeMenu = () => setMenu(null);
  const d = duty.duty;
  const newPoint = () => setAskText({
    title: `New point under ${duty.die} / ${duty.config}`,
    label: 'Point name',
    hint: 'Captures the CURRENT Simulation point — current, rpm, γ, mode',
    onSubmit: (name) => { void mutate(`create ${name}`,
      () => createDuty(duty.die, duty.config, name, readOpMode())); },
  });
  const rename = () => setAskText({
    title: `Rename point '${d}'`, label: 'New name', initial: d,
    hint: 'Operating point, targets, the recorded result, the stored runs, the solved maps and every thermal / mechanical / coupled answer follow the new name',
    onSubmit: (name) => { if (name === d) return;
      void mutate(`rename ${d}`, () => renameDuty(duty.die, duty.config, d, name),
        async () => { setBusy(null); await load(name); }); },
  });
  const duplicate = () => setAskText({
    title: `Duplicate point '${d}'`, label: 'Name of the copy', initial: `${d} copy`,
    hint: 'Full copy: operating point, targets, note AND the recorded result',
    onSubmit: (name) => { void mutate(`duplicate ${d}`, () => duplicateDuty(duty.die, duty.config, d, name)); },
  });
  const remove = () => setAskConfirm({
    title: `Delete point '${d}' from ${duty.die} / ${duty.config}?`,
    body: 'Its saved operating point and recorded results go with it.',
    onConfirm: () => { void mutate(`delete ${d}`, () => deleteDuty(duty.die, duty.config, d), async () => {
      // The loaded point is gone: open the one the catalog loads for this motor.
      const t = await fetchFamilyTree({ fresh: true });
      const rest = pointsOf(configOfTree(t.dies as never, duty.die, duty.config), canWrite, driveLabel, cycleChip);
      const next = rest.find((p) => p.isDefault) ?? rest[0];
      setBusy(null);
      if (next) await load(next.name);
    }); },
  });
  const makeDefault = () => { void mutate(`default ${d}`, () => setDefaultDuty(duty.die, duty.config, d)); };
  const saveRun = async () => {
    const s = getSavers();
    if (!s?.canSaveResults) { setMsg('nothing to save — Run first'); return; }
    setBusy('save'); setMsg(null);
    const why = await s.saveResults();
    setBusy(null);
    if (why) setMsg(why);
  };
  const picKey = reportPicturesKey(duty.die, duty.config);
  const picHere = (() => { try { return localStorage.getItem(picKey) === d; } catch { return false; } })();
  const togglePictures = () => {
    try { if (picHere) localStorage.removeItem(picKey); else localStorage.setItem(picKey, d); }
    catch { /* a per-viewer nicety only */ }
    setMsg(null); setCfg((c) => (c ? { ...c } : c));
  };

  const item = (label: string, fn: () => void, opts: { disabled?: boolean; tip?: string } = {}) => (
    <Tooltip title={opts.tip ?? ''} placement="left">
      <span>
        <MenuItem dense disabled={opts.disabled || !!busy} sx={{ fontSize: 12 }}
          onClick={() => { closeMenu(); fn(); }}>{label}</MenuItem>
      </span>
    </Tooltip>
  );

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.6, flexWrap: 'wrap' }}>
      <Tooltip title="Computed points of this motor. Click one to load it — its settings, charts and summary come back with nothing recomputed. The motor list loads the default point (★).">
        <Box component="span" sx={{ fontSize: 10, color: 'var(--text-4)', cursor: 'help' }}>points</Box>
      </Tooltip>
      {layout.chips && <PointChips points={points} loaded={d} busy={busy} onPick={(n) => { void load(n); }} />}
      {layout.controls && (
        <>
          <Tooltip title="Save the CURRENT Simulation point as a new point of this motor">
            <Box component="span" onClick={() => { if (!busy) newPoint(); }}
              sx={{ fontSize: 12, px: 0.6, cursor: busy ? 'wait' : 'pointer', color: 'var(--text-3)' }}>＋</Box>
          </Tooltip>
          <Tooltip title={`Point '${d}': default, rename, duplicate, delete, save run, report pictures, history`}>
            <Box component="span" role="button" aria-label="point actions"
              onClick={(e) => { if (!busy) setMenu(e.currentTarget as HTMLElement); }}
              sx={{ fontSize: 13, px: 0.6, cursor: busy ? 'wait' : 'pointer', color: 'var(--text-3)' }}>⋮</Box>
          </Tooltip>
        </>
      )}
      {busy && !points.some((p) => p.name === busy) && <CircularProgress size={10} />}
      {msg && <Box component="span" sx={{ fontSize: 10, color: '#fca5a5' }}>✗ {msg}</Box>}

      <Menu open={!!menu} anchorEl={menu} onClose={closeMenu}>
        {item('Save the last run into this point', () => { void saveRun(); },
          { tip: 'Records the finished run and the panel’s point into this point (the header’s Save to duty)' })}
        {item(here?.isDefault ? 'Default point ★' : 'Make default point (★)', makeDefault,
          { disabled: !!here?.isDefault, tip: 'The motor list shows and loads this point' })}
        {item('Rename…', rename)}
        {item('Duplicate…', duplicate)}
        {item('Delete…', remove)}
        <Divider />
        {item(picHere ? 'Report pictures: from this point ✓' : 'Report pictures from this point', togglePictures,
          { tip: 'Which point’s maps the configuration’s report draws (otherwise the rated point, then the loaded one)' })}
        {item('History of the configuration…', () => setHist(true),
          { tip: 'Every saved version of this configuration — pick one to roll back' })}
      </Menu>
      <TextPromptDialog state={askText} onClose={() => setAskText(null)} />
      <ConfirmDialog state={askConfirm} onClose={() => setAskConfirm(null)} />
      {hist && (
        <ConfigHistoryDialog open onClose={() => setHist(false)} die={duty.die} config={duty.config}
          canWrite={canWrite}
          onRestored={() => { window.dispatchEvent(new CustomEvent('family-changed')); }} />
      )}
    </Box>
  );
};

export default SimPointSwitcher;
