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
import { useMotorStore, useUIStore } from '../../stores/motorStore';
import MotorThumbnail from './MotorThumbnail';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface DutyResult {
  efficiency_pct?: number; ripple_pct?: number; v_ll_peak_v?: number;
  loss_w?: number; mass_kg?: number; recorded_at?: string;
  p_core_w?: number; p_stranded_w?: number; p_solid_w?: number;
  v_phase_peak_v?: number; j_coil_a_mm2?: number;
  build_sig?: string;
}
interface Duty {
  name: string; mode: string; saved_at?: string | null;
  current_arms: number; rpm: number;
  gamma_deg: number; torque_nm?: number | null; power_kw?: number | null;
  note: string; result?: DutyResult | null;
}
interface Cfg {
  name: string; role: string; locked?: boolean; build_sig?: string;
  stack_mm: number | null;
  wire_height_mm: number | null; wire_width_mm: number | null;
  turns: number | null; connection: string | null;
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

const readLS = (k: string, d: any) => {
  try { const v = localStorage.getItem('sim.' + k); return v == null ? d : JSON.parse(v); }
  catch { return d; }
};

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

  const load = async () => {
    try {
      const t = await (await fetch(`${API}/api/family/tree`)).json();
      setDies(t.dies || []);
      setCanWrite(t.can_write === true);
      const c = await (await fetch(`${API}/api/family/context`)).json();
      setActive(c?.active ? c : null);
    } catch (e) { setMsg(`catalog load failed: ${e}`); }
  };
  useEffect(() => { load(); }, []);
  // The page-level "+ die" button (and sibling Ø sections) announce family
  // mutations with this event — every embedded instance refetches.
  useEffect(() => {
    const onChanged = () => { void load(); };
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
    await load();
    try { window.dispatchEvent(new CustomEvent('family-changed')); } catch { /* SSR */ }
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
  const renameDie = (die: string) => {
    const name = window.prompt(`New name for die '${die}':`, die);
    if (!name || name === die) return;
    mutate(`die renamed to '${name}'`, () =>
      fetch(`${API}/api/family/die/${encodeURIComponent(die)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }));
  };
  const deleteCfg = (die: string, cfg: string) => {
    if (!window.confirm(`Delete configuration '${die}/${cfg}' and all its duties?`)) return;
    mutate(`configuration '${cfg}' deleted`, () =>
      del(`/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}`));
  };
  const renameCfg = (die: string, cfg: string) => {
    const name = window.prompt(`New name for configuration '${cfg}':`, cfg);
    if (!name || name === cfg) return;
    mutate(`configuration renamed to '${name}'`, () =>
      fetch(`${API}/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }));
  };
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
  const deleteDuty = (die: string, cfg: string, duty: string) => {
    if (!window.confirm(`Delete duty '${duty}' from ${die}/${cfg}?`)) return;
    mutate(`duty '${duty}' deleted`, () =>
      del(`/api/family/duty/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/${encodeURIComponent(duty)}`));
  };

