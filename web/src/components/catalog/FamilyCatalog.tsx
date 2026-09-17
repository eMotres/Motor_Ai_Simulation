/**
 * FamilyCatalog — Die → Configuration → Duty.
 *
 * One stamped lamination (die, locked) carries several buildable
 * configurations (stack length, wire, winding); each configuration runs at a
 * few named duties (mode, current, rpm, γ).  Clicking a duty applies the
 * WHOLE machine state — geometry, winding, operating point — through the
 * same endpoints the rest of the app already uses; this panel writes nothing
 * of its own into the live config.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  Box, Paper, Typography, Button, Chip, Tooltip, IconButton, CircularProgress,
} from '@mui/material';
import { useMotorStore, useUIStore } from '../../stores/motorStore';
import MotorThumbnail from './MotorThumbnail';
import {
  TextPromptDialog, ConfirmDialog,
  type TextPromptState, type ConfirmState,
} from '../common/PromptDialogs';
import BatteryDialog, { type BatteryValue } from './BatteryDialog';
import ConfigHistoryDialog from './ConfigHistoryDialog';
import {
  clearDutyMaterialsKeys, dutyCycleChip, setActiveDuty,
} from '../../lib/dutySettings';
import { gatedDutyCycleChip } from '../../lib/dutyCycleFlag';
import { timeToLimitChip, timeToLimitChipTip, type DutyTimeToLimit }
  from '../../lib/timeToLimitChip';
import { driveLabel } from '../../lib/dutyRuns';
// The LOCAL half of this load — the panel's own operating point, the duty's
// settings block, its materials and its stored runs.  Shared verbatim with the
// follower that catches a SECOND browser up when the machine is loaded here
// (lib/dutyLocalApply, lib/familyFollow), so the two can never drift.
import {
  applyDutyLocal, fetchDutyPayload, leaveForDuty,
} from '../../lib/dutyLocalApply';
import { beginDutyApply, endDutyApply } from '../../lib/familyFollow';
import { downloadExport } from '../../lib/exportDownload';
import {
  IDLE as RING_IDLE, newRunId, ringBusy, ringDone, ringFail, ringPoll,
  ringStart, ringTip, type ReportProgressInfo, type ReportRing,
} from './reportProgress';
import { fetchFamilyTree, SIGN_IN_NOTE } from '../../lib/familyTree';
import { pageVisible } from '../../lib/pageVisible';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface DutyResult {
  efficiency_pct?: number; ripple_pct?: number; v_ll_peak_v?: number;
  loss_w?: number; loss_mech_w?: number; loss_mech_derived?: boolean;
  mass_kg?: number; recorded_at?: string;
  p_core_w?: number; p_stranded_w?: number; p_solid_w?: number;
  v_phase_peak_v?: number; j_coil_a_mm2?: number;
  build_sig?: string;
}
/** One stored run of a duty, as the TREE describes it — no payload. */
interface DutyRunRow {
  drive: string; primary?: boolean; recorded_at?: string | null;
  stale?: boolean; ripple_pct?: number | null;
  f_switch_hz?: number | null; steps?: number | null;
  assignment_sig?: string | null; has_payload?: boolean;
}
interface Duty {
  name: string; mode: string; saved_at?: string | null;
  current_arms: number; rpm: number;
  gamma_deg: number; torque_nm?: number | null; power_kw?: number | null;
  /** 'star' | 'delta' — the terminal connection this duty was solved with
   *  (per duty since 2026-09-13; absent on older duties). */
  star_delta?: string | null;
  note: string; result?: DutyResult | null;
  /** what the machine DOES with this point — S1 continuous, S2 one pull, S3 an
   *  ED % of a cycle, or an explicit segment list (2026-09-14).  Absent on
   *  every duty saved before the cycle existed, which reads as the continuous
   *  point they were always assumed to be. */
  duty_cycle?: Record<string, unknown> | null;
  // Every excitation this point has been run and saved at.  The row's numbers
  // are always the PRIMARY (sine) run; these say what else is remembered, and
  // ▶ loads them all so the Simulation panel can switch between them.
  primary_drive?: string;
  runs?: DutyRunRow[];
  /** HOW LONG MAY IT RUN — the coupled loop's step response, when this point is
   *  past a limit (2026-09-17).  Absent on a duty with no coupled record, on a
   *  record older than the feature, and on a point inside every limit it has. */
  time_to_limit?: DutyTimeToLimit | null;
}
interface Battery {
  chemistry?: string | null; cells?: number | null;
  v_cell_min?: number; v_cell_nom?: number | null; v_cell_max?: number;
  v_min: number; v_max: number; v_nom?: number | null;
}
interface Cfg {
  name: string; role: string; locked?: boolean; build_sig?: string;
  /** 'duties' when the role was read off the saved duties, 'stored' when there
   *  are none yet and the creation-time toggle is all there is. */
  role_source?: string;
  /** what the yaml still says, kept visible when it disagrees. */
  role_stored?: string;
  battery?: Battery | null;
  stack_mm: number | null;
  wire_height_mm: number | null; wire_width_mm: number | null;
  turns: number | null; connection: string | null; star_delta?: string | null;
  magnet?: string | null; steel?: string | null;
  duties: Duty[];
}
interface Die {
  name: string; locked: boolean; created?: string;
  slots: number; poles: number; stator_diameter: number;
  thumb_svg?: string | null; configs: Cfg[];
}

/** "21:24" today, "19.08 21:24" otherwise — the exact stamp lives in the tooltip. */
const fmtWhen = (iso?: string | null) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(+d)) return '';
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  if (d.toDateString() === new Date().toDateString()) return `${hh}:${mi}`;
  return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')} ${hh}:${mi}`;
};

const fmt = (v: number | null | undefined, digits = 1) =>
  v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(digits).replace(/\.0+$/, '');

/** The whole `duty_cycle:` block, as one sentence for the chip's tooltip.  The
 *  chip says WHAT the machine does; this says what that means and what the
 *  answer will be fitted at — the calibration duty decides every conductance of
 *  the network, so it is never left implicit. */
const dutyCycleTip = (b?: Record<string, unknown> | null): string => {
  if (!b || !Object.keys(b).length) return '';
  const kind = String(b.kind ?? 'S1');
  const g = (v: unknown) => (v == null || v === '' ? '—' : String(v));
  const head =
    kind === 'S2' ? `S2 — ONE pull of ${g(b.t_on_s)} s from the start temperature, never repeated.`
    : kind === 'S3' ? `S3 — intermittent: ${g(b.ed_pct)} % of every ${g(b.cycle_s)} s powered, resting ${b.rest_duty == null ? 'UNPOWERED' : `at "${String(b.rest_duty)}"`}, repeated until the cycle repeats itself.`
    : kind === 'segments' ? `An explicit segment list, repeated as one cycle: ${(Array.isArray(b.segments) ? b.segments : []).map((s) => { const e = s as Record<string, unknown>; return `${e?.duty == null ? 'unpowered' : String(e.duty)} ${g(e?.t_s)} s`; }).join(' → ')}.`
    : 'S1 — continuous duty: the machine runs this point until it stops changing.';
  const bits = [head];
  if (b.duty) bits.push(`Runs "${String(b.duty)}".`);
  bits.push(b.calibration_duty
    ? `The lumped network is fitted to the steady map of "${String(b.calibration_duty)}".`
    : 'The network is fitted to the configuration’s rated duty (no calibration duty stated).');
  if (b.t_start_c != null) bits.push(`Starts at ${g(b.t_start_c)} °C.`);
  else bits.push('Starts at the ambient.');
  if (b.n_cycles_max != null) bits.push(`At most ${g(b.n_cycles_max)} cycles are integrated.`);
  if (b.saved_at) bits.push(`Defined ${fmtWhen(String(b.saved_at))}.`);
  bits.push('Solve it on the Thermal tab.');
  return bits.join(' ');
};

const readLS = (k: string, d: any) => {
  try { const v = localStorage.getItem('sim.' + k); return v == null ? d : JSON.parse(v); }
  catch { return d; }
};

/**
 * The report button's ring — MINIMAL by request (2026-09-16): a small circle
 * and a percentage, no bar, no line of text.  It spins (indeterminate) from
 * the click until the backend's first answer, so something moves within a
 * second of pressing; after that it is the real fraction of the stages the
 * build publishes.  Everything it knows is in the tooltip, one line.
 */
