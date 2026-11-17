/**
 * FamilyCatalog — Die → Configuration (motors only).
 *
 * One stamped lamination (die, locked) carries several buildable
 * configurations (stack length, wire, winding).  A configuration button loads
 * the configuration's default duty (`rated`) — the WHOLE machine state:
 * geometry, winding, operating point — through the same endpoints the rest of
 * the app already uses; this panel writes nothing of its own into the live
 * config.
 *
 * The configuration's OTHER duties (operating points) are not rows here any
 * more (owner 2026-11-04: «все duty переходят внутрь моделирования»): they
 * appear as the point switcher of the Electromagnetic Simulation tab once the
 * motor is loaded, together with creating, renaming, duplicating, deleting
 * and defaulting them (components/simulation/SimPointSwitcher).
 */
import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  Box, Paper, Typography, Button, Chip, Tooltip, IconButton, CircularProgress,
  Dialog, DialogTitle, DialogContent,
} from '@mui/material';
import PublicOutlinedIcon from '@mui/icons-material/PublicOutlined';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import { useUIStore } from '../../stores/motorStore';
import { thumbOf, type DieThumbs } from '../../lib/dieThumbs';
import CatalogByDie, { type ByDieActions, type RowCells } from './CatalogByDie';
import StartProject from './StartProject';
import {
  TextPromptDialog, ConfirmDialog,
  type TextPromptState, type ConfirmState,
} from '../common/PromptDialogs';
import BatteryDialog, { type BatteryValue } from './BatteryDialog';
import ConfigHistoryDialog from './ConfigHistoryDialog';
import type { DutyTimeToLimit } from '../../lib/timeToLimitChip';
import type { DutyContinuousRating } from '../../lib/continuousRatingChip';
// The WHOLE load — the server half (owner) and the local half the follower in
// ActiveFamilyStrip shares (lib/dutyLocalApply, lib/familyFollow) — lives in
// lib/dutyApply so the strip's "reload ▶" and this ▶ can never drift.
import { applyCatalogDuty } from '../../lib/catalogDutyApply';
import { downloadExport } from '../../lib/exportDownload';
import { configMoveSummary, type MovePlan } from '../../lib/configMove';
import {
  IDLE as RING_IDLE, newRunId, ringBusy, ringDone, ringFail, ringPoll,
  ringStart, ringTip, type ReportProgressInfo, type ReportRing,
} from './reportProgress';
import { fetchDieThumbs, fetchFamilyTree, peekFamilyTree, SIGN_IN_NOTE } from '../../lib/familyTree';
import { pageVisible } from '../../lib/pageVisible';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { coggingDigits, coggingTipText, coggingValue, type CoggingView } from '../../lib/coggingTile';
import { useAuth } from '../../contexts/AuthContext';
import { canWriteServer } from '../../lib/localAuth';
import { emptyCatalogLine, isStandardUser, uiCanWrite } from '../../lib/accessUi';
import { staleCatalogDutyNames } from '../../lib/catalogCardStaleness';
import { cardStatusTip, dieOpenness, OPENNESS_COLOR, OPENNESS_TIP_KEY } from '../../lib/catalogOpenness';
import { configButtons, isListableForClient, isPointsOnly, loadedMotor, loadTargetTab } from '../../lib/catalogRows';
import ApplicationEditor from '../admin/ApplicationEditor';
import { cleanApplications, diesOfDiameter, filterDiesByApplication, type ApplicationFilter } from '../../lib/applications';
import { getFamilyLoad, subscribeFamilyLoad } from '../../lib/familyLoad';
import { useLastMotorState } from '../../lib/lastMotor';
import { isConfigHighlighted, isConfigRecommended, isDieHighlighted } from '../../modules/development/projectFocus';
import { useProjectHighlightStore } from '../../stores/projectHighlightStore';
import type { DutyClassInfo } from '../../lib/dutyClass';
import { suggestDieName } from '../../lib/dieNames';

// UI strings: locales/<lng>/motors.json (docs/I18N.md).
const tx = nsT('motors');
const txCommon = nsT('common');   // the passport-card tooltip strings (card.*) live in the common namespace
const txc = nsT('controller');   // the cogging tile's strings live with Configure's (one label, one tooltip everywhere)

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

// FamilyCatalog can be mounted once per stator diameter. Keep one browser-side
// refresh timer and let the shared familyTree memo coalesce those subscribers
// into one request, so an admin's newly saved shared motor appears without a
// reload while already-open Configure state is left untouched.
const familyCatalogRefreshers = new Set<() => void>();
let familyCatalogRefreshTimer: number | null = null;
let familyCatalogRefreshInProgress = false;
function subscribeFamilyCatalogRefresh(refresh: () => void): () => void {
  familyCatalogRefreshers.add(refresh);
  if (familyCatalogRefreshTimer === null) {
    familyCatalogRefreshTimer = window.setInterval(() => {
      if (!pageVisible() || familyCatalogRefreshInProgress) return;
      familyCatalogRefreshInProgress = true;
      void fetchFamilyTree({ fresh: true }).then(() => {
        window.dispatchEvent(new Event('family-catalog-refreshed'));
        for (const subscriber of familyCatalogRefreshers) subscriber();
      }).catch(() => {
        for (const subscriber of familyCatalogRefreshers) subscriber();
      }).finally(() => { familyCatalogRefreshInProgress = false; });
    }, 5000);
  }
  return () => {
    familyCatalogRefreshers.delete(refresh);
    if (!familyCatalogRefreshers.size && familyCatalogRefreshTimer !== null) {
      window.clearInterval(familyCatalogRefreshTimer);
      familyCatalogRefreshTimer = null;
    }
  };
}

