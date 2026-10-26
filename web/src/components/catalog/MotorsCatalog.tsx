/**
 * MotorsCatalog — the Motors tab: ONE hierarchy, grouped by stator diameter.
 *
 *   Ø <diameter>
 *     🔒 die (stamped lamination, real cross-section)
 *        configuration (stack · wire · turns · connection · steel · magnet)
 *           duty table (kW/Nm/rpm/A/V/η/ripple/loss/kg/γ) → ▶ load / 📥 record / ✕
 *
 * Every motor lives as a die with configurations and duties — the legacy
 * card/preset catalog was migrated into this structure (2026-08-19); the
 * preset backend still exists for auto-save flows, it just has no separate
 * UI here any more.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Typography, Button, Tooltip } from '@mui/material';
import FamilyCatalog from './FamilyCatalog';
import MyMotorsPanel from './MyMotorsPanel';
import ProjectHighlightSelect from './ProjectHighlightSelect';
import HelpTip from '../common/HelpTip';
import { TextPromptDialog, type TextPromptState } from '../common/PromptDialogs';
import { fetchFamilyTree, SIGN_IN_NOTE } from '../../lib/familyTree';
import { APPLICATIONS, catalogDiameters, parseApplicationFilter, type ApplicationFilter } from '../../lib/applications';
import { useScrollMemory } from '../../lib/scrollMemory';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { canWriteServer } from '../../lib/localAuth';
import { emptyCatalogLine, uiCanWrite } from '../../lib/accessUi';
import { useAuth } from '../../contexts/AuthContext';
import { suggestDieName } from '../../lib/dieNames';

// UI strings: locales/<lng>/motors.json (docs/I18N.md).
const tx = nsT('motors');

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

const MotorsCatalog: React.FC = () => {
  useTranslation('motors'); // re-render on language change; lazy-loads the namespace
  // The tab's scroll box is this component's parent (App.tsx wraps it in the
  // overflowY: auto Box); its position is remembered across tab switches and
  // reloads (user 2026-09-13).
  const rootRef = useRef<HTMLDivElement>(null);
  useScrollMemory('motors', rootRef, 'parent');
  // Each Ø section folds on a click of its header and stays folded across
  // tab switches and reloads (user 2026-09-13: "you click 200 —
  // the whole list dumps out, click again — it closes").
  const [folded, setFolded] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem('motors.folded') || '{}') || {}; }
    catch { return {}; }
  });
  const toggleFold = (d: number) => setFolded(prev => {
    const next = { ...prev, [String(d)]: !prev[String(d)] };
    try { localStorage.setItem('motors.folded', JSON.stringify(next)); } catch { /* storage blocked */ }
    return next;
  });
  // Application filter (owner 2026-10-13): all / propeller / robot / aviation / marine.
  const [appFilter, setAppFilter] = useState<ApplicationFilter>(() => {
    try { return parseApplicationFilter(localStorage.getItem('motors.application')); } catch { return 'all'; }
  });
  const [allDies, setAllDies] = useState<{ name: string; stator_diameter: number; configs: { name: string; applications?: unknown; card_date?: unknown }[] }[]>([]);
  const pickFilter = (v: string) => {
    const f = parseApplicationFilter(v);
    setAppFilter(f);
    try { localStorage.setItem('motors.application', f); } catch { /* storage blocked */ }
  };
  const [canWrite, setCanWrite] = useState(false);
  // `loaded` = the tree has ANSWERED once; `failed` = the last attempt did not.  Until then the page must not claim "no motors".
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  // The backend's one-line reason for an empty catalog (a signed-in account
  // with no motors granted yet) — shown instead of the vendor's "no dies yet".
  const [note, setNote] = useState<string | null>(null);
  // ONE request for this component and the N Ø sections it mounts
  // (lib/familyTree — 2026-09-13, the tab took seconds because each section
  // fetched the 900 KB tree for itself).  `fresh` after a mutation.
  const load = (fresh = false) =>
    fetchFamilyTree({ fresh })
      .then(t => {
        // may write the SHARED config (admin / local dev) - not merely "a copy into my own workspace"
        setCanWrite(uiCanWrite(t.can_write, canWriteServer()));
        setNote(typeof t.note === 'string' ? t.note : null);
        setAllDies((t.dies || []) as never);
        setLoaded(true); setFailed(false);
      })
      .catch((e: { status?: number }) => {
        // 401 = this server keeps nothing public (PUBLIC_EXHIBIT=0) and nobody
        // is signed in.  Say so and STOP: retrying every 3 s cannot mint a
        // session, it just hammers the door while the user reads the message.
        if (e?.status === 401) { setCanWrite(false); setAllDies([]); setLoaded(true); setFailed(false); setNote(SIGN_IN_NOTE); return; }
        // Any other failure keeps the dies already on screen (a failed REFRESH used to blank the page) and retries.
        setFailed(true);
        // Backend away (a restart window): retry instead of freezing a wrong
        // answer — a failed first load left an ADMIN's catalog stripped of
        // its lock/duplicate buttons until a manual F5 (measured live
        // 2026-08-25: "I don't see the lock icons").
        setTimeout(() => { void load(true); }, 3000);
      });
  // Ø sections come from the dies that survive the application filter - and, for clients, only the dies with a computed
  // configuration (FamilyCatalog hides the rest), so no Ø header stands over nothing (owner 2026-10-18: the empty Ø 100)
  const diams = useMemo(() => {
    const visible = canWrite ? allDies : allDies
      .map((d) => ({ ...d, configs: d.configs.filter((c) => !!c.card_date) }))
      .filter((d) => d.configs.length > 0);
    return catalogDiameters(visible, appFilter);
  }, [allDies, appFilter, canWrite]);
  // The page can mount BEFORE /api/me has answered, or outlive a sign-in / sign-out: the tree is per account, so a change of
  // identity re-reads it (and tells the Ø sections, which hold their own copy, through the same event as every mutation).
  const { user, role, isAdmin, enforced, resolved } = useAuth();
  const identity = `${user?.email ?? ''}|${role}|${isAdmin}|${enforced}|${resolved}`;
  const lastIdentity = useRef(identity);
  useEffect(() => {
    if (lastIdentity.current === identity) return;
    lastIdentity.current = identity;
    try { window.dispatchEvent(new CustomEvent('family-changed')); } catch { /* SSR */ }
  }, [identity]);
  useEffect(() => {
    load();
    const onChanged = () => { void load(true); };
    window.addEventListener('family-changed', onChanged);
    return () => window.removeEventListener('family-changed', onChanged);
  }, []);

  // Page-level "+ die": freeze the CURRENT geometry as a new stamped die.
  const [dieMsg, setDieMsg] = useState<string | null>(null);
  const [askDie, setAskDie] = useState<TextPromptState | null>(null);
  const createDie = async () => setAskDie({
    title: 'New die from the current geometry',
    label: 'Die name',
    // pre-filled with "<family base> <your suffix> <n>" (owner 2026-10-26); editable
    initial: await suggestDieName(),
    onSubmit: async (name) => {
      setDieMsg(null);
      try {
        const r = await fetch(`${API}/api/family/die`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        });
        if (!r.ok) { setDieMsg(`\u2717 ${(await r.json()).detail ?? `HTTP ${r.status}`}`); return; }
        setDieMsg(`\u2713 die '${name}' created`);
        window.dispatchEvent(new CustomEvent('family-changed'));
      } catch (e) { setDieMsg(`\u2717 ${e}`); }
    },
  });

  return (
    <Box ref={rootRef}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1.5 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-1)' }}>
          {tx('catalog')}
        </Typography>
        <HelpTip title={tx('everythingGroupedByStatorDiameterA')} />
        <Box sx={{ flex: 1 }} />
        <ProjectHighlightSelect />
        <Tooltip title={tx('applications.filterTip')}>
          <select value={appFilter} onChange={(e) => pickFilter(e.target.value)} aria-label={tx('applications.label')}
            style={{ fontSize: 11, padding: '1px 4px', background: 'transparent', color: 'var(--text-2)',
                     border: '1px solid var(--line-soft)', borderRadius: 4 }}>
            <option value="all">{tx('applications.all')}</option>
            {APPLICATIONS.map((a) => <option key={a} value={a}>{tx(`applications.${a}`)}</option>)}
          </select>
        </Tooltip>
        {dieMsg && (
          <Typography sx={{ fontSize: 11,
            color: dieMsg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{dieMsg}</Typography>
        )}
        {canWrite && (
          <Button size="small" variant="outlined" onClick={createDie}
            sx={{ textTransform: 'none', fontSize: 11 }}>
            ＋ die from current geometry
          </Button>
        )}
      </Box>
      <TextPromptDialog state={askDie} onClose={() => setAskDie(null)} />

      {/* The signed-in user's private space — above the shared catalog.
          Renders nothing while empty. */}
      <MyMotorsPanel />
      {/* MCP Stage 3 drafts + the agent job queue used to show here too
          (owner 2026-09-30: not a catalog concern) — drafts are now in
          Configure ("My agent drafts"), and every account's drafts + runs are
          in Admin -> Agent activity. */}

      {diams.length === 0 && appFilter !== 'all' && allDies.length > 0 && loaded && !failed ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', color: 'var(--text-4)' }}>
          <Typography sx={{ fontSize: 11 }}>{tx('noMotorsForApplication', { application: tx(`applications.${appFilter}`) })}</Typography>
          <Button size="small" onClick={() => pickFilter('all')} sx={{ textTransform: 'none', fontSize: 11, minWidth: 0, p: 0.25 }}>
            {tx('clearApplicationFilter')}
          </Button>
        </Box>
      ) : diams.length === 0 && (() => {
        const line = emptyCatalogLine({ loaded, failed, note, canWrite });
        return (
          <Typography sx={{ fontSize: 11, color: line === 'unreachable' ? '#f59e0b' : 'var(--text-4)' }}>
            {line === 'loading' ? tx('catalogLoading')
              : line === 'unreachable' ? tx('catalogUnreachable')
              : line === 'note' ? note
              : line === 'noMotors' ? tx('noMotorsAssigned')
              : 'no dies yet — freeze the current geometry with the button above'}
          </Typography>
        );
      })()}

      {diams.map(d => (
        <Box key={d} sx={{ p: 2, mb: 2, borderRadius: 2,
                           border: '1px solid var(--line-soft)',
                           bgcolor: 'var(--panel-2)' }}>
          <Box onClick={() => toggleFold(d)} role="button" aria-expanded={!folded[String(d)]}
               sx={{ display: 'flex', alignItems: 'center', gap: 1,
                     mb: folded[String(d)] ? 0 : 1.25, cursor: 'pointer', userSelect: 'none' }}>
            <Typography sx={{ fontWeight: 800, color: '#60a5fa', fontSize: '1rem' }}>
              {folded[String(d)] ? '▸' : '▾'} Ø {d} mm
            </Typography>
            <Box sx={{ flex: 1, height: '1px', bgcolor: 'var(--panel)' }} />
          </Box>
          {!folded[String(d)] && <FamilyCatalog embedded diameter={d} application={appFilter} />}
        </Box>
      ))}
    </Box>
  );
};

export default MotorsCatalog;
