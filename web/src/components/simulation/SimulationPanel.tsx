/**
 * Simulation tab — 2D Magnetostatics PINN (NVIDIA Modulus)
 *
 * Layout:
 *   Left  — operating-point controls + run button
 *   Right — status / results / log
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { healPanelSettingsFromActiveDuty, panelSettingsMissing } from '../../lib/dutySnapshot';
import { whenVisible } from '../../lib/pageVisible';
import {
  Box, Typography, TextField, Button, Chip, Divider,
  LinearProgress, Alert, Tooltip, IconButton, Paper,
  CircularProgress, Dialog, DialogTitle, DialogContent, DialogActions,
  Checkbox, FormControlLabel, FormControl, InputLabel, Select, MenuItem,
  InputAdornment, ListSubheader, Switch,
} from '@mui/material';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import { useMotorStore } from '../../stores/motorStore';
import { geoSignature } from '../common/geoSig';
import { windingConnections } from '../../lib/referencePassports';
import { currentGeoJson, currentMatJson } from '../../lib/apiAuth';
import PlayArrowIcon    from '@mui/icons-material/PlayArrow';
import StopIcon         from '@mui/icons-material/Stop';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import CheckCircleIcon  from '@mui/icons-material/CheckCircle';
import ErrorIcon        from '@mui/icons-material/Error';
import BoltIcon         from '@mui/icons-material/Bolt';
import PhysicsDashboard from './PhysicsDashboard';
import SolveProgressStrip from './SolveProgressStrip';
// The SAME bar, pointed at the orchestrator's own counter: the transient's strip
// says which FRAME is solving, this one says which ITERATION of how many.  Both
// render nothing when their endpoint reports no solve, so mounting the second
// costs an idle page one cheap poll.
import CommonProgressStrip from '../common/SolveProgressStrip';
import HelpTip from '../common/HelpTip';
import { fetchCoupledLast } from './coupledApi';
import { syncActiveMotor, getActiveMotor } from '../common/motorSettings';
import BatteryDialog, { type BatteryValue } from '../catalog/BatteryDialog';
import {
  packFromCells, machineKey, readLocalBattery, writeLocalBattery,
  readLocalContext, batteryChipLabel, batteryPayload,
  type BatteryPack, type CellSpec,
} from '../../lib/machineBattery';
import {
  activeDuty, dutyKey, noteDutyOpEdit, isDutyOpKey,
  activeDutyMaterials, setDutyMagnet,
} from '../../lib/dutySettings';
import { MACHINE_CHANGED_EVENT } from '../../lib/dutyLocalApply';
import { setSolveBusy } from '../../lib/familyFollow';
import { magnetVariants } from '../../lib/magnetVariants';
import { effectiveAssignment } from '../../lib/dutyMaterials';
import { runNoticeFor, type RunNotice } from '../../lib/runNotice';
import { useMotorAssignments } from '../materials/useMotorAssignments';
import { useMaterialsLibrary } from '../materials/useMaterialsLibrary';

// NOTE: using port 8001 (new backend with loss calculations)
// Change back to 8000 after restarting the main backend
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

// ── what the FIELD views are making the server do ─────────────────────────
// Field solves (J⟳ / Loss / Temp / Demag, the field inspector) run on the same
// machine as the transient, but the progress strip only tracks the transient —
// so on 2026-09-03 the API sat at ~17 cores of field solves with the panel
// showing nothing running at all.  The backend now publishes them, and rides
// them along on the transient-progress response so the panel gets them without
// a second poll.
interface FieldBusyItem {
  key_short: string;
  kind:      string;
  state?:    string;      // 'solving' | 'queued'
  since_s:   number;
  waiters?:  number;
}
interface FieldBusy {
  solving: number;
  queued:  number;
  limit?:  number;
  items:   FieldBusyItem[];
}

// ── excitation sources ────────────────────────────────────────────────────
// 'voltage' (the ideal sinusoidal voltage drive) stays in the type but not on
// the menu — see the mount effect in the panel.
type DriveKind = 'current' | 'voltage' | 'pwm_voltage' | 'custom_current'
               | 'bldc_current';

// Switching frequencies that real controllers actually offer, grouped by the
// power stage they belong to.  A free-text kHz box invites numbers no inverter
// runs at; these are the settings an engineer would find in the drive's own
// menu.  'custom' reveals the numeric field for everything else — the backend
// takes any frequency.
const FSW_GROUPS: { label: string; hint: string; values: number[]; def: number }[] = [
  { label: 'SiC MOSFET · 400–800 V',
    hint: 'EV traction / industrial SiC stage — the CILN28 class (640–860 V pack)',
    values: [16000, 24000, 32000, 48000], def: 24000 },
  { label: 'IGBT · 400–800 V',
    hint: 'classic traction inverter — switching loss keeps the carrier low',
    values: [4000, 8000, 12000, 16000], def: 8000 },
  { label: 'LV MOSFET ESC · < 100 V',
    hint: 'drone / hobby controllers (BLHeli_32, AM32) and LV FOC stages — the 40 mm class',
    values: [24000, 48000, 64000, 96000], def: 48000 },
];
const FSW_ALL = Array.from(new Set(FSW_GROUPS.flatMap(g => g.values)));
const fswLabel = (hz: number) =>
  (hz >= 1000 ? `${+(hz / 1000).toFixed(hz % 1000 ? 1 : 0)} kHz` : `${hz} Hz`);

// The V_bus value WE last prefilled from the machine's pack.  Kept outside the
// sim.* block on purpose: sim.* is snapshotted into duty saves and the per-die
// settings memory, and this is bookkeeping about the panel, not a setting of
// the machine.  It is what tells a battery change whether the field still
// holds our prefill (safe to refresh) or a number the user typed (never
// touched — an explicit DC link is an explicit answer).
const BUS_SEED_KEY = 'battery.busSeed';
const readBusSeed = (): number | null => {
  try {
    const raw = JSON.parse(localStorage.getItem(BUS_SEED_KEY) || 'null');
    // Number(null) === 0 — the absent-key case must stay null, not become a
    // phantom «0 V seed» that fails every prefill match (measured live:
    // V_bus stuck on the previous machine's 750 because of exactly this).
    if (raw == null) return null;
    const v = Number(raw);
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch { return null; }
};
const writeBusSeed = (v: number): void => {
  try { localStorage.setItem(BUS_SEED_KEY, JSON.stringify(v)); } catch { /* quota */ }
};
/** True while V_bus is empty, still equal to the prefill we wrote, or a
 *  value with NO seed on record — that last case is a bus that travelled in
 *  with a duty-settings restore from before vBus was machine-scoped (measured
 *  live 2026-08-31: 750 V from the CILN28 duty sitting over a 6S 22 V pack).
 *  A hand-typed bus always has a seed mismatch WITH a seed present, and only
 *  that combination is protected. */
const busIsPrefill = (vBus: number): boolean => {
  if (!(vBus > 0)) return true;
  const s = readBusSeed();
  return s == null || Math.abs(vBus - s) <= 0.05;
};

// ── types ─────────────────────────────────────────────────────────────────────
interface SimStatus {
  modulus_available: boolean;
  operating_point: {
    max_current: number;
    frequency_hz: number;
    rpm: number;
    Br_magnet_T: number;
  };
  solver: string;
}

interface JobStatus {
  job_id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  progress: number;
  result?: {
    torque_Nm: number;
    B_max_T: number;
    B_mean_T: number;
    training_steps: number;
    output_dir: string;
    status?: string;
    modulus_available?: boolean;
    // Copper losses (always available)
    P_cu_total_W?: number;
    R_phase_ohm?: number;
    R_coil_ohm?: number;
    L_turn_mm?: number;
    I_coil_rms_A?: number;
    // Iron / magnet losses (require PINN)
    P_fe_stator_W?: number | null;
    P_fe_rotor_W?: number | null;
    P_mag_eddy_W?: number | null;
    // Power & efficiency
    P_mech_W?: number | null;
    P_input_W?: number | null;
    P_loss_total_W?: number;
    efficiency_pct?: number | null;
    note?: string;
  };
  error?: string;
  elapsed_s?: number;
}

// ── small helpers ─────────────────────────────────────────────────────────────
const Row: React.FC<{ label: string; value: string; unit?: string; highlight?: boolean }> = ({
  label, value, unit, highlight,
}) => (
  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', py: 0.4 }}>
    <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{label}</Typography>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <Typography sx={{ fontSize: 12, fontWeight: 600, color: highlight ? '#4ade80' : 'var(--text-0)' }}>
        {value}
      </Typography>
      {unit && <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{unit}</Typography>}
    </Box>
  </Box>
);

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────
// ── helpers ───────────────────────────────────────────────────────────────────
function gcd(a: number, b: number): number { return b === 0 ? a : gcd(b, a % b); }
function lcm(a: number, b: number): number { return (a * b) / gcd(a, b); }

// Winding connection options come from the backend (/api/winding/config), which
// derives them from the slot count: coils/phase C = slots/6, every factor pair
// (nS series × nP parallel = C) is an option.  See api._winding_connections.
interface WindConn { label: string; n_parallel: number; n_series: number; }