interface DutyResult {
  efficiency_pct?: number; ripple_pct?: number; v_ll_peak_v?: number;
  loss_w?: number; loss_mech_w?: number; loss_mech_derived?: boolean;
  mass_kg?: number; mass_active_kg?: number; mass_partial?: boolean; mass_partial_reason?: string; recorded_at?: string;
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
  /** THE CONTINUOUS (S1) RATING at this duty's own saved cooling (2026-09-21).
   *  Absent on a duty that never asked `solve_to: continuous`. */
  continuous_rating?: DutyContinuousRating | null;
  /** SHORT-TIME OR CONTINUOUS (owner 2026-10-19) — absent when nothing says. */
  duty_class?: DutyClassInfo['duty_class'];
  duty_label?: string | null;
  short_time?: DutyClassInfo['short_time'];
}
interface Battery {
  chemistry?: string | null; cells?: number | null;
  v_cell_min?: number; v_cell_nom?: number | null; v_cell_max?: number;
  v_min: number; v_max: number; v_nom?: number | null;
}
interface Cfg {
  name: string; role: string; locked?: boolean; build_sig?: string; build_sig_aliases?: string[];
  /** 'duties' when the role was read off the saved duties, 'stored' when there
   *  are none yet and the creation-time toggle is all there is. */
  role_source?: string;
  /** what the yaml still says, kept visible when it disagrees. */
  role_stored?: string;
  /** what the configuration is for (first = default); [] = not set */
  applications?: string[];
  application?: string | null;
  applications_error?: string | null;
  battery?: Battery | null;
  /** the saved no-load cogging torque of the configuration (owner 2026-10-26); absent = none saved */
  cogging?: CoggingView | null;
  /** the mode the row shows and loads (server-resolved; owner 2026-10-13) */
  default_duty?: string | null;
  stack_mm: number | null;
  wire_height_mm: number | null; wire_width_mm: number | null;
  turns: number | null; connection: string | null; star_delta?: string | null;
  /** 6 = two in-phase 3-phase sets: currents are shown PER SET (lib/perSetCurrent) */
  phases?: number | null;
  magnet?: string | null; steel?: string | null;
  duties: Duty[];
  /** date of this configuration's FULL passport card (v1 record), null = none */
  card_date?: string | null;
}
interface Die {
  name: string; locked: boolean; created?: string;
  slots: number; poles: number; stator_diameter: number;
  /** older servers still send the drawing here; since 2026-11-12 it comes from GET /api/family/thumbs (lib/dieThumbs) */
  thumb_svg?: string | null; has_thumb?: boolean; configs: Cfg[];
  /** how many of its configurations have a full passport card */
  cards?: number; config_count?: number;
  /** "workspace" | "published" | "shared" — absent when layering is off
   *  (single-catalog workstation). Only a catalog admin may delete a
   *  published or shared die; see AGENTS.md / DELETE /api/family/die. */
  layer?: string;
  /** 'open' = the die's data lives in the public repository; anything else is a closed
   *  motor (owner 2026-10-27).  Stamped by GET /api/family/tree; see lib/catalogOpenness. */
  source?: string | null;
}


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

/** THE one per-row distinction of the Motors list (owner 2026-10-27): open (public data) or
 *  closed (private, NDA) - the same small icon on every row, muted grey for open, amber for
 *  closed, the meaning in the tooltip. */
const OpennessMarker: React.FC<{ die: Die }> = ({ die }) => {
  const kind = dieOpenness(die);
  const Icon = kind === 'open' ? PublicOutlinedIcon : LockOutlinedIcon;
  return (
    <Tooltip title={tx(OPENNESS_TIP_KEY[kind])}>
      <Box component="span" data-testid="openness-marker" data-openness={kind}
        aria-label={tx(OPENNESS_TIP_KEY[kind])}
        sx={{ display: 'inline-flex', alignItems: 'center', cursor: 'help',
              color: OPENNESS_COLOR[kind] }}>
        <Icon sx={{ fontSize: 15 }} />
      </Box>
    </Tooltip>
  );
};

/** The configuration's application as a small badge (owner 2026-10-13); a configuration with no
 *  list shows nothing here (Admin flags it), a malformed one shows a red "?" with the reason. */