  // ── apply a duty: geometry + winding + materials + operating point, all
  //    through the EXISTING endpoints (this panel owns no write-path of its
  //    own into the live config) ─────────────────────────────────────────────
  const applyDuty = async (die: string, cfg: string, duty: string) => {
    const label = `${cfg} / ${duty}`;
    setBusy(label); setMsg(null);
    try {
      const r = await fetch(`${API}/api/family/payload/${encodeURIComponent(die)}/`
        + `${encodeURIComponent(cfg)}?duty=${encodeURIComponent(duty)}`);
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      const p = await r.json();
      // 0) mark WHICH die/config/duty the editor is about to become — the
      //    geometry route enforces the locks against this context, and
      //    applying the configuration's own canonical values passes.
      await fetch(`${API}/api/family/activate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die, config: cfg, duty }),
      });
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
      //     loading it must load what it is made of.
      for (const part of ['magnet', 'stator_core', 'rotor_core']) {
        const mat = p.materials?.[part];
        if (!mat) continue;
        const mr = await fetch(`${API}/api/materials`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ part, material: mat }),
        });
        if (!mr.ok) throw new Error((await mr.json()).detail ?? `materials HTTP ${mr.status}`);
      }
      // 3) shared simulation config — what the sweep/optimizer read off-tab
      const simPatch: any = {
        max_current: p.sim.current_a, rpm: p.sim.rpm, frequency: p.sim.frequency,
        phase_offset_deg: p.sim.gamma_deg, mode: p.sim.mode,
        connection: p.sim.connection,
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
      set('opMode', p.sim.mode);
      if (p.sim.connection) set('connection', p.sim.connection);
      if (p.sim.daxis_deg != null) set('daxisDeg', String(p.sim.daxis_deg));
      window.dispatchEvent(new CustomEvent('sim-operating-point', {
        detail: { current: p.sim.current_a, gamma: p.sim.gamma_deg, rpm: p.sim.rpm,
                  mode: p.sim.mode, connection: p.sim.connection } }));
      window.dispatchEvent(new CustomEvent('sim-design-applied'));
      window.dispatchEvent(new CustomEvent('family-changed'));
      // The duty's SAVED results go straight onto the Simulation dashboard —
      // recorded numbers, no re-run, no field views (user request).  A fresh
      // Run supersedes them, same as a Sweep-applied point.
      const dd = p.duty || {};
      const rr = dd.result || {};
      if (rr.efficiency_pct != null || dd.torque_nm != null) {
        const rpm = Number(p.sim.rpm) || 0;
        const omega = 2 * Math.PI * rpm / 60;
        const T = Number(dd.torque_nm) || 0;
        const Pmech = dd.power_kw != null ? Number(dd.power_kw) * 1000 : T * omega;
        const mass = Number(rr.mass_kg) || 0;
        const Vlpk = Number(rr.v_ll_peak_v) || 0;
        const ploss = Number(rr.loss_w) || 0;
        const summary: any = {
          rpm, I_phase_rms_A: Number(p.sim.current_a) || 0,
          gamma_deg: Number(p.sim.gamma_deg) || 0,
          connection: p.sim.connection, op_mode: p.sim.mode,
          T_em_avg_Nm: T, T_ripple_pct: Number(rr.ripple_pct) || 0,
          P_mech_W: Pmech,
          V_line_peak_V: Vlpk, V_line_rms_V: Vlpk / Math.SQRT2,
          KV_rpm_per_V_line: Vlpk > 0 ? rpm / (Vlpk / Math.SQRT2) : 0,
          P_loss_total_W: ploss,
          P_core_W: rr.p_core_w ?? undefined,
          P_stranded_W: rr.p_stranded_w ?? undefined,
          P_solid_W: rr.p_solid_w ?? undefined,
          V_phase_peak_V: rr.v_phase_peak_v ?? undefined,
          V_phase_rms_V: rr.v_phase_peak_v != null
            ? Number(rr.v_phase_peak_v) / Math.SQRT2 : undefined,
          J_coil_A_per_mm2: rr.j_coil_a_mm2 ?? undefined,
          efficiency: rr.efficiency_pct != null ? Number(rr.efficiency_pct) / 100 : 0,
          mass_total_kg: mass, mass_components: [],
          torque_per_mass_Nm_kg: mass > 0 && T > 0 ? T / mass : 0,
          power_per_mass_W_kg: mass > 0 ? Pmech / mass : 0,
          loss_density_W_kg: mass > 0 ? ploss / mass : 0,
        };
        window.dispatchEvent(new CustomEvent('sim-apply-summary', { detail: { summary } }));
        setMsg(`✓ applied ${label} — saved results are on the dashboard; Run to recompute`);
      } else {
        setMsg(`✓ applied ${label} — no saved results yet; press Run in Simulation`);
      }
      // Straight to the machine the user just loaded (user request).
      setActiveTab('geometry');
    } catch (e: any) { setMsg(`✗ apply ${label}: ${e?.message ?? e}`); }
    setBusy(null);
  };

  const roleColor = (role: string) =>
    role === 'generator' ? '#38bdf8' : '#f59e0b';

  const shown = dies.filter(d =>
    diameter == null || Number(d.stator_diameter) === Number(diameter));
  if (embedded && shown.length === 0 && !msg) return null;

  const body = (
    <>
      {msg && (
        <Typography sx={{ fontSize: 11,
          color: msg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{msg}</Typography>
      )}

      {!embedded && shown.length === 0 && !msg && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
          no dies yet — freeze the current geometry with the button above
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
                <Chip size="small" label={c.role}
                  sx={{ height: 18, fontSize: 10, color: roleColor(c.role),
                        bgcolor: 'transparent', border: `1px solid ${roleColor(c.role)}55` }} />
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  {c.stack_mm} mm · wire {c.wire_height_mm}×{c.wire_width_mm} mm
                  {' '}· {c.turns} turns
                  {c.connection ? ` · ${c.connection}` : ''}
                  {c.steel ? ` · ${c.steel}` : ''}
                  {c.magnet ? ` · ${c.magnet}` : ''}
                </Typography>
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
                      <th>loss W</th><th>kg</th><th>γ°</th>
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
                          </td>
                          <td>{fmt(d.power_kw)}</td>
                          <td>{fmt(d.torque_nm)}</td>
                          <td>{fmt(d.rpm, 0)}</td>
                          <td>{fmt(d.current_arms)}</td>
                          <td style={staleRes ? { color: '#fbbf24' } : undefined}>{fmt(r.v_ll_peak_v, 0)}</td>
                          <td style={staleRes ? { color: '#fbbf24' } : undefined}>{fmt(r.efficiency_pct, 2)}</td>
                          <td style={staleRes ? { color: '#fbbf24' } : undefined}>{fmt(r.ripple_pct)}</td>
                          <td style={staleRes ? { color: '#fbbf24' } : undefined}>{fmt(r.loss_w, 0)}</td>
                          <td style={staleRes ? { color: '#fbbf24' } : undefined}>{fmt(r.mass_kg, 2)}</td>
                          <td>{fmt(d.gamma_deg)}</td>
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
                              <Tooltip title="Delete duty">
                                <span>
                                  {/* Deliberate distance from ▶ — load and DELETE
                                      must not be a one-pixel slip apart. */}
                                  <IconButton size="small" disabled={!!busy}
                                    onClick={() => deleteDuty(die.name, c.name, d.name)}
                                    sx={{ fontSize: 12, p: 0.2, ml: 2.5,
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

  if (embedded) {
    return <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>{body}</Box>;
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
      </Box>
      {body}
    </Paper>
  );
};

export default FamilyCatalog;