const SimulationPanel: React.FC<{ active?: boolean }> = ({ active = false }) => {
  // localStorage-backed state so the whole left column survives reloads.
  const usePersisted = <T,>(key: string, def: T) => {
    const [v, setV] = useState<T>(() => {
      try {
        const raw = localStorage.getItem(`sim.${key}`);
        return raw == null ? def : (JSON.parse(raw) as T);
      } catch { return def; }
    });
    useEffect(() => {
      try { localStorage.setItem(`sim.${key}`, JSON.stringify(v)); } catch {}
      // ── PER-DUTY memory (user 2026-09-01: "токи и температуры должны быть
      //    разные для каждого duty") ──────────────────────────────────────
      // sim.* is ONE global block, so every duty of a machine used to share
      // the same current / rpm / γ / coil temp / drive: editing S2 at 200 °C
      // left S1 sitting at 200 °C for ever.  An operating-point field written
      // while a duty is selected is ALSO filed under that duty, so switching
      // back brings its own numbers with it.
      //
      // Only the LOCAL overlay is touched — the stored duty snapshot changes
      // exclusively on an explicit "Save to duty" (no-silent-state rule).
      // With no duty selected this is a no-op and nothing changes.
      try {
        if (isDutyOpKey(`sim.${key}`)) {
          const a = activeDuty();
          noteDutyOpEdit(dutyKey(a?.die, a?.config, a?.duty), `sim.${key}`, v);
        }
      } catch { /* memory is a convenience, never a blocker */ }
    }, [key, v]);
    // Loading a duty restores the SAVED panel settings into localStorage and
    // fires this event — every persisted field re-reads its key so the UI
    // shows the restored value without a remount (the panel is keepMounted;
    // measured live: steps stayed 24 on screen after a 40-step duty load).
    useEffect(() => {
      // runNonce is the RUN GATE, never a restorable field: re-reading it here
      // turned a duty load into an automatic solve (2026-08-25).  Third guard
      // on the same hole — the save and the restore paths skip it too.
      if (key === 'runNonce') return;
      const onRestore = () => {
        try {
          const raw = localStorage.getItem(`sim.${key}`);
          if (raw != null) setV(JSON.parse(raw) as T);
        } catch { /* keep current */ }
      };
      window.addEventListener('sim-settings-restored', onRestore);
      return () => window.removeEventListener('sim-settings-restored', onRestore);
    }, [key]);
    // ANOTHER WINDOW of this app wrote the key (an optimizer Apply pins the
    // run's eval params — 48 steps — into sim.stepsPP; a duty or stored-run
    // load rewrites the block).  Those writers fire their re-read events in
    // THEIR window only, so this panel kept showing its own value while every
    // direct reader of localStorage — the Sweep, the Thermal tab — solved with
    // the other one (2026-09-07: the field said 36, the sweep ran 48 steps,
    // cold).  The browser's cross-window `storage` event closes that gap.
    useEffect(() => {
      if (key === 'runNonce') return;
      const onStorage = (e: StorageEvent) => {
        if (e.key !== `sim.${key}` || e.newValue == null) return;
        try { setV(JSON.parse(e.newValue) as T); } catch { /* keep current */ }
      };
      window.addEventListener('storage', onStorage);
      return () => window.removeEventListener('storage', onStorage);
    }, [key]);
    return [v, setV] as const;
  };

  // ── server status ─────────────────────────────────────────────────────────
  const [srvStatus, setSrvStatus] = useState<SimStatus | null>(null);
  const [srvErr, setSrvErr]       = useState<string | null>(null);

  // ── geometry (for period + winding calc) ─────────────────────────────────
  const [numPoles,      setNumPoles]      = useState<number>(28);
  const [numSlots,      setNumSlots]      = useState<number>(24);
  const [nWiresPerSlot, setNWiresPerSlot] = useState<number>(14);
  const [nCoilsPerPhase, setNCoilsPerPhase] = useState<number>(4);

  // ── winding connection ────────────────────────────────────────────────────
  const [connection, setConnection] = usePersisted<string>('connection', '4S');
  // TERMINAL connection of the three phases — independent of `connection`,
  // which groups a phase's own coils.  Star is the default because it is what
  // every number this solver produced before the choice existed meant.
  const [starDelta, setStarDelta] = usePersisted<string>('starDelta', 'star');

  // ── winding LAYOUT (per-slot phase + sign = coil currents) ─────────────────
  const [windCfg, setWindCfg]       = useState<any>(null);     // /api/winding/config
  const [windErr, setWindErr]       = useState<string | null>(null);  // refused winding change
  // connection options + the selected connection's parallel-path count come from
  // the backend (slot-derived). I_coil = I_phase / n_parallel.
  // Prefer the backend's slot-derived options; fall back to deriving them locally
  // from the slot count so the UI still works against an older backend that
  // doesn't yet return `connections`.
  const windConns: WindConn[] = windCfg?.connections
    ?? windingConnections(windCfg?.num_slots ?? 0).map((c) => ({ label: c.label, n_parallel: c.nP, n_series: c.nS }));
  const connDef = windConns.find((c) => c.label === connection);
  const nParallel = connDef?.n_parallel ?? windCfg?.n_parallel ?? 1;

  const loadWinding = useCallback(() => {
    fetch(`${API}/api/winding/config`)
      .then(r => r.json())
      .then(d => {
        setWindCfg(d);
        // config.yaml is the persistent, cross-browser source for the connection;
        // adopt it on load so a fresh browser / other tab reflects the real value.
        if (d.connection) setConnection(d.connection);
      })
      .catch(() => {});
  }, []);
  useEffect(() => { loadWinding(); }, [loadWinding]);

  // D-AXIS PIN: adopt the backend's value into an EMPTY field on load, and
  // write it ONLY from an explicit edit (see the field's onChange).  It used to
  // ride the ambient auto-sync like every other setting, and that erased it:
  // the sync fires on mount, an idle tab's field is empty, empty meant "clear
  // the pin" — so simply OPENING the app in a second browser un-pinned the
  // d-axis the user had just set (observed twice, config daxis_deg -> null
  // with nobody touching the panel).  A pin that any bystander tab can silently
  // remove is not a pin.
  useEffect(() => {
    fetch(`${API}/api/config`).then(r => r.json()).then(d => {
      const v = d?.simulation?.daxis_deg;
      if (v != null && String(v).trim() !== '') {
        setDaxisDeg(prev => (prev.trim() === '' ? String(v) : prev));
      }
    }).catch(() => {});
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps


  // A REFUSED winding change used to be swallowed here (`.catch(() => {})`):
  // the button lit up, the backend kept the old connection, and every number
  // afterwards belonged to a winding the panel was no longer showing — "I
  // change the connection and nothing happens", with nothing on screen to say
  // why.  The backend refuses for real reasons (a label that is not valid for
  // this slot count, a config whose winding block lacks the key), so the reason
  // is shown and the selection snaps back to what the backend actually holds.
  const applyWinding = useCallback((patch: Record<string, any>) => {
    setWindErr(null);
    fetch(`${API}/api/winding/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
      .then(async r => {
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `HTTP ${r.status}`); }
      })
      .then(() => loadWinding())
      .catch((e) => {
        setWindErr(String(e?.message || e));
        loadWinding();          // re-adopt the connection the backend really has
      });
  }, [loadWinding]);

  // Phase → colour for the slot map (A=red, B=green, C=blue); +full, −faded.
  // self-heal: if the persisted connection isn't valid for THIS motor's slot count
  // (e.g. a 4-coil connection inherited on a 12-slot motor), switch to the first valid one.
  //
  // ONLY against the BACKEND's option list (windCfg loaded).  It used to run on
  // the local fallback too, and on first mount that fallback is derived from
  // num_slots = 0 — a one-entry list ('1S') that no real connection is in.  So
  // every page load "healed" the user's 4P to 1S: the PATCH was refused (1S is
  // invalid for 24 slots, and the refusal was swallowed), but the panel's own
  // connection had already become 1S, and a Run pressed in that window solved
  // ONE parallel path while the buttons — re-adopted from the backend a moment
  // later — showed 4P.  That is the "I change the connection and nothing
  // changes" report: 4x the coil MMF, under the wrong name.
  useEffect(() => {
    if (!windCfg || !Array.isArray(windCfg.connections) || !windCfg.connections.length) return;
    if (!windConns.some((c) => c.label === connection)) {
      setConnection(windConns[0].label);
      applyWinding({ connection: windConns[0].label });
    }
  }, [windCfg]); // eslint-disable-line react-hooks/exhaustive-deps

  const PHASE_COLOR: Record<string, string> = { A: '#ef4444', B: '#22c55e', C: '#3b82f6' };

  // ── derived periodicity ───────────────────────────────────────────────────
  const polePairs         = Math.round(numPoles / 2);
  const elecPeriod_deg    = 360 / polePairs;
  const coggingPeriod_deg = 360 / lcm(numSlots, numPoles);

  // ── form state (current = I_phase_rms) ───────────────────────────────────
  const [current,       setCurrent]       = usePersisted('current',   85.0);
  const [frequency,     setFrequency]     = usePersisted('frequency', 921.67);
  const [rpm,           setRpm]           = usePersisted('rpm',       3950.0);
  const [phaseOffset,   setPhaseOffset]   = usePersisted('gamma',     0.0);   // γ [deg]
  // Frequency is DERIVED (f = rpm·pp/60) — recompute whenever rpm OR the pole
  // count changes.  It used to be seeded once from the config's saved operating
  // point and only refreshed on manual rpm edits, so a pole-count change left
  // the stale value (e.g. 921.67 Hz shown for a 20-pole motor instead of 658.33).
  useEffect(() => {
    const f = +(rpm * Math.round(numPoles / 2) / 60).toFixed(2);
    if (Number.isFinite(f) && f > 0 && Math.abs(f - frequency) > 0.01) setFrequency(f);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpm, numPoles]);
  // Drive mode for the transient: imposed sinusoidal CURRENT (design work) or
  // imposed sinusoidal VOLTAGE (FOC-drive verification — the currents become
  // the machine's own response, incl. the parasitic harmonics a distorted
  // back-EMF forces through the winding + their real extra losses).
  const [drive,   setDrive]   = usePersisted<DriveKind>('drive', 'current');
  // Target mode: the duty is given as torque (Nm) or power (kW) and the
  // CURRENT is fitted by cheap FEM probes + secant before the real run.
  // Solver-side this is still current drive — the backend never sees it.
  const [targetKind,  setTargetKind]  = usePersisted<'off' | 'nm' | 'kw'>('targetKind', 'off');
  // The SINUSOIDAL voltage drive is still hidden from the menu (user request) —
  // a persisted 'voltage' selection would strand the panel in an invisible
  // mode, so it is rewritten on mount.  The PWM / BLDC / custom-current sources
  // ARE on the menu, so they are left alone.
  useEffect(() => {
    if (drive === 'voltage') setDrive('current');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [targetValue, setTargetValue] = usePersisted<number>('targetValue', 850);
  const [fitBusy, setFitBusy] = useState(false);
  const [fitMsg,  setFitMsg]  = useState<string | null>(null);
  // Why the last Run did not produce a solve, shown UNDER THE RUN BUTTON.
  // TransientCharts owns the message but renders it beside the waveforms, far
  // below the fold; a refusal there is invisible from the rail the user just
  // clicked.  Live on production 2026-09-16: "Coupled thermal" on + an S3 duty
  // → `POST /api/coupled/run` 422 with a sentence naming the reason, and on
  // screen the button simply flicked back to "Re-run Simulation".  The event is
  // published on every change of that state, so a new run (which clears it)
  // clears this line too — no separate reset path.
  const [runNotice, setRunNotice] = useState<RunNotice | null>(null);
  useEffect(() => {
    const onNotice = (e: Event) =>
      setRunNotice(runNoticeFor((e as CustomEvent<{ message?: string | null }>).detail?.message));
    window.addEventListener('sim:run-notice', onNotice);
    return () => window.removeEventListener('sim:run-notice', onNotice);
  }, []);
  // Operating mode.  Generator = the SAME gamma, current shifted 180 deg el —
  // torque brakes, mechanical power flows in, the card's efficiency flips to
  // P_electrical_out / P_mechanical_in (the backend decides by power-flow sign).
  const [opMode,  setOpMode]  = usePersisted<'motor' | 'generator'>('opMode', 'motor');
  const [vPeak,   setVPeak]   = usePersisted('vPeak',  30.0);  // phase-voltage amplitude [V]
  const [vDelta,  setVDelta]  = usePersisted('vDelta',  0.0);  // voltage angle δ [°el], same frame as γ
  // ── PWM inverter source ────────────────────────────────────────────────
  // All persisted under sim.* like every other operating-point field, so they
  // ride along into duty saves and the per-die settings memory for free.
  const [vBus,    setVBus]    = usePersisted('vBus', 0);        // DC link [V]; 0 = not set yet
  const [fSwitch, setFSwitch] = usePersisted('fSwitch', 24000); // carrier [Hz]
  // Which controller class the carrier was picked from (index into FSW_GROUPS).
  // Only a UI grouping — the backend takes the frequency — but it has to be
  // remembered because the same kHz belongs to more than one class.
  const [fSwGroup, setFSwGroup] = usePersisted('fSwGroup', 0);
  const [fSwCustom, setFSwCustom] = usePersisted('fSwCustom', false);
  // ── GENERATOR → BATTERY (boost mode) ───────────────────────────────────
  // Iterate the bus against the pack instead of assuming an infinitely stiff
  // supply.  Persisted ON, but only ever SENT on a generator run of a machine
  // that has a battery (see busCoupleEff below) — a motoring run and a
  // battery-less machine keep the request they have always sent.
  const [busCouple, setBusCouple] = usePersisted('busCouple', true);
  // One-shot: the next launch is a max-charge SEARCH (a dozen solves), cleared
  // the moment that solve finishes so it can never become a sticky mode.
  const [chargeMaxOnce, setChargeMaxOnce] = useState(false);
  // ── BLDC block source ──────────────────────────────────────────────────
  // FLAT-TOP amplitude, not an rms — see the tooltip on the field.
  const [iBlock,  setIBlock]  = usePersisted('iBlock', 0);      // [A]; 0 = seed from I rms
  // ── imposed arbitrary current ──────────────────────────────────────────
  const [waveform, setWaveform] = usePersisted<string>('waveform', '');
  const [wfBusy,   setWfBusy]   = useState(false);
  const [wfMsg,    setWfMsg]    = useState<string | null>(null);
  // ── The MACHINE's battery ──────────────────────────────────────────────
  // Shown as a chip next to the drive controls and edited in the SAME dialog
  // the Family catalog uses.  Where it is SAVED depends on whose machine this
  // is: a writer with an active family context patches the configuration yaml
  // (one write path, the catalog's own endpoint), so the catalog, Configure,
  // the datasheet and this panel's V_bus all move together; a client's ▶-copy
  // or a legacy preset remembers it per MACHINE in this browser.
  const [battery, setBattery] = useState<BatteryPack | null>(null);
  // Which machine the chip is editing: die/config when there is one, plus
  // whether this user may write the family yaml (server's answer, never a
  // client-side guess — the PATCH is not even attempted without it).
  const [batCtx, setBatCtx] = useState<{ die?: string; config?: string;
                                         canWrite: boolean }>({ canWrite: false });
  const [batOpen, setBatOpen] = useState(false);
  const [batMsg, setBatMsg]   = useState<string | null>(null);

  // A failed context fetch must RETRY: the panel mounts during backend
  // restarts (measured live: the chip stuck on "no battery" for a machine
  // whose yaml carries one, because the one mount fetch hit a dying API and
  // nothing ever re-asked).  Same medicine as the catalog's tree loader.
  const batRetry = React.useRef<number | null>(null);
  const loadBattery = useCallback(async () => {
    let ctx: any = null;
    let netFail = false;
    try { ctx = await fetch(`${API}/api/family/context`).then(r => r.json()); }
    catch { ctx = null; netFail = true; }
    if (batRetry.current != null) { window.clearTimeout(batRetry.current); batRetry.current = null; }
    if (netFail) {
      batRetry.current = window.setTimeout(() => { void loadBattery(); }, 3000);
    }
    // Owner / admin with a machine loaded from the catalog — the yaml IS the
    // battery, straight from the context the strip and Configure read.
    if (ctx?.active && ctx?.can_write === true) {
      setBatCtx({ die: ctx.die, config: ctx.config, canWrite: true });
      setBattery((ctx.battery ?? null) as BatteryPack | null);
      return;
    }
    // No write access: the machine on screen is THIS CLIENT's copy, named by
    // local context (the same record ActiveFamilyStrip trusts).
    const loc = readLocalContext();
    setBatCtx({ die: loc?.die, config: loc?.config, canWrite: false });
    const local = readLocalBattery(machineKey(loc, getActiveMotor()?.id ?? null));
    if (local) { setBattery(local); return; }
    // Never edited here → the machine's own catalog entry.  The server context
    // belongs to the OWNER, so it only counts when it names the same machine.
    if (loc) {
      if (ctx?.active && ctx.die === loc.die && ctx.config === loc.config) {
        setBattery((ctx.battery ?? null) as BatteryPack | null);
        return;
      }
      try {
        const t = await fetch(`${API}/api/family/tree`).then(r => r.json());
        const d = (t?.dies || []).find((x: any) => x?.name === loc.die);
        const c = (d?.configs || []).find((x: any) => x?.name === loc.config);
        setBattery((c?.battery ?? null) as BatteryPack | null);
      } catch { setBattery(null); }
      return;
    }
    setBattery((ctx?.battery ?? null) as BatteryPack | null);
  }, []);

  useEffect(() => {
    void loadBattery();
    const on = () => { void loadBattery(); };
    window.addEventListener('family-context-changed', on);
    window.addEventListener('family-changed', on);
    window.addEventListener('sim-design-applied', on);
    return () => {
      window.removeEventListener('family-context-changed', on);
      window.removeEventListener('family-changed', on);
      window.removeEventListener('sim-design-applied', on);
      if (batRetry.current != null) window.clearTimeout(batRetry.current);
    };
  }, [loadBattery]);
  // Prefill V_bus and the switching-frequency GROUP from that supply, once, and
  // only while the user has not set a bus of their own: a typed DC link is a
  // number nobody checks against the pack that is really there, and the ripple a
  // PWM run reports scales directly with it.  The default is the NOMINAL pack
  // voltage — v_min and v_max are the corners a duty is judged against, not the
  // voltage the machine runs at.  ≥300 V reads as a traction stage (SiC
  // default), <100 V as an ESC; no battery leaves the field empty and the SiC
  // 24 kHz default standing.
  const busSeeded = useRef(false);
  // Write the pack's nominal into V_bus (and pick the controller class from
  // it).  `force` is the after-a-save path: the battery CHANGED, so a field
  // still holding the old prefill must follow it — but a hand-typed bus is
  // never overwritten, which is what busIsPrefill() checks.
  const seedBusFromBattery = useCallback((b: BatteryPack | null, force: boolean) => {
    const vn = Number(b?.v_nom ?? 0);
    if (!(vn > 0)) return;
    if (!force && (busSeeded.current || vBus > 0)) return;
    if (force && !busIsPrefill(vBus)) return;
    const v = +vn.toFixed(1);
    busSeeded.current = true;
    setVBus(v);
    writeBusSeed(v);
    const gi = v >= 300 ? 0 : v < 100 ? 2 : 1;    // SiC | LV ESC | IGBT
    setFSwGroup(gi);
    setFSwitch(FSW_GROUPS[gi].def);
  }, [vBus]);
  useEffect(() => {
    // Always the prefill-aware path: the battery under the panel CHANGES when
    // the user loads another machine (CILN28's 750 V pack -> the L12's 6S
    // 22 V), and the old `if (vBus > 0) bail` kept the previous machine's bus
    // in the field (measured live 2026-08-31: V_bus 750 over a 22 V battery).
    // busIsPrefill() still protects a hand-typed DC link — only a value WE
    // wrote is ever replaced.
    seedBusFromBattery(battery, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [battery]);

  // Saving the dialog = saving THE MACHINE's battery.
  const saveBattery = useCallback(async (spec: CellSpec) => {
    setBatOpen(false);
    const pack = packFromCells(spec);
    const { die, config, canWrite } = batCtx;
    if (canWrite && die && config) {
      // The catalog's endpoint, verbatim — one write path, one yaml, and the
      // pack totals come back computed by the backend rather than guessed here.
      try {
        const r = await fetch(
          `${API}/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(config)}/battery`,
          { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(spec) });
        if (!r.ok) {
          let detail = `HTTP ${r.status}`;
          try { detail = (await r.json()).detail ?? detail; } catch { /* keep */ }
          setBatMsg(`✗ ${detail}`);
          return;
        }
        const saved = ((await r.json())?.battery ?? pack) as BatteryPack;
        setBattery(saved);
        seedBusFromBattery(saved, true);
        setBatMsg(`✓ battery saved on ${die}/${config}`);
        // Everyone else reads the same yaml — tell them it moved, then re-read
        // the context so this panel shows what the file actually holds.
        try { window.dispatchEvent(new CustomEvent('family-changed')); } catch { /* SSR */ }
        void loadBattery();
      } catch (e) { setBatMsg(`✗ ${e}`); }
      return;
    }
    // No write access (or no family machine): remember it for THIS machine, in
    // this browser.  Never a PATCH — the backend would refuse it, and a refusal
    // per click is not an editor.
    const key = machineKey(readLocalContext(), getActiveMotor()?.id ?? null);
    if (!key) {
      setBatMsg('✗ no motor is loaded — open one first, then set its battery');
      return;
    }
    writeLocalBattery(key, pack);
    setBattery(pack);
    seedBusFromBattery(pack, true);
    setBatMsg('✓ battery saved for this motor (this browser)');
  }, [batCtx, loadBattery, seedBusFromBattery]);

  // The save note is one short line and it goes away by itself.
  useEffect(() => {
    if (!batMsg) return;
    const id = setTimeout(() => setBatMsg(null), 5000);
    return () => clearTimeout(id);
  }, [batMsg]);

  // γ-as-optimization-variable: a checkbox here marks the load angle γ for the
  // Sweep/Optimize grid (like the chart icon on geometry params).  When checked,
  // gamma_deg becomes a sweep variation; the user sets its min/max/step on the
  // Sweep tab card.  γ is an operating variable — it rotates the current vector,
  // not the mesh — so it never rebuilds geometry.
  const gammaIsVar    = useMotorStore(s => (s.sweepConfig.variations['gamma_deg']?.mode ?? 'fixed') !== 'fixed');
  const updateVariation = useMotorStore(s => s.updateVariation);
  const toggleGammaVar = (on: boolean) =>
    updateVariation('gamma_deg', on
      ? { mode: 'sweep', min: phaseOffset, max: phaseOffset + 30, step: 5 }
      : { mode: 'fixed' });
  const [coilTemp,      setCoilTemp]      = usePersisted('coilTemp',  120.0); // °C
  // MAGNET temperature, °C — a STRING because empty is a real answer: "the
  // magnet card exactly as the library quotes it", which is what every run this
  // app has ever made used.  A number corrects Br, the coercivity and the whole
  // demagnetisation curve to it (materials.at_temperature, phase 1).  The
  // coupled loop writes its converged value here.
  const [magnetTempC,   setMagnetTempC]   = usePersisted<string>('magnetTempC', '');
  // The EM<->thermal ORCHESTRATOR.  OFF by default and off is today, bit for
  // bit: the Run request goes where it has always gone and nothing about the
  // solve changes.  On, it goes to POST /api/coupled/run instead — see
  // ./coupledApi and TransientCharts' `run()`.
  const [coupled,       setCoupled]       = usePersisted('coupled', false);
  // WHICH QUESTION the loop is asked (owner 2026-09-18: *«надо сделать выбор —
  // или считать до конца стабилизации температуры, или считать до лимитов и
  // находить время работы при заданных условиях»*).  Per DUTY, like the
  // operating point (`lib/dutySettings` carries the key), because a continuous
  // duty is a steady state by definition and a peak is a pull with a length.
  const [solveTo,       setSolveTo]       = usePersisted<'steady' | 'limits'>(
    'coupledSolveTo', 'steady');
  // A DIFFERENT MACHINE is on the panel — loaded here, or (since 2026-09-08) in
  // another browser and followed by the header strip.  lib/dutyLocalApply says
  // so with this event, after it has reset these two fields to the incoming
  // duty's own winding temperature and cleared the magnet field; the loop's
  // last answer is then re-checked below and adopted only when the server says
  // it is NOT stale, i.e. it was solved for the machine now on screen.
  const [machineEpoch, setMachineEpoch] = useState(0);
  useEffect(() => {
    const on = () => setMachineEpoch(n => n + 1);
    window.addEventListener(MACHINE_CHANGED_EVENT, on);
    return () => window.removeEventListener(MACHINE_CHANGED_EVENT, on);
  }, []);
  // With the switch ON the two temperature fields show what the loop last
  // CONCLUDED, from the server's own memory of it — so a page reload (or a
  // duty overlay restoring the guess the loop started from) cannot leave the
  // starting guess on screen as if it were the answer (2026-09-08: the field
  // still said 130 after a run that settled at 109.7).  Through the setters,
  // so the per-duty overlay follows and the next plain Run reproduces it.
  // A STALE result is never adopted — that is how the previous machine's
  // 111 °C / 137 °C stop surviving a machine change.
  useEffect(() => {
    if (!coupled) return;
    let alive = true;
    void fetchCoupledLast().then((r) => {
      if (!alive || !r || r.stale) return;
      const c = r.coupling;
      setCoilTemp(Math.round(c.coil_temp_c * 10) / 10);
      setMagnetTempC(c.magnet_temp_c == null ? '' : String(Math.round(c.magnet_temp_c * 10) / 10));
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coupled, machineEpoch]);
  const [endWinding,    setEndWinding]    = usePersisted('endWinding', 0.0);  // k_end (editable)
  // D-AXIS REFERENCE, electrical degrees.  '' = measure it (a 24-frame
  // no-load solve, ~39 s, once per geometry); a number is used AS IS and
  // nothing is solved to find it.  Stored as a STRING so 'empty' is a
  // state the user can type — 0 is a legal angle and cannot mean 'auto'.
  const [daxisDeg,      setDaxisDeg]      = usePersisted<string>('daxisDeg', '');
  const daxisPatchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Last geometry-derived k_end we seeded the cell with — shown in the tooltip.
  const [endWindingGeo, setEndWindingGeo] = usePersisted('endWindingGeo', 0.0);
  // k_end = (π·(wire_w/2 + tooth_w/2) + L_stack)/L_stack, so it moves with every tooth/wire /
  // stack-length edit.  Re-seeding is keyed on this signature instead of running
  // once on mount, which used to leave a stale (or bare 0) factor behind in every
  // consumer that reads `sim.endWinding` (TransientCharts, motorStore, sweep).
  const geomSig = useMotorStore(s => JSON.stringify(s.geometry ?? {}));

  // ── Run-Simulation gating ──────────────────────────────────────────────
  // The FEM transient + field animation only (re)compute when runNonce
  // ticks, i.e. when the user presses "Run Simulation".  This lets them
  // change several parameters (γ, current, mesh settings) and launch ONE
  // solve instead of re-running on every keystroke.
  // Persisted so a computed simulation SURVIVES an F5: on reload runNonce is
  // restored >0, the FEM viewers refetch (a fast backend cache hit) and the
  // result reappears WITHOUT recomputing. It only changes when the user presses
  // Run for a new simulation. (Was useState(0) → every reload wiped it.)
  const [runNonce, setRunNonce] = usePersisted('runNonce', 0);

  // ── field solves in flight (see FieldBusy above) ─────────────────────────
  // Polled off the transient-progress endpoint — the one the strip already
  // polls, so this adds a field to a response, not a request.  2 s while
  // something is running, 4 s when idle; the state is only replaced when the
  // ANSWER changes (this component is expensive to re-render, and a poll that
  // repaints the whole panel every two seconds would be its own bug).
  const [fieldBusy, setFieldBusy] = useState<FieldBusy | null>(null);
  const fieldJobs = (fieldBusy?.solving ?? 0) + (fieldBusy?.queued ?? 0);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const sig = (f: FieldBusy | null) => (f
      ? `${f.solving}/${f.queued}/` + (f.items || [])
          .map(i => `${i.key_short}:${i.state}:${Math.round(i.since_s / 5)}`)
          .join(',')
      : '-');
    const tick = async () => {
      if (!alive) return;
      await whenVisible();              // a hidden tab polls nothing (lib/pageVisible)
      if (!alive) return;
      try {
        const r = await fetch(`${API}/api/simulation/physics/fem_transient/progress`);
        if (r.ok) {
          const j = await r.json();
          const next: FieldBusy | null = (j && j.field_busy) ? j.field_busy : null;
          if (alive) setFieldBusy(prev => (sig(prev) === sig(next) ? prev : next));
        }
      } catch { /* polling errors are not user-facing */ }
      if (alive) timer = window.setTimeout(tick, fieldJobs > 0 ? 2000 : 4000);
    };
    tick();
    return () => { alive = false; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldJobs > 0]);
  // What the LAST run actually solved at — shown as the placeholder, so the
  // number to pin is the one on screen instead of something to go hunting for.
  const lastDaxis = React.useMemo<number | null>(() => {
    try {
      const t = JSON.parse(localStorage.getItem('sim.lastTransient') || '{}');
      const v = Number(t?.daxis_deg);
      return Number.isFinite(v) ? v : null;
    } catch { return null; }
  }, [runNonce]);

  // Active magnet + the two numbers that decide how it behaves: Br sets the flux,
  // the BH-curve knee sets how much demagnetising field it survives.  Refreshed on
  // the same event a material change fires, so it never lags the assignment.
  const [magInfo, setMagInfo] = useState<{ name: string; Br?: number; knee?: number } | null>(null);
  // The SAME hook that feeds ?mat= to the solver (via MaterialOverrideSync) is
  // this badge's source of truth: overlay-merged, optimistic on assign, and it
  // refreshes itself on every mat-assign event.  Two earlier versions of this
  // badge were both stale by construction — reading /api/materials alone missed
  // the overlay, and reading currentMatJson() inside an event handler raced the
  // sync's own async refresh (user 2026-09-01, twice: "меняю магниты — в
  // симуляции те же самые").  Depending on the hook's state closes the race:
  // when the data actually lands, React re-runs this effect.
  const { assignments: liveAssign, refresh: refreshAssign } = useMotorAssignments();
  const machineMagnet = liveAssign?.magnet || '';
  // ── the ACTIVE DUTY's magnet TEMPERATURE ──────────────────────────────────
  // The machine's magnet is a machine property (Materials tab).  Its operating
  // TEMPERATURE is a property of the duty — a continuous duty runs the rotor at
  // 80 °C where the peak duty of the same motor sits at 120–150 — and in this
  // library a temperature IS a record of the grade (`F52SH_80C`, `F52SH_120C`).
  // The pick is stored per duty and rides ?mat= only; nothing here rewrites the
  // assignment (lib/dutySettings.ts, lib/magnetVariants.ts).
  const { library: matLib } = useMaterialsLibrary();
  const magNames = React.useMemo(
    () => Object.keys(matLib?.magnet ?? {}), [matLib]);
  const [dutyMats, setDutyMats] =
    useState<Record<string, string | null>>(() => activeDutyMaterials());
  const [dutyOn,  setDutyOn]  = useState<boolean>(() => !!activeDuty());
  useEffect(() => {
    // Both facts are localStorage, written by the catalog, by the Materials tab
    // and by the picker below — re-read them on every event that can move
    // either.  'sim-settings-restored' is the last thing a duty load fires,
    // i.e. AFTER the snapshot and the overlay have both spoken, so the badge
    // shows what the next solve will use.  'mat-assign-changed' is here because
    // a Materials-tab assignment now files itself against the active duty too.
    const EV = ['duty-materials-changed', 'mat-assign-changed',
                'mat-assign-local-changed', 'sim-settings-restored',
                'sim-design-applied', 'family-changed'];
    const sync = () => { setDutyMats(activeDutyMaterials()); setDutyOn(!!activeDuty()); };
    for (const ev of EV) window.addEventListener(ev, sync);
    return () => { for (const ev of EV) window.removeEventListener(ev, sync); };
  }, []);
  // What the solve will actually use, resolved by the SAME function the ?mat=
  // getter uses — the badge cannot drift from the payload.
  const liveMagnet = effectiveAssignment({ magnet: machineMagnet }, dutyMats,
    matLib as unknown as Record<string, Record<string, unknown>>).magnet;
  // The grade's other temperature records.  < 2 → nothing to choose between
  // (a single-record grade, or a name that carries no `_<T>C` suffix at all).
  const magVariants = React.useMemo(
    () => magnetVariants(machineMagnet, magNames), [machineMagnet, magNames]);
  useEffect(() => {
    if (!liveMagnet) return;
    let gone = false;
    (async () => {
      let Br: number | undefined, knee: number | undefined;
      const kneeOf = (bh: unknown) => (Array.isArray(bh) && bh.length > 1)
        ? Number((bh[0] as any)?.[1] <= 0 ? (bh[1] as any)?.[0] : (bh[0] as any)?.[0])
        : undefined;
      try {   // a user material's props travel with the request, not the library
        const ov = JSON.parse(currentMatJson() || 'null');
        const p = ov?.materials?.[liveMagnet];
        if (p) { Br = Number(p.Br); knee = kneeOf(p.bh_curve); }
      } catch { /* no override props — library below */ }
      if (!Number.isFinite(Br as number)) try {
        const m = await (await fetch(`${API}/api/materials/library/magnet/${encodeURIComponent(liveMagnet)}`)).json();
        const mm = m?.material ?? m;
        Br = Number(mm?.Br);
        knee = kneeOf(mm?.bh_curve);
      } catch { /* library lookup is optional */ }
      if (!gone) setMagInfo({ name: liveMagnet,
                              Br: Number.isFinite(Br as number) ? Br : undefined,
                              knee: Number.isFinite(knee as number) ? knee : undefined });
    })();
    return () => { gone = true; };
  }, [liveMagnet]);
  // A design load can swap the shared config's materials without firing a
  // mat-assign event — bridge that one event to the hook's refresh.
  useEffect(() => {
    const on = () => refreshAssign();
    window.addEventListener('sim-design-applied', on);
    return () => window.removeEventListener('sim-design-applied', on);
  }, [refreshAssign]);
  const [simBusy,  setSimBusy]  = useState(false);
  // Tell the follower (lib/familyFollow) that THIS browser has a solve in
  // flight: a duty loaded in another window must not swap these fields under a
  // run whose fetch is already out — the charts would describe one point and
  // the panel another.  Postponed, never dropped: the header strip re-checks on
  // its next poll and adopts as soon as the run is over.
  useEffect(() => {
    setSolveBusy('simulation', simBusy);
    return () => setSolveBusy('simulation', false);
  }, [simBusy]);
  // Clear the one-shot max-charge flag the moment its solve is over, so the
  // next ordinary Run is an ordinary run.
  useEffect(() => {
    if (!simBusy && chargeMaxOnce) setChargeMaxOnce(false);
  }, [simBusy, chargeMaxOnce]);
  // ── Physics caches, visible ──────────────────────────────────────────────
  // Every finished Run REPLACES the field / snapshot / transient stores
  // server-side; this line is how that is checked without reading the log.
  // Owner-only endpoint — a non-owner just never sees the line.
  const [caches, setCaches] = useState<{ field_cache: number; snapshots: number;
    last_refreshed_by: string | null } | null>(null);
  const loadCaches = useCallback(() => {
    fetch(`${API}/api/simulation/caches`)
      .then(r => (r.ok ? r.json() : null))
      .then(j => setCaches(j))
      .catch(() => { /* diagnostics must never surface as an error */ });
  }, []);
  useEffect(() => { if (!simBusy) loadCaches(); }, [simBusy, runNonce, loadCaches]);
  // "fresh" tells the backend to discard any frames cached from a Stopped
  // run and recompute everything; cancelledRun remembers that the last run
  // was Stopped so the next Run offers Continue / Start-fresh.
  const [freshRun,     setFreshRun]     = useState(false);
  const [cancelledRun, setCancelledRun] = useState(false);
  const [askResume,    setAskResume]    = useState(false);
  // ── Target T/P: the FULL run self-corrects once ──────────────────────────
  // Probe frames and full frames disagree by up to ~3 % when demag is on
  // (the ratchet samples different worst-case fields at 12 vs 40 steps —
  // measured: probes converged at 847 Nm, the full run gave 874).  The full
  // run is its own frame, so ONE linear Kt step on its own numbers lands
  // ±0.2 %.  Fires on every finished fresh run while target mode is active;
  // guarded to a single correction per attempt.
  const fitCorrRef = useRef(0);
  useEffect(() => {
    const onCost = () => {
      if (drive !== 'current' || targetKind === 'off') return;
      try {
        const ls = JSON.parse(localStorage.getItem('sim.lastSummary') || 'null');
        const Tl = Math.abs(Number(ls?.T_em_avg_Nm));
        const Il = Number(ls?.I_phase_rms_A);
        if (!(Tl > 0) || !(Il > 0)) return;
        const err = (Tl - targetValue) / targetValue;
        if (Math.abs(err) <= 0.01) {
          fitCorrRef.current = 0;
          setFitMsg(`on target: ${Tl.toFixed(1)} Nm (${err >= 0 ? '+' : ''}${(100 * err).toFixed(2)} %)`);
          return;
        }
        if (fitCorrRef.current >= 1) {
          fitCorrRef.current = 0;
          setFitMsg(`✗ still ${(100 * err).toFixed(1)} % off after one correction — check γ / speed / settings`);
          return;
        }
        fitCorrRef.current += 1;
        const If = Il * (targetValue / Tl);
        setCurrent(+If.toFixed(2));
        setFitMsg(`full run ${Tl.toFixed(1)} Nm (${err >= 0 ? '+' : ''}${(100 * err).toFixed(1)} %) — correcting to ${If.toFixed(1)} A, re-running`);
        setTimeout(() => launchRun(true), 400);
      } catch { /* no summary — nothing to correct against */ }
    };
    window.addEventListener('sim:solve-cost', onCost);
    return () => window.removeEventListener('sim:solve-cost', onCost);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drive, targetKind, targetValue]);

  // ── Target T/P: fit the current with cheap probes, then run for real ─────
  const fitAndRun = async () => {
    setCancelledRun(false); setAskResume(false);
    const Ttar = targetValue;      // torque is the canon; kW is a linked view
    if (!(Ttar > 0)) { setFitMsg('✗ target must be > 0'); return; }
    setFitBusy(true); setFitMsg(null);
    const readMesh = <T,>(k: string, d: T): T => {
      try { const v = localStorage.getItem(`mesh.${k}`); return v == null ? d : JSON.parse(v); }
      catch { return d; }
    };
    const probe = async (Ii: number): Promise<number> => {
      const payload: Record<string, unknown> = {
        n_steps_per_period: 12, n_periods: 1,
        gamma_deg: phaseOffset, I_phase_rms: Ii,
        drive: 'current', v_phase_peak: 0, v_delta_deg: 0,
        // Cheap probes drop the eddy solve (its torque effect is ~0.1 %) but
        // MUST keep demag as the panel has it: on this machine the ratchet
        // takes 3.7 % of the torque at 400 A (measured — a demag-free fit
        // landed 818 Nm against a 850 Nm target).
        eddy: false, demag, rotor_eddy: false, torque_filter: false,
        mesh_size_mm: readMesh('meshSize', 4.0), min_size_mm: readMesh('minSize', 0.3),
        outer_air_factor: readMesh('outerAir', 1.3), gap_layers: readMesh('gapLayers', 2),
        n_sectors: readMesh('nSectors', 1), stator_fillet_mm: 0,
        sliding_band: true, element_order: 2,
        iron_template: readMesh('ironTemplate', true), geo_mesh: readMesh('geoMesh', true),
        structured_gap: readMesh('structuredGap', false) || readMesh('ironTemplate', true),
        airgap_macro: readMesh('harmonicGap', false),
        pole_copy: readMesh('poleCopy', false),
        coil_temp_c: coilTemp, end_winding_factor: endWinding,
        // motor frame on purpose: γ+180 (generator) flips T's sign exactly in
        // current drive, so |T| is mode-independent and the probes stay cheap
        mode: 'motor',
        ...(String(daxisDeg ?? '').trim() !== '' && Number.isFinite(Number(daxisDeg))
          ? { daxis_deg: Number(daxisDeg) } : {}),
        ...(connection ? { connection } : {}),
        // kernel POST bypasses the ?geo=/?mat= interceptor — the probes must
        // solve the caller's OWN machine, not the shared config.
        ...(() => { const g = currentGeoJson(); return g ? { geo: g } : {}; })(),
        ...(() => { const m = currentMatJson(); return m ? { mat: m } : {}; })(),
      };
      const r = await fetch(`${API}/api/kernel/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capability: 'solver.em_transient', payload }),
      });
      if (!r.ok) throw new Error(`probe HTTP ${r.status}`);
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || 'probe failed');
      if (j.result && j.result.ok === false) throw new Error(String(j.result.error || 'probe refused'));
      const T = Math.abs(Number(j.result?.raw?.T_avg_Nm));
      if (!Number.isFinite(T) || T <= 0) throw new Error('probe returned no torque');
      return T;
    };
    try {
      // Seed from the last run's measured Kt when there is one.
      let I = current > 0 ? current : 100;
      try {
        const raw = localStorage.getItem('sim.lastSummary');
        const ls = raw ? JSON.parse(raw) : null;
        if (ls && Math.abs(Number(ls.T_em_avg_Nm)) > 0 && Number(ls.I_phase_rms_A) > 0) {
          I = Ttar * Number(ls.I_phase_rms_A) / Math.abs(Number(ls.T_em_avg_Nm));
        }
      } catch { /* seed stays */ }
      // NEAR the last run's torque?  Within ±2 % the T(I) curve is straight
      // to <0.1 %, so a LINEAR Kt correction replaces the probes entirely:
      // I_new = I_last · T_target/T_last — the elementary by-hand step, done
      // automatically.  A flat "reuse the current" shortcut ate a +0.4 %
      // target edit and returned identical numbers (user report).
      try {
        const raw = localStorage.getItem('sim.lastSummary');
        const ls = raw ? JSON.parse(raw) : null;
        const Tl = ls ? Math.abs(Number(ls.T_em_avg_Nm)) : NaN;
        const Il = ls ? Number(ls.I_phase_rms_A) : NaN;
        if (Number.isFinite(Tl) && Tl > 0 && Il > 0
            && Math.abs(Tl - Ttar) / Ttar < 0.02) {
          const If = Il * (Ttar / Tl);
          setCurrent(+If.toFixed(2));
          setFitMsg(`last run ${Tl.toFixed(1)} Nm is within 2 % — linear Kt step `
            + `${Il.toFixed(1)} → ${If.toFixed(1)} A, no probes; full run`);
          setFitBusy(false);
          setTimeout(() => launchRun(true), 0);
          return;
        }
      } catch { /* fall through to the probes */ }
      let prev: { I: number; T: number } | null = null;
      for (let k = 0; k < 4; k++) {
        setFitMsg(`fitting: probe ${k + 1} @ ${I.toFixed(1)} A…`);
        const T = await probe(I);
        const err = (T - Ttar) / Ttar;
        setFitMsg(`probe ${k + 1}: ${T.toFixed(1)} Nm @ ${I.toFixed(1)} A (${err >= 0 ? '+' : ''}${(100 * err).toFixed(1)} %)`);
        if (Math.abs(err) < 0.005) { prev = { I, T }; break; }
        let In: number;
        if (prev && Math.abs(T - prev.T) > 1e-9) {
          // true secant through the last two probes — handles saturation
          In = I + (Ttar - T) * (I - prev.I) / (T - prev.T);
        } else {
          In = I * (Ttar / T);           // first step: Kt through the origin
        }
        prev = { I, T };
        if (!(In > 0) || In > 3000) throw new Error('the fit diverged — check γ / winding');
        I = In;
      }
      setCurrent(+I.toFixed(2));
      setFitMsg(`fitted ${I.toFixed(1)} A → full run`);
      // launchRun PATCHes the shared config with the panel state, which now
      // carries the fitted current.
      setTimeout(() => launchRun(true), 0);
    } catch (e: any) {
      setFitMsg(`✗ ${e?.message ?? e}`);
    } finally {
      setFitBusy(false);
    }
  };

  const launchRun = (fresh: boolean, chargeMaxOnce = false) => {
    setFreshRun(fresh);
    setCancelledRun(false);
    setAskResume(false);
    // ONE-SHOT.  The max-charge search is a dozen solves, so it must never be a
    // sticky mode that quietly re-runs on the next ordinary press: the flag is
    // set for exactly this launch and cleared as soon as the solve finishes
    // (the effect on simBusy below).
    setChargeMaxOnce(chargeMaxOnce);
    // Persist the operating point to config.yaml on every Run — so it's
    // permanent across sessions/browsers (same principle as Rebuild mesh).
    fetch(`${API}/api/simulation/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      // EVERY physics switch this tab owns goes into the shared config, not
      // just the operating point.  The sweep / optimizer / descent read that
      // config for anything the request does not name, and the optimizer's eval
      // cache is keyed by a hash of the same block — so a switch kept in
      // localStorage meant swept points solved different physics AND reused
      // results from before it was flipped.  The backend flushes the simulation
      // caches on every one of these writes.
      body: JSON.stringify(simPhysicsPatch()),
    }).catch(() => {});
    setRunNonce(n => n + 1);
  };

  const simReady = useRef(false);   // gate: persist only AFTER the mount load populated state

  // Auto-run ONCE when the Simulation tab first becomes visible (on the first
  // open or after an F5). The runNonce===0 guard means it computes exactly one
  // time per page load — the panel stays mounted, so switching to another tab
  // and back keeps the result without recomputing. Without this the right pane
  // is a black void after every reload until you click Run. The backend caches
  // the transient, so the auto-run is a fast cache hit on subsequent reloads.
  // NO auto-run on open.  The transient charts restore the LAST result on mount
  // (from localStorage, or the backend's persisted last via ?restore=true), so a
  // reload SHOWS the previous simulation instead of silently recomputing it.  A
  // fresh state with nothing saved shows the "press Run" prompt; only the Run
  // button (or a settings change + Re-run) computes.  (Was: auto-launchRun when
  // runNonce===0 — that recomputed on every reload where runNonce hadn't been
  // persisted or the backend cache key missed.)
  useEffect(() => { void active; void simBusy; }, [active]);
  // Steps per electrical period (transient time resolution).  Persisted.
  // A Select over the divisors of stepsMax — the set is fixed, so free typing
  // only produced snap surprises (user, 2026-08-01: "если фиксированные
  // значения — давай выбор только их из списка").
  // Defaults follow the user's standing practice (2026-09-03): 40 steps per
  // period minimum and demag ON on every machine — a fresh browser profile
  // must not silently solve at 24 steps without the de-rate.
  const [steps,    setSteps]    = usePersisted('stepsPP', 40);   // transient frames/period — single source (optimizer reads this too)
  // Magnet/shaft eddy losses ALWAYS come from the real field solve
  // (J = σ(−∂A/∂t + U), per-magnet ∫J=0, assigned-material σ — the Ansys way),
  // never the classical slab d²/12 estimate.  No toggle: real fields only.
  const fieldLosses = true;
  // Per-element irreversible demagnetisation: a pre-pass sweeps the period at
  // full Br, finds the worst demagnetising field at every magnet element, and
  // de-rates Br along the recoil line (Ansys-style) so the transient torque /
  // back-EMF reflect the weakened magnets.  Opt-in (adds a pre-pass sweep).
  const [demag, setDemag] = usePersisted('demag', true);
  // Coupled σ·∂A/∂t eddy-current solve (P2): the currents induced in copper,
  // magnets and shaft are solved TOGETHER with the field instead of estimated
  // afterwards, so the run reports the SOLVED copper loss (DC + the real AC /
  // proximity part) and its field snapshot carries the true eddy J⟳.  That
  // snapshot is what makes the J⟳ / Loss field views instant: they replay this
  // run's own last frame instead of launching a second transient.  Turning it
  // OFF is honest too — the run is then magnetostatic and those views solve on
  // demand, exactly as they used to (and say so in their header).
  // ALWAYS ON (user 2026-09-05: "можно эту кнопку убрать — я всегда её
  // использую").  The checkbox is gone; the persisted key is pinned to true
  // so every reader of `sim.eddyCoupled` (the field views' snapshot probes,
  // the duty settings block, the ETA) agrees with what the run does.
  const eddyCoupled = true;
  useEffect(() => {
    try { localStorage.setItem('sim.eddyCoupled', 'true'); } catch { /* quota */ }
  }, []);
  // EMPTY-STORE NOTICE (user 2026-09-03): a browser whose store came back
  // empty (reset profile, new browser, another origin) shows the factory
  // defaults under the name of the active duty.  NOTHING is applied by itself
  // — the user's rule the same day: never load anything without permission.
  // The panel only SAYS so and offers one click to pull the duty's snapshot.
  const [emptyStoreDuty, setEmptyStoreDuty] = useState<string | null>(null);
  useEffect(() => {
    if (!panelSettingsMissing()) return;
    let alive = true;
    (async () => {
      try {
        const ctx = await fetch(`${API}/api/family/context`).then(r => r.json());
        if (alive && ctx?.active && ctx.duty) setEmptyStoreDuty(`${ctx.die} / ${ctx.config} / ${ctx.duty}`);
      } catch { /* offline — nothing to offer */ }
    })();
    return () => { alive = false; };
  }, []);
  const restoreFromDuty = async () => {
    const done = await healPanelSettingsFromActiveDuty(true);
    setEmptyStoreDuty(null);
    if (done) window.dispatchEvent(new CustomEvent('sim-settings-restored'));
  };
  // ── Measured cost of the LAST solve, for the pre-run line below ──────────
  // TransientCharts stores {frames, wall_s} from every FRESH run (never from a
  // restored/cached one — that would quote a rate no solve on this machine
  // produced).  Seeded from localStorage so the estimate survives a reload.
  const [solveCost, setSolveCost] =
    useState<{ frames: number; wall_s: number; warm?: number } | null>(() => {
      try {
        const s = localStorage.getItem('sim.lastSolveCost');
        return s ? JSON.parse(s) as { frames: number; wall_s: number; warm?: number } : null;
      } catch { return null; }
    });
  useEffect(() => {
    const onCost = (e: Event) =>
      setSolveCost((e as CustomEvent).detail as { frames: number; wall_s: number; warm?: number });
    window.addEventListener('sim:solve-cost', onCost);
    return () => window.removeEventListener('sim:solve-cost', onCost);
  }, []);
  // Band-limit the transient torque to the physical 6·k electrical orders
  // (drops the broadband slip-node noise a balanced 3-phase machine cannot
  // produce).  ON by default; turn off to inspect the raw per-frame torque.
  // Always raw torque — the filter checkbox is gone, and a stale persisted
  // `true` must not keep silently filtering, so this is a constant, not state.
  const torqueFilter = false;

  // ONE body for both config writes (Run and the debounced auto-save), so the
  // two can never disagree about what the shared physics is.
  const simPhysicsPatch = () => ({
    max_current: current, frequency, rpm, phase_offset_deg: phaseOffset,
    coil_temp_c: coilTemp, steps_per_period: steps,
    end_winding_factor: endWinding, connection, star_delta: starDelta,
    demag, eddy: eddyCoupled, rotor_eddy: fieldLosses, torque_filter: torqueFilter,
    drive, v_phase_peak: vPeak, v_delta_deg: vDelta,
    // The PWM inverter's bus and carrier and the BLDC block amplitude are
    // PHYSICS, so they go where every other physics field goes: the shared
    // config that the sweep / optimizer / descent read.  (The custom waveform
    // does not — it is up to 20k samples and belongs to its run.)
    v_bus: vBus, f_switch: fSwitch, i_block: iBlock,
    mode: opMode,
  });

  // Persist the operating point to config.yaml on ANY change (debounced), not
  // only on Run — config wins on mount (so presets / Reset apply), so it must
  // stay current or a change made without pressing Run would be lost on reload.
  // (`simReady` is declared with the other mount state above — the gate has to
  //  exist before the effect that SETS it, which runs earlier in the file.)
  useEffect(() => {
    if (!simReady.current) return;   // skip until the operating point is loaded from config
    const id = setTimeout(() => {
      fetch(`${API}/api/simulation/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(simPhysicsPatch()),
      }).catch(() => {});
    }, 700);
    return () => clearTimeout(id);
  }, [current, frequency, rpm, phaseOffset, demag, eddyCoupled, fieldLosses,
      coilTemp, steps, endWinding, connection, starDelta, drive, opMode, vPeak,
      vDelta, vBus, fSwitch, iBlock]);

  // Auto-save EVERY simulation change into the active motor ("my copy").
  // syncActiveMotor is internally debounced, so firing on each change is fine.
  useEffect(() => {
    if (!simReady.current) return;
    syncActiveMotor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, frequency, rpm, phaseOffset, steps, coilTemp, endWinding, demag, eddyCoupled, torqueFilter, connection, starDelta, daxisDeg]);
  // HARD upper bound: the sliding-band rotor can only sit on slip-ring nodes,
  // so steps/period must DIVIDE the nodes-per-electrical-period count.  The
  // backend (fem_solver_2d.fem_transient_sliding_band) makes that count adaptive:
  //   _slip_base       = round(1008·(gap_layers+2)/3)        // scales with air-gap layers
  //   _slip_per_period = 24·max(5, ceil(_slip_base/(24·pp))) // multiple of 24, pole-pair-divisible
  // Mirror the SAME formula (incl. the gap_layers term — previously this used a
  // fixed 1008 ≙ gap_layers=1, so it predicted 120 while the solver used 144 at
  // gap_layers=2: the field kept 60 but the solver snapped it to 72).
  const gapLayers = (() => {
    try { return Number(JSON.parse(localStorage.getItem('mesh.gapLayers') ?? '2')) || 2; }
    catch { return 2; }
  })();
  const _slipBase = Math.round(1008 * (Math.max(1, gapLayers) + 2) / 3);
  const SLIP_PER_PERIOD = 24 * Math.max(5, Math.ceil(_slipBase / (24 * Math.max(polePairs, 1))));
  const stepsMax = SLIP_PER_PERIOD;         // nodes per electrical period
  // valid step counts shown in the helper (divisors of stepsMax, ≥12)
  const validSteps = Array.from({ length: stepsMax }, (_, i) => i + 1)
    .filter(d => stepsMax % d === 0 && d >= 12);
  const snapSteps = (v: number) => {
    // ABOVE the ring is legal now: the solver RAISES the slip density to the
    // requested count (fine PWM steps must be honoured, not capped), so a
    // count past stepsMax is run exactly as asked and must not be pulled back.
    if (v > stepsMax) return v;
    if (stepsMax % v === 0) return v;       // already a divisor
    let best = stepsMax;
    for (let d = 1; d <= stepsMax; d++)
      if (stepsMax % d === 0 && (Math.abs(d - v) < Math.abs(best - v)
          || (Math.abs(d - v) === Math.abs(best - v) && d > best))) best = d;
    return best;
  };
  // Snap the persisted steps onto the valid grid whenever it changes (motor /
  // gap_layers change) or on mount — the divisor set depends on the machine, so
  // a stored value can fall off the list.
  useEffect(() => {
    const s = snapSteps(steps);
    if (s !== steps) setSteps(s);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepsMax]);
  // ── V₁ SEED from the last CURRENT-drive run of THIS machine ────────────
  // The two-pass workflow the user works in: fix the point on the current
  // drive, where the torque is what you dial in; then run the inverter at the
  // fundamental voltage that reproduces that current.  The solver reports that
  // fundamental in exactly the (v_phase_peak, v_delta_deg) coordinates these
  // sources take (summary.V1_seed_peak_V / V1_seed_delta_deg).
  //
  // MACHINE-SCOPED, and this is the whole reason the check is here: every
  // summary carries `_geoSig`, the stamp of the geometry it was solved on, and
  // seeding a PWM run from another motor's voltage is exactly the stale-machine
  // leak the card's banner and the V_bus prefill already guard against.  No
  // matching summary → no chip, rather than a plausible wrong number.
  // It NEVER writes on its own: the chip is a click, so a hand-typed V is safe.
  const liveGeoSig = React.useMemo(() => {
    try { return geoSignature(JSON.parse(geomSig) as Record<string, unknown>); }
    catch { return ''; }
  }, [geomSig]);
  const v1Seed = React.useMemo(() => {
    try {
      const s = JSON.parse(localStorage.getItem('sim.lastSummary') || 'null');
      if (!s) return null;
      // Only a CURRENT-drive run is a seed: on a voltage/PWM run these two
      // fields are just the voltage that was applied, so offering them back
      // would be a no-op dressed as a measurement.
      const d = String(s.drive ?? 'current');
      if (d !== 'current' && d !== 'custom_current' && d !== 'bldc_current') return null;
      if (s._geoSig && liveGeoSig && s._geoSig !== liveGeoSig) return null;
      const pk = Number(s.V1_seed_peak_V);
      const dl = Number(s.V1_seed_delta_deg);
      if (!(pk > 0) || !Number.isFinite(dl)) return null;
      // The OPERATING POINT the seed belongs to.  A fundamental voltage is a
      // property of one (rpm, I, γ) point — the EMF alone scales with speed —
      // and a seed applied across points misses the torque by exactly that
      // scaling (measured live 2026-08-31: 10.11 V from the 13000-rpm run,
      // applied at 14400 rpm, gave 0.46 N·m instead of 0.66).
      return { pk: +pk.toFixed(2), dl: +dl.toFixed(1),
               rpm: Number(s.rpm) || 0,
               iA: Number(s.I_phase_rms_A) || 0,
               gam: Number(s.gamma_deg) };
    } catch { return null; }
  }, [liveGeoSig, runNonce, drive]);
  // Does the seed's point match the point on the panel right now?
  const v1SeedMatches = React.useMemo(() => {
    if (!v1Seed) return false;
    const relOk = (a: number, b: number, tol: number) =>
      b > 0 ? Math.abs(a - b) / b <= tol : a === b;
    return relOk(v1Seed.rpm, rpm, 0.005)
        && relOk(v1Seed.iA, current, 0.01)
        && Math.abs((v1Seed.gam ?? 0) - phaseOffset) <= 0.5;
  }, [v1Seed, rpm, current, phaseOffset]);
  // ── AUTO-apply of the seed (user 2026-08-31: "она должна ставить это
  // значение автоматом после короткого первого прогона, и угол тоже").
  // The write happens by itself when a matching seed exists AND the fields do
  // not hold a value the user typed for THIS machine.  Ownership is a record
  // of what WE last applied ('battery.vseed': {sig, pk, dl}) — same discipline
  // as the V_bus prefill: fields still equal to our last write are ours to
  // move; a typed value mismatches the record and is never touched (the chip
  // stays for explicit override).  No record at all counts as ours: any value
  // present then predates seeding (the 30 V default that jammed a 22 V bus).
  React.useEffect(() => {
    if (drive !== 'pwm_voltage' && drive !== 'voltage') return;
    if (!v1Seed || !v1SeedMatches) return;
    let rec: { sig?: string; pk?: number; dl?: number } | null = null;
    try { rec = JSON.parse(localStorage.getItem('battery.vseed') || 'null'); }
    catch { rec = null; }
    const near = (a: number, b: number) => Math.abs(a - b) <= 0.051;
    const owned = !rec
      || (near(Number(rec.pk ?? NaN), vPeak) && near(Number(rec.dl ?? NaN), vDelta));
    const already = near(v1Seed.pk, vPeak) && near(v1Seed.dl, vDelta);
    if (!owned || already) return;
    setVPeak(v1Seed.pk);
    setVDelta(v1Seed.dl);
    try {
      localStorage.setItem('battery.vseed',
        JSON.stringify({ sig: liveGeoSig, pk: v1Seed.pk, dl: v1Seed.dl }));
    } catch { /* quota */ }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v1Seed, drive]);

  // ── PWM time-resolution rule (mirrors the solver's hard gate) ──────────
  // The carrier is snapped to a whole number per electrical period, and the
  // solver REFUSES fewer than 8 solve steps per switching period: below that
  // the exact-mean voltage integration averages the pulses away and the run
  // reproduces the ideal sinusoid at PWM cost.  Computed here so the panel can
  // say the number to set BEFORE the run is launched, not after it is refused.
  const pwmCarriers = (drive === 'pwm_voltage' && fSwitch > 0 && frequency > 0)
    ? Math.max(1, Math.round(fSwitch / frequency)) : 0;
  const stepsPerSwitch = pwmCarriers ? steps / pwmCarriers : 0;
  // The step count to SET: 16 samples per switching period, snapped UP onto a
  // count the sliding band can actually run (a divisor of the slip ring, or —
  // above the ring — any count, because the ring then follows the request).
  const suggestSteps = (() => {
    if (!pwmCarriers) return 0;
    const want = 16 * pwmCarriers;
    if (want > stepsMax) return want;
    for (let d = want; d <= stepsMax; d++) if (stepsMax % d === 0) return d;
    return stepsMax;
  })();
  // The picker's options: the divisor grid, plus whatever is currently set and
  // whatever the PWM rule suggests when those sit above the ring (legal now —
  // the solver raises the slip density instead of capping the request).  An
  // option that is not in the list renders as an empty Select.
  const stepsOptions = Array.from(new Set([
    ...validSteps,
    ...(steps > stepsMax ? [steps] : []),
    ...(suggestSteps > stepsMax ? [suggestSteps] : []),
  ])).sort((a, b) => a - b);
  // ── rotor angle / PINN training settings removed ──────────────────────
  // FEM auto-run now sweeps the rotor through the full electrical period,
  // and the PINN run button is gone (no Modulus dependency).  Kept as
  // placeholders so the legacy fetch payload below still type-checks.
  const rotorAngle = 0;
  const maxSteps   = 10000;
  const device: 'cpu' | 'cuda' = 'cpu';

  // ── derived winding values ────────────────────────────────────────────────
  const I_coil_rms  = current / nParallel;                   // Arms per coil
  const I_coil_peak = I_coil_rms * Math.sqrt(2);             // A peak per coil

  // ── job state ─────────────────────────────────────────────────────────────
  const [jobId,   setJobId]   = useState<string | null>(null);
  const [job,     setJob]     = useState<JobStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── "Apply best design" (Sweep) pushes the optimizer's operating point here ──
  // so simulating the saved geometry reproduces the optimizer's result instead of
  // running at the panel's idle current.  This panel is ALWAYS mounted (hidden
  // when inactive), so it won't re-read on a tab switch — update live via event.
  useEffect(() => {
    const onOp = (e: Event) => {
      const d = (e as CustomEvent).detail || {};
      if (typeof d.current === 'number') setCurrent(d.current);
      if (typeof d.gamma === 'number') setPhaseOffset(d.gamma);
      // A family DUTY is a whole operating point — rpm / mode / coil temp /
      // connection arrive with it (the Sweep apply keeps sending just I and γ).
      if (typeof d.rpm === 'number' && d.rpm > 0) {
        setRpm(d.rpm);
        setFrequency(+((d.rpm * polePairs) / 60).toFixed(2));
      }
      if (d.mode === 'motor' || d.mode === 'generator') setOpMode(d.mode);
      if (typeof d.coilTemp === 'number') setCoilTemp(d.coilTemp);
      if (typeof d.connection === 'string' && d.connection) setConnection(d.connection);
      if (typeof d.star_delta === 'string' && d.star_delta) setStarDelta(d.star_delta);
    };
    window.addEventListener('sim-operating-point', onOp as EventListener);
    return () => window.removeEventListener('sim-operating-point', onOp as EventListener);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polePairs]);

  // Pin: when a descent design is applied, adopt the run's eval params so re-running
  // the Simulation reproduces the picked point (mesh params are read fresh from
  // localStorage; these persisted-state fields need a live nudge, like the op-point).
  useEffect(() => {
    const onEval = (e: Event) => {
      const p = (e as CustomEvent).detail || {};
      // Every setter here must EXIST: this handler once called two that had
      // been removed (setStepsStr, setTorqueFilter — torque_filter is a
      // hardwired const now).  The ReferenceError aborted the handler at the
      // first line, so coilTemp/endWinding/demag were never adopted — while
      // restoreDescentEvalParams had ALREADY written them to localStorage,
      // which is what the solve reads.  The panel then showed one coil temp
      // and the Re-run solved at another: the exact silent divergence this
      // pin exists to prevent.  (vite build ships without a type-check, so a
      // missing name here reaches production.)
      if (typeof p.steps_per_period === 'number') setSteps(p.steps_per_period);
      if (typeof p.coil_temp_c === 'number') setCoilTemp(p.coil_temp_c);
      // 0 = "auto" (the descent let the solver derive k_end per candidate); adopting
      // that 0 would blank a cell that must always show the geometry's real factor.
      if (Number(p.end_winding_factor) > 0) setEndWinding(Number(p.end_winding_factor));
      if (typeof p.demag === 'boolean') setDemag(p.demag);
    };
    window.addEventListener('descent-eval-params', onEval as EventListener);
    return () => window.removeEventListener('descent-eval-params', onEval as EventListener);
  }, []);

  // Applying a design (Optimization / Sweep) swapped the geometry AND the operating
  // point, so the dashboard has to recompute for it — otherwise it keeps showing the
  // previous design until the user notices and presses Re-run.  The apply already
  // PATCHed the operating point, so only tick the run gate here: re-PATCHing would
  // write back this listener's stale current/γ over the values just applied.
  useEffect(() => {
    const onRerun = () => {
      setFreshRun(false); setCancelledRun(false); setAskResume(false);
      setRunNonce(n => n + 1);
    };
    window.addEventListener('sim-rerun', onRerun);
    return () => window.removeEventListener('sim-rerun', onRerun);
  }, []);

  // ── load server status + physics config + geometry ────────────────────────
  // SAME contract as the Mesh tab after the 2026-09-07 incident (user: "захожу
  // в Mesh и опять не сохранено то, что было до этого — там точно стояло 1/2"):
  // the SERVER config is the single source of truth, it is adopted BEFORE any
  // save is allowed, and a failed load retries with backoff instead of letting
  // constants stand in for it.
  //
  // This panel carried the same clobber, one step milder.  The debounced PATCH
  // below writes the WHOLE physics block, but the mount only ever adopted
  // current / rpm / γ.  Everything else — steps, coil temp, demag, drive, mode,
  // V̂, δ, V_bus, f_sw, I_block — came from this browser's localStorage, or from
  // the CONSTANT defaults when that storage was empty (the in-app browser pane
  // loses it on every Claude app restart, which is exactly what the Mesh tab hit
  // between 09:0x and 09:2x that day).  The moment the status answer flipped
  // simReady, those constants were written into config.yaml — measured: the file
  // holds steps_per_period 36 while the constant here is 40.  Adopt them all
  // from the server first, so the block that gets PATCHed is the server's own
  // state plus the user's edits, never a factory default.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    const load = () => {
      const j = (url: string) => fetch(url).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
      Promise.all([j(`${API}/api/simulation/status`), j(`${API}/api/simulation/config`)])
        .then(([d, sim]) => {
          if (!alive) return;
          setSrvStatus(d);
          setSrvErr(null);
          if (d.operating_point) {
            setCurrent(d.operating_point.max_current ?? 85);
            // frequency is NOT seeded from the saved operating point — it is
            // fully derived (rpm·pp/60, effect above); seeding it here raced the
            // geometry load and pinned the stale value after a pole-count change.
            setRpm(d.operating_point.rpm ?? 3950);
            // γ is PATCHed to config on every edit but was never read back, so it
            // survived only in this browser's localStorage: a fresh browser, or any
            // motor load that seeded a different value, showed 0 and looked like the
            // setting "never saves".  Config is the durable, cross-browser source —
            // restore from it exactly like current and rpm above.
            const _g = Number(d.operating_point.phase_offset_deg);
            if (Number.isFinite(_g)) setPhaseOffset(_g);
          }
          // ── the rest of the shared physics block ──────────────────────────
          // Adopted here and nowhere else.  NOT adopted on purpose:
          //   connection        — owned by /api/winding/config (loadWinding),
          //   end_winding_factor— derived from the geometry (k_end effect),
          //   frequency         — derived from rpm·pp/60,
          //   daxis_deg         — has its own explicit pin effect above.
          const s = (sim ?? {}) as Record<string, unknown>;
          const num = (k: string): number | null => {
            const v = Number(s[k]);
            return Number.isFinite(v) ? v : null;
          };
          const _steps = num('steps_per_period'); if (_steps && _steps > 0) setSteps(_steps);
          const _temp  = num('coil_temp_c');      if (_temp  !== null) setCoilTemp(_temp);
          const _vpk   = num('v_phase_peak');     if (_vpk   !== null) setVPeak(_vpk);
          const _vdl   = num('v_delta_deg');      if (_vdl   !== null) setVDelta(_vdl);
          const _vbus  = num('v_bus');            if (_vbus  !== null) setVBus(_vbus);
          const _fsw   = num('f_switch');         if (_fsw   !== null && _fsw > 0) setFSwitch(_fsw);
          const _ibl   = num('i_block');          if (_ibl   !== null) setIBlock(_ibl);
          if (typeof s.demag === 'boolean') setDemag(s.demag);
          if (s.mode === 'motor' || s.mode === 'generator') setOpMode(s.mode);
          const _drv = s.drive;
          if (_drv === 'current' || _drv === 'voltage' || _drv === 'pwm_voltage'
              || _drv === 'custom_current' || _drv === 'bldc_current') setDrive(_drv);
          if (import.meta.env.DEV) console.info('[sim] physics adopted from server config');
          simReady.current = true;   // server state adopted → debounced saves allowed
        })
        .catch(e => {
          if (!alive) return;
          setSrvErr(String(e));
          // Backoff 1, 2, 4, 8, 16, 32, 60 s.  simReady stays false the whole
          // time: a panel that could not read the config must not write one.
          timer = setTimeout(load, Math.min(60_000, 1000 * 2 ** Math.min(attempt, 6)));
          attempt += 1;
        });
    };
    load();

    // Get geometry for periodicity + winding
    fetch(`${API}/api/config`)
      .then(r => r.json())
      .then(d => {
        const g = d.geometry ?? {};
        if (g.num_poles)        setNumPoles(g.num_poles);
        if (g.num_slots)        setNumSlots(g.num_slots);
        if (g.num_wires_per_slot) setNWiresPerSlot(g.num_wires_per_slot);
      })
      .catch(() => {});
    return () => { alive = false; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── k_end always tracks the CURRENT geometry ──────────────────────────────
  // masses.end_winding_factor is the SINGLE SOURCE (the copper mass, phase
  // resistance and copper loss all scale by it), so re-fetch the canonical value
  // on every geometry change rather than once on mount.  Persisting the real
  // number instead of a bare 0 ("auto") also fixes every consumer that reads
  // `sim.endWinding` straight from localStorage — the transient charts, the
  // dashboard and a sweep now all scale the copper loss by the same end-turn
  // length the solver uses.
  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/config`)
      .then(r => r.json())
      .then(d => {
        if (cancelled) return;
        const kAuto = Number(d.end_winding_factor);
        if (!Number.isFinite(kAuto) || kAuto <= 0) return;
        const k = +kAuto.toFixed(3);
        setEndWindingGeo(k);
        setEndWinding(k);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [geomSig]);

  // ── polling ───────────────────────────────────────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setPolling(false);
  }, []);

  useEffect(() => {
    if (!jobId || !polling) return;
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/simulation/result/${jobId}`);
        const d: JobStatus = await r.json();
        setJob(d);
        if (d.status === 'done' || d.status === 'error') stopPolling();
      } catch { /* ignore transient */ }
    }, 1500);
    return stopPolling;
  }, [jobId, polling, stopPolling]);

  // ── run ───────────────────────────────────────────────────────────────────
  const handleRun = async () => {
    setJob(null);
    try {
      const r = await fetch(`${API}/api/simulation/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          max_current:      parseFloat(I_coil_peak.toFixed(2)),  // peak A per coil
          frequency:        frequency,
          rpm:              rpm,
          rotor_angle:      rotorAngle,
          phase_offset_deg: phaseOffset,
          max_steps:        maxSteps,
          device:           device,
        }),
      });
      const d: JobStatus = await r.json();
      setJobId(d.job_id);
      setJob(d);
      setPolling(true);
    } catch (e) {
      setSrvErr(String(e));
    }
  };

  const isRunning = job?.status === 'queued' || job?.status === 'running';

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden', bgcolor: 'var(--panel-2)' }}>

      {/* ── LEFT: controls ── */}
      <Box sx={{
        width: 320, flexShrink: 0, overflowY: 'auto',
        borderRight: '1px solid var(--line-soft)', p: 2,
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>

        {/* Winding connection */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Winding Connection
          </Typography>
          <Typography sx={{ fontSize: 10, color: 'var(--line)', mb: 1.2 }}>
            {nCoilsPerPhase} coils/phase · {nWiresPerSlot} wires/slot
          </Typography>

          {/* Connection buttons */}
          <Box sx={{ display: 'flex', gap: 0.75, mb: 1.5, flexWrap: 'wrap' }}>
            {windConns.map(c => (
              <Tooltip key={c.label} title={`${c.n_series} series × ${c.n_parallel} parallel`} placement="top">
                <Button
                  size="small"
                  variant={connection === c.label ? 'contained' : 'outlined'}
                  onClick={() => { setConnection(c.label); applyWinding({ connection: c.label }); }}
                  disabled={isRunning}
                  sx={{ flex: 1, minWidth: 52, fontSize: 11, fontWeight: 700, py: 0.5,
                    textTransform: 'none',
                    ...(connection === c.label ? {} : { color: 'var(--text-3)', borderColor: 'var(--line)' })
                  }}
                >
                  {c.label}
                </Button>
              </Tooltip>
            ))}
          </Box>

          {/* STAR / DELTA — the terminal connection.  Changes no ampere-turn
              and therefore no torque: it trades √3 of current for √3 of
              voltage, which is the whole reason to want delta (√3 more turns
              on the same bus), and it closes the loop the winding's own
              zero-sequence triplen EMF drives a circulating current round. */}
          <Box sx={{ display: 'flex', gap: 0.75, mb: 1.25 }}>
            {([
              ['star',  'Y',  'Star — V_line = √3·V_phase, I_line = I_phase. No zero-sequence loop: the winding\u2019s triplen EMF cancels line-to-line and nothing circulates.'],
              ['delta', '\u0394', 'Delta — V_line = V_phase, I_line = √3·I_phase, so √3 more turns fit the same bus. Closes a loop the triplen EMF drives current round; the run reports that current and adds its loss to the copper.'],
            ] as const).map(([val, glyph, tip]) => (
              <Tooltip key={val} title={tip} placement="top">
                <Button
                  size="small"
                  variant={starDelta === val ? 'contained' : 'outlined'}
                  onClick={() => setStarDelta(val)}
                  disabled={isRunning}
                  sx={{ flex: 1, fontSize: 11, fontWeight: 700, py: 0.5,
                    textTransform: 'none',
                    ...(starDelta === val ? {} : { color: 'var(--text-3)', borderColor: 'var(--line)' })
                  }}
                >
                  {glyph}&nbsp;{val === 'star' ? 'Star' : 'Delta'}
                </Button>
              </Tooltip>
            ))}
          </Box>

          {/* The backend refused the change — one line, the reason it gave. */}
          {windErr && (
            <Typography sx={{ fontSize: 10.5, color: '#f87171', mt: -0.75, mb: 1 }}>
              ⚠ winding not changed — {windErr}
            </Typography>
          )}

        </Box>

        <Divider sx={{ borderColor: 'var(--panel)' }}/>

        {/* ── Coil layout — currents (phase + sign) per slot ── */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Coil Layout — currents per slot
          </Typography>

          {/* single-layer winding (this machine has no double-layer variant) */}
          <Typography sx={{ fontSize: 10, color: 'var(--text-3)', mb: 1 }}>
            Single-layer winding · {windCfg?.num_slots ?? 24} slots
          </Typography>

          {/* phase map: one cell per slot (A=red B=green C=blue, +full −faded) */}
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '2px', mb: 1 }}>
            {(windCfg?.layout_slots || []).map(([ph, d]: [string, number], i: number) => (
              <Tooltip key={i} title={`slot ${i}: ${ph}${d > 0 ? '+' : '−'}`}>
                <Box sx={{ width: 16, height: 18, borderRadius: '2px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 9, fontWeight: 700, color: '#fff',
                  bgcolor: PHASE_COLOR[ph] || 'var(--text-3)',
                  opacity: d > 0 ? 1 : 0.4,
                  border: d > 0 ? '1px solid rgba(255,255,255,0.45)' : '1px solid transparent' }}>
                  {d > 0 ? ph : ph.toLowerCase()}
                </Box>
              </Tooltip>
            ))}
          </Box>

        </Box>

        <Divider sx={{ borderColor: 'var(--panel)' }}/>

        {/* Operating point */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1.5 }}>
            Operating Point
          </Typography>
          {magInfo && (
            <Tooltip placement="right" title={
              `The magnet these results are solved with. Br sets the flux; the knee is how much `
              + `demagnetising field it survives. Two magnets with the same Br but different knees `
              + `behave IDENTICALLY unless "Demagnetisation" below is ticked — that is the only `
              + `model that reads the knee.`}>
              {/* Two rows (user 2026-09-09): the name on the first, Br and the
                  knee on the second; the temperature picker keeps its click
                  but shows no arrow. */}
              <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 0.75, mb: 1.25,
                px: 1, py: 0.5, borderRadius: 0.5, cursor: 'help',
                border: '1px solid var(--line)', bgcolor: 'var(--panel-2)' }}>
                <Typography sx={{ fontSize: 9.5, color: 'var(--text-4)', textTransform: 'uppercase',
                  letterSpacing: '0.06em' }}>magnet</Typography>
                {/* The name is the TEMPERATURE picker when the grade is in the
                    library at more than one temperature — the duty's magnet
                    temperature, one line, the rest in the tooltip. */}
                {magVariants.length >= 2 ? (
                  <Tooltip title={dutyOn
                    ? 'Temperature this DUTY solves its magnets at — same grade, the '
                      + 'library\'s record for that temperature. Stored for this duty and '
                      + 'sent with the solve; it reaches the yaml on Save to duty. The '
                      + 'machine\'s magnet itself is changed in Materials.'
                    : 'A magnet temperature belongs to a duty — select a duty first. '
                      + 'The machine\'s own magnet is changed in Materials.'}>
                    <span onClick={(e) => e.stopPropagation()}>
                      {/* The live magnet is always an option, even when the
                          library list this page fetched at mount predates it
                          (a card added while the page was open showed a blank
                          picker — user 2026-09-09: "почему тут не пишет, какие
                          магниты"). */}
                      <Select value={liveMagnet || ''}
                        disabled={!dutyOn} variant="standard" disableUnderline
                        IconComponent={() => null}
                        onChange={(e) => {
                          const v = String(e.target.value);
                          const pick = v === machineMagnet ? null : v;
                          setDutyMagnet(pick);       // per-duty overlay + ?mat=
                          // Belt and braces: the setter's own event already
                          // syncs this state; re-reading keeps the badge right
                          // even if the dispatch is ever swallowed.
                          setDutyMats(activeDutyMaterials());
                        }}
                        sx={{ fontSize: 11, fontWeight: 700, color: '#2563eb',
                          '& .MuiSelect-select': { py: 0, pl: 0, pr: '0 !important',
                            fontSize: 11, fontWeight: 700, color: '#2563eb' },
                          '&.Mui-disabled .MuiSelect-select': {
                            WebkitTextFillColor: '#2563eb', opacity: 0.7 } }}>
                        {(liveMagnet && !magVariants.includes(liveMagnet)
                          ? [...magVariants, liveMagnet] : magVariants).map(v => (
                          <MenuItem key={v} value={v} sx={{ fontSize: 11 }}>
                            {v}{v === machineMagnet ? ' (machine)' : ''}
                          </MenuItem>
                        ))}
                      </Select>
                    </span>
                  </Tooltip>
                ) : (
                  <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#2563eb' }}>{magInfo.name}</Typography>
                )}
                {/* second row: the numbers */}
                <Box sx={{ flexBasis: '100%', display: 'flex', gap: 1.25, alignItems: 'baseline' }}>
                  {magInfo.Br != null && (
                    <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>Br {magInfo.Br.toFixed(2)} T</Typography>
                  )}
                  {magInfo.knee != null && (
                    <Typography sx={{ fontSize: 10, color: demag ? 'var(--text-3)' : 'var(--text-4)' }}>
                      knee {(magInfo.knee / 1000).toFixed(0)} kA/m{demag ? '' : ' (unused)'}
                    </Typography>
                  )}
                </Box>
              </Box>
            </Tooltip>
          )}

          {/* The machine's SUPPLY, in the same chip form as the magnet: what
              the PWM source's DC link is prefilled from, and what a duty's
              voltage is judged against.  Click to edit — same dialog as the
              Family catalog's 🔋. */}
          <Tooltip placement="right" title={
            (battery
              ? `${battery.cells ?? '?'} cells in series, `
                + `${battery.v_min}–${battery.v_max} V pack`
                + (battery.v_nom != null ? ` (nominal ${battery.v_nom} V)` : '')
                + '. The PWM source’s V_bus is prefilled from the NOMINAL — v_min/v_max are '
                + 'the corners a duty is judged against, not the voltage it runs at. '
              : 'This machine has no battery yet. ')
            + (batCtx.canWrite && batCtx.die && batCtx.config
                ? `Click to edit — it is saved on the configuration ${batCtx.die}/${batCtx.config}, `
                  + 'so the catalog, Configure and the datasheet see the same pack.'
                : 'Click to set one — it is remembered for THIS motor in this browser '
                  + '(the vendor’s catalog copy is untouched).')}>
            <Box onClick={() => setBatOpen(true)}
              sx={{ display: 'flex', alignItems: 'baseline', gap: 0.75, mb: 1.25,
                px: 1, py: 0.5, borderRadius: 0.5, cursor: 'pointer',
                border: '1px solid var(--line)', bgcolor: 'var(--panel-2)',
                '&:hover': { borderColor: 'var(--text-4)' } }}>
              <Typography sx={{ fontSize: 9.5, color: 'var(--text-4)', textTransform: 'uppercase',
                letterSpacing: '0.06em' }}>battery</Typography>
              <Typography sx={{ fontSize: 11, fontWeight: 700,
                color: battery ? '#f59e0b' : 'var(--text-4)' }}>
                {batteryChipLabel(battery)}
              </Typography>
              {/* no machine tag on the chip (user 2026-09-09) — the tooltip
                  says where the pack is saved */}
            </Box>
          </Tooltip>
          {batMsg && (
            <Typography sx={{ fontSize: 10, mb: 1,
              color: batMsg.startsWith('✗') ? '#fca5a5' : '#38bdf8' }}>
              {batMsg}
            </Typography>
          )}
          <BatteryDialog open={batOpen}
            configName={batCtx.canWrite && batCtx.die && batCtx.config
              ? `${batCtx.die}/${batCtx.config}`
              : (batCtx.die && batCtx.config ? `${batCtx.die}/${batCtx.config} (your copy)`
                                             : (getActiveMotor()?.name ?? 'this motor'))}
            initial={battery as BatteryValue | null}
            onClose={() => setBatOpen(false)}
            onSave={(v) => { void saveBattery(v as CellSpec); }} />

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            {/* Drive mode: imposed sinusoidal current (design work) vs imposed
                sinusoidal voltage (FOC-drive verification — currents become the
                machine's own response incl. back-EMF-harmonic parasitics). */}
            <Box sx={{ display: 'flex', gap: 0.5 }}>
              {(['motor', 'generator'] as const).map(m => (
                <Button key={m} size="small" fullWidth disabled={isRunning}
                  variant={opMode === m ? 'contained' : 'outlined'}
                  color={m === 'generator' ? 'warning' : 'primary'}
                  onClick={() => setOpMode(m)}
                  sx={{ fontSize: '0.7rem', textTransform: 'none', py: 0.3 }}>
                  {m === 'motor' ? 'Motor' : 'Generator'}
                </Button>
              ))}
              <HelpTip title={'Generator mode drives the SAME gamma shifted 180 deg el: the current '
                + 'vector opposes the EMF, torque is braking (negative), mechanical power flows in and '
                + 'the efficiency becomes P_electrical_out / P_mechanical_in. Everything else — current, '
                + 'speed, winding, d-axis — stays exactly as set here.'} />
            </Box>
            <Box sx={{ display: 'flex', gap: 0.5 }}>
              {/* The ideal sinusoidal VOLTAGE drive is HIDDEN from the menu,
                  not deleted (user request) — everything behind it stays; the
                  PWM source below drives the same circuit with a real
                  inverter's chopped voltage. */}
              {(['current'] as const).map(m => (
                <Button key={m} size="small" fullWidth disabled={isRunning || fitBusy}
                  variant={drive === m && targetKind === 'off' ? 'contained' : 'outlined'}
                  onClick={() => { setDrive(m); setTargetKind('off'); }}
                  sx={{ fontSize: '0.7rem', textTransform: 'none', py: 0.3 }}>
                  Sine current
                </Button>
              ))}
              <Button size="small" fullWidth disabled={isRunning || fitBusy}
                variant={targetKind !== 'off' ? 'contained' : 'outlined'}
                onClick={() => { setDrive('current'); setTargetKind(k => (k === 'off' ? 'nm' : k)); }}
                sx={{ fontSize: '0.7rem', textTransform: 'none', py: 0.3 }}>
                Target T / P
              </Button>
              <HelpTip title={'Sine current: imposed sinusoidal phase currents — the ideal ' +
                'source, and the right one for design work. PWM inverter: an ideal two-level ' +
                'stage chops the DC bus, so the currents are the machine’s own response and ' +
                'carry the real switching ripple (and what it costs in torque ripple, copper ' +
                'and core loss) — the honest answer for a low-inductance machine. BLDC 120°: ' +
                'six-step block commutation. Custom I: any periodic phase-current waveform.'} />
            </Box>
            {/* The non-ideal sources, on their own row so the labels fit. */}
            <Box sx={{ display: 'flex', gap: 0.5 }}>
              {([['pwm_voltage', 'PWM inverter'],
                 ['bldc_current', 'BLDC 120°'],
                 ['custom_current', 'Custom I']] as const).map(([m, label]) => (
                <Button key={m} size="small" fullWidth disabled={isRunning || fitBusy}
                  variant={drive === m ? 'contained' : 'outlined'}
                  color="secondary"
                  onClick={() => {
                    setDrive(m); setTargetKind('off');
                    // Seed the amplitudes from what is already on screen so the
                    // source arrives at the SAME operating point rather than at
                    // zero: the BLDC block that matches this run's copper loss
                    // (I_rms·√(3/2)), and the fundamental voltage from the last
                    // run's V₁ when the panel has one.
                    if (m === 'bldc_current' && !(iBlock > 0))
                      setIBlock(+(current * Math.sqrt(1.5)).toFixed(2));
                    if (m === 'pwm_voltage' && !(vPeak > 0)) setVPeak(30);
                  }}
                  sx={{ fontSize: '0.7rem', textTransform: 'none', py: 0.3 }}>
                  {label}
                </Button>
              ))}
            </Box>
            {drive === 'pwm_voltage' && (<>
              <Box sx={{ display: 'flex', gap: 1 }}>
                <TextField label="V bus (V)" type="number" size="small" fullWidth
                  value={vBus > 0 ? vBus : ''} onChange={e => setVBus(+e.target.value)}
                  placeholder={battery?.v_nom ? String(battery.v_nom) : 'no battery set'}
                  inputProps={{ step: 1, min: 0 }} disabled={isRunning}
                  InputLabelProps={{ shrink: true }}
                  InputProps={{ endAdornment: <HelpTip title={
                    'DC link voltage — each leg swings ±V_bus/2. '
                    + (battery?.v_nom
                        ? `Prefilled from this machine's pack NOMINAL voltage (`
                          + `${battery.cells ?? '?'}s, v_nom ${battery.v_nom} V; v_min/v_max are `
                          + `the corners a duty is judged against, not the voltage it runs at). `
                        : 'This machine has no battery yet — set one on the battery chip above, '
                          + 'or type the link voltage here. ')
                    + 'The switching ripple scales directly with it, and the modulation index '
                    + 'm = 2·V₁/V_bus must stay ≤1.15 — above that the run is refused rather '
                    + 'than silently overmodulated.'} /> }} />
                {/* The same frequency appears in more than one controller
                    class (16 kHz is both a SiC and an IGBT setting), so the
                    option VALUE carries the group index too — a Select with
                    duplicate values renders every match's label at once. */}
                <FormControl size="small" fullWidth disabled={isRunning}>
                  <InputLabel>f switch</InputLabel>
                  <Select label="f switch"
                    value={(!fSwCustom && FSW_GROUPS[fSwGroup]?.values.includes(fSwitch))
                      ? `${fSwGroup}:${fSwitch}` : 'custom'}
                    onChange={e => {
                      const raw = String(e.target.value);
                      if (raw === 'custom') { setFSwCustom(true); return; }
                      const [gi, hz] = raw.split(':');
                      setFSwCustom(false); setFSwGroup(+gi); setFSwitch(+hz);
                    }}>
                    {FSW_GROUPS.flatMap((g, gi) => [
                      <ListSubheader key={g.label} sx={{ fontSize: 10.5, lineHeight: '22px' }}>
                        {g.label}
                      </ListSubheader>,
                      ...g.values.map(v => (
                        <MenuItem key={`${gi}:${v}`} value={`${gi}:${v}`} sx={{ fontSize: 12 }}>
                          {fswLabel(v)}{v === g.def ? ' · typical' : ''}
                        </MenuItem>
                      )),
                    ])}
                    <MenuItem value="custom" sx={{ fontSize: 12 }}>custom…</MenuItem>
                  </Select>
                </FormControl>
                <HelpTip title={'Carrier frequency, from the settings real controllers offer: '
                  + FSW_GROUPS.map(g => `${g.label} — ${g.hint}`).join('; ')
                  + '. It is SNAPPED to a whole number of carriers per electrical period '
                  + '(synchronous PWM — the reported period has to repeat), and the effective '
                  + 'value comes back with the result. Pick "custom…" for anything off the list.'} />
              </Box>
              {(fSwCustom || !FSW_ALL.includes(fSwitch)) && (
                <TextField label="f switch (Hz)" type="number" size="small" fullWidth
                  value={fSwitch} onChange={e => setFSwitch(+e.target.value)}
                  inputProps={{ step: 1000, min: 0 }} disabled={isRunning}
                  InputProps={{ endAdornment: <HelpTip title={
                    'Any carrier the backend can build. Snapped to a whole number of carriers '
                    + 'per electrical period; the effective frequency is reported.'} /> }} />
              )}
            </>)}
            {drive === 'bldc_current' && (
              <TextField label="I block, flat top (A)" type="number" size="small" fullWidth
                value={iBlock} onChange={e => setIBlock(+e.target.value)}
                inputProps={{ step: 1, min: 0 }} disabled={isRunning}
                InputProps={{ endAdornment: <HelpTip title={
                  'FLAT-TOP terminal current of the 120° block — NOT an rms and not a sinusoid '
                  + 'peak: it is the number a block-commutated controller\'s current limit is set '
                  + 'to. A 120° block of amplitude I carries I·√(2/3) = 0.8165·I rms, so the '
                  + `copper-loss-matched equivalent of the ${current.toFixed(1)} Arms sine run is `
                  + `${(current * Math.sqrt(1.5)).toFixed(1)} A here (I_rms·√(3/2)); its `
                  + 'fundamental is I·2√3/π = 1.103·I. Use the matched value if you want the two '
                  + 'waveforms compared at the same watts in the winding. γ is the commutation '
                  + 'advance, in the same frame as every other source.'} /> }} />
            )}
            {drive === 'custom_current' && (<>
              <TextField label="Phase-A waveform — [[θe_deg, i_A], …]" size="small" fullWidth
                multiline minRows={2} maxRows={6} value={waveform}
                onChange={e => setWaveform(e.target.value)} disabled={isRunning}
                InputProps={{ sx: { fontSize: 10.5, fontFamily: 'monospace' },
                  endAdornment: <HelpTip title={
                    'JSON array of [electrical angle °, terminal phase current A] over ONE '
                    + 'electrical period; B and C are the same shape shifted ∓120°el and the '
                    + 'samples are linearly interpolated. γ still rotates it against the rotor, '
                    + 'so a sampled cos() reproduces the sine-current drive exactly. Up to 20k '
                    + 'points; gaps wider than 30° (the wrap counts) are refused.'} /> }} />
              <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
                <Button size="small" variant="outlined" fullWidth
                  disabled={isRunning || wfBusy}
                  onClick={() => {
                    setWfBusy(true); setWfMsg(null);
                    const q = new URLSearchParams({
                      v_bus: String(vBus || 0), f_switch: String(fSwitch),
                      I_phase_rms: String(current), gamma_deg: String(phaseOffset),
                      rpm: String(rpm),
                    });
                    fetch(`${API}/api/simulation/pwm_waveform?${q}`)
                      .then(async r => {
                        const j = await r.json();
                        if (!r.ok) throw new Error(j?.detail ?? r.statusText);
                        setWaveform(JSON.stringify(j.waveform));
                        setWfMsg(`✓ ${j.n_samples} pts · ripple ${j.I_ripple_pp_pct}% pp `
                          + `· THD ${j.I_thd_pct}% · f_sw ${fswLabel(j.f_switch_eff_Hz)} eff`);
                      })
                      .catch(e => setWfMsg(`✗ ${e.message}`))
                      .finally(() => setWfBusy(false));
                  }}
                  sx={{ fontSize: '0.7rem', textTransform: 'none', py: 0.3 }}>
                  {wfBusy ? 'Synthesising…' : 'Generate from PWM model'}
                </Button>
                <Button size="small" variant="outlined" component="label"
                  disabled={isRunning}
                  sx={{ fontSize: '0.7rem', textTransform: 'none', py: 0.3, minWidth: 76 }}>
                  Upload
                  <input type="file" hidden accept=".json,.csv,.txt"
                    onChange={e => {
                      const f = e.target.files?.[0]; if (!f) return;
                      f.text().then(t => {
                        // Accept a JSON array as-is, or two-column CSV/TSV.
                        const s = t.trim();
                        if (s.startsWith('[')) { setWaveform(s); setWfMsg('✓ loaded'); return; }
                        const rows = s.split(/\r?\n/).map(l => l.split(/[,;\t ]+/).filter(Boolean))
                          .filter(r => r.length >= 2 && Number.isFinite(+r[0]) && Number.isFinite(+r[1]))
                          .map(r => [+r[0], +r[1]]);
                        setWaveform(JSON.stringify(rows));
                        setWfMsg(`✓ ${rows.length} rows from ${f.name}`);
                      }).catch(() => setWfMsg('✗ could not read the file'));
                      e.target.value = '';
                    }} />
                </Button>
                <HelpTip title={'“Generate from PWM model” calls the PWM calculator: it '
                  + 'integrates the inverter’s chopped voltage through THIS machine’s measured '
                  + 'R, Ld, Lq and ψ_PM (constant-L circuit, no FEM, milliseconds) at the V_bus '
                  + 'and f_switch set on the PWM source, and pastes the resulting phase current '
                  + 'here. It is the cheap route the published PWM studies use; “PWM inverter” '
                  + 'is the honest one — there the currents come out of the field solve with '
                  + 'saturation and the real back-EMF in the loop. Upload takes a JSON array or '
                  + 'a two-column CSV.'} />
              </Box>
              {wfMsg && (
                <Typography sx={{ fontSize: 10, color: wfMsg.startsWith('✗') ? '#fca5a5' : '#38bdf8' }}>
                  {wfMsg}
                </Typography>
              )}
            </>)}
            {drive === 'current' && targetKind !== 'off' ? (
            /* TARGET drive: torque ⇄ power are mutually locked (P = T·ω), the
               same pattern as I rms ⇄ peak — edit either, the other follows.
               Torque is the stored canon; the current is fitted at Run. */
            <Box sx={{ display: 'flex', gap: 1 }}>
              <TextField label="Target torque (Nm)" type="number" size="small" fullWidth
                value={Number(targetValue.toFixed(2))}
                onChange={e => setTargetValue(+e.target.value)}
                inputProps={{ step: 10, min: 0 }} disabled={isRunning || fitBusy}
                InputProps={{ endAdornment: <HelpTip title={
                  'The current is FITTED to hit this torque: 2-3 cheap FEM probes '
                  + '(12 steps, demag as set, no eddy, ~40-80 s each) walk a secant '
                  + 'through the saturation curve, then the full run fires at the '
                  + 'fitted current. γ stays as set — find the optimal angle with a '
                  + 'sweep, as usual.'} /> }} />
              <TextField label="Target power (kW)" type="number" size="small" fullWidth
                value={Number((targetValue * (2 * Math.PI * rpm / 60) / 1000).toFixed(2))}
                onChange={e => {
                  const w = 2 * Math.PI * rpm / 60;
                  if (w > 1e-9) setTargetValue((+e.target.value * 1000) / w);
                }}
                inputProps={{ step: 1, min: 0 }} disabled={isRunning || fitBusy}
                InputProps={{ endAdornment: <HelpTip title={
                  'P = T·ω at the set speed — editing this recomputes the torque '
                  + 'target; the fit always converges on torque.'} /> }} />
            </Box>
            ) : (drive === 'voltage' || drive === 'pwm_voltage') ? (<>
            {/* PRE-FLIGHT card: one aligned row per launch criterion —
                status left, one-click fix right.  Replaces the pile of loose
                chips that accumulated here (user 2026-08-31: "лежит всё как
                попало").  Full explanations live in each row's tooltip. */}
            {(() => {
              type Row = { key: string; label: string; ok: boolean; warn?: boolean;
                           status: string; tip: string;
                           actions: Array<{ text: string; onClick: () => void;
                                            active?: boolean }> };
              const rows: Row[] = [];
              if (drive === 'pwm_voltage' && pwmCarriers > 0) {
                const fastN = snapSteps(4 * pwmCarriers) >= 4 * pwmCarriers
                  ? snapSteps(4 * pwmCarriers) : stepsMax;
                rows.push({
                  key: 'res', label: 'Resolution',
                  ok: stepsPerSwitch >= 16, warn: stepsPerSwitch >= 4,
                  status: stepsPerSwitch >= 16
                    ? stepsPerSwitch.toFixed(1) + '/carrier · resolved'
                    : stepsPerSwitch >= 8 ? stepsPerSwitch.toFixed(1) + '/carrier · partial'
                    : stepsPerSwitch >= 4 ? stepsPerSwitch.toFixed(1) + '/carrier · coarse pass'
                    : stepsPerSwitch.toFixed(1) + '/carrier · too few — run will be refused',
                  tip: fswLabel(pwmCarriers * frequency) + ' effective carrier / '
                    + frequency.toFixed(0) + ' Hz electrical = ' + pwmCarriers
                    + ' switching periods per electrical period; ' + steps + ' steps gives '
                    + stepsPerSwitch.toFixed(1) + ' samples per switching period. Below 4: '
                    + 'refused (Nyquist edge — the ripple averages away). 4–8: coarse first '
                    + 'pass, ripple-driven losses read ~15–20 % low (measured). 16: fully resolved.',
                  actions: [
                    { text: 'fast ' + fastN, onClick: () => setSteps(fastN),
                      active: steps === fastN },
                    { text: 'full ' + suggestSteps, onClick: () => setSteps(suggestSteps),
                      active: steps === suggestSteps },
                  ],
                });
                const m = vBus > 0 && vPeak > 0 ? 2 * vPeak / vBus : 0;
                // Only when it BLOCKS: a legal m is not a launch criterion
                // worth a standing row (user 2026-09-01: "вот это можно
                // выбросить" — the amber 3rd-harmonic note was daily noise;
                // the zone semantics stay in the V-peak tooltip).
                if (m > 1.15) rows.push({
                  key: 'mod', label: 'Modulation',
                  ok: false, warn: false,
                  status: 'm = ' + m.toFixed(2) + ' · over limit — run will be refused',
                  tip: 'Modulation index m = 2·V_peak/V_bus. Up to 1.00 plain linear; '
                    + '1.00–1.15 needs third-harmonic injection (allowed); above 1.15 the '
                    + 'inverter cannot form the fundamental and the solver refuses the run.',
                  actions: [{ text: 'set ' + (0.5 * vBus).toFixed(1) + ' V',
                              onClick: () => setVPeak(+(0.5 * vBus).toFixed(1)) }],
                });
              }
              if ((drive === 'pwm_voltage' || drive === 'voltage') && v1Seed) {  // no seed -> no row: absence is not an alert
                rows.push({
                  key: 'seed', label: 'V\u2081 seed',
                  ok: v1SeedMatches, warn: !v1SeedMatches,
                  status: v1SeedMatches
                      ? v1Seed.pk.toFixed(1) + ' V @ ' + v1Seed.dl.toFixed(1) + '° · this point'
                      : 'from ' + v1Seed.rpm.toFixed(0) + ' rpm / ' + v1Seed.iA.toFixed(1)
                        + ' A — rerun Sine current here',
                  tip: 'Two-pass workflow: run the point with Sine current first; its '
                    + 'fundamental voltage (V\u2081, \u03b4) is extracted and applied here '
                    + 'automatically for the same operating point. A seed from another point '
                    + 'misses the torque (the EMF scales with speed), so it is offered, not applied.',
                  actions: [{ text: 'apply',
                              onClick: () => { setVPeak(v1Seed.pk); setVDelta(v1Seed.dl); } }],
                });
              }
              // ── CHARGING THE PACK ────────────────────────────────────
              // Generator + an imposed voltage + a battery is exactly the
              // boost-mode case: the winding is the boost inductor, the
              // modulation index sets the step-up, and the pack is a source
              // with a resistance rather than a stiff rail.  The row appears
              // only for that combination, and it carries the two things that
              // combination needs: whether the bus is being iterated against
              // the pack, and the button that hunts for the most charge power
              // this speed can deliver.
              if (opMode === 'generator'
                  && (drive === 'pwm_voltage' || drive === 'voltage')) {
                const hasBat = !!battery && Number.isFinite(Number(battery.v_nom ?? battery.v_max));
                rows.push({
                  key: 'charge', label: 'Charging',
                  ok: hasBat, warn: !hasBat,
                  status: !hasBat
                      ? 'no battery on this machine — set one to get the charging card'
                      : (busCouple
                          ? 'bus iterated against the pack (V_oc + I·R)'
                          : 'stiff bus at ' + vBus.toFixed(1) + ' V — pack R ignored'),
                  tip: 'Generator into the machine’s own pack through the same bridge: the '
                    + 'winding IS the boost inductor and the modulation index sets the step-up, so '
                    + 'charging works with the EMF below V_bus. Bus coupling re-solves the whole '
                    + 'transient two to four times until V_bus = V_oc + I_charge·R_pack settles '
                    + '(charging lifts the terminal it charges into). "Max charge" then searches '
                    + '(V₁, δ) for the most watts this speed can put in, held under the '
                    + 'panel’s current and the pack’s charge limit — a dozen coarse '
                    + 'solves plus one confirm at full resolution, so it is not a quick press.',
                  actions: hasBat
                    ? [{ text: busCouple ? 'stiff bus' : 'couple bus',
                         onClick: () => setBusCouple(!busCouple), active: busCouple },
                       { text: chargeMaxOnce ? 'searching…' : 'max charge',
                         onClick: () => launchRun(false, true), active: chargeMaxOnce }]
                    : [],
                });
              }
              if (!rows.length) return null;
              return (
                <Box sx={{ border: '1px solid var(--panel)', borderRadius: 1,
                  px: 1, py: 0.5, display: 'flex', flexDirection: 'column', gap: 0.25 }}>
                  {rows.map(r => (
                    <Box key={r.key} sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                      <Typography sx={{ fontSize: 10, color: 'var(--text-3)', width: 70,
                        flexShrink: 0 }}>{r.label}</Typography>
                      <Tooltip placement="right" title={r.tip}>
                        <Typography sx={{ fontSize: 10.5, fontWeight: 600, flex: 1,
                          color: r.ok ? '#4ade80' : r.warn ? '#fbbf24' : '#f87171' }}>
                          {r.ok ? '\u2713 ' : r.warn ? '\u25b3 ' : '\u2717 '}{r.status}
                        </Typography>
                      </Tooltip>
                      {r.actions.map(a => (
                        <Chip key={a.text} size="small" clickable
                          variant={a.active ? 'filled' : 'outlined'}
                          color={a.active ? 'primary' : 'default'}
                          onClick={a.onClick} label={a.text}
                          sx={{ fontSize: 9.5, height: 18 }} />
                      ))}
                    </Box>
                  ))}
                </Box>
              );
            })()}
            <TextField label="V phase peak (V)" type="number" size="small" fullWidth
              value={vPeak} onChange={e => setVPeak(+e.target.value)}
              inputProps={{ step: 1, min: 0, max: 2000 }} disabled={isRunning}
              InputProps={{ endAdornment: <HelpTip title={'Amplitude of the FUNDAMENTAL phase ' +
                'voltage. Under PWM this is what the inverter must APPLY: the modulator’s ' +
                'sampled-reference delay and gain are compensated so it lands here, and what it ' +
                'really applied is measured and reported with the result. ' +
                'Tip: run a current-drive simulation first — its V₁ is the natural starting value.'} /> }}/>
            <TextField label="δ — voltage angle (°el)" type="number" size="small" fullWidth
              value={vDelta} onChange={e => setVDelta(+e.target.value)}
              inputProps={{ step: 5, min: -180, max: 180 }} disabled={isRunning}
              InputProps={{ endAdornment: <HelpTip title={'Voltage-vector angle in the same electrical frame as γ ' +
                '(0° = q-axis). The load angle: more δ → more torque until pull-out.'} /> }}/>
            </>) : (
            /* RMS ↔ peak are mutually locked (peak = rms·√2), same pattern as
               Speed ↔ Frequency below: edit either, the other recomputes.
               The SOLVER input stays the RMS value — peak is a pure UI view.
               PEAK comes FIRST (user's standing choice, 2026-08-22): the
               inverter and ANSYS both speak amplitude, so peak is the primary
               field everywhere current is entered. */
            <Box sx={{ display: 'flex', gap: 1 }}>
              <TextField label="I phase peak (A)" type="number" size="small" fullWidth
                value={Number((current * Math.SQRT2).toFixed(2))}
                onChange={e => setCurrent(+e.target.value / Math.SQRT2)}
                inputProps={{ step: 5, min: 0, max: 707 }} disabled={isRunning}
                InputProps={{ endAdornment: <HelpTip title={'Peak of the sinusoidal phase current = RMS·√2. Editing this recomputes the RMS — the solver always receives RMS.'} /> }}/>
              <TextField label={starDelta === 'delta' ? 'I line RMS (Arms) — winding ÷√3' : 'I phase RMS (Arms)'} type="number" size="small" fullWidth
                value={Number(current.toFixed(2))} onChange={e => setCurrent(+e.target.value)}
                inputProps={{ step: 5, min: 0, max: 500 }} disabled={isRunning}
                InputProps={{ endAdornment: <HelpTip title={
                  `TERMINAL phase current. With ${connection} (${nParallel} parallel `
                  + `path${nParallel > 1 ? 's' : ''}) each coil carries I/${nParallel}: `
                  + `I coil = ${I_coil_rms.toFixed(1)} Arms (${I_coil_peak.toFixed(1)} A peak) → sent to solver. `
                  + `Comparing with ANSYS Maxwell: its winding must have Number of Parallel `
                  + `Branches = ${nParallel}, else Maxwell drives every coil at the full `
                  + `phase current and reports ~${nParallel}x the torque for the same input.`} /> }}/>
            </Box>
            )}
            {/* The current field above does NOT drive the block or the imposed
                waveform — say so once, rather than let it look like it does. */}
            {(drive === 'bldc_current' || drive === 'custom_current') && (
              <Typography sx={{ fontSize: 10, color: 'var(--text-4)', mt: -0.75 }}>
                {drive === 'bldc_current'
                  ? 'reference only — the block runs at I block above'
                  : 'reference only — the run uses the waveform above'}
              </Typography>
            )}
            {/* Frequency ↔ Speed are mutually locked:
                  f_elec [Hz]  =  rpm × pole_pairs / 60
                  rpm          =  f_elec × 60 / pole_pairs
                Editing one immediately recomputes the other. */}
            <TextField label="Speed (rpm)" type="number" size="small" fullWidth
              value={Number(rpm.toFixed(0))}
              onChange={e => {
                const r = +e.target.value;
                setRpm(r);
                setFrequency(+(r * polePairs / 60).toFixed(2));
              }}
              inputProps={{ step: 100, min: 0 }} disabled={isRunning}
              InputProps={{ endAdornment: <HelpTip title={`electrical f = ${Number(frequency.toFixed(1))} Hz`} /> }}/>
            {/* Frequency is DERIVED from rpm (f = rpm × pole_pairs / 60) — read-only,
                single source is the speed above.  Editing rpm recomputes it. */}
            <TextField label="Frequency (Hz) — derived" type="number" size="small" fullWidth
              value={Number(frequency.toFixed(2))}
              disabled
              InputProps={{ endAdornment: <HelpTip title={`f = rpm × ${polePairs} / 60 — derived from speed`} /> }}/>
            {/* The "Rotor Angle (°)" initial-position field was removed —
                in the auto-run architecture the Field Animation sweeps the
                whole 25.71° electrical period itself, so picking a single
                starting angle adds nothing.  Only the current-vector
                load-angle γ is user-facing now. */}

            {/* Current-vector angle (γ) — the only operating-point control
                left.  Convention:  I_total points at (90° + γ) electrical
                relative to the rotor d-axis.  γ = 0 keeps I purely on the
                q-axis (max torque); γ < 0 advances the vector for field
                weakening at high speed; γ > 0 retards (used to flatten
                cogging-torque ripple in some control schemes). */}
            <TextField
              label="γ — current-vector offset from q-axis (°)"
              type="number" size="small" fullWidth
              value={phaseOffset}
              onChange={e => setPhaseOffset(+e.target.value)}
              inputProps={{ step: 5, min: -90, max: 90 }}
              InputProps={{ endAdornment: <HelpTip title={`I direction = 90° + γ elec from d-axis.  ` +
                          `γ=0 → q-axis (max torque),  γ=±90 → d-axis (field weakening).  ` +
                          `SAME near-zero range in BOTH modes — Generator adds its 180° internally, ` +
                          `never type it into γ.`} /> }}
              disabled={isRunning}
            />

            {/* ── Copper-loss physics: temperature + end-winding ──
                The 2-D field only sees the in-slot (active) copper.  ρ_Cu rises
                with coil temperature, and the end-turns that loop outside the
                stack add series resistance the 2-D model can't see. */}
            {/* With the coupled switch ON these two are OUTPUTS of the loop
                (user 2026-09-08: "когда я нажимаю каплинг, она не должна быть
                редактируемой, она вычисляется"): read-only, showing the
                converged values — adopted from the run's answer and, on
                mount / switch-on, from the server's last coupled run. */}
            <TextField
              label={coupled ? 'Coil temperature (°C) — from the coupled loop' : 'Coil temperature (°C)'}
              type="number" size="small" fullWidth
              value={coilTemp}
              onChange={e => setCoilTemp(+e.target.value)}
              inputProps={{ step: 10, min: -40, max: 220, readOnly: coupled }}
              InputProps={{ endAdornment: <HelpTip title={coupled
                ? 'Computed by the coupled EM ↔ thermal loop: the winding AVERAGE the last converged run settled on. Switch the loop off to type a temperature.'
                : `ρ_Cu(T): +0.393 %/°C from 20 °C → higher copper loss`} /> }}
              disabled={isRunning}
              sx={coupled ? { '& input': { color: '#34d399' } } : undefined}
            />
            {/* MAGNET temperature.  Empty = the assigned card as the library
                quotes it — the only behaviour this app had before 2026-09-08 —
                so nothing moves for anyone who leaves it alone. */}
            <TextField
              label={coupled ? 'Magnet temperature (°C) — from the coupled loop' : 'Magnet temperature (°C) — empty = card'}
              type="text" size="small" fullWidth
              value={magnetTempC}
              placeholder="as quoted"
              onChange={e => setMagnetTempC(e.target.value)}
              inputProps={{ readOnly: coupled }}
              InputProps={{ endAdornment: <HelpTip title={coupled
                ? 'Computed by the coupled EM ↔ thermal loop: the magnet AVERAGE the last converged run settled on (the hottest element is on the summary card). Switch the loop off to type a temperature.'
                : 'Corrects the assigned magnet to this temperature: Br, the coercivity '
                + 'and the whole demagnetisation curve including the knee. Empty = the '
                + "card as the library quotes it (every run before this field existed). "
                + 'The Coupled thermal switch below writes its converged value here.'} /> }}
              disabled={isRunning}
              sx={coupled ? { '& input': { color: '#34d399' } } : undefined}
            />
            <TextField
              label="End-winding factor k_end (editable)"
              type="number" size="small" fullWidth
              value={endWinding}
              onChange={e => setEndWinding(+e.target.value)}
              inputProps={{ step: 0.05, min: 0, max: 6 }}
              InputProps={{ endAdornment: <HelpTip title={`End-winding copper: scales the coil resistance/loss by the end-turn length.` +
                          ` k_end = (π·(wire_w/2 + tooth_w/2) + L_stack)/L_stack = ${endWindingGeo ? endWindingGeo.toFixed(3) : '—'} for THIS geometry.` +
                          ` Re-derived on EVERY geometry change and used in all simulations AND optimizations —` +
                          ` it scales the copper loss and phase resistance for the end-turns a 2-D solve can't see.` +
                          ` Type a value to override it until the geometry changes again.`} /> }}
              disabled={isRunning}
            />
            <TextField
              label="d-axis DAXIS (°) — empty = measure"
              type="text" size="small" fullWidth
              value={daxisDeg}
              placeholder={lastDaxis != null ? `${lastDaxis.toFixed(4)} (measured)` : 'auto'}
              onChange={e => {
                const v = e.target.value;
                setDaxisDeg(v);
                // EXPLICIT write, debounced: a number pins, an emptied field
                // clears — but only a hand that typed here does either.
                if (daxisPatchTimer.current) clearTimeout(daxisPatchTimer.current);
                daxisPatchTimer.current = setTimeout(() => {
                  const t = v.trim();
                  const body = (t === '' || !Number.isFinite(Number(t)))
                    ? { daxis_deg: '' } : { daxis_deg: Number(t) };
                  fetch(`${API}/api/simulation/config`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                  }).catch(() => {});
                }, 600);
              }}
              InputProps={{ endAdornment: <HelpTip title={
                'The zero γ is measured FROM. γ is the angle from the q-axis, so the solver has to know'
                + ' where the q-axis sits in this cross-section — it finds it by holding the current still,'
                + ' turning the rotor and taking the peak of the phase-A flux linkage (a 24-frame no-load'
                + ' solve, ~39 s, cached per geometry).'
                + (lastDaxis != null ? ` Last run solved at ${lastDaxis.toFixed(4)}°.` : '')
                + ' Type a number to PIN it: the calibration is then skipped entirely and your value is used'
                + ' as is — right when the zero of this topology is already known (24s/28p sits on 60.000° across'
                + ' every cross-section measured here). Clear the box to measure it again. Every result says'
                + ' which of the two it used.'} /> }}
              disabled={isRunning}
            />

            {/* Cooling inputs live on the Thermal tab (2026-09-07), together
                with the solve they are inputs to. */}

            {/* Slot currents bar / PINN Training settings / RUN SIMULATION
                button removed.  The Simulation tab now uses real FEM via the
                Physics Dashboard auto-runs on the right — no PINN training
                step is needed.  The instantaneous phase currents are still
                visible inside the Transient I(t) chart with full per-step
                detail. */}
          </Box>
        </Box>

        {srvErr && (
          <Alert severity="error" sx={{ fontSize: 11 }}>{srvErr}</Alert>
        )}

        {/* ── Run Simulation — launches ONE FEM solve with the current
              operating point + mesh settings.  Flows directly under the fields
              above (the panel scrolls if needed); no bottom-pin, which used to
              leave a big empty gap now that the cooling/PINN sections are gone. ── */}
        <Box sx={{ pt: 1 }}>
          <FormControl size="small" fullWidth sx={{ mb: 1.25 }} disabled={simBusy}>
            <InputLabel id="steps-pp-label">Steps per electrical period</InputLabel>
            <Select
              labelId="steps-pp-label"
              label="Steps per electrical period"
              value={stepsOptions.includes(steps) ? steps : snapSteps(steps)}
              onChange={e => setSteps(Number(e.target.value))}
              endAdornment={
                <InputAdornment position="end" sx={{ mr: 2.5 }}>
                  <HelpTip title={`Transient time resolution. Up to ${stepsMax} the count must be a DIVISOR of ${stepsMax} — the slip-ring nodes per electrical period for this machine — so the rotor lands on whole mesh nodes. ABOVE ${stepsMax} the solver raises the slip-ring density to match the request exactly (needed to resolve a PWM carrier); the band mesh is then denser and the run slower.`} />
                </InputAdornment>
              }
            >
              {stepsOptions.map(v => (
                <MenuItem key={v} value={v}>
                  {v}{v > stepsMax ? '  · raises the slip ring' : ''}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          {/* Per-element irreversible demagnetisation (Ansys-style).  A pre-pass
              sweeps the period at full Br, finds the worst demag field at every
              magnet element, and de-rates Br on the recoil line → the torque /
              back-EMF reflect the weakened magnets, plus a Demag-% map. */}
          <Tooltip title="Account for irreversible magnet demagnetisation. A pre-pass sweeps the whole period at full strength, finds the worst demagnetising field H at EVERY magnet element, and permanently de-rates Br along the recoil line where H crosses the BH-curve knee (per element, like Ansys). The torque and back-EMF then reflect the weakened magnets, and a Demag-% map is produced. COST — measured, not modest: the pre-pass is a WHOLE EXTRA PERIOD of FEM frames, so the run solves twice the steps you asked for, and each frame re-solves while the magnet is still moving. On the 40 mm 12s/14p at 0.6 mm mesh, 4 steps/period: 37 s off → 86 s on (2.3×). The line under the Run button shows the frame count your current settings imply." placement="right">
            <FormControlLabel
              sx={{ mt: -0.5, mb: 0.75, ml: 0.25 }}
              control={
                <Checkbox size="small" checked={demag}
                  onChange={e => setDemag(e.target.checked)}
                  disabled={simBusy}
                  sx={{ p: 0.5, color: 'var(--text-4)', '&.Mui-checked': { color: '#c084fc' } }} />
              }
              label={
                <Typography variant="caption" sx={{ color: demag ? '#c084fc' : 'var(--text-2)' }}>
                  Demagnetisation — de-rate torque (FEM, per element)
                </Typography>
              }
            />
          </Tooltip>
          {/* The "Coupled eddy solve" checkbox lived here until 2026-09-05;
              the coupled σ·∂A/∂t solve is now always on (user: "я всегда её
              использую") — see `eddyCoupled` above. */}
          {/* The torque band-limit checkbox that lived here was removed at the
              user's request (2026-07-29): it was a P1-era crutch — P2's raw
              ripple is mesh-convergent and honest, so the headline is ALWAYS
              the raw torque now.  The backend still returns both series and
              T_noise_floor_pct, so the 6·k decomposition remains inspectable
              in the harmonic chart without a mode switch. */}
          {/* ── What this run will actually SOLVE, before it is launched ──────
              A run does not solve `steps` frames.  fem_transient_sliding_band
              prepends a whole extra period when demag is on (_dmskip, the
              settling pass whose frames are stripped from the result) and
              warm-up frames at θ<0 when the coupled eddy solve is on — so with
              both ticked at 36 steps it solves 74+, and nothing on screen said
              so.  The rule is mirrored here (same shape as SLIP_PER_PERIOD
              above) and RECONCILED after every run against the solver's own
              n_frames_solved, so the seconds-per-frame quoted is one this
              machine actually produced.  No history yet → the frame count only;
              a made-up time is worse than no time.
              The eddy warm-up is ADAPTIVE (the solver keeps solving discarded
              frames until the σ·∂A/∂t start-up transient is quiet, up to one
              electrical period), so it CANNOT be computed here: a big solid
              shaft needs a whole period where a small one needs 2.  Quote the
              count the last run on this machine actually needed, and the probe
              length when there is no history — never a constant that the
              solver stopped honouring. */}
          {(() => {
            // BOTH imposed-voltage sources march the settling periods: the
            // currents are circuit state either way, and the PWM run pays the
            // same ten periods (times its much finer step count — which is why
            // this line is worth reading before launching one).
            const vdrive    = drive === 'voltage' || drive === 'pwm_voltage';
            // _vskip: settling PERIODS, because the currents are state.  The
            // sinusoid marches 10 at the run's own step.  PWM (solver
            // 2026-09-02): periods = clamp(ceil(3·τ_e/T_e), 2, 12) with τ_e =
            // max(Ld, Lq)/R from the last summary on this machine (2 when
            // there is none), marched COARSE (largest divisor of `steps` ≤ 40)
            // when the run is a coarse/partial pass (< 16 steps per carrier),
            // plus a two-carrier fine pre-roll — mirrors _build_schedule.
            const vSettle = (() => {
              if (!vdrive) return 0;
              if (drive === 'voltage') return 10 * Math.max(2, steps);
              let per = 2;
              try {
                const ls = JSON.parse(localStorage.getItem('sim.lastSummary') || 'null');
                const L = Math.max(Number(ls?.Ld_mH) || 0, Number(ls?.Lq_mH) || 0) * 1e-3;
                const R = Number(ls?.R_phase_ohm) || 0;
                if (L > 0 && R > 0 && frequency > 0)
                  per = Math.min(12, Math.max(2, Math.ceil(3 * (L / R) * frequency)));
              } catch { /* no history → the static default */ }
              const carriers = Math.max(1, Math.round(fSwitch / Math.max(frequency, 1e-9)));
              if (steps / carriers >= 16) return per * Math.max(2, steps);
              let coarse = 1;
              for (let d = 1; d <= steps; d++) if (steps % d === 0 && d <= 40) coarse = d;
              const ratio = Math.max(1, Math.floor(steps / coarse));
              const preroll = Math.min(steps - ratio,
                ratio * Math.ceil((steps * 2 / carriers) / ratio));
              // the pre-roll is carved OUT of the last coarse period, not added
              return per * coarse - Math.floor(Math.max(0, preroll) / ratio) + Math.max(0, preroll);
            })();
            const dmSettle  = demag ? steps : 0;              // _dmskip
            const eddyWarm  = (eddyCoupled && !vdrive)
              ? Math.max(2, solveCost?.warm ?? 0) : 0;   // adaptive: >= the probe
            // Voltage drive also runs the matched-fundamental CURRENT-drive
            // reference for ΔP_harm (harm_ref): a second transient, same steps
            // and same demag, no eddy, no settling prefix.
            const refFrames = vdrive ? steps + dmSettle : 0;
            const framesSolved = steps + vSettle + dmSettle + eddyWarm + refFrames;
            const rate = (solveCost && solveCost.frames > 0)
              ? solveCost.wall_s / solveCost.frames : 0;
            const est = rate * framesSolved;
            const hhmm = (s: number) => (s >= 90
              ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`
              : `${Math.round(s)} s`);
            // ONE short neutral line (the amber multi-line breakdown read as a
            // warning — user asked for it gone); the full frame budget lives
            // in the tooltip.
            const detail = `${steps} reported`
              + (vSettle ? ` + ${vSettle} voltage settling` : '')
              + (dmSettle ? ` + ${dmSettle} demag settling` : '')
              + (eddyWarm ? ` + ${eddyWarm} eddy warm-up (adaptive)` : '')
              + (refFrames ? ` + ${refFrames} ΔP_harm reference` : '')
              + (rate > 0 ? ` · at the last run's ${rate.toFixed(1)} s/frame` : '');
            return (
              <Typography sx={{ fontSize: 10, mb: 0.75, color: 'var(--text-4)' }}>
                {rate > 0 ? `≈ ${hhmm(est)} · ` : ''}{framesSolved} frames
                <Tooltip title={detail} placement="top">
                  <span style={{ marginLeft: 4, cursor: 'help' }}>ⓘ</span>
                </Tooltip>
                {/* The ETA above is a clean-machine estimate.  The transient
                    keeps its OWN lock — it does not wait in the field-solve
                    queue — but it does share the CPU with whatever is in it,
                    so the estimate is optimistic while that is non-zero, and
                    the honest thing is to say which of the two it is. */}
                {fieldJobs > 0 && (
                  <span style={{ color: '#fbbf24' }}>
                    {' · sharing the CPU with '}{fieldJobs} field solve{fieldJobs > 1 ? 's' : ''}
                  </span>
                )}
              </Typography>
            );
          })()}
          {fieldJobs > 0 && (
            <Tooltip placement="top" title={
              <Box sx={{ whiteSpace: 'pre-line', fontSize: 11 }}>
                {(fieldBusy?.items ?? [])
                  .map(it => `${it.state === 'queued' ? 'queued ' : 'solving'} · `
                    + `${it.kind} · ${Math.round(it.since_s)}s · ${it.key_short}`
                    + (it.waiters ? ` · ${it.waiters} duplicate request(s) riding on it` : ''))
                  .join('\n')
                + `\n\nField views (J⟳ / Loss / Temp / Demag) solve on this same `
                + `server, at most ${fieldBusy?.limit ?? 2} at a time; the rest queue. `
                + `Your transient does not wait for them — it has its own lock — `
                + `but it shares the machine, so it will run slower until they finish.`}
              </Box>}>
              <Typography sx={{ fontSize: 11, color: '#fbbf24', textAlign: 'center',
                mb: 0.75, cursor: 'help' }}>
                server busy: {fieldBusy?.solving ?? 0} field solve
                {(fieldBusy?.solving ?? 0) === 1 ? '' : 's'}
                {' '}({fieldBusy?.queued ?? 0} queued) ⓘ
              </Typography>
            </Tooltip>
          )}
          {/* ── The EM<->thermal orchestrator, on or off ────────────────────
              User 2026-09-08: "не надо всё смешивать, нужен оркестратор" and
              "чтобы можно было его включать и отключать".  One switch, one
              short line, everything else in the tooltip (UI rule). */}
          <Tooltip placement="right" title={
            'Run EM → Thermal → EM until the winding and magnet temperatures '
            + 'settle (±2 K). Thermal boundary conditions = the Thermal tab\'s '
            + 'current settings. Off = temperatures are the fields on this tab.'}>
            <FormControlLabel
              sx={{ ml: 0.25, mb: 0.5 }}
              control={
                <Switch size="small" checked={coupled}
                  onChange={e => setCoupled(e.target.checked)}
                  disabled={simBusy} />
              }
              label={
                <Typography variant="caption"
                  sx={{ color: coupled ? '#34d399' : 'var(--text-2)' }}>
                  Coupled thermal — solve for the temperatures
                </Typography>
              }
            />
          </Tooltip>
          {/* ── …AND WHICH QUESTION IT ANSWERS (owner 2026-09-18) ───────────
              "или считать до конца стабилизации температуры, или считать до
              лимитов и находить время работы при заданных условиях".  A CHOICE,
              not a rule the backend applies by itself — one short line, both
              modes in the HelpTip (UI rule). */}
          {coupled && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
              ml: 0.25, mb: 0.75 }}>
              <Typography variant="caption" sx={{ color: 'var(--text-2)' }}>
                Solve to
              </Typography>
              <Select size="small" value={solveTo} disabled={simBusy}
                onChange={e => setSolveTo(
                  e.target.value === 'limits' ? 'limits' : 'steady')}
                sx={{ fontSize: 11, '& .MuiSelect-select': { py: 0.25 } }}>
                <MenuItem value="steady" sx={{ fontSize: 11 }}>
                  steady state
                </MenuItem>
                <MenuItem value="limits" sx={{ fontSize: 11 }}>
                  the limits (time at this power and cooling)
                </MenuItem>
              </Select>
              <HelpTip title={
                'Steady state: iterate until the winding, the magnets and the '
                + 'bearing seat stop moving, and report that state — even when '
                + 'it is past a limit.\n\n'
                + 'The limits: stop at the FIRST limit any part reaches and '
                + 'report the machine at that moment — "Runs 24 s from cold at '
                + 'this power and cooling, then the winding reaches 200 °C". '
                + 'Torque, losses, efficiency, KV/Kt, demagnetisation and the '
                + 'maps are all that state. A point that is inside every limit '
                + 'comes back as a steady answer and says so.'} />
            </Box>
          )}
          {simBusy ? (
            <Button
              fullWidth
              variant="contained"
              onClick={() => { window.dispatchEvent(new CustomEvent('sim:stop')); setCancelledRun(true); }}
              startIcon={<StopIcon />}
              sx={{
                py: 1.2, fontWeight: 700, fontSize: 13, letterSpacing: 0.5,
                textTransform: 'none', borderRadius: 2,
                bgcolor: '#dc2626', '&:hover': { bgcolor: '#b91c1c' },
                boxShadow: '0 2px 12px rgba(220,38,38,0.4)',
              }}
            >
              Stop Simulation
            </Button>
          ) : (
            <Button
              fullWidth
              variant="contained"
              disabled={fitBusy}
              onClick={() => {
                if (cancelledRun) { setAskResume(true); return; }
                if (drive === 'current' && targetKind !== 'off') { void fitAndRun(); return; }
                launchRun(true);
              }}
              startIcon={<PlayArrowIcon />}
              sx={{
                py: 1.2, fontWeight: 700, fontSize: 13, letterSpacing: 0.5,
                textTransform: 'none', borderRadius: 2,
                bgcolor: '#2563eb', '&:hover': { bgcolor: '#1d4ed8' },
                boxShadow: '0 2px 12px rgba(37,99,235,0.4)',
              }}
            >
              {fitBusy ? 'Fitting current…'
                : drive === 'current' && targetKind !== 'off'
                  ? `Fit ${Number(targetValue.toFixed(1))} Nm & Run`
                  : runNonce === 0 ? 'Run Simulation' : 'Re-run Simulation'}
            </Button>
          )}
          {fitMsg && (
            <Typography sx={{ fontSize: 10.5, textAlign: 'center', mt: 0.5,
              color: fitMsg.startsWith('✗') ? '#fca5a5' : '#38bdf8' }}>
              {fitMsg}
            </Typography>
          )}
          {/* WHY the run did not happen — one line, right under the button that
              was pressed.  The whole sentence is the tooltip (UI rule: one short
              line + tooltip, never a wall of text in the rail). */}
          {runNotice && !simBusy && (
            <Tooltip placement="top" title={runNotice.full}>
              <Typography sx={{ fontSize: 10.5, mt: 0.75, px: 1, py: 0.5,
                borderRadius: 1, cursor: 'help',
                color: runNotice.kind === 'error' ? '#fca5a5' : '#38bdf8',
                bgcolor: runNotice.kind === 'error'
                  ? 'rgba(239,68,68,0.10)' : 'rgba(56,189,248,0.10)',
                border: `1px solid ${runNotice.kind === 'error' ? '#7f1d1d' : '#0369a1'}` }}>
                {runNotice.kind === 'error' ? '⚠ not solved — ' : ''}{runNotice.text}
              </Typography>
            </Tooltip>
          )}
          {emptyStoreDuty && (
            <Typography component="div" sx={{ fontSize: 11, color: '#fbbf24', textAlign: 'center', mt: 0.75 }}
              title="This browser holds no panel settings (a reset profile or a new browser), so the fields show factory defaults. Nothing is applied by itself — click to pull the active duty's saved settings and operating point.">
              panel settings are empty — {' '}
              <span onClick={() => void restoreFromDuty()}
                style={{ cursor: 'pointer', textDecoration: 'underline', color: '#60a5fa' }}>
                restore from {emptyStoreDuty}
              </span>
            </Typography>
          )}
          <Typography sx={{ fontSize: 10, color: 'var(--text-4)', textAlign: 'center', mt: 0.75 }}>
            {simBusy
              ? 'Solving the transient — press Stop to cancel'
              : cancelledRun
                ? 'Stopped — Run to resume the finished frames or start fresh'
                : 'Edit γ / current / mesh settings, then launch one solve'}
          </Typography>
          {caches && (
            <Typography sx={{ fontSize: 10, color: 'var(--text-4)', textAlign: 'center', mt: 0.25 }}>
              <Tooltip placement="top" title={
                'Server-side physics caches.  Every Run solves and then REPLACES them: '
                + 'the field-view cache is emptied and the snapshot store is reduced to '
                + 'that run alone, so no older entry can be served beside the new one.  '
                + '"clear" empties them by hand — memory only, the saved last run and the '
                + 'optimizer cache on disk are untouched.'}>
                <span style={{ cursor: 'help' }}>
                  caches: field {caches.field_cache} · snapshots {caches.snapshots}
                  {caches.last_refreshed_by
                    ? ` · refreshed ${String(caches.last_refreshed_by).slice(11, 16)}`
                    : ''}
                </span>
              </Tooltip>
              {' · '}
              <span
                onClick={() => {
                  fetch(`${API}/api/simulation/caches/clear`, { method: 'POST' })
                    .then(r => (r.ok ? r.json() : null))
                    .then(j => { if (j) setCaches(j); })
                    .catch(() => { /* nothing user-facing */ });
                }}
                style={{ cursor: 'pointer', textDecoration: 'underline', color: '#60a5fa' }}>
                clear
              </span>
            </Typography>
          )}
        </Box>

        {/* ── Resume / fresh dialog (after a Stop) ── */}
        <Dialog open={askResume} onClose={() => setAskResume(false)}
          PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2 } }}>
          <DialogTitle sx={{ fontSize: 15, color: 'var(--text-0)' }}>Resume the stopped run?</DialogTitle>
          <DialogContent>
            <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>
              <b>Continue</b> keeps the solved frames · <b>Start fresh</b> recomputes the period.
            </Typography>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
            <Button onClick={() => launchRun(true)} sx={{ textTransform: 'none', color: 'var(--text-2)' }}>
              Start fresh
            </Button>
            <Button onClick={() => launchRun(false)} variant="contained"
              sx={{ textTransform: 'none', bgcolor: '#2563eb', '&:hover': { bgcolor: '#1d4ed8' } }}>
              Continue
            </Button>
          </DialogActions>
        </Dialog>
      </Box>

      {/* ── RIGHT: results ── */}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 3, display: 'flex', flexDirection: 'column', gap: 3 }}>
        {/* Live solve progress — PINNED to the very top of the page (sticky),
            visible without scrolling wherever the solve was launched from. */}
        {/* The ORCHESTRATOR's counter, only while the toggle is on: which
            iteration of how many.  It sits above the transient's own strip
            because the two answer different questions — "pass 2 of 6" and
            "frame 17 of 48" — and on a six-pass run the frame count alone
            cannot tell a slow loop from a stuck one.  Renders nothing when the
            endpoint reports no solve, so it costs an idle page one poll. */}
        {coupled && (
          <CommonProgressStrip endpoint="/api/coupled/progress" unit="steps"
            kindLabels={{ coupled: 'Coupled EM ↔ thermal' }} />
        )}
        <SolveProgressStrip runId={runNonce ? String(runNonce) : undefined} />

        {/* Header + Physics overview card removed by user request.
            • The "2D Magnetostatics / Governing equation / Rotor
              periodicity / Domains" block is dropped entirely.
            • Rotor-periodicity info now lives in the LEFT control
              panel, in a compact 2×2 grid right under the Solver
              badge.  See <Box>{Rotor Periodicity}</Box> above. */}
        <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
          borderRadius: 2, display: 'none' }}>
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1.5,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Governing Equation
          </Typography>
          <Box sx={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-2)', lineHeight: 2 }}>
            <Box>∂/∂x(ν ∂A_z/∂x) + ∂/∂y(ν ∂A_z/∂y) = −J_z</Box>
            <Box sx={{ color: 'var(--text-4)', fontSize: 10, mt: 0.5 }}>
              ν = reluctivity = 1/(μ₀ μᵣ) &nbsp;|&nbsp;
              B_x = ∂A_z/∂y &nbsp;|&nbsp;
              B_y = −∂A_z/∂x
            </Box>
          </Box>

          <Divider sx={{ borderColor: 'var(--panel)', my: 1.5 }}/>

          {/* Periodicity info */}
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Rotor Periodicity
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, mb: 1.5 }}>
            {[
              { label: 'Pole pairs',       value: polePairs.toString(),                    sub: `${numPoles} poles / 2` },
              { label: 'Electrical period', value: `${elecPeriod_deg.toFixed(2)}°`,        sub: `360° / ${polePairs}` },
              { label: 'Cogging period',    value: `${coggingPeriod_deg.toFixed(3)}°`,     sub: `360° / LCM(${numSlots},${numPoles})` },
              { label: 'Cogging per elec', value: Math.round(elecPeriod_deg / coggingPeriod_deg).toString(), sub: 'samples for full curve' },
            ].map(item => (
              <Box key={item.label} sx={{ bgcolor: 'var(--panel-2)',
                border: '1px solid var(--line-soft)', borderRadius: 1, p: 1 }}>
                <Typography sx={{ fontSize: 9, color: 'var(--text-4)', textTransform: 'uppercase',
                  letterSpacing: '0.08em' }}>{item.label}</Typography>
                <Typography sx={{ fontSize: 14, fontWeight: 700, color: '#93c5fd',
                  fontVariantNumeric: 'tabular-nums' }}>{item.value}</Typography>
                <Typography sx={{ fontSize: 9, color: 'var(--line)' }}>{item.sub}</Typography>
              </Box>
            ))}
          </Box>
          <Alert severity="info" sx={{ fontSize: 10, py: 0.5, mb: 1.5,
            '& .MuiAlert-message': { py: 0 } }}>
            Full T(θ) curve needs {Math.round(elecPeriod_deg / coggingPeriod_deg)} points × one simulation each,
            or one parametric PINN with θ as input.
          </Alert>

          <Divider sx={{ borderColor: 'var(--panel)', my: 1.5 }}/>

          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Domains
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
            {[
              { name: 'Stator Core', color: '#3b82f6',  pde: 'μᵣ = 5000' },
              { name: 'Air Gap',     color: 'var(--text-2)',  pde: 'μᵣ = 1' },
              { name: 'Rotor Core',  color: '#2563eb',  pde: 'μᵣ = 5000' },
              { name: 'Magnets',     color: '#ef4444',  pde: 'Br = 1.2 T' },
              { name: 'Windings',    color: '#f59e0b',  pde: 'J = ±J_peak' },
              { name: 'Shaft',       color: 'var(--text-3)',  pde: 'μᵣ = 1000' },
            ].map(d => (
              <Tooltip key={d.name} title={`PDE: ${d.pde}`} placement="top">
                <Chip label={d.name} size="small" sx={{
                  fontSize: 10, height: 20,
                  bgcolor: `${d.color}18`, color: d.color,
                  border: `1px solid ${d.color}44`,
                  cursor: 'help',
                }}/>
              </Tooltip>
            ))}
          </Box>
        </Paper>

        {/* Job progress */}
        {job && (
          <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2, borderRadius: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
              <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-4)',
                textTransform: 'uppercase', letterSpacing: 1 }}>
                Job {job.job_id}
              </Typography>
              {job.status === 'done'  && <CheckCircleIcon sx={{ fontSize: 16, color: '#4ade80' }}/>}
              {job.status === 'error' && <ErrorIcon       sx={{ fontSize: 16, color: '#f87171' }}/>}
              {isRunning && <CircularProgress size={14} sx={{ color: '#3b82f6' }}/>}
            </Box>

            <Box sx={{ display: 'flex', gap: 1, mb: 1.5 }}>
              <Chip
                label={job.status.toUpperCase()}
                size="small"
                sx={{ fontSize: 10,
                  bgcolor: job.status === 'done' ? 'var(--ok-bg)' : job.status === 'error' ? '#7f1d1d' : 'var(--line-accent)',
                  color:   job.status === 'done' ? '#4ade80' : job.status === 'error' ? '#f87171' : '#93c5fd',
                }}
              />
              {job.elapsed_s != null && (
                <Chip label={`${job.elapsed_s.toFixed(1)}s`} size="small"
                  sx={{ fontSize: 10, bgcolor: 'var(--panel)', color: 'var(--text-3)' }}/>
              )}
            </Box>

            <LinearProgress
              variant="determinate"
              value={job.progress * 100}
              sx={{ borderRadius: 1, height: 4, bgcolor: 'var(--panel)',
                '& .MuiLinearProgress-bar': {
                  bgcolor: job.status === 'error' ? '#ef4444' : '#3b82f6',
                }
              }}
            />

            {job.error && (
              <Alert severity="error" sx={{ mt: 1.5, fontSize: 11 }}>{job.error}</Alert>
            )}
          </Paper>
        )}

        {/* Results */}
        {job?.result && (
          <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2, borderRadius: 2 }}>
            {job.result.status === 'dry_run' && (
              <Alert severity="info" sx={{ fontSize: 10, mb: 1.5, py: 0.5,
                '& .MuiAlert-message': { py: 0 } }}>
                Dry-run: copper losses computed. Install NVIDIA Modulus for torque, iron &amp; magnet losses, η.
              </Alert>
            )}

            {/* ── Efficiency banner ── */}
            {job.result.efficiency_pct != null && (
              <Box sx={{ textAlign: 'center', py: 1.5, mb: 1.5,
                bgcolor: 'var(--ok-bg)', borderRadius: 1, border: '1px solid var(--ok-bg)' }}>
                <Typography sx={{ fontSize: 28, fontWeight: 800,
                  color: job.result.efficiency_pct > 90 ? '#4ade80' : '#fbbf24' }}>
                  {job.result.efficiency_pct.toFixed(1)} %
                </Typography>
                <Typography sx={{ fontSize: 10, color: '#16a34a' }}>efficiency η</Typography>
              </Box>
            )}

            {/* ── Power balance ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#3b82f6',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Power Balance
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="Torque"    value={job.result.torque_Nm.toFixed(4)} unit="N·m"
                   highlight={job.result.torque_Nm !== 0}/>
              <Row label="P mech"   value={job.result.P_mech_W != null ? job.result.P_mech_W.toFixed(0) : '—'} unit="W"
                   highlight={(job.result.P_mech_W ?? 0) > 0}/>
              <Row label="P input"  value={job.result.P_input_W != null ? job.result.P_input_W.toFixed(0) : '—'} unit="W"/>
            </Box>

            {/* ── Losses breakdown ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#ef4444',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Losses
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="Cu (winding)"   value={job.result.P_cu_total_W != null ? job.result.P_cu_total_W.toFixed(1) : '—'} unit="W"/>
              <Row label="Fe stator"      value={job.result.P_fe_stator_W != null ? job.result.P_fe_stator_W.toFixed(1) : '— (need Modulus)'} unit={job.result.P_fe_stator_W != null ? 'W' : ''}/>
              <Row label="Fe rotor"       value={job.result.P_fe_rotor_W  != null ? job.result.P_fe_rotor_W.toFixed(1)  : '— (need Modulus)'} unit={job.result.P_fe_rotor_W  != null ? 'W' : ''}/>
              <Row label="Mag eddy"       value={job.result.P_mag_eddy_W  != null ? job.result.P_mag_eddy_W.toFixed(1)  : '— (need Modulus)'} unit={job.result.P_mag_eddy_W  != null ? 'W' : ''}/>
              <Row label="Total losses"   value={job.result.P_loss_total_W != null ? job.result.P_loss_total_W.toFixed(1) : '—'} unit="W"/>
            </Box>

            {/* ── Winding params ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: 'var(--text-4)',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Winding (computed)
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="R phase"    value={job.result.R_phase_ohm != null ? (job.result.R_phase_ohm * 1000).toFixed(2) : '—'} unit="mΩ"/>
              <Row label="L turn"     value={job.result.L_turn_mm != null ? job.result.L_turn_mm.toFixed(1) : '—'} unit="mm"/>
              <Row label="I coil rms" value={job.result.I_coil_rms_A != null ? job.result.I_coil_rms_A.toFixed(1) : '—'} unit="A"/>
            </Box>

            {/* ── Field ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: 'var(--text-4)',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Magnetic Field
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
              <Row label="B max"  value={job.result.B_max_T.toFixed(4)}  unit="T"/>
              <Row label="B mean" value={job.result.B_mean_T.toFixed(4)} unit="T"/>
              <Row label="Steps"  value={job.result.training_steps.toString()}/>
            </Box>

            <Divider sx={{ borderColor: 'var(--panel)', my: 1.5 }}/>

            <Typography sx={{ fontSize: 10, color: 'var(--line)' }}>
              Next steps: open output_dir in ParaView to visualise A_z, B field,
              and H field maps.
            </Typography>
          </Paper>
        )}

        {/* No-job empty state ("Set operating point and press Run") removed
            — the right panel now goes straight from the operating-point
            controls to the Physics Dashboard, which auto-runs the FEM
            transient on mount.  No manual Run button needed. */}

        {/* Analytical SimulationCharts (currents / voltages / losses) deleted —
            the FEM transient panel inside PhysicsDashboard below shows all
            three waveforms computed from the actual mesh solve.  (It had been
            un-rendered but still imported; with it went TorqueWaveformChart,
            whose Maxwell-stress-on-free-space torque was scaled ×8-10 by an
            analytic flux-linkage formula to look plausible.) */}

        {/* The coupled EM↔thermal card moved to the Thermal tab on 2026-09-07,
            with the rest of the thermal solve: its cooling inputs, its
            iteration history and its converged coil temperature all belong
            beside the temperature map they describe. */}

        {/* ── Physics dashboard (the standard FEM interface) — FIRST so the
            FEM results + fields + transient are the prominent view ── */}
        <PhysicsDashboard
          gamma_deg={phaseOffset}
          I_phase_rms={current}
          rpm={rpm}
          connection={connection}
          runNonce={runNonce}
          fresh={freshRun}
          onBusyChange={setSimBusy}
          steps={steps}
          fieldLosses={fieldLosses}
          demag={demag}
          eddyCoupled={eddyCoupled}
          torqueFilter={torqueFilter}
          drive={drive}
          vPeak={vPeak}
          vDelta={vDelta}
          vBus={vBus}
          fSwitch={fSwitch}
          iBlock={iBlock}
          waveform={waveform}
          // ── the pack on the DC link ──────────────────────────────────
          // Sent as a plain payload (never a path into the family config —
          // the solver must not know where a battery is stored), and only
          // when this machine actually has one.  The bus-coupling loop is
          // additionally gated on GENERATOR: on a motoring run the same
          // iteration is a different question (sag, not rise) and the user
          // did not ask for it here.
          battery={batteryPayload(battery) as Record<string, unknown> | null}
          busCouple={busCouple && opMode === 'generator' && !!battery}
          chargeMax={chargeMaxOnce}
        />

      </Box>
    </Box>
  );
};

export default SimulationPanel;
