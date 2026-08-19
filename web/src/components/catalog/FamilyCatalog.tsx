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
import React, { useEffect, useState } from 'react';
import {
  Box, Paper, Typography, Button, Chip, Tooltip, IconButton, CircularProgress,
} from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface Duty {
  name: string; mode: string; current_arms: number; rpm: number;
  gamma_deg: number; note: string;
}
interface Cfg {
  name: string; role: string; stack_mm: number | null; wire: string;
  connection: string | null; coil_temp_c: number | null; duties: Duty[];
}
interface Die {
  name: string; locked: boolean; created?: string;
  slots: number; poles: number; stator_diameter: number; configs: Cfg[];
}

const readLS = (k: string, d: any) => {
  try { const v = localStorage.getItem('sim.' + k); return v == null ? d : JSON.parse(v); }
  catch { return d; }
};

const FamilyCatalog: React.FC = () => {
  const { updateGeometryViaApi } = useMotorStore();
  const [dies, setDies] = useState<Die[]>([]);
  const [busy, setBusy] = useState<string | null>(null);   // what is being applied/created
  const [msg,  setMsg]  = useState<string | null>(null);   // last outcome (ok or error)

  const load = async () => {
    try {
      const t = await (await fetch(`${API}/api/family/tree`)).json();
      setDies(t.dies || []);
    } catch (e) { setMsg(`catalog load failed: ${e}`); }
  };
  useEffect(() => { load(); }, []);

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
    await load();
  };

  const post = (path: string, body: any) => fetch(`${API}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const del = (path: string) => fetch(`${API}${path}`, { method: 'DELETE' });

  // ── create/delete ─────────────────────────────────────────────────────────
  const createDie = () => {
    const name = window.prompt('New die name (snapshot of the CURRENT geometry):');
    if (!name) return;
    mutate(`die '${name}' created`, () => post('/api/family/die', { name }));
  };
  const createCfg = (die: string) => {
    const name = window.prompt(
      `New configuration under ${die} (snapshot of the CURRENT stack/wire/winding):`);
    if (!name) return;
    const role = readLS('opMode', 'motor') === 'generator' ? 'generator' : 'motor';
    mutate(`configuration '${name}' created`, () =>
      post('/api/family/config', { die, name, role }));
  };
  const createDuty = (die: string, cfg: string) => {
    const name = window.prompt(
      `New duty under ${die}/${cfg} (captures the CURRENT Simulation point — current, rpm, γ, mode):`);
    if (!name) return;
    const mode = readLS('opMode', 'motor') === 'generator' ? 'generator' : 'motor';
    mutate(`duty '${name}' saved from Simulation`, () =>
      post('/api/family/duty', { die, config: cfg, duty: { name, mode, from_current: true } }));
  };
  const deleteDie = (die: string) => {
    if (!window.confirm(`Delete die '${die}'? Its configurations must be deleted first.`)) return;
    mutate(`die '${die}' deleted`, () => del(`/api/family/die/${encodeURIComponent(die)}`));
  };
  const deleteCfg = (die: string, cfg: string) => {
    if (!window.confirm(`Delete configuration '${die}/${cfg}' and all its duties?`)) return;
    mutate(`configuration '${cfg}' deleted`, () =>
      del(`/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}`));
  };
  const deleteDuty = (die: string, cfg: string, duty: string) => {
    if (!window.confirm(`Delete duty '${duty}' from ${die}/${cfg}?`)) return;
    mutate(`duty '${duty}' deleted`, () =>
      del(`/api/family/duty/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/${encodeURIComponent(duty)}`));
  };

  // ── apply a duty: geometry + winding + operating point, existing endpoints ─
  const applyDuty = async (die: string, cfg: string, duty: string) => {
    const label = `${cfg} / ${duty}`;
    setBusy(label); setMsg(null);
    try {
      const r = await fetch(`${API}/api/family/payload/${encodeURIComponent(die)}/`
        + `${encodeURIComponent(cfg)}?duty=${encodeURIComponent(duty)}`);
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      const p = await r.json();
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
      // 3) shared simulation config — what the sweep/optimizer read off-tab
      const simPatch: any = {
        max_current: p.sim.current_a, rpm: p.sim.rpm, frequency: p.sim.frequency,
        phase_offset_deg: p.sim.gamma_deg, mode: p.sim.mode,
        coil_temp_c: p.sim.coil_temp_c, connection: p.sim.connection,
      };
      if (p.sim.daxis_deg != null) simPatch.daxis_deg = p.sim.daxis_deg;
      Object.keys(simPatch).forEach(k => simPatch[k] == null && delete simPatch[k]);
      const sr = await fetch(`${API}/api/simulation/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(simPatch),
      });
      if (!sr.ok) throw new Error((await sr.json()).detail ?? `sim HTTP ${sr.status}`);
      // 4) panel-owned persisted values + live nudge for the mounted panel
      const set = (k: string, v: any) => {
        try { localStorage.setItem('sim.' + k, JSON.stringify(v)); } catch { /* quota */ }
      };
      set('current', p.sim.current_a); set('gamma', p.sim.gamma_deg);
      set('rpm', p.sim.rpm); set('frequency', p.sim.frequency);
      set('opMode', p.sim.mode); set('coilTemp', p.sim.coil_temp_c);
      if (p.sim.connection) set('connection', p.sim.connection);
      if (p.sim.daxis_deg != null) set('daxisDeg', String(p.sim.daxis_deg));
      window.dispatchEvent(new CustomEvent('sim-operating-point', {
        detail: { current: p.sim.current_a, gamma: p.sim.gamma_deg, rpm: p.sim.rpm,
                  mode: p.sim.mode, coilTemp: p.sim.coil_temp_c,
                  connection: p.sim.connection } }));
      window.dispatchEvent(new CustomEvent('sim-design-applied'));
      setMsg(`✓ applied ${label} — open Simulation and press Run`);
    } catch (e: any) { setMsg(`✗ apply ${label}: ${e?.message ?? e}`); }
    setBusy(null);
  };

  const roleColor = (role: string) =>
    role === 'generator' ? '#38bdf8' : '#f59e0b';

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
        <Button size="small" variant="outlined" onClick={createDie}
          sx={{ textTransform: 'none', fontSize: 11 }}>
          ＋ die from current geometry
        </Button>
      </Box>
      {msg && (
        <Typography sx={{ fontSize: 11,
          color: msg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{msg}</Typography>
      )}

      {dies.length === 0 && !msg && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
          no dies yet — freeze the current geometry with the button above
        </Typography>
      )}

      {dies.map(die => (
        <Box key={die.name} sx={{ border: '1px solid var(--line-soft)',
          borderRadius: 1, p: 1.2, display: 'flex', flexDirection: 'column', gap: 0.8 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-1)' }}>
              {die.locked ? '🔒 ' : ''}{die.name}
            </Typography>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
              {die.slots}s/{die.poles}p · D{die.stator_diameter} ·
              {' '}{die.configs.length} configuration{die.configs.length === 1 ? '' : 's'}
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Button size="small" variant="text" disabled={!!busy}
              onClick={() => createCfg(die.name)}
              sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>
              ＋ configuration
            </Button>
            <Tooltip title="Delete die (configurations must be deleted first)">
              <span>
                <IconButton size="small" disabled={!!busy}
                  onClick={() => deleteDie(die.name)}
                  sx={{ color: 'var(--text-4)', fontSize: 13, p: 0.4 }}>🗑</IconButton>
              </span>
            </Tooltip>
          </Box>

          {die.configs.map(c => (
            <Box key={c.name} sx={{ display: 'flex', alignItems: 'center', gap: 0.8,
              flexWrap: 'wrap', pl: 1, py: 0.3,
              borderTop: '1px solid var(--panel)' }}>
              <Typography sx={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-2)',
                                minWidth: 84 }}>
                {c.name}
              </Typography>
              <Chip size="small" label={c.role}
                sx={{ height: 18, fontSize: 10, color: roleColor(c.role),
                      bgcolor: 'transparent', border: `1px solid ${roleColor(c.role)}55` }} />
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                L={c.stack_mm} · {c.wire} · {c.connection}
                {c.coil_temp_c != null ? ` · ${c.coil_temp_c}°C` : ''}
              </Typography>
              <Box sx={{ flex: 1 }} />
              {c.duties.map(d => (
                <Tooltip key={d.name} placement="top"
                  title={`${d.mode} · ${d.current_arms} Arms @ ${d.rpm} rpm · γ=${d.gamma_deg}°`
                         + (d.note ? ` — ${d.note}` : '')
                         + ' · click = load into Simulation'}>
                  <Chip size="small" clickable
                    label={busy === `${c.name} / ${d.name}` ? '…' : d.name}
                    onClick={() => applyDuty(die.name, c.name, d.name)}
                    onDelete={() => deleteDuty(die.name, c.name, d.name)}
                    sx={{ height: 22, fontSize: 11,
                          bgcolor: 'var(--panel)', color: 'var(--text-1)',
                          border: `1px solid ${roleColor(d.mode)}55`,
                          '& .MuiChip-deleteIcon': { fontSize: 14 } }} />
                </Tooltip>
              ))}
              <Tooltip title="Save the CURRENT Simulation point as a new duty here">
                <span>
                  <Button size="small" variant="text" disabled={!!busy}
                    onClick={() => createDuty(die.name, c.name)}
                    sx={{ textTransform: 'none', fontSize: 11, minWidth: 0, px: 0.5 }}>
                    ＋ duty
                  </Button>
                </span>
              </Tooltip>
              <Tooltip title="Delete this configuration and its duties">
                <span>
                  <IconButton size="small" disabled={!!busy}
                    onClick={() => deleteCfg(die.name, c.name)}
                    sx={{ color: 'var(--text-4)', fontSize: 12, p: 0.3 }}>🗑</IconButton>
                </span>
              </Tooltip>
            </Box>
          ))}
        </Box>
      ))}
    </Paper>
  );
};

export default FamilyCatalog;
