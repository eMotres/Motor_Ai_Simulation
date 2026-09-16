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
import React, { useEffect, useRef, useState } from 'react';
import { Box, Typography, Button } from '@mui/material';
import FamilyCatalog from './FamilyCatalog';
import MyMotorsPanel from './MyMotorsPanel';
import HelpTip from '../common/HelpTip';
import { TextPromptDialog, type TextPromptState } from '../common/PromptDialogs';
import { fetchFamilyTree, SIGN_IN_NOTE } from '../../lib/familyTree';
import { useScrollMemory } from '../../lib/scrollMemory';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

const MotorsCatalog: React.FC = () => {
  // The tab's scroll box is this component's parent (App.tsx wraps it in the
  // overflowY: auto Box); its position is remembered across tab switches and
  // reloads (user 2026-09-13).
  const rootRef = useRef<HTMLDivElement>(null);
  useScrollMemory('motors', rootRef, 'parent');
  // Each Ø section folds on a click of its header and stays folded across
  // tab switches and reloads (user 2026-09-13: "нажимаешь на 200 —
  // вываливается весь список, снова нажимаешь — закрывается").
  const [folded, setFolded] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem('motors.folded') || '{}') || {}; }
    catch { return {}; }
  });
  const toggleFold = (d: number) => setFolded(prev => {
    const next = { ...prev, [String(d)]: !prev[String(d)] };
    try { localStorage.setItem('motors.folded', JSON.stringify(next)); } catch { /* storage blocked */ }
    return next;
  });
  // Ø sections come from the dies themselves.
  const [diams, setDiams] = useState<number[]>([]);
  const [canWrite, setCanWrite] = useState(false);
  // The backend's one-line reason for an empty catalog (a signed-in account
  // with no motors granted yet) — shown instead of the vendor's "no dies yet".
  const [note, setNote] = useState<string | null>(null);
  // ONE request for this component and the N Ø sections it mounts
  // (lib/familyTree — 2026-09-13, the tab took seconds because each section
  // fetched the 900 KB tree for itself).  `fresh` after a mutation.
  const load = (fresh = false) =>
    fetchFamilyTree({ fresh })
      .then(t => {
        setCanWrite(t.can_write === true);
        setNote(typeof t.note === 'string' ? t.note : null);
        setDiams(Array.from(new Set(
          ((t.dies || []) as { stator_diameter: number }[])
            .map(d => Number(d.stator_diameter)).filter(Number.isFinite),
        )).sort((a, b) => a - b));
      })
      .catch((e: { status?: number }) => {
        setDiams([]);
        // 401 = this server keeps nothing public (PUBLIC_EXHIBIT=0) and nobody
        // is signed in.  Say so and STOP: retrying every 3 s cannot mint a
        // session, it just hammers the door while the user reads the message.
        if (e?.status === 401) { setCanWrite(false); setNote(SIGN_IN_NOTE); return; }
        // Backend away (a restart window): retry instead of freezing a wrong
        // answer — a failed first load left an ADMIN's catalog stripped of
        // its lock/duplicate buttons until a manual F5 (measured live
        // 2026-08-25: "не вижу замочков").
        setTimeout(() => { void load(true); }, 3000);
      });
  useEffect(() => {
    load();
    const onChanged = () => { void load(true); };
    window.addEventListener('family-changed', onChanged);
    return () => window.removeEventListener('family-changed', onChanged);
  }, []);

  // Page-level "+ die": freeze the CURRENT geometry as a new stamped die.
  const [dieMsg, setDieMsg] = useState<string | null>(null);
  const [askDie, setAskDie] = useState<TextPromptState | null>(null);
  const createDie = () => setAskDie({
    title: 'New die from the current geometry',
    label: 'Die name',
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
          Catalog
        </Typography>
        <HelpTip title="Everything grouped by stator diameter. A 🔒 die is a stamped lamination: configurations (stack, wire, winding, materials) live under it, and each configuration has its duty table — click ▶ on a duty to load the whole machine into Simulation. ＋ buttons snapshot the CURRENT state at each level." />
        <Box sx={{ flex: 1 }} />
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

      {diams.length === 0 && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
          {note ?? 'no dies yet — freeze the current geometry with the button above'}
        </Typography>
      )}

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
          {!folded[String(d)] && <FamilyCatalog embedded diameter={d} />}
        </Box>
      ))}
    </Box>
  );
};

export default MotorsCatalog;
