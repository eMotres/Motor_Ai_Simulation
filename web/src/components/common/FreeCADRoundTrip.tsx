// FreeCAD round-trip buttons — export the ACTIVE machine as a .FCStd project
// (solids + Parameters spreadsheet) and import an edited one back.  The import
// goes through the SAME PUT-geometry path as manual edits (validator, family
// locks, clamp report), so refusals surface exactly like a typed value would.
// One shared component: it first shipped inside GeometryForm, which turned out
// to be DEAD code — the Geometry tab actually renders ParameterVariationTable
// (found live 2026-08-23: "не вижу кнопку экспорта").
import React, { useState } from 'react';
import { Box, Button, Typography } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

const FreeCADRoundTrip: React.FC = () => {
  const { connectedToApi, fetchGeometryFromApi } = useMotorStore();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const doExport = async () => {
    setBusy(true); setMsg('exporting…');
    try {
      const r = await fetch(`${API}/api/freecad/export`);
      if (!r.ok) throw new Error(String((await r.json())?.detail ?? r.status));
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (r.headers.get('Content-Disposition') ?? '')
        .match(/filename="(.+?)"/)?.[1] ?? 'motor_freecad.zip';
      a.click(); URL.revokeObjectURL(a.href);
      // App Control blocks OCP on the server (2026-09-14) — that bundle is a
      // success with an extra step, not a failure.
      setMsg(r.headers.get('X-Bundle-Kind') === 'macro-dxf'
        ? '✓ exported (macro bundle — OCP is blocked here): unzip, then run '
          + 'build_motor.FCMacro in FreeCAD to get the solids (README inside)'
        : '✓ exported — open the .FCStd in FreeCAD (README inside)');
    } catch (e: any) { setMsg('✗ export failed: ' + String(e?.message ?? e)); }
    finally { setBusy(false); }
  };

  const doImport = async (f: File) => {
    setBusy(true); setMsg('importing…');
    try {
      const fd = new FormData(); fd.append('file', f);
      const r = await fetch(`${API}/api/freecad/import`, { method: 'POST', body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(typeof j?.detail === 'string' ? j.detail : JSON.stringify(j?.detail));
      const n = Object.keys(j.applied ?? {}).length;
      setMsg(`✓ ${n} parameter(s) applied`
        + (j.unknown?.length ? ` · ${j.unknown.length} alias(es) not ours — ignored` : ''));
      await fetchGeometryFromApi();
    } catch (err: any) { setMsg('✗ import failed: ' + String(err?.message ?? err)); }
    finally { setBusy(false); }
  };

  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
      <Button size="small" variant="outlined" disabled={!connectedToApi || busy}
        sx={{ fontSize: 11, textTransform: 'none' }} onClick={doExport}>
        ⇩ Export FreeCAD
      </Button>
      <Button size="small" variant="outlined" component="label"
        disabled={!connectedToApi || busy}
        sx={{ fontSize: 11, textTransform: 'none' }}>
        ⇧ Import FreeCAD
        <input type="file" accept=".FCStd,.fcstd,.zip" hidden
          onChange={(e) => {
            const f = e.target.files?.[0]; e.target.value = '';
            if (f) void doImport(f);
          }} />
      </Button>
      {msg && <Typography variant="caption" color="text.secondary">{msg}</Typography>}
    </Box>
  );
};

export default FreeCADRoundTrip;
