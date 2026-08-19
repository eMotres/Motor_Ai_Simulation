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
import React, { useEffect, useState } from 'react';
import { Box, Typography, Button } from '@mui/material';
import MyDesigns from './MyDesigns';
import FamilyCatalog from './FamilyCatalog';
import HelpTip from '../common/HelpTip';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

const MotorsCatalog: React.FC = () => {
  // Ø sections come from the dies themselves.
  const [diams, setDiams] = useState<number[]>([]);
  const load = () =>
    fetch(`${API}/api/family/tree`).then(r => r.json())
      .then(t => setDiams(Array.from(new Set(
        ((t.dies || []) as { stator_diameter: number }[])
          .map(d => Number(d.stator_diameter)).filter(Number.isFinite),
      )).sort((a, b) => a - b)))
      .catch(() => setDiams([]));
  useEffect(() => {
    load();
    const onChanged = () => { void load(); };
    window.addEventListener('family-changed', onChanged);
    return () => window.removeEventListener('family-changed', onChanged);
  }, []);

  // Page-level "+ die": freeze the CURRENT geometry as a new stamped die.
  const [dieMsg, setDieMsg] = useState<string | null>(null);
  const createDie = async () => {
    const name = window.prompt('New die name (snapshot of the CURRENT geometry):');
    if (!name) return;
    setDieMsg(null);
    try {
      const r = await fetch(`${API}/api/family/die`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) { setDieMsg(`✗ ${(await r.json()).detail ?? `HTTP ${r.status}`}`); return; }
      setDieMsg(`✓ die '${name}' created`);
      window.dispatchEvent(new CustomEvent('family-changed'));
    } catch (e) { setDieMsg(`✗ ${e}`); }
  };

  return (
    <Box>
      <MyDesigns />

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
        <Button size="small" variant="outlined" onClick={() => void createDie()}
          sx={{ textTransform: 'none', fontSize: 11 }}>
          ＋ die from current geometry
        </Button>
      </Box>

      {diams.length === 0 && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
          no dies yet — freeze the current geometry with the button above
        </Typography>
      )}

      {diams.map(d => (
        <Box key={d} sx={{ p: 2, mb: 2, borderRadius: 2,
                           border: '1px solid var(--line-soft)',
                           bgcolor: 'var(--panel-2)' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.25 }}>
            <Typography sx={{ fontWeight: 800, color: '#60a5fa', fontSize: '1rem' }}>
              Ø {d} mm
            </Typography>
            <Box sx={{ flex: 1, height: '1px', bgcolor: 'var(--panel)' }} />
          </Box>
          <FamilyCatalog embedded diameter={d} />
        </Box>
      ))}
    </Box>
  );
};

export default MotorsCatalog;
