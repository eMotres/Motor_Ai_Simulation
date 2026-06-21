/**
 * ModulesPanel — shows the portal's registered modules, read live from
 * GET /api/modules. The portal is meant to build itself FROM these manifests
 * (web interfaces are modules too: each declares its UI panel). This panel is
 * the first consumer of that registry — proof the web-as-modules wiring works.
 */
import React, { useEffect, useState } from 'react';
import { Box, Paper, Typography, Chip, CircularProgress, Button } from '@mui/material';
import { useUIStore } from '../../stores/motorStore';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

// Manifest panel_id -> main-nav tab value: the portal navigates FROM the module
// manifests (web-as-module). Only entries that map to a real tab are clickable.
const PANEL_TO_TAB: Record<string, string> = {
  geometry: 'geometry', mesh: 'mesh', simulation: 'simulation',
  cost: 'cost', optimization: 'sweep',
};

interface UIContribution {
  panel_id: string;
  title: string;
  frontend_module?: string | null;
  order: number;
  as_tab: boolean;
}
interface ModuleManifest {
  name: string;
  version: string;
  capability: string;
  kind: string;
  depends_on: string[];
  inputs?: string[];
  outputs?: string[];
  contracts_version: string;
  summary: string;
  ui?: UIContribution | null;
}
interface ModulesResp { modules: ModuleManifest[]; capabilities: string[]; count: number; }

const PANEL = { bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1.5, p: 2, mt: 3 } as const;
const ITEM = { bgcolor: '#060d17', border: '1px solid #1e293b', borderRadius: 1, px: 1.5, py: 1 } as const;