const ApplicationBadge: React.FC<{ cfg?: Cfg; die?: string; canEdit?: boolean }> = ({ cfg, die, canEdit }) => {
  const [editing, setEditing] = useState(false);
  if (!cfg) return null;
  const apps = cleanApplications(cfg.applications);
  if (cfg.applications_error) {
    return (
      <Tooltip title={tx('applications.invalidTip', { error: cfg.applications_error })}>
        <Chip size="small" label="?" sx={{ height: 16, fontSize: 10, color: '#f87171', bgcolor: 'transparent',
                                         border: '1px solid #f8717155', cursor: 'help' }} />
      </Tooltip>
    );
  }
  // admin / owner only: a small pencil next to the badge opens the editor
  const pencil = canEdit && die ? (
    <>
      <Tooltip title={tx('applications.edit')}>
        <IconButton size="small" onClick={() => setEditing(true)} sx={{ fontSize: 10, p: 0.1, color: 'var(--text-4)' }}>✎</IconButton>
      </Tooltip>
      {editing && (
        <Dialog open onClose={() => setEditing(false)}>
          <DialogTitle sx={{ fontSize: 13 }}>{die} / {cfg.name} · {tx('applications.label')}</DialogTitle>
          <DialogContent>
            <ApplicationEditor die={die} config={cfg.name} applications={cfg.applications}
              onDone={() => setEditing(false)} />
          </DialogContent>
        </Dialog>
      )}
    </>
  ) : null;
  if (!apps.length) return pencil;
  return (
    <>
      <Tooltip title={tx('applications.badgeTip', { application: apps.map((a) => tx(`applications.${a}`)).join(', ') })}>
        <Chip size="small" label={tx(`applications.${apps[0]}`)}
          sx={{ height: 16, fontSize: 10, color: 'var(--text-3)', bgcolor: 'transparent',
                border: '1px solid var(--line-soft)', cursor: 'help' }} />
      </Tooltip>
      {pencil}
    </>
  );
};

