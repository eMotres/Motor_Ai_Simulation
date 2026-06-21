/**
 * ModulesPanel — shows the portal's registered modules, read live from
 * GET /api/modules. The portal is meant to build itself FROM these manifests
 * (web interfaces are modules too: each declares its UI panel). This panel is
 * the first consumer of that registry — proof the web-as-modules wiring works.
 */
import React, { useEffect, useState } from 'react';
import { Box, Paper, Typography, Chip, CircularProgress } from '@mui/material';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

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
                {m.ui && (
                  <Chip size="small" variant="outlined" label={`UI: ${m.ui.panel_id}`}
                    sx={{ height: 18, fontSize: 10, color: '#34d399', borderColor: '#34d399' }} />
                )}
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