const ModulesPanel: React.FC = () => {
  const setActiveTab = useUIStore((s: any) => s.setActiveTab);
  const [data, setData] = useState<ModulesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    fetch(`${API}/api/modules`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d: ModulesResp) => { if (alive) { setData(d); setLoading(false); } })
      .catch((e) => { if (alive) { setErr(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, []);

  // Run real multi-module pipelines THROUGH the kernel, proving the app computes
  // through the chain of modules (not just listing them) — including the
  // end-to-end mesh -> solver handoff (em_static solves on the mesh module's
  // MeshIR, no re-mesh).
  const [study, setStudy] = useState<Record<string, any>>({});
  const [studyBusy, setStudyBusy] = useState<string | null>(null);
  const runStudy = async (key: string, capabilities: string[]) => {
    setStudyBusy(key); setStudy((s) => ({ ...s, [key]: null }));
    try {
      const r = await fetch(`${API}/api/kernel/study`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capabilities }),
      });
      const j = await r.json();
      setStudy((s) => ({ ...s, [key]: j }));
    } catch (e) { setStudy((s) => ({ ...s, [key]: { error: String(e) } })); }
    setStudyBusy(null);
  };
  const steps = (j: any) => Object.entries(j?.steps || {})
    .map(([cap, s]: any) => `${cap}:${s.ok ? 'ok' : 'fail'}`).join('  ·  ');

  return (
    <Paper sx={PANEL}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>Platform Modules</Typography>
        {data && <Chip size="small" label={`${data.count} registered`} sx={{ height: 20, fontSize: 11 }} />}
      </Box>
      <Typography sx={{ fontSize: 11.5, color: '#64748b', mb: 1.5 }}>
        Read live from <code>/api/modules</code> — the portal builds itself from these manifests
        (web interfaces are modules too: each declares its UI panel).
      </Typography>

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mb: 1.5 }}>
        {/* geometry.2d -> cost */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <Button size="small" variant="outlined" disabled={!!studyBusy}
            onClick={() => runStudy('cost', ['geometry.2d', 'cost'])}
            sx={{ textTransform: 'none', fontSize: 11 }}>
            {studyBusy === 'cost' ? 'Running…' : 'Run pipeline: geometry.2d → cost'}
          </Button>
          {study.cost && !study.cost.error && (
            <Typography sx={{ fontSize: 11, fontFamily: 'monospace', color: study.cost.ok ? '#34d399' : '#fbbf24' }}>
              study {study.cost.ok ? 'ok' : 'partial'} — {steps(study.cost)}
              {study.cost.steps?.cost?.result?.total != null && `   →   cost $${study.cost.steps.cost.result.total}`}
            </Typography>
          )}
          {study.cost?.error && <Typography sx={{ fontSize: 11, color: '#fca5a5' }}>{study.cost.error}</Typography>}
        </Box>

        {/* geometry.2d -> mesh -> solver.em_static  (the mesh -> solver handoff) */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <Button size="small" variant="outlined" disabled={!!studyBusy}
            onClick={() => runStudy('field', ['geometry.2d', 'mesh', 'solver.em_static'])}
            sx={{ textTransform: 'none', fontSize: 11 }}>
            {studyBusy === 'field' ? 'Solving…' : 'Run pipeline: geometry.2d → mesh → solver.em_static'}
          </Button>
          {study.field && !study.field.error && (() => {
            const em = study.field.steps?.['solver.em_static']?.result;
            const meshN = study.field.steps?.mesh?.result?.n_nodes;
            const nn = em?.raw?.n_nodes;
            const bmean = em?.scalars?.b_mag_mean_T;
            const src = em?.provenance?.notes?.mesh_source;
            return (
              <Typography sx={{ fontSize: 11, fontFamily: 'monospace', color: study.field.ok ? '#34d399' : '#fbbf24' }}>
                study {study.field.ok ? 'ok' : 'partial'} — {steps(study.field)}
                {bmean != null && `   →   B̄=${Number(bmean).toFixed(2)} T on ${nn} nodes${meshN === nn ? ' (= mesh ✓)' : ''} via ${src}`}
              </Typography>
            );
          })()}
          {study.field?.error && <Typography sx={{ fontSize: 11, color: '#fca5a5' }}>{study.field.error}</Typography>}
        </Box>

        {/* FULL chain: 4 modules compose in one kernel study */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <Button size="small" variant="contained" disabled={!!studyBusy}
            onClick={() => runStudy('full', ['geometry.2d', 'mesh', 'solver.em_static', 'cost'])}
            sx={{ textTransform: 'none', fontSize: 11 }}>
            {studyBusy === 'full' ? 'Running full pipeline…' : 'Run FULL pipeline: geometry.2d → mesh → solver.em_static → cost'}
          </Button>
          {study.full && !study.full.error && (() => {
            const em = study.full.steps?.['solver.em_static']?.result;
            const bmean = em?.scalars?.b_mag_mean_T;
            const nn = em?.raw?.n_nodes;
            const total = study.full.steps?.cost?.result?.total;
            return (
              <Typography sx={{ fontSize: 11, fontFamily: 'monospace', color: study.full.ok ? '#34d399' : '#fbbf24' }}>
                study {study.full.ok ? 'ok' : 'partial'} — {steps(study.full)}
                {bmean != null && `   →   B̄=${Number(bmean).toFixed(2)} T on ${nn} nodes`}
                {total != null && ` · cost $${total}`}
              </Typography>
            );
          })()}
          {study.full?.error && <Typography sx={{ fontSize: 11, color: '#fca5a5' }}>{study.full.error}</Typography>}
        </Box>
      </Box>

      {loading && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: '#64748b' }}>
          <CircularProgress size={14} /> Loading modules…
        </Box>
      )}
      {err && (
        <Typography sx={{ fontSize: 12, color: '#fca5a5' }}>
          Failed to load modules: {err} — is the backend updated and restarted?
        </Typography>
      )}

      {data && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {data.modules.map((m) => (
            <Box key={m.name} sx={ITEM}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                <Typography sx={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>{m.name}</Typography>
                <Chip size="small" label={m.capability} sx={{ height: 18, fontSize: 10, bgcolor: '#1d4ed8', color: '#fff' }} />
                <Chip size="small" variant="outlined" label={`v${m.version}`} sx={{ height: 18, fontSize: 10 }} />
                <Chip size="small" variant="outlined" label={m.kind} sx={{ height: 18, fontSize: 10 }} />
                {m.ui && (() => {
                  const tab = PANEL_TO_TAB[m.ui!.panel_id];
                  return (
                    <Chip size="small" variant="outlined" label={`UI: ${m.ui!.panel_id}`}
                      clickable={!!tab} onClick={tab ? () => setActiveTab(tab as any) : undefined}
                      title={tab ? `Open the ${m.ui!.panel_id} tab` : undefined}
                      sx={{ height: 18, fontSize: 10, color: '#34d399', borderColor: '#34d399',
                            cursor: tab ? 'pointer' : 'default' }} />
                  );
                })()}
              </Box>
              {m.summary && <Typography sx={{ fontSize: 11, color: '#94a3b8', mt: 0.5 }}>{m.summary}</Typography>}
              {(m.inputs?.length || m.outputs?.length) ? (
                <Typography sx={{ fontSize: 10.5, color: '#7dd3fc', mt: 0.5, fontFamily: 'monospace' }}>
                  in: {(m.inputs && m.inputs.length) ? m.inputs.join(', ') : '∅'}
                  {'  →  '}
                  out: {(m.outputs && m.outputs.length) ? m.outputs.join(', ') : '∅'}
                </Typography>
              ) : null}
              <Box sx={{ display: 'flex', gap: 1.5, mt: 0.5, fontSize: 10.5, color: '#64748b', flexWrap: 'wrap' }}>
                <span>contracts {m.contracts_version}</span>
                {m.depends_on.length > 0 && <span>depends on: {m.depends_on.join(', ')}</span>}
                {m.ui?.frontend_module && <span>panel → {m.ui.frontend_module}</span>}
              </Box>
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  );
};

export default ModulesPanel;