const FamilyCatalog: React.FC<{
  /** Show only dies of this stator diameter (the page groups by Ø). */
  diameter?: number;
  /** Render bare die blocks (no panel, no header) for embedding in a Ø group. */
  embedded?: boolean;
  /** Motors application filter (all / propeller / robot / aviation / marine). */
  application?: ApplicationFilter;
}> = ({ diameter, embedded, application = 'all' }) => {
  useTranslation('motors'); // re-render on language change; lazy-loads the namespace
  useTranslation('controller'); // the cogging chip borrows Configure's label and tooltip
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const [dies, setDies] = useState<Die[]>(() => ((peekFamilyTree()?.dies ?? []) as Die[]));
  const [busy, setBusy] = useState<string | null>(null);   // what is being applied/created
  // A load started from this panel outlives it (the view switches at once): while one runs
  // anywhere, no second load starts.
  const loadRunning = useSyncExternalStore(subscribeFamilyLoad, () => getFamilyLoad().status === 'loading', () => false);
  const [msg,  setMsg]  = useState<string | null>(null);   // last outcome (ok or error)
  // Clients browse and LOAD; editing the family catalog is the vendor's job.
  // The backend enforces this on every mutating endpoint — this flag only
  // decides whether the editing controls are drawn at all.
  const [canWrite, setCanWrite] = useState(() => { const t = peekFamilyTree(); return t ? uiCanWrite(t.can_write, canWriteServer()) : false; });
  const { user, enforced, isAdmin, role } = useAuth();
  const standardUser = isStandardUser(enforced, isAdmin, role);


  // What is loaded in the editor — its die/config/duty rows get highlighted.
  // `ctxActive` = the SHARED machine the server reports (/api/family/context); `active` below = the machine
  // THIS browser has loaded.  For an ordinary account those differ (incident 2026-10-15), so every
  // "is it loaded" test reads `active`, never `ctxActive`.
  const [ctxActive, setActive] = useState<{ die?: string; config?: string;
                                         duty?: string | null } | null>(null);
  const lastMotor = useLastMotorState();
  // the selected project's motors are marked (owner 2026-10-26); a view aid, the list itself is unchanged
  const projectIndex = useProjectHighlightStore((s) => s.index);
  const identity = user?.email?.trim().toLowerCase() ?? null;
  const active = loadedMotor(
    enforced && !!identity && !isAdmin,
    lastMotor.email === identity && lastMotor.status === 'loaded' ? lastMotor.selection : null,
    ctxActive);

  // MUI dialogs instead of window.prompt/confirm — Chrome suppresses the
  // native ones after "prevent additional dialogs" and the buttons look dead
  // (user hit it on the battery editor).
  const [askText, setAskText] = useState<TextPromptState | null>(null);
  // History dialog target: a configuration, or (config null) the die geometry.
  const [histOf, setHistOf] = useState<{ die: string; config: string | null } | null>(null);
  const [askConfirm, setAskConfirm] = useState<ConfirmState | null>(null);
  const [batteryFor, setBatteryFor] = useState<{ die: string; cfg: Cfg } | null>(null);
  // the admin overflow menu of a die row (owner 2026-10-27): holds "Retire for all users"

  // null = not loaded yet / load FAILED — a different state from "loaded and
  // empty".  A backend mid-restart used to render as "no dies yet" (live
  // 2026-08-24: the user read it as their catalog being gone), so a failed
  // load now says so and RETRIES until the backend answers.
  const [loadFailed, setLoadFailed] = useState(false);
  const [treeLoaded, setTreeLoaded] = useState(false);   // the tree has answered once
  // the dies' cross-section drawings: a separate request since 2026-11-12 (they were two thirds of the tree)
  const [thumbs, setThumbs] = useState<DieThumbs | null>(null);
  // The backend's one-line reason for an empty catalog (a signed-in account
  // with no motors granted yet) — an empty page tells that user nothing.
  const [note, setNote] = useState<string | null>(null);
  const [noteCode, setNoteCode] = useState<string | null>(null);
  // The tree comes through lib/familyTree: every Ø section on the Motors tab
  // is one of these, and each used to fetch the 900 KB tree for itself
  // (2026-09-13).  `fresh` after a mutation or a `family-changed` event.
  const load = async (fresh = false) => {
    try {
      const t = await fetchFamilyTree({ fresh });
      setDies((t.dies as Die[]) || []);
      void fetchDieThumbs().then(setThumbs);
      // The server's can_write = "may write a copy into MY workspace" (true for every
      // registered account); the UI's = may write the SHARED config (admin / local dev).
      setCanWrite(uiCanWrite(t.can_write, canWriteServer()));
      setNote(typeof t.note === 'string' ? t.note : null);
      setNoteCode(typeof t.note_code === 'string' ? t.note_code : null);
      const c = await (await fetch(`${API}/api/family/context`, { cache: 'no-store' })).json();
      setActive(c?.active ? c : null);
      setLoadFailed(false); setTreeLoaded(true);
    } catch (e) {
      // 401: the server publishes nothing to anonymous visitors
      // (PUBLIC_EXHIBIT=0).  That is an answer, not an outage — no retry loop,
      // no red "load failed", just the line that tells the user to sign in.
      if ((e as { status?: number })?.status === 401) {
        setDies([]); setCanWrite(false); setActive(null);
        setLoadFailed(false); setTreeLoaded(true); setMsg(null); setNote(SIGN_IN_NOTE);
        return;
      }
      setLoadFailed(true);      // retried below every 3 s; the tab (MotorsCatalog) says so in red after a quiet ~20 s
    }
  };
  useEffect(() => { load(); }, []);
  // Other sessions may have updated the shared catalog. Poll while visible;
  // the tree refresh changes this catalog view only and never applies geometry.
  useEffect(() => subscribeFamilyCatalogRefresh(() => { void load(); }), []);
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
  const mutate = async (label: string | ((data: any) => string), fn: () => Promise<Response>) => {
    const pendingLabel = typeof label === 'string' ? label : 'saving configuration';
    setBusy(pendingLabel); setMsg(null);
    try {
      const r = await fn();
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { detail = (await r.json()).detail ?? detail; } catch { /* keep */ }
        setMsg(`✗ ${detail}`);
      } else {
        let data: any = {};
        if (typeof label === 'function') {
          try { data = await r.json(); } catch { /* response data is optional */ }
        }
        setMsg(`✓ ${typeof label === 'function' ? label(data) : label}`);
      }
    } catch (e) { setMsg(`✗ ${pendingLabel}: ${e}`); }
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
  //                      WORD, not PDF, since 2026-09-09 (user: "better output
  //                      the report in doc format") — he edits the document and
  //                      forwards it to clients, and Word exports its own PDF.
  //                      The backend serves docx by default and pdf on
  //                      `?format=pdf`, which the small PDF button asks for.
  //
  // `exporting` holds "<kind>:<die>/<cfg>", so pressing one button does not
  // grey out the other on the same row.
  const [, setExporting] = useState<string | null>(null);   // the button of an export in flight (the ring shows it)

  // THE PROGRESS RING (user 2026-09-16: "we also need a minimal progress ring
  // for report generation, so you can see it's working and not stuck").
  // A report is ~50 s of figures and the PDF half a minute more, and
  // the download is ONE request whose body is the file — nothing on the wire
  // until it is finished.  So the click mints a run id, sends it with the
  // download, and a second request polls the stages the backend publishes into
  // the shared progress registry.  Rules in `./reportProgress`; this is the
  // plumbing.
  const [ring, setRing] = useState<ReportRing>(RING_IDLE);
  const pollRef = useRef(0);

  // WHICH DUTY'S PICTURES go into the report (user 2026-09-11: "we also need
  // a choice of which mode we publish the pictures from into the report").  The
  // backend's own rule is "the rated duty, then the loaded one"; this lets the
  // user override it per configuration.  Remembered per machine in
  // localStorage — a convenience, never state the report depends on: an empty
  // choice means the backend's rule, and every read is guarded because the
  // accessor itself can throw in a locked-down browser.
  const picFor = (die: string, cfg: string): string => {
    try { return localStorage.getItem(`report.pictures:${die}/${cfg}`) ?? ''; } catch { return ''; }
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
  // The name opens pre-filled with "<family base> <your suffix> <n>" (editable) - owner 2026-10-26.
  const createDie = async () => {
    const initial = await suggestDieName();
    setAskText({
      title: 'New die from the current geometry',
      label: 'Die name',
      initial,
      onSubmit: (name) => mutate((d) => `die '${String(d?.die || name)}' created`,
        () => post('/api/family/die', { name })),
    });
  };
  const createCfg = (die: string) => setAskText({
    title: `New configuration under ${die}`,
    label: 'Configuration name',
    hint: 'Snapshots the CURRENT stack / wire / winding / materials',
    onSubmit: (name) => {
      const role = readLS('opMode', 'motor') === 'generator' ? 'generator' : 'motor';
      mutate((data) => `configuration '${String(data?.config || name)}' created`, () =>
        post('/api/family/config', { die, name, role }));
    },
  });
  const deleteDie = (die: Die) => {
    const name = die.name;
    // A shared/published die's files are the vendor's / another author's —
    // deleting one reaches everybody, not just this workspace, so the dialog
    // says so in one line before the (still-generic) empty/has-configs
    // sentence.  A non-admin who tries anyway gets the backend's own 403
    // detail below, not a silent nothing (live 2026-09-24: two 403s in the
    // server log and no visible reason in the UI).
    // 2026-10-10 (owner): a DELETE never hides a SHARED die for other users —
    // it removes only this workspace's own copy; "Retire for all users" is the
    // separate action below.
    const wide = die.layer === 'published' ? 'the published catalog for everyone who sees it'
      : null;
    setAskConfirm({
      title: `Delete die '${name}'?`,
      body: (die.layer === 'shared' ? `${tx('deleteSharedDieBody')} ` : '')
        + (wide ? `Removes it from ${wide}. ` : '')
        + 'An empty die is removed at once; a die that still has configurations asks once more.',
      onConfirm: () => {
        void (async () => {
          setBusy(`delete ${name}`); setMsg(null);
          try {
            const r = await del(`/api/family/die/${encodeURIComponent(name)}`);
            let data: any = null;
            try { data = await r.json(); } catch { /* keep null */ }
            if (r.ok) {
              const note = data?.note ? ` (${data.note})` : '';
              setMsg(`✓ die '${name}' deleted${note}`);
              window.dispatchEvent(new CustomEvent('family-changed'));
              return;
            }
            if (data?.code === 'die.retire_required') {
              // A shared catalog die cannot be unlinked (the shared store is read-only to the API):
              // the same Delete click offers to retire it for everyone right here, instead of a
              // one-line message that was easy to miss (owner 2026-10-20: «нажимаю на delete, но они
              // не убираются»).  Retiring is reversible (Restore retired).
              setBusy(null);
              retireDie(name);
              return;
            }
            if (r.status !== 409) {
              setMsg(`✗ ${data?.detail ?? `HTTP ${r.status}`}`);
              return;
            }
            // 409 = the die still has configurations. Name them and ask ONCE
            // more — then delete the whole subtree with force. (It used to stop
            // here with a message the user could miss: "I'm deleting it and it
            // isn't deleting", live 2026-08-20.)
            const detail = String(data?.detail ?? '');
            setBusy(null);
            setAskConfirm({
              title: `Delete '${name}' WITH its configurations?`,
              body: (wide ? `Removes it from ${wide}. ` : '')
                + (detail || 'The die still has configurations.')
                + ' They and their duties are removed permanently.',
              onConfirm: () => mutate(`die '${name}' deleted with its configurations`,
                () => del(`/api/family/die/${encodeURIComponent(name)}?force=true`)),
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
  };
  // "Retire for all users" — admin only, shared dies only.  The dialog names
  // the die and how many accounts can see it; the request repeats the name.
  const retireDie = (name: string) => {
    void (async () => {
      let users: number | null = null;
      try {
        const r = await fetch(`${API}/api/family/die/${encodeURIComponent(name)}/audience`);
        if (r.ok) users = Number((await r.json())?.users ?? 0);
      } catch { /* the dialog still opens; the count reads unknown */ }
      setAskConfirm({
        title: tx('retireDieTitle', { die: name }),
        body: users == null ? tx('retireDieBodyUnknown', { die: name })
          : tx('retireDieBody', { die: name, n: users }),
        confirmLabel: tx('retireDieConfirm'),
        onConfirm: () => mutate(tx('retireDieDone', { die: name }),
          () => post(`/api/family/die/${encodeURIComponent(name)}/retire`, { confirm: name })),
      });
    })();
  };
  // "Archive" — admin only, a die of a workspace or a published layer (owner 2026-11-17: archive what is not needed; we
  // never delete).  Nothing on disk moves; the server refuses a die in use and says why.
  const archiveDie = (name: string) => setAskConfirm({
    title: tx('archiveDieTitle', { die: name }),
    body: tx('archiveDieBody', { die: name }),
    confirmLabel: tx('archiveDieConfirm'),
    onConfirm: () => mutate(tx('archiveDieDone', { die: name }),
      () => post(`/api/die-archive/${encodeURIComponent(name)}/archive`, { confirm: name })),
  });
  const restoreRetired = () => {
    void (async () => {
      let names: string[] = [];
      const archived = new Set<string>();             // archived in this workspace: restored through /api/die-archive
      try {
        const r = await fetch(`${API}/api/family/retired`);
        if (r.ok) names = ((await r.json())?.dies ?? []).map((d: { die: string }) => d.die);
      } catch { /* empty list below */ }
      try {
        const r = await fetch(`${API}/api/die-archive`);
        if (r.ok) for (const d of ((await r.json())?.dies ?? []) as Array<{ die: string }>) { archived.add(d.die); names.push(d.die); }
      } catch { /* the retired ones still show */ }
      if (!names.length) { setMsg(tx('retiredNone')); return; }
      setAskText({
        title: tx('restoreRetiredTitle'),
        label: tx('restoreRetiredLabel'),
        initial: names[0],
        hint: tx('restoreRetiredHint', { names: names.join(', ') }),
        okLabel: tx('restoreRetiredOk'),
        onSubmit: (name) => (archived.has(name)
          ? mutate(tx('archiveDieRestored', { die: name }), () => post(`/api/die-archive/${encodeURIComponent(name)}/restore`, {}))
          : mutate(tx('restoreRetiredDone', { die: name }), () => post(`/api/family/die/${encodeURIComponent(name)}/restore`, {}))),
      });
    })();
  };
  const renameDie = (die: string) => setAskText({
    title: `Rename die '${die}'`,
    label: 'New name', initial: die,
    // The server moves every store keyed by the name (results, grants, projects, production ...) in one
    // transaction; `manual` lists deploy files it cannot rewrite - say so, they are the operator's to move.
    onSubmit: (name) => { if (name !== die) mutate((data) => {
      const manual = Array.isArray(data?.manual) ? data.manual.length : 0;
      return `die renamed to '${String(data?.die || name)}'` + (manual ? ` - ${manual} deploy file(s) still name '${die}'` : '');
    }, () =>
      fetch(`${API}/api/family/die/${encodeURIComponent(die)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })); },
  });
  const duplicateDie = async (die: string) => {
    const initial = (await suggestDieName(die)) || `${die} copy`;
    setAskText({
      title: `Duplicate die '${die}'`,
      label: 'Name of the copy', initial,
      hint: 'Copies the stamped geometry + EVERY configuration with its duties and results; the copy starts unlocked',
      onSubmit: (name) => mutate((d) => `die '${die}' duplicated as '${String(d?.die || name)}'`,
        () => post(`/api/family/die/${encodeURIComponent(die)}/duplicate`, { name })),
    });
  };
  const duplicateCfg = (die: string, cfg: string) => setAskText({
    title: `Duplicate '${cfg}' under ${die}`,
    label: 'Name of the copy', initial: `${cfg} copy`,
    hint: 'Copies the build, winding, materials, battery and EVERY duty (with results)',
    onSubmit: (name) => mutate((d) => `configuration '${cfg}' duplicated as '${String(d?.config || name)}'`,
      () => post(`/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/duplicate`,
                 { name })),
  });
  // Admin: move (or copy) a configuration to ANOTHER die.  The server carries every store that names
  // "<die>/<cfg>" (results + history, projects, customer grants, production, tests, CAD) in one
  // transaction; the dry run is shown first and nothing is written until it is confirmed.
  const moveCfg = (die: string, cfg: string, mode: 'move' | 'copy') => setAskText({
    title: `${mode === 'move' ? 'Move' : 'Copy'} '${die} / ${cfg}' to another die`,
    label: 'Target die (it must exist)', initial: '',
    hint: mode === 'move'
      ? 'Runs, results, history, project links, customer access, production, tests and CAD follow; you see every change first'
      : 'The source stays; the copy takes the runs, results and history and joins the projects of the source',
    okLabel: 'Check',
    onSubmit: (target) => { void (async () => {
      const url = `${API}/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/move`;
      const init = { method: 'POST', headers: { 'Content-Type': 'application/json' },
                     body: JSON.stringify({ target_die: target.trim(), mode }) };
      let plan: MovePlan = {};
      try {
        const r = await fetch(`${url}?dry_run=1`, init);
        plan = await r.json().catch(() => ({}));
        if (!r.ok) { setMsg(`✗ ${(plan as { detail?: string }).detail ?? `HTTP ${r.status}`}`); return; }
      } catch (e) { setMsg(`✗ checking the ${mode}: ${e}`); return; }
      setAskConfirm({
        title: `${mode === 'move' ? 'Move' : 'Copy'} '${cfg}' to '${target.trim()}'?`,
        body: configMoveSummary(plan),
        confirmLabel: mode === 'move' ? 'Move' : 'Copy',
        onConfirm: () => mutate((d) => `'${cfg}' ${mode === 'move' ? 'moved' : 'copied'} to '${target.trim()}'`
          + (d?.manual?.length ? ` - ${d.manual.length} deploy file(s) to move by hand` : ''),
          () => fetch(url, init)),
      });
    })(); },
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
  // Rename = one server transaction (config_move.py, rename mode): results, history, project links, customer
  // access, cost estimate, production, tests, CAD and revision records follow the new name.  An admin sees the
  // dry run first; any other writer renames in their own workspace through the PATCH (same transaction).
  const renameCfg = (die: string, cfg: string) => setAskText({
    title: `Rename configuration '${cfg}'`,
    label: 'New name', initial: cfg,
    hint: isAdmin ? 'Results, project links, customer access, production and revisions follow; you see every change first'
                  : undefined,
    okLabel: isAdmin ? 'Check' : undefined,
    onSubmit: (raw) => {
      const name = raw.trim();
      if (!name || name === cfg) return;
      if (!isAdmin) {
        mutate((d) => `configuration renamed to '${String(d?.config || name)}'`, () =>
          fetch(`${API}/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
          }));
        return;
      }
      void (async () => {
        const url = `${API}/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/rename`;
        const init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) };
        let plan: MovePlan = {};
        try {
          const r = await fetch(`${url}?dry_run=1`, init);
          plan = await r.json().catch(() => ({}));
          if (!r.ok) { setMsg(`✗ ${(plan as { detail?: string }).detail ?? `HTTP ${r.status}`}`); return; }
        } catch (e) { setMsg(`✗ checking the rename: ${e}`); return; }
        setAskConfirm({
          title: `Rename '${cfg}' to '${name}'?`,
          body: configMoveSummary(plan),
          confirmLabel: 'Rename',
          onConfirm: () => mutate((d) => `configuration renamed to '${name}'`
            + (d?.manual?.length ? ` - ${d.manual.length} deploy folder(s) to move by hand` : ''),
            () => fetch(url, init)),
        });
      })();
    },
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
  // ── apply a duty: geometry + winding + materials + operating point, all
  //    through the EXISTING endpoints (this panel owns no write-path of its
  //    own into the live config) ─────────────────────────────────────────────
  //    The load itself — server half (owner) + local half — is
  //    lib/dutyApply.applyDutyEverywhere since 2026-09-20, shared with the
  //    header strip's "discard live changes and reload ▶" offer on a released
  //    context, so both entry points run the very same apply.
  const applyDuty = async (die: string, cfg: string, duty: string) => {
    const label = `${cfg} / ${duty}`;
    setBusy(label); setMsg(null);
    // The view opens AT ONCE (owner 2026-10-13: the load takes seconds, the click must not
    // wait for it): the owner lands on the Geometry tab, a standard account (Motors +
    // Configure only) on Configure.  The load goes on behind it; lib/familyLoad carries the
    // progress line - and the failure, which this panel (unmounted by the switch) cannot.
    try {
      const done = await applyCatalogDuty(die, cfg, duty, canWrite, () => setActiveTab(loadTargetTab(canWrite, standardUser)));
      setMsg(done.declined ? null : `✓ applied ${label} — ${done.message}`);
    } catch (e: any) {
      const why = String(e?.message ?? e);
      setMsg(`✗ apply ${label}: ${why}`);
    }
    setBusy(null);
  };

  const shownAll = diameter == null ? filterDiesByApplication(dies, application) : diesOfDiameter(dies, application, diameter);
  // Clients see what is computed (owner 2026-10-17) - a configuration with a catalogue card - AND, since 2026-10-26, every
  // configuration they may open that stores computed points (no card yet) or that belongs to the project the tab highlights;
  // those open points-limited in Configure.  A motor with none left is not listed.  Editors keep every row.
  const shown = canWrite ? shownAll : shownAll
    .map((d) => {
      const configs = d.configs.filter((c) => isListableForClient(c, isConfigHighlighted(projectIndex, d.name, c.name)));
      return { ...d, configs, config_count: configs.length };
    })
    .filter((d) => d.configs.length > 0);
  if (embedded && shown.length === 0 && !msg) return null;

  // what the by-die view's rows and menus do: the SAME handlers as the list below, so no action is lost
  const dieOf = (name: string) => dies.find((d) => d.name === name);
  const cfgOf = (die: string, cfg: string) => dieOf(die)?.configs.find((c) => c.name === cfg);
  const ratedOf = (die: string, cfg: string) => {
    const d = dieOf(die);
    const b = d ? configButtons(d as never).find((x) => x.config === cfg) : undefined;
    return b ? { duty: b.duty, power_kw: b.power_kw, torque_nm: b.torque_nm, mass_kg: b.mass_kg } : null;
  };
  // what the old list showed beside a configuration and the by-die row keeps: what the motor is for (editable for a writer),
  // its saved cogging torque, and the state of a report being built or the reason it failed
  /** Editors only: the passport card / older-build line of a configuration, as tooltip text (on the motor name). */
  const configCardTip = (die: Die, config: string): string => {
    if (!canWrite) return '';
    const c = die.configs.find((x) => x.name === config);
    if (!c) return '';
    const line = cardStatusTip(txCommon, { date: c.card_date, staleDuties: staleCatalogDutyNames(c.build_sig_aliases ?? c.build_sig, c.duties) });
    return line ? ` ${line}` : '';
  };
  // the table's cells beyond the quoted point (owner 2026-11-17, columns): what the motor is for (editable for a writer), the
  // marks (project focus, points only, the report being built or why it failed) and the saved cogging torque
  const rowCells = (dieName: string, cfgName: string): RowCells | null => {
    const c = cfgOf(dieName, cfgName);
    if (!c) return null;
    const key = `${dieName}/${cfgName}`;
    // the project the tab highlights (ProjectHighlightSelect): ◆ its motor, ★ the one it recommends
    const mark = isConfigRecommended(projectIndex, dieName, cfgName) ? '\u2605' : isConfigHighlighted(projectIndex, dieName, cfgName) ? '\u25c6' : '';
    const cog = coggingValue(c.cogging);
    return {
      application: <ApplicationBadge cfg={c} die={dieName} canEdit={canWrite} />,
      cogging: cog, coggingStale: !!c.cogging?.stale,
      coggingTip: cog !== null ? `${coggingTipText(c.cogging, txc)} (${cog.toFixed(coggingDigits(cog))} N·m)` : txc('configure.cogging'),
      marks: (
        <>
          {mark && <Tooltip title={tx('projectFocus.configTip')}><Box component="span" data-testid="project-focus-mark" sx={{ color: '#f59e0b', fontSize: 12 }}>{mark}</Box></Tooltip>}
          {isPointsOnly(c) && (
            <Tooltip title={tx('pointsOnlyTip')}><Chip size="small" data-testid="points-only-badge" label={tx('pointsOnly')} sx={{ height: 18, fontSize: 10, color: 'var(--text-3)' }} /></Tooltip>
          )}
          {(ringBusy(ring, `report:${key}`) || ringBusy(ring, `report:pdf:${key}`)) && (
            <Tooltip title={ringTip(ring)}><span><ReportRing ring={ring} size={13} /></span></Tooltip>
          )}
          {ring.notice && ring.key.endsWith(`:${key}`) && (
            <Tooltip title={ring.notice.full}>
              <Typography component="span" onClick={() => setRing(RING_IDLE)}
                sx={{ fontSize: 11, cursor: 'pointer', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      color: ring.notice.kind === 'error' ? '#f87171' : 'var(--text-3)' }}>✗ {ring.notice.text}</Typography>
            </Tooltip>
          )}
        </>
      ),
    };
  };
  const byDieActions: ByDieActions = {
    load: (die, cfg) => { const r = ratedOf(die, cfg); if (r?.duty) void applyDuty(die, cfg, r.duty); },
    renameDie, toggleDieLock: (n) => { const d = dieOf(n); if (d) toggleDieLock(d); },
    dieHistory: (n) => setHistOf({ die: n, config: null }), duplicateDie: (n) => void duplicateDie(n),
    addConfig: createCfg, deleteDie: (n) => { const d = dieOf(n); if (d) deleteDie(d); },
    retireOrArchive: (n) => (dieOf(n)?.layer === 'shared' ? retireDie(n) : archiveDie(n)),
    renameCfg, toggleCfgLock: (d, c) => { const x = cfgOf(d, c); if (x) toggleCfgLock(d, x); },
    cfgHistory: (d, c) => setHistOf({ die: d, config: c }), duplicateCfg, moveCfg,
    datasheet: (d, c) => void runExport('datasheet', d, c), report: (d, c, pdf) => void runExport('report', d, c, pdf),
    toMyMotors: duplicateToMySpace, battery: (d, c) => { const x = cfgOf(d, c); if (x) editBattery(d, x); }, deleteCfg,
  };

  const body = (
    <>
      {msg && (
        <Typography sx={{ fontSize: 11,
          color: msg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{msg}</Typography>
      )}

      {!embedded && shown.length === 0 && !msg && !loadFailed && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
          {(() => {
            const line = emptyCatalogLine({ loaded: treeLoaded, failed: loadFailed, note, canWrite });
            if (line === 'note' && noteCode === 'no_motors_granted') return <StartProject />;
            return line === 'loading' ? tx('catalogLoading')
              : line === 'note' ? note
              : line === 'noMotors' ? tx('noMotorsAssigned')
              : 'no dies yet — freeze the current geometry with the button above';
          })()}
        </Typography>
      )}

      {/* THE Motors catalog for every role (owner 2026-11-17: "the catalog view must be the same for everybody"); the
          admin sees more in it, gated in the routes (designations, Overview, jobs).  The old list is gone. */}
      <CatalogByDie dies={shown as never} thumbs={(d) => thumbOf(d as never, thumbs)} rated={ratedOf}
        active={active ? { die: active.die, config: active.config } : null} isAdmin={isAdmin} canWrite={canWrite}
        standardUser={standardUser} busy={!!busy || loadRunning} actions={byDieActions}
        dieExtras={(n) => { const d = dieOf(n); return d ? (
          <>
            <OpennessMarker die={d} />
            {isDieHighlighted(projectIndex, d.name) && <Box component="span" data-project-motor="true" sx={{ width: 3, height: 14, bgcolor: '#f59e0b' }} />}
          </>) : null; }}
        cells={rowCells}
        nameTip={(n, cn) => { const d = dieOf(n); return d ? configCardTip(d, cn).trim() : ''; }} />
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
          {tx('familiesDieConfigurationDuty')}
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
        {canWrite && isAdmin && (
          <Tooltip title={tx('restoreRetiredTip')}>
            <Button size="small" variant="text" onClick={restoreRetired}
              sx={{ textTransform: 'none', fontSize: 12 }}>
              {tx('restoreRetiredButton')}
            </Button>
          </Tooltip>
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