const ReportRing: React.FC<{ ring: ReportRing; size?: number }> = ({ ring, size = 15 }) => (
  <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.45 }}>
    <CircularProgress
      variant={ring.pct == null ? 'indeterminate' : 'determinate'}
      value={ring.pct ?? 0} size={size} thickness={6}
      sx={{ color: 'inherit' }} />
    {ring.pct != null && <span>{ring.pct} %</span>}
  </Box>
);

const FamilyCatalog: React.FC<{
  /** Show only dies of this stator diameter (the page groups by Ø). */
  diameter?: number;
  /** Render bare die blocks (no panel, no header) for embedding in a Ø group. */
  embedded?: boolean;
}> = ({ diameter, embedded }) => {
  const { updateGeometryViaApi } = useMotorStore();
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const [dies, setDies] = useState<Die[]>([]);
  const [busy, setBusy] = useState<string | null>(null);   // what is being applied/created
  const [msg,  setMsg]  = useState<string | null>(null);   // last outcome (ok or error)
  // Clients browse and LOAD; editing the family catalog is the vendor's job.
  // The backend enforces this on every mutating endpoint — this flag only
  // decides whether the editing controls are drawn at all.
  const [canWrite, setCanWrite] = useState(false);

  // What is loaded in the editor — its die/config/duty rows get highlighted.
  const [active, setActive] = useState<{ die?: string; config?: string;
                                         duty?: string | null } | null>(null);

  // MUI dialogs instead of window.prompt/confirm — Chrome suppresses the
  // native ones after "prevent additional dialogs" and the buttons look dead
  // (user hit it on the battery editor).
  const [askText, setAskText] = useState<TextPromptState | null>(null);
  // History dialog target: a configuration, or (config null) the die geometry.
  const [histOf, setHistOf] = useState<{ die: string; config: string | null } | null>(null);
  const [askConfirm, setAskConfirm] = useState<ConfirmState | null>(null);
  const [batteryFor, setBatteryFor] = useState<{ die: string; cfg: Cfg } | null>(null);

  // null = not loaded yet / load FAILED — a different state from "loaded and
  // empty".  A backend mid-restart used to render as "no dies yet" (live
  // 2026-08-24: the user read it as their catalog being gone), so a failed
  // load now says so and RETRIES until the backend answers.
  const [loadFailed, setLoadFailed] = useState(false);
  // The backend's one-line reason for an empty catalog (a signed-in account
  // with no motors granted yet) — an empty page tells that user nothing.
  const [note, setNote] = useState<string | null>(null);
  // The tree comes through lib/familyTree: every Ø section on the Motors tab
  // is one of these, and each used to fetch the 900 KB tree for itself
  // (2026-09-13).  `fresh` after a mutation or a `family-changed` event.
  const load = async (fresh = false) => {
    try {
      const t = await fetchFamilyTree({ fresh });
      setDies((t.dies as Die[]) || []);
      setCanWrite(t.can_write === true);
      setNote(typeof t.note === 'string' ? t.note : null);
      const c = await (await fetch(`${API}/api/family/context`, { cache: 'no-store' })).json();
      setActive(c?.active ? c : null);
      setLoadFailed(false);
    } catch (e) {
      // 401: the server publishes nothing to anonymous visitors
      // (PUBLIC_EXHIBIT=0).  That is an answer, not an outage — no retry loop,
      // no red "load failed", just the line that tells the user to sign in.
      if ((e as { status?: number })?.status === 401) {
        setDies([]); setCanWrite(false); setActive(null);
        setLoadFailed(false); setMsg(null); setNote(SIGN_IN_NOTE);
        return;
      }
      setLoadFailed(true); setMsg(`catalog load failed: ${e}`);
    }
  };
  useEffect(() => { load(); }, []);
  // Failed load (backend restarting / briefly unreachable): retry every 3 s
  // until it answers — the catalog reappears by itself, no manual F5 needed.
  useEffect(() => {
    if (!loadFailed) return;
    const id = window.setInterval(() => { if (pageVisible()) void load(true); }, 3000);
    return () => window.clearInterval(id);
  }, [loadFailed]);   // eslint-disable-line react-hooks/exhaustive-deps
  // The page-level "+ die" button (and sibling Ø sections) announce family
  // mutations with this event — every embedded instance refetches (one shared
  // request between them, see lib/familyTree).
  useEffect(() => {
    const onChanged = () => { void load(true); };
    window.addEventListener('family-changed', onChanged);
    return () => window.removeEventListener('family-changed', onChanged);
  }, []);

  // Every mutation goes through here: run it, surface the backend's own error
  // text (they are written for engineers), reload the tree.
  const mutate = async (label: string, fn: () => Promise<Response>) => {
    setBusy(label); setMsg(null);
    try {
      const r = await fn();
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { detail = (await r.json()).detail ?? detail; } catch { /* keep */ }
        setMsg(`✗ ${detail}`);
      } else {
        setMsg(`✓ ${label}`);
      }
    } catch (e) { setMsg(`✗ ${label}: ${e}`); }
    setBusy(null);
    await load(true);
    try { window.dispatchEvent(new CustomEvent('family-changed')); } catch { /* SSR */ }
  };

  const post = (path: string, body: any) => fetch(`${API}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const del = (path: string) => fetch(`${API}${path}`, { method: 'DELETE' });

  // ── exports ───────────────────────────────────────────────────────────────
  // Two files per configuration, both read-only, so clients get them too:
  //
  //   datasheet (.xlsx)  a duty column each, the design and battery blocks, the
  //                      cross-section, the measured curves and a page
  //                      explaining every number.  Google Sheets opens it.
  //   report (.docx)     the same machine plus what every SOLVER last answered
  //                      about it — losses, temperatures, stresses, bearings and
  //                      the field maps, with each result's own timestamp.
  //                      WORD, not PDF, since 2026-09-09 (user: "репорт лучше
  //                      выдавать в формате doc") — he edits the document and
  //                      forwards it to clients, and Word exports its own PDF.
  //                      The backend serves docx by default and pdf on
  //                      `?format=pdf`, which the small PDF button asks for.
  //
  // `exporting` holds "<kind>:<die>/<cfg>", so pressing one button does not
  // grey out the other on the same row.
  const [exporting, setExporting] = useState<string | null>(null);

  // THE PROGRESS RING (user 2026-09-16: "нужно сделать ещё минимальный
  // прогресс-ринг генерации отчёта, чтобы было видно, что работает, а не
  // висит").  A report is ~50 s of figures and the PDF half a minute more, and
  // the download is ONE request whose body is the file — nothing on the wire
  // until it is finished.  So the click mints a run id, sends it with the
  // download, and a second request polls the stages the backend publishes into
  // the shared progress registry.  Rules in `./reportProgress`; this is the
  // plumbing.
  const [ring, setRing] = useState<ReportRing>(RING_IDLE);
  const pollRef = useRef(0);
  const reportBusy = (die: string, cfg: string) =>
    exporting === `report:${die}/${cfg}` || exporting === `report:pdf:${die}/${cfg}`;

  // WHICH DUTY'S PICTURES go into the report (user 2026-09-11: "нужно ещё
  // сделать выбор, из какого режима мы публикуем картинки в отчёте").  The
  // backend's own rule is "the rated duty, then the loaded one"; this lets the
  // user override it per configuration.  Remembered per machine in
  // localStorage — a convenience, never state the report depends on: an empty
  // choice means the backend's rule, and every read is guarded because the
  // accessor itself can throw in a locked-down browser.
  const picKey = (die: string, cfg: string) => `report.pictures:${die}/${cfg}`;
  const [picDuty, setPicDuty] = useState<Record<string, string>>(() => ({}));
  const picFor = (die: string, cfg: string): string => {
    const k = picKey(die, cfg);
    if (k in picDuty) return picDuty[k];
    try { return localStorage.getItem(k) ?? ''; } catch { return ''; }
  };
  const setPicFor = (die: string, cfg: string, v: string) => {
    const k = picKey(die, cfg);
    setPicDuty((m) => ({ ...m, [k]: v }));
    try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch { /* per-viewer nicety only */ }
  };

  const runExport = async (kind: 'datasheet' | 'report',
                           die: string, cfg: string, asPdf = false) => {
    // The key carries the format too, so the Word button and the PDF one on the
    // same row grey out independently.
    const key = `${kind}${asPdf ? ':pdf' : ''}:${die}/${cfg}`;
    setExporting(key);
    const ext = kind !== 'report' ? 'xlsx' : asPdf ? 'pdf' : 'docx';
    // The extension and the `format` the backend is asked for are decided in
    // ONE place: saving a Word body under a .pdf name is what Windows opens
    // with the wrong application (2026-09-09).
    const qs: string[] = [];
    if (kind === 'report' && asPdf) qs.push('format=pdf');
    // …and the duty whose maps the report draws, when the user picked one.
    const pic = kind === 'report' ? picFor(die, cfg) : '';
    if (pic) qs.push(`pictures=${encodeURIComponent(pic)}`);
    // …and the id this build's stages are published under.  Only the report
    // has one: the datasheet is a second or two of openpyxl, and a ring that
    // appears and vanishes is worse than no ring.
    const runId = kind === 'report' ? newRunId() : '';
    if (runId) qs.push(`run_id=${encodeURIComponent(runId)}`);
    const q = qs.length ? `?${qs.join('&')}` : '';

    let poll = 0;
    if (runId) {
      const t0 = Date.now();
      setRing(ringStart(key, runId));
      // Every second: slow enough to be free next to a 50 s build, fast enough
      // that the ring is never more than a second stale.  The first answer
      // usually lands before the first figure, which is what turns the
      // indeterminate spin into a percentage.
      poll = pollRef.current = window.setInterval(() => {
        void (async () => {
          let info: ReportProgressInfo | null = null;
          try {
            const r = await fetch(`${API}/api/family/report/progress`
              + `?run_id=${encodeURIComponent(runId)}`);
            if (r.ok) info = await r.json();
          } catch { /* a lost poll is not a failed build */ }
          setRing((prev) => (prev.runId === runId
            ? ringPoll(prev, info, (Date.now() - t0) / 1000) : prev));
        })();
      }, 1000);
    }

    const err = await downloadExport(
      `${API}/api/family/${kind}/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}${q}`,
      `${die} ${cfg} ${kind}.${ext}`);
    if (poll) { window.clearInterval(poll); if (pollRef.current === poll) pollRef.current = 0; }
    // A failed REPORT says so where it was clicked — the ring becomes the
    // notice line.  The datasheet has no ring, so it keeps the panel message.
    if (err && kind !== 'report') setMsg(`Datasheet failed: ${err}`);
    if (runId) {
      // The download's own answer is the last word — it has the sentence the
      // backend refused with, which a poll may never have seen.
      setRing((prev) => (prev.runId === runId || prev.key === key
        ? (err ? ringFail(prev, err) : ringDone(prev)) : prev));
    }
    setExporting((k) => (k === key ? null : k));
  };

  // The ring stops with the panel: an unmounted catalogue must not keep a 1 s
  // timer polling a backend nobody is watching (the build itself carries on —
  // it is the download's own request — and the file still arrives).
  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  // ── create/delete ─────────────────────────────────────────────────────────
  const createDie = () => setAskText({
    title: 'New die from the current geometry',
    label: 'Die name',
    onSubmit: (name) => mutate(`die '${name}' created`,
      () => post('/api/family/die', { name })),
  });
  const createCfg = (die: string) => setAskText({
    title: `New configuration under ${die}`,
    label: 'Configuration name',
    hint: 'Snapshots the CURRENT stack / wire / winding / materials',
    onSubmit: (name) => {
      const role = readLS('opMode', 'motor') === 'generator' ? 'generator' : 'motor';
      mutate(`configuration '${name}' created`, () =>
        post('/api/family/config', { die, name, role }));
    },
  });
  const createDuty = (die: string, cfg: string) => setAskText({
    title: `New duty under ${die} / ${cfg}`,
    label: 'Duty name',
    hint: 'Captures the CURRENT Simulation point — current, rpm, γ, mode',
    onSubmit: (name) => {
      const mode = readLS('opMode', 'motor') === 'generator' ? 'generator' : 'motor';
      mutate(`duty '${name}' saved from Simulation`, () =>
        post('/api/family/duty', { die, config: cfg, duty: { name, mode, from_current: true } }));
    },
  });
  const deleteDie = (die: string) => setAskConfirm({
    title: `Delete die '${die}'?`,
    body: 'An empty die is removed at once; a die that still has configurations asks once more.',
    onConfirm: () => {
      void (async () => {
        setBusy(`delete ${die}`); setMsg(null);
        try {
          const r = await del(`/api/family/die/${encodeURIComponent(die)}`);
          if (r.ok) {
            setMsg(`✓ die '${die}' deleted`);
            window.dispatchEvent(new CustomEvent('family-changed'));
            return;
          }
          if (r.status !== 409) {
            let detail = `HTTP ${r.status}`;
            try { detail = (await r.json()).detail ?? detail; } catch { /* keep */ }
            setMsg(`✗ ${detail}`);
            return;
          }
          // 409 = the die still has configurations. Name them and ask ONCE
          // more — then delete the whole subtree with force. (It used to stop
          // here with a message the user could miss: "стираю а она не
          // стирается", live 2026-08-20.)
          let detail = '';
          try { detail = String((await r.json()).detail ?? ''); } catch { /* keep */ }
          setBusy(null);
          setAskConfirm({
            title: `Delete '${die}' WITH its configurations?`,
            body: (detail || 'The die still has configurations.')
              + ' They and their duties are removed permanently.',
            onConfirm: () => mutate(`die '${die}' deleted with its configurations`,
              () => del(`/api/family/die/${encodeURIComponent(die)}?force=true`)),
          });
          return;
        } catch (e) {
          setMsg(`✗ ${e}`);
        } finally {
          setBusy(null);
        }
      })();
    },
  });
  const renameDie = (die: string) => setAskText({
    title: `Rename die '${die}'`,
    label: 'New name', initial: die,
    onSubmit: (name) => { if (name !== die) mutate(`die renamed to '${name}'`, () =>
      fetch(`${API}/api/family/die/${encodeURIComponent(die)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })); },
  });
  const duplicateDie = (die: string) => setAskText({
    title: `Duplicate die '${die}'`,
    label: 'Name of the copy', initial: `${die} copy`,
    hint: 'Copies the stamped geometry + EVERY configuration with its duties and results; the copy starts unlocked',
    onSubmit: (name) => mutate(`die '${die}' duplicated as '${name}'`,
      () => post(`/api/family/die/${encodeURIComponent(die)}/duplicate`, { name })),
  });
  const duplicateCfg = (die: string, cfg: string) => setAskText({
    title: `Duplicate '${cfg}' under ${die}`,
    label: 'Name of the copy', initial: `${cfg} copy`,
    hint: 'Copies the build, winding, materials, battery and EVERY duty (with results)',
    onSubmit: (name) => mutate(`configuration '${cfg}' duplicated as '${name}'`,
      () => post(`/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/duplicate`,
                 { name })),
  });
  // Client path: snapshot the machine into the caller's PRIVATE "My motors"
  // space (server-side per-user store; nothing shared changes).
  const duplicateToMySpace = (die: string, cfg: string) => setAskText({
    title: `Copy '${die} / ${cfg}' to MY MOTORS`,
    label: 'Name in your space', initial: `${die} / ${cfg}`,
    hint: 'A private self-contained copy only you can see — share it later if you want',
    onSubmit: (name) => mutate(`'${name}' saved to your motors`,
      () => post('/api/my_motors/duplicate', { die, config: cfg, name })),
  });
  const deleteCfg = (die: string, cfg: string) => setAskConfirm({
    title: `Delete configuration '${die} / ${cfg}'?`,
    body: 'All its duties and recorded results go with it. This cannot be undone.',
    onConfirm: () => mutate(`configuration '${cfg}' deleted`, () =>
      del(`/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}`)),
  });
  const renameCfg = (die: string, cfg: string) => setAskText({
    title: `Rename configuration '${cfg}'`,
    label: 'New name', initial: cfg,
    onSubmit: (name) => { if (name !== cfg) mutate(`configuration renamed to '${name}'`, () =>
      fetch(`${API}/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })); },
  });
  const toggleDieLock = (d: Die) => {
    mutate(`die '${d.name}' ${d.locked ? 'unlocked' : 'locked'}`, () =>
      fetch(`${API}/api/family/die/${encodeURIComponent(d.name)}/lock`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locked: !d.locked }),
      }));
  };
  const toggleCfgLock = (dieName: string, c: Cfg) => {
    mutate(`configuration '${c.name}' ${c.locked ? 'unlocked' : 'locked'}`, () =>
      fetch(`${API}/api/family/config/${encodeURIComponent(dieName)}/${encodeURIComponent(c.name)}/lock`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locked: !c.locked }),
      }));
  };
  const editBattery = (dieName: string, c: Cfg) =>
    setBatteryFor({ die: dieName, cfg: c });
  const renameDuty = (die: string, cfg: string, duty: string) => setAskText({
    title: `Rename duty '${duty}' in ${die} / ${cfg}`,
    label: 'New name', initial: duty,
    hint: 'Operating point, targets, the recorded result, the stored runs, the solved maps and every thermal / mechanical / coupled answer follow the new name',
    onSubmit: (name) => { if (name !== duty) mutate(`duty renamed to '${name}'`, () =>
      fetch(`${API}/api/family/duty/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/${encodeURIComponent(duty)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })); },
  });
  const duplicateDuty = (die: string, cfg: string, duty: string) => setAskText({
    title: `Duplicate duty '${duty}' in ${die} / ${cfg}`,
    label: 'Name of the copy', initial: `${duty} copy`,
    hint: 'Full copy: operating point, targets, note AND the recorded result',
    onSubmit: (name) => mutate(`duty '${duty}' duplicated as '${name}'`,
      () => post(`/api/family/duty/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/${encodeURIComponent(duty)}/duplicate`,
                 { name })),
  });
  const deleteDuty = (die: string, cfg: string, duty: string) => setAskConfirm({
    title: `Delete duty '${duty}' from ${die} / ${cfg}?`,
    body: 'Its saved operating point and recorded results go with it.',
    onConfirm: () => mutate(`duty '${duty}' deleted`, () =>
      del(`/api/family/duty/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/${encodeURIComponent(duty)}`)),
  });

  // ── apply a duty: geometry + winding + materials + operating point, all
  //    through the EXISTING endpoints (this panel owns no write-path of its
  //    own into the live config) ─────────────────────────────────────────────
  const applyDuty = async (die: string, cfg: string, duty: string) => {
    const label = `${cfg} / ${duty}`;
    setBusy(label); setMsg(null);
    // A poll in another part of THIS browser must not read the activate below
    // as "the machine changed elsewhere" and start following the duty we are
    // in the middle of applying (lib/familyFollow).
    beginDutyApply();
    try {
      // Step 0, shared with the follower: file the panel state under the die
      // and the duty being LEFT, and say what they were (the machine-change
      // test the local half needs for the coupled-loop temperatures).
      const prev = leaveForDuty(die, cfg, duty);
      const p = await fetchDutyPayload(die, cfg, duty);
      // From this line on, the panel is editing THIS duty: every
      // operating-point field it writes is filed under this key (and never
      // under the duty we just left, which is why the marker moves BEFORE the
      // first sim.* write rather than after the last one).  Claimed HERE, not
      // only inside the local half below, because the server writes in between
      // can move panel fields (the battery's V_bus prefill) and those belong to
      // the incoming duty.  applyDutyLocal repeats both — idempotent.
      try { setActiveDuty(die, cfg, duty); } catch { /* quota */ }
      // The MATERIALS are per-duty too, and their default is "the machine's
      // own" — an ABSENT key, not a value.  So they are cleared here, before
      // this duty's snapshot (the `materials:` dict applied below) and its
      // overlay (restoreDutyOp, inside applyDutyLocal) get to state their own: without
      // the clear, a duty that never picked any would keep solving with the
      // magnet and the steel the PREVIOUS duty chose.  lib/dutySettings.ts.
      try { clearDutyMaterialsKeys(); } catch { /* nothing to clear */ }
      if (canWrite) {
        // ── OWNER: load the duty into the SHARED server config ─────────────
        // -1) DROP any queued geometry edits: they belong to the machine that
        //     is being replaced, and a debounced replay landing after the
        //     context switch would save a foreign machine into the new die
        //     (the backend's stranger guard refuses it too — this closes the
        //     race at the source; incident 2026-08-24).
        useMotorStore.setState({ pendingGeometryEdits: null });
        // 0) mark WHICH die/config/duty the editor is about to become — the
        //    geometry route enforces the locks against this context, and
        //    applying the configuration's own canonical values passes.
        const ar = await fetch(`${API}/api/family/activate`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ die, config: cfg, duty }),
        });
        // A failed activate (expired session, 401/403) used to be SILENT and
        // the geometry PUT below still ran — the live editor then held this
        // machine while the server context still named the previous die, and
        // the die sync wrote this machine into THAT die.  Nothing may be
        // applied when the context could not follow.
        if (!ar.ok) {
          let why = `HTTP ${ar.status}`;
          try { why = (await ar.json()).detail ?? why; } catch { /* no body */ }
          throw new Error(`cannot activate ${die} / ${cfg}: ${why} — sign in again and retry`);
        }
        // 1) geometry — the die's stamped section + this configuration's stack/wire
        await updateGeometryViaApi(p.geometry);
        // 2) winding connection (authoritative endpoint; validates against layout)
        if (p.sim.connection) {
          const wr = await fetch(`${API}/api/winding/config`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ connection: p.sim.connection,
                                   layers: p.winding?.layers }),
          });
          if (!wr.ok) throw new Error((await wr.json()).detail ?? `winding HTTP ${wr.status}`);
        }
        // 2b) the build's materials — a configuration is a physical product;
        //     loading it must load what it is made of.  EVERY part the payload
        //     names (2026-09-09): it now spells out the liner and the enamel
        //     too, because loading only the three build parts left the
        //     previous machine's Al2O3 liner on every motor after the Ø200.
        for (const [part, mat] of Object.entries(p.materials ?? {})) {
          if (!mat) continue;
          const mr = await fetch(`${API}/api/materials`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ part, material: mat }),
          });
          if (!mr.ok) throw new Error((await mr.json()).detail ?? `materials HTTP ${mr.status}`);
        }
        // The PATCHes above bypass useMotorAssignments.assign(), so NOTHING
        // told the other hook instances — including the one that feeds ?mat=
        // to the solver (MaterialOverrideSync).  It kept the PREVIOUS machine's
        // assignment and every run after this load was solved with it: the G2
        // generator (B15AHV950M) ran on the 85 mm die's 20SW1200 all morning
        // 2026-09-02 (+3.9 % iron, +3.8 % torque, +10 % core loss), invisible
        // on the panel because only the magnet is shown there.  Same bug class
        // as 2026-08-25, from the other entry point.  Broadcast, always.
        try { window.dispatchEvent(new CustomEvent('mat-assign-changed')); } catch { /* SSR */ }
        // 3) shared simulation config — what the sweep/optimizer read off-tab
        const simPatch: any = {
          max_current: p.sim.current_a, rpm: p.sim.rpm, frequency: p.sim.frequency,
          phase_offset_deg: p.sim.gamma_deg, mode: p.sim.mode,
          connection: p.sim.connection,
          star_delta: p.sim.star_delta ?? 'star',
        };
        Object.keys(simPatch).forEach(k => simPatch[k] == null && delete simPatch[k]);
        // The d-axis pin is a property of the DIE's topology, so it is sent
        // ALWAYS — a blank ('' = measure) when the new machine carries none.
        // Skipping it left the previous machine's pin in the shared config:
        // the G2's 60° (24s/28p) was applied to the CIANO10 200 opt (12s/10p,
        // its own d-axis 120°) and every Run was refused as "d-axis pin 60°
        // does not belong to this machine" while the field on screen was
        // empty (user 2026-09-09: "не запускается моделирование").  The local
        // field was already cleared (lib/dutyLocalApply) — the config was not.
        simPatch.daxis_deg = (p.sim.daxis_deg != null && Number.isFinite(Number(p.sim.daxis_deg)))
          ? p.sim.daxis_deg : '';
        const sr = await fetch(`${API}/api/simulation/config`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(simPatch),
        });
        if (!sr.ok) throw new Error((await sr.json()).detail ?? `sim HTTP ${sr.status}`);
      } else {
        // ── ORDINARY USER: the duty becomes THIS CLIENT'S COPY ─────────────
        // Nothing on the server changes.  The geometry goes into the local
        // store (every compute request carries it as ?geo=), the operating
        // point into the panel's localStorage below, the materials into the
        // local assignment overlay (sent as ?mat=), and the strip shows the
        // copied die/config/duty from local context.
        await updateGeometryViaApi(p.geometry);   // local-mode merge, no PUT
        try {
          const cur = JSON.parse(localStorage.getItem('mat.assign.local') || '{}');
          for (const [part, mat] of Object.entries(p.materials ?? {})) {
            if (mat) cur[part] = mat;
          }
          localStorage.setItem('mat.assign.local', JSON.stringify(cur));
          window.dispatchEvent(new CustomEvent('mat-assign-local-changed'));
        } catch { /* quota — materials stay whatever they were */ }
        try {
          localStorage.setItem('family.localContext',
            JSON.stringify({ die, config: cfg, duty, at: Date.now() }));
        } catch { /* quota */ }
      }
      // 4) THE LOCAL HALF — panel-owned persisted values, the live nudges,
      //    the duty's settings block, its materials and its stored runs.  It
      //    is the SAME function a second browser runs when it notices this
      //    load in /api/family/context (lib/dutyLocalApply, lib/familyFollow),
      //    so the two paths cannot drift apart.
      const done = await applyDutyLocal(die, cfg, duty, p, prev, canWrite);
      setMsg(`✓ applied ${label} — ${done.message}`);
      // Straight to the machine the user just loaded (user request).
      setActiveTab('geometry');
    } catch (e: any) { setMsg(`✗ apply ${label}: ${e?.message ?? e}`); }
    endDutyApply();
    setBusy(null);
  };

  const roleColor = (role: string) =>
    role === 'generator' ? '#38bdf8' : role === 'mixed' ? '#a78bfa' : '#f59e0b';
  /** Where the chip's word came from.  The role is READ OFF THE DUTIES since
   *  2026-09-10 — it used to be the Simulation toggle's value on the day the
   *  configuration was created, frozen in the yaml, so "L180 gen" wore a
   *  `motor` chip over two generator duties (user: "почему здесь motor, хотя
   *  это генератор"). */
  const roleTip = (c: { role: string; role_source?: string; role_stored?: string }) => (
    c.role_source === 'duties'
      ? (c.role === 'mixed'
        ? 'Read off this configuration’s duties: some drive, some generate.'
        : `Read off this configuration’s duties — every one of them is a ${c.role} duty.`)
        + (c.role_stored && c.role_stored !== c.role
          ? ` The yaml still says "${c.role_stored}" from the day it was created; the duties are what count.`
          : '')
      : 'No duties saved yet, so this is the Simulation mode that was set when the configuration was created. It follows the duties as soon as there is one.');

  const shown = dies.filter(d =>
    diameter == null || Number(d.stator_diameter) === Number(diameter));
  if (embedded && shown.length === 0 && !msg) return null;

  const body = (
    <>
      {msg && (
        <Typography sx={{ fontSize: 11,
          color: msg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{msg}</Typography>
      )}

      {!embedded && loadFailed && (
        <Typography sx={{ fontSize: 11, color: '#f59e0b' }}>
          backend unreachable — the catalog is safe on disk; retrying…
        </Typography>
      )}
      {!embedded && shown.length === 0 && !msg && !loadFailed && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
          {note ?? 'no dies yet — freeze the current geometry with the button above'}
        </Typography>
      )}

      {shown.map(die => (
        <Box key={die.name} sx={{
          border: `1px solid ${active?.die === die.name ? '#60a5fa88' : 'var(--line-soft)'}`,
          borderRadius: 1, p: 1.2, display: 'flex', flexDirection: 'column', gap: 0.8 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.2 }}>
            <Box sx={{ flexShrink: 0, lineHeight: 0 }}>
              <MotorThumbnail slots={die.slots} poles={die.poles} size={56}
                thumbSvg={die.thumb_svg ?? undefined} />
            </Box>
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-1)' }}>
              {die.locked ? '🔒 ' : ''}{die.name}
            </Typography>
            {canWrite && (
              <Tooltip title="Rename die">
                <span>
                  <IconButton size="small" disabled={!!busy}
                    onClick={() => renameDie(die.name)}
                    sx={{ fontSize: 11, p: 0.2, color: 'var(--text-4)' }}>✎</IconButton>
                </span>
              </Tooltip>
            )}
            {canWrite && (
              <Tooltip title={die.locked
                ? 'Die is LOCKED — stamped geometry is read-only. Click to unlock.'
                : 'Die is unlocked — the whole geometry is editable. Click to lock.'}>
                <span>
                  {/* The glyph shows the CURRENT STATE (🔒 = locked), never the
                      action — an action-glyph read as state locked the user's
                      mental model backwards. */}
                  <IconButton size="small" disabled={!!busy}
                    onClick={() => toggleDieLock(die)}
                    sx={{ fontSize: 11, p: 0.2,
                          color: die.locked ? '#fbbf24' : 'var(--text-4)' }}>
                    {die.locked ? '🔒' : '🔓'}
                  </IconButton>
                </span>
              </Tooltip>
            )}
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
              {die.configs.length} configuration{die.configs.length === 1 ? '' : 's'}
            </Typography>
            <Tooltip title="History of the die geometry (die.yaml) — every saved version; pick one to roll back.">
              <span>
                <Button size="small" disabled={!!busy}
                  onClick={() => setHistOf({ die: die.name, config: null })}
                  sx={{ fontSize: 11, py: 0, px: 0.6, minWidth: 0,
                        textTransform: 'none', color: 'var(--text-3)' }}>
                  ⟲ history
                </Button>
              </span>
            </Tooltip>
            {canWrite && (
              <Tooltip title="Duplicate the WHOLE die — stamped geometry + every configuration with its duties and results. The copy starts unlocked.">
                <span>
                  <Button size="small" disabled={!!busy}
                    onClick={() => duplicateDie(die.name)}
                    sx={{ fontSize: 11, py: 0, px: 0.6, minWidth: 0,
                          textTransform: 'none', color: '#60a5fa' }}>
                    ⧉ duplicate
                  </Button>
                </span>
              </Tooltip>
            )}
            <Box sx={{ flex: 1 }} />
            {canWrite && (
              <Button size="small" variant="text" disabled={!!busy}
                onClick={() => createCfg(die.name)}
                sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>
                ＋ configuration
              </Button>
            )}
            {canWrite && (
              <Tooltip title="Delete die (configurations must be deleted first)">
                <span>
                  <IconButton size="small" disabled={!!busy}
                    onClick={() => deleteDie(die.name)}
                    sx={{ color: 'var(--text-4)', fontSize: 13, p: 0.4 }}>🗑</IconButton>
                </span>
              </Tooltip>
            )}
          </Box>

          {die.configs.map(c => (
            <Box key={c.name} sx={{ pl: 1, py: 0.3, borderTop: '1px solid var(--panel)' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8, flexWrap: 'wrap' }}>
                <Typography sx={{ fontSize: 12.5, fontWeight: 600, minWidth: 84,
                  color: (active?.die === die.name && active?.config === c.name)
                    ? '#60a5fa' : 'var(--text-2)' }}>
                  {c.name}
                  {(active?.die === die.name && active?.config === c.name)
                    ? ' ●' : ''}
                </Typography>
                {c.locked && !canWrite && (
                  <Typography component="span" sx={{ fontSize: 11 }}>🔒</Typography>
                )}
                {canWrite && (
                  <Tooltip title="Rename configuration">
                    <span>
                      <IconButton size="small" disabled={!!busy}
                        onClick={() => renameCfg(die.name, c.name)}
                        sx={{ fontSize: 11, p: 0.2, color: 'var(--text-4)' }}>✎</IconButton>
                    </span>
                  </Tooltip>
                )}
                {canWrite && (
                  <Tooltip title={c.locked
                    ? 'Configuration is LOCKED — with the locked die the geometry is fully read-only. Click to unlock.'
                    : 'Configuration is unlocked — stack length, wire height and turns are editable. Click to lock.'}>
                    <span>
                      <IconButton size="small" disabled={!!busy}
                        onClick={() => toggleCfgLock(die.name, c)}
                        sx={{ fontSize: 11, p: 0.2,
                              color: c.locked ? '#fbbf24' : 'var(--text-4)' }}>
                        {c.locked ? '🔒' : '🔓'}
                      </IconButton>
                    </span>
                  </Tooltip>
                )}
                <Tooltip title="History — every saved version of this configuration (geometry, winding, materials, duties). Pick one to roll back; the current version is kept as a snapshot too.">
                  <span>
                    <Button size="small" disabled={!!busy}
                      onClick={() => setHistOf({ die: die.name, config: c.name })}
                      sx={{ fontSize: 11, py: 0, px: 0.6, minWidth: 0,
                            textTransform: 'none', color: 'var(--text-3)' }}>
                      ⟲ history
                    </Button>
                  </span>
                </Tooltip>
                {canWrite && (
                  <Tooltip title="Duplicate — copy this configuration with ALL its duties (a starting point for a variant)">
                    <span>
                      <Button size="small" disabled={!!busy}
                        onClick={() => duplicateCfg(die.name, c.name)}
                        sx={{ fontSize: 11, py: 0, px: 0.6, minWidth: 0,
                              textTransform: 'none', color: '#60a5fa' }}>
                        ⧉ duplicate
                      </Button>
                    </span>
                  </Tooltip>
                )}
                {(c.duties?.length ?? 0) > 0 && (
                  <Tooltip title="Download the datasheet (.xlsx) — every duty in a column, the design and battery blocks, the cross-section, the measured curves and a page explaining each number. Opens in Google Sheets or Excel.">
                    <span>
                      <Button size="small"
                        disabled={exporting === `datasheet:${die.name}/${c.name}`}
                        onClick={() => runExport('datasheet', die.name, c.name)}
                        sx={{ fontSize: 11, py: 0, px: 0.6, minWidth: 0,
                              textTransform: 'none', color: '#34d399' }}>
                        {exporting === `datasheet:${die.name}/${c.name}`
                          ? '… datasheet' : '⭳ datasheet'}
                      </Button>
                    </span>
                  </Tooltip>
                )}
                <Tooltip title={ringBusy(ring, `report:${die.name}/${c.name}`)
                  ? ringTip(ring)
                  : 'Full report of the last results of every solver for this configuration — as a WORD document you can edit and forward: every duty compared in tables, the field maps, and the warnings with what to do about each.'}>
                  <span>
                    <Button size="small"
                      disabled={reportBusy(die.name, c.name)}
                      onClick={() => runExport('report', die.name, c.name)}
                      sx={{ fontSize: 11, py: 0, px: 0.6, minWidth: 0,
                            textTransform: 'none', color: '#a78bfa' }}>
                      {ringBusy(ring, `report:${die.name}/${c.name}`)
                        ? <ReportRing ring={ring} />
                        : (exporting === `report:${die.name}/${c.name}`
                          ? '… report' : '⭳ report')}
                    </Button>
                  </span>
                </Tooltip>
                {/* The failure, where it was clicked: one short sentence, the
                    whole text in the tooltip (lib/runNotice, the Run button's
                    own pattern).  A report that dies must not leave the user
                    looking at a button that simply came back. */}
                {ring.notice && ring.key.endsWith(`:${die.name}/${c.name}`) && (
                  <Tooltip title={ring.notice.full}>
                    <Typography onClick={() => setRing(RING_IDLE)}
                      sx={{ fontSize: 11, cursor: 'pointer', maxWidth: 320,
                            overflow: 'hidden', textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            color: ring.notice.kind === 'error'
                              ? '#f87171' : 'var(--text-3)' }}>
                      ✗ {ring.notice.text}
                    </Typography>
                  </Tooltip>
                )}
                {(c.duties?.length ?? 0) > 1 && (
                  <Tooltip title="Which duty's field maps go into the report — |B|, A_z, losses, temperature, stress, mode shapes. 'auto' is the backend's rule: the duty named rated, else the one loaded in the editor. Only a duty with stored fields can be drawn from; one without falls back to auto.">
                    <select
                      value={picFor(die.name, c.name)}
                      onChange={(e) => setPicFor(die.name, c.name, e.target.value)}
                      style={{ fontSize: 11, padding: '0 2px', marginLeft: 2,
                               background: 'transparent', color: '#a78bfa',
                               border: '1px solid var(--line-soft)', borderRadius: 4,
                               maxWidth: 150 }}>
                      <option value="">pictures: auto</option>
                      {(c.duties ?? []).map((d) => (
                        <option key={d.name} value={d.name}>pictures: {d.name}</option>
                      ))}
                    </select>
                  </Tooltip>
                )}
                {/* …and the same report as a PDF, for sending it read-only.
                    Small, because Word is the one the user asked to lead. */}
                <Tooltip title={ringBusy(ring, `report:pdf:${die.name}/${c.name}`)
                  ? ringTip(ring)
                  : 'The same report as a PDF — read-only, for sending on. Word can export one itself; this is the shortcut.'}>
                  <span>
                    <Button size="small"
                      disabled={reportBusy(die.name, c.name)}
                      onClick={() => runExport('report', die.name, c.name, true)}
                      sx={{ fontSize: 10, py: 0, px: 0.4, minWidth: 0,
                            textTransform: 'none', color: 'var(--text-4)' }}>
                      {ringBusy(ring, `report:pdf:${die.name}/${c.name}`)
                        ? <ReportRing ring={ring} size={13} />
                        : (exporting === `report:pdf:${die.name}/${c.name}`
                          ? '…' : 'pdf')}
                    </Button>
                  </span>
                </Tooltip>
                {!canWrite && (
                  <Tooltip title="Copy this machine into MY MOTORS — your private space, visible only to you until you share it">
                    <span>
                      <Button size="small" disabled={!!busy}
                        onClick={() => duplicateToMySpace(die.name, c.name)}
                        sx={{ fontSize: 11, py: 0, px: 0.6, minWidth: 0,
                              textTransform: 'none', color: '#60a5fa' }}>
                        ⧉ to my motors
                      </Button>
                    </span>
                  </Tooltip>
                )}
                <Tooltip title={roleTip(c)}>
                  <Chip size="small" label={c.role}
                    sx={{ height: 18, fontSize: 10, color: roleColor(c.role),
                          bgcolor: 'transparent', cursor: 'help',
                          border: `1px solid ${roleColor(c.role)}55` }} />
                </Tooltip>
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  {c.stack_mm} mm · wire {c.wire_height_mm}×{c.wire_width_mm} mm
                  {' '}· {c.turns} turns
                  {c.connection ? ` · ${c.connection}` : ''}
                  {String(c.star_delta ?? 'star').startsWith('d') ? ' · Δ' : ' · Y'}
                  {c.steel ? ` · ${c.steel}` : ''}
                  {c.magnet ? ` · ${c.magnet}` : ''}
                </Typography>
                {(c as any).name_stack_mismatch && (
                  <Tooltip title={`The L-number in the name contradicts the stored stack (${c.stack_mm} mm). New saves can no longer create this; rename the configuration (✎) so the catalog stops repeating it.`}>
                    <Chip size="small" label="⚠ name ≠ stack"
                      sx={{ height: 18, fontSize: 10, color: '#f59e0b',
                            bgcolor: 'transparent', border: '1px solid #f59e0b88' }} />
                  </Tooltip>
                )}
                {c.battery && (
                  <Tooltip title={`Supply: ${c.battery.cells ?? '?'} cells, `
                    + `${c.battery.v_min}–${c.battery.v_max} V pack`
                    + (c.battery.cells ? ` (${(c.battery.v_min / c.battery.cells).toFixed(2)}–${(c.battery.v_max / c.battery.cells).toFixed(2)} V/cell)` : '')
                    + '. Duty V L-L cells are judged against this range.'}>
                    <Typography component="span"
                      sx={{ fontSize: 11, color: 'var(--text-3)', cursor: canWrite ? 'pointer' : 'help' }}
                      onClick={canWrite ? () => editBattery(die.name, c) : undefined}>
                      🔋 {c.battery.chemistry ? `${c.battery.chemistry} ` : ''}{c.battery.cells ?? '?'}s · {fmt(c.battery.v_min, 0)}–{fmt(c.battery.v_max, 0)} V
                    </Typography>
                  </Tooltip>
                )}
                {!c.battery && canWrite && (
                  <Tooltip title="Set the supply battery — duties will be judged against its voltage range">
                    <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-4)', cursor: 'pointer' }}
                      onClick={() => editBattery(die.name, c)}>🔋 —</Typography>
                  </Tooltip>
                )}
                <Box sx={{ flex: 1 }} />
                {canWrite && (
                  <Tooltip title="Save the CURRENT Simulation point as a new duty here">
                    <span>
                      <Button size="small" variant="text" disabled={!!busy}
                        onClick={() => createDuty(die.name, c.name)}
                        sx={{ textTransform: 'none', fontSize: 11, minWidth: 0, px: 0.5 }}>
                        ＋ duty
                      </Button>
                    </span>
                  </Tooltip>
                )}
                {canWrite && (
                  <Tooltip title="Delete this configuration and its duties">
                    <span>
                      <IconButton size="small" disabled={!!busy}
                        onClick={() => deleteCfg(die.name, c.name)}
                        sx={{ color: 'var(--text-4)', fontSize: 12, p: 0.3 }}>🗑</IconButton>
                    </span>
                  </Tooltip>
                )}
              </Box>
              {c.duties.length > 0 && (
                <Box component="table" sx={{
                  width: '100%', mt: 0.4, mb: 0.4, borderCollapse: 'collapse',
                  '& th': { fontSize: 10, fontWeight: 600, color: 'var(--text-4)',
                            textAlign: 'right', p: '2px 6px', whiteSpace: 'nowrap' },
                  '& td': { fontSize: 11.5, color: 'var(--text-2)', textAlign: 'right',
                            p: '2px 6px', whiteSpace: 'nowrap',
                            fontVariantNumeric: 'tabular-nums',
                            borderTop: '1px solid var(--panel)' },
                  '& th:first-of-type, & td:first-of-type': { textAlign: 'left' },
                }}>
                  <thead>
                    <tr>
                      <th>duty</th><th>kW</th><th>Nm</th><th>rpm</th>
                      <th>A</th><th>V L-L</th><th>η %</th><th>ripple %</th>
                      <th>loss W</th><th>kg</th><th>KV</th>
                      <th style={{ textAlign: 'center' }} />
                    </tr>
                  </thead>
                  <tbody>
                    {c.duties.map(d => {
                      const r = d.result || {};
                      // Result recorded on an OLDER build (the configuration's
                      // geometry/winding/materials changed since) — show it,
                      // but say loudly that it needs a re-run.
                      const staleRes = !!(r.build_sig && c.build_sig
                                          && r.build_sig !== c.build_sig);
                      const applying = busy === `${c.name} / ${d.name}`;
                      const isActive = active?.die === die.name
                        && active?.config === c.name && active?.duty === d.name;
                      return (
                        <tr key={d.name}
                          style={isActive ? { background: '#60a5fa14' } : undefined}>
                          <td>
                            <Tooltip placement="top-start"
                              title={`${d.mode}${d.note ? ` — ${d.note}` : ''}`
                                     + (d.saved_at ? ` · saved ${d.saved_at}` : '')
                                     + (r.recorded_at ? ` · recorded ${r.recorded_at}` : '')
                                     + (staleRes ? ' · ⚠ results computed on an OLDER build — load (▶), Run, then Save' : '')}>
                              <span style={{ color: roleColor(d.mode), fontWeight: 600,
                                             cursor: 'help' }}>
                                {staleRes ? '⚠ ' : ''}{d.name}
                                {d.saved_at && (
                                  <span style={{ color: 'var(--text-4)', fontWeight: 400,
                                                 fontSize: 10, marginLeft: 6 }}>
                                    {fmtWhen(d.saved_at)}
                                  </span>
                                )}
                              </span>
                            </Tooltip>
                            {/* What ELSE this point has been run on.  Badges, not
                                buttons: there is exactly one load action (▶),
                                and it brings every stored run with it — the
                                Simulation panel switches between them. */}
                            {(d.runs ?? []).filter(rn => !rn.primary).map(rn => (
                              <Tooltip key={rn.drive} placement="top"
                                title={`${driveLabel(rn.drive)} run of this point, stored`
                                  + (rn.recorded_at ? ` ${rn.recorded_at}` : '')
                                  + (rn.f_switch_hz ? ` · carrier ${(Number(rn.f_switch_hz) / 1000).toFixed(1)} kHz` : '')
                                  + (rn.steps ? ` · ${rn.steps} steps/period` : '')
                                  + (rn.ripple_pct != null ? ` · ripple ${Number(rn.ripple_pct).toFixed(1)} %` : '')
                                  + (rn.stale ? ' · ⚠ solved on a different build/materials — re-run it'
                                              : '')
                                  + '. Press ▶ to load the duty; pick it in the Simulation run selector.'}>
                                <span style={{
                                  marginLeft: 5, fontSize: 9.5, padding: '0 4px',
                                  borderRadius: 3, cursor: 'help', fontWeight: 600,
                                  color: rn.stale ? '#f59e0b' : '#38bdf8',
                                  border: `1px solid ${rn.stale ? '#f59e0b88' : '#38bdf855'}`,
                                }}>
                                  {rn.stale ? '⚠' : ''}{driveLabel(rn.drive)}
                                </span>
                              </Tooltip>
                            ))}
                            {/* WHAT THE MACHINE DOES with this point
                                (2026-09-14).  A duty with no cycle gets no chip
                                — saying "S1" for it would claim a continuous
                                rating nobody wrote.  One short chip, the block
                                itself in the tooltip (no-walls-of-text rule). */}
                            {/* …and NOTHING at all while the duty-cycle feature
                                is off (owner 2026-09-17) — the chip is the one
                                place a hidden feature would still name itself
                                in the catalog.  `lib/dutyCycleFlag`. */}
                            {gatedDutyCycleChip(dutyCycleChip(d.duty_cycle)) && (
                              <Tooltip key="dc" placement="top"
                                title={dutyCycleTip(d.duty_cycle)}>
                                <span style={{
                                  marginLeft: 5, fontSize: 9.5, padding: '0 4px',
                                  borderRadius: 3, cursor: 'help', fontWeight: 600,
                                  color: '#a78bfa', border: '1px solid #a78bfa55',
                                }}>
                                  {gatedDutyCycleChip(dutyCycleChip(d.duty_cycle))}
                                </span>
                              </Tooltip>
                            )}
                            {/* HOW LONG MAY IT RUN (owner 2026-09-17).  A point
                                the coupled loop found past a limit gets the
                                other half of the answer right here, where the
                                reader is when they ask whether they may pull
                                it.  NOT gated by the duty-cycle flag — this is
                                not a cycle — and absent entirely on a point
                                inside every limit (`lib/timeToLimitChip`). */}
                            {timeToLimitChip(d.time_to_limit) && (
                              <Tooltip key="ttl" placement="top"
                                title={timeToLimitChipTip(d.time_to_limit)}>
                                <span style={{
                                  marginLeft: 5, fontSize: 9.5, padding: '0 4px',
                                  borderRadius: 3, cursor: 'help', fontWeight: 600,
                                  color: '#f59e0b', border: '1px solid #f59e0b88',
                                }}>
                                  {timeToLimitChip(d.time_to_limit)}
                                </span>
                              </Tooltip>
                            )}
                          </td>
                          {/* kW keeps its decimal even when it is .0 (user
                              2026-08-25) — fmt() strips trailing zeros. */}
                          <td>{d.power_kw == null || !Number.isFinite(Number(d.power_kw))
                            ? '—' : Number(d.power_kw).toFixed(1)}</td>
                          <td>{fmt(d.torque_nm)}</td>
                          <td>{fmt(d.rpm, 0)}</td>
                          {/* The current is the LINE current; Δ / Y says which
                              connection the duty was solved with (winding
                              current = ÷√3 in delta). */}
                          <td title={d.star_delta
                            ? (String(d.star_delta).startsWith('d')
                                ? 'delta (Δ): line current — the winding carries ÷√3'
                                : 'star (Y): line = winding current')
                            : undefined}>
                            {fmt(d.current_arms)}
                            {d.star_delta && (
                              <span style={{ marginLeft: 4, fontSize: 9.5, fontWeight: 700,
                                             color: String(d.star_delta).startsWith('d') ? '#fbbf24' : '#94a3b8' }}>
                                {String(d.star_delta).startsWith('d') ? 'Δ' : 'Y'}
                              </span>
                            )}
                          </td>
                          {(() => {
                            const v = Number(r.v_ll_peak_v);
                            const b = c.battery;
                            let col: string | undefined;
                            let tip = '';
                            if (b && Number.isFinite(v) && v > 0) {
                              if (v <= b.v_min) { col = '#34d399'; tip = `needs ≥${v.toFixed(0)} V DC — fits the whole ${b.v_min}–${b.v_max} V range, margin ${(100 * (b.v_min / v - 1)).toFixed(0)}% at empty`; }
                              else if (v <= b.v_max) { col = '#fbbf24'; tip = `needs ≥${v.toFixed(0)} V DC — fits only above ${(100 * v / b.v_max).toFixed(0)}% of the charged pack (${b.v_min}–${b.v_max} V)`; }
                              else { col = '#f87171'; tip = `needs ≥${v.toFixed(0)} V DC — DOES NOT fit the ${b.v_min}–${b.v_max} V pack`; }
                            }
                            if (staleRes) col = '#fbbf24';
                            return (
                              <td style={col ? { color: col } : undefined}>
                                {tip ? (
                                  <Tooltip title={tip}><span style={{ cursor: 'help' }}>{fmt(r.v_ll_peak_v, 0)}</span></Tooltip>
                                ) : fmt(r.v_ll_peak_v, 0)}
                              </td>
                            );
                          })()}
                          <td style={staleRes ? { color: '#fbbf24' } : undefined}>{fmt(r.efficiency_pct, 2)}</td>
                          <td style={staleRes ? { color: '#fbbf24' } : undefined}>{fmt(r.ripple_pct)}</td>
                          {/* TOTAL loss, the one the efficiency beside it is
                              computed from: electromagnetic plus bearings and
                              windage when the machine has them (2026-09-11).
                              The tooltip splits it, so the column stays one
                              number. */}
                          <Tooltip title={r.loss_mech_w != null
                            ? `${fmt(r.loss_w, 0)} W electromagnetic + ${fmt(r.loss_mech_w, 0)} W bearings and windage`
                              + (r.loss_mech_derived
                                 ? ' — the mechanical watts read back from this duty’s own run, which was saved before they were stored beside the result'
                                 : '')
                            : 'electromagnetic losses; this run carried no bearings, so the mechanical loss is UNKNOWN, not zero'}>
                            <td style={staleRes ? { color: '#fbbf24' } : undefined}>
                              {fmt((r.loss_w ?? 0) + (r.loss_mech_w ?? 0), 0)}
                              {r.loss_mech_w == null ? ' *' : ''}
                            </td>
                          </Tooltip>
                          <td style={staleRes ? { color: '#fbbf24' } : undefined}>{fmt(r.mass_kg, 2)}</td>
                          {/* KV instead of γ (user 2026-08-25) — rpm per volt
                              of the recorded LINE PEAK, the same max/max
                              convention as the Simulation tile; γ moved into
                              the tooltip. */}
                          <td>
                            <Tooltip title={(r && (r as any).kv_rpm_per_v != null
                              ? `KV as saved from the card (${Number((r as any).kv_is_noload) ? 'no-load / bench' : 'loaded'} convention). `
                              : 'rpm / V_line_peak of the recorded run (loaded). ')
                              + `γ = ${fmt(d.gamma_deg)}°`}>
                              <span style={{ cursor: 'help' }}>
                                {r && (r as any).kv_rpm_per_v != null
                                  ? Number((r as any).kv_rpm_per_v).toFixed(1)
                                  : (r && Number(r.v_ll_peak_v) > 0 && Number(d.rpm) > 0
                                      ? (Number(d.rpm) / Number(r.v_ll_peak_v)).toFixed(1)
                                      : '—')}
                              </span>
                            </Tooltip>
                          </td>
                          <td style={{ textAlign: 'center' }}>
                            <Tooltip title="Load into Simulation">
                              <span>
                                <IconButton size="small" disabled={!!busy}
                                  onClick={() => applyDuty(die.name, c.name, d.name)}
                                  sx={{ fontSize: 12, p: 0.2, color: '#34d399' }}>
                                  {applying ? '…' : '▶'}
                                </IconButton>
                              </span>
                            </Tooltip>
                            {canWrite && (
                              <Tooltip title="Rename duty">
                                <span>
                                  <IconButton size="small" disabled={!!busy}
                                    onClick={() => renameDuty(die.name, c.name, d.name)}
                                    sx={{ fontSize: 11, p: 0.2, ml: 0.5,
                                          color: 'var(--text-3)' }}>✎</IconButton>
                                </span>
                              </Tooltip>
                            )}
                            {canWrite && (
                              <Tooltip title="Duplicate duty (full copy incl. result)">
                                <span>
                                  <IconButton size="small" disabled={!!busy}
                                    onClick={() => duplicateDuty(die.name, c.name, d.name)}
                                    sx={{ fontSize: 12, p: 0.2, ml: 0.5,
                                          color: 'var(--text-3)' }}>⧉</IconButton>
                                </span>
                              </Tooltip>
                            )}
                            {canWrite && (
                              <Tooltip title="Delete duty">
                                <span>
                                  {/* Deliberate distance from ▶/⧉ — load and DELETE
                                      must not be a one-pixel slip apart. */}
                                  <IconButton size="small" disabled={!!busy}
                                    onClick={() => deleteDuty(die.name, c.name, d.name)}
                                    sx={{ fontSize: 12, p: 0.2, ml: 2,
                                          color: 'var(--text-4)' }}>✕</IconButton>
                                </span>
                              </Tooltip>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </Box>
              )}
            </Box>
          ))}
        </Box>
      ))}
    </>
  );

  const dialogs = (
    <>
      <TextPromptDialog state={askText} onClose={() => setAskText(null)} />
      <ConfirmDialog state={askConfirm} onClose={() => setAskConfirm(null)} />
      <BatteryDialog open={!!batteryFor}
        configName={batteryFor?.cfg.name ?? ''}
        initial={(batteryFor?.cfg.battery ?? null) as BatteryValue | null}
        onClose={() => setBatteryFor(null)}
        onSave={(v) => {
          const t = batteryFor; setBatteryFor(null);
          if (!t) return;
          mutate(`battery set on '${t.cfg.name}'`, () =>
            fetch(`${API}/api/family/config/${encodeURIComponent(t.die)}/${encodeURIComponent(t.cfg.name)}/battery`, {
              method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(v),
            }));
        }} />
    </>
  );

  if (embedded) {
    return <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>{body}{dialogs}</Box>;
  }
  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
                 p: 2, mb: 2, display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-1)' }}>
          Families — die / configuration / duty
          <Tooltip placement="top" title="One stamped lamination (die) is frozen geometry. A configuration changes only what the stamp does not fix: stack length, wire and winding. A duty is a named operating point (mode, current, rpm, γ). Click a duty to load the whole machine into Simulation; ＋ buttons snapshot the CURRENT state at each level.">
            <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
          </Tooltip>
        </Typography>
        <Box sx={{ flex: 1 }} />
        {busy && <CircularProgress size={14} />}
        {canWrite && (
          <Button size="small" variant="outlined" onClick={createDie}
            sx={{ textTransform: 'none', fontSize: 11 }}>
            ＋ die from current geometry
          </Button>
        )}
      {histOf && (
        <ConfigHistoryDialog open onClose={() => setHistOf(null)}
          die={histOf.die} config={histOf.config} canWrite={canWrite}
          onRestored={() => { void load(); try { window.dispatchEvent(new CustomEvent('family-changed')); } catch { /* SSR */ } }}/>
      )}
      </Box>
      {body}
      {dialogs}
    </Paper>
  );
};

export default FamilyCatalog;
