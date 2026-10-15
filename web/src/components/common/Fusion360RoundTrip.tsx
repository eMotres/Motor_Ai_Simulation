// Fusion 360 parameter round-trip of the ACTIVE machine - the geometry as a Parameter I/O CSV.
// Export: download the CSV, then in Fusion: Parameter I/O → Import (existing user parameters are updated, missing ones created).
// Import: Parameter I/O → Export in Fusion, upload here - the changes are shown BEFORE they land (dry run), and applied only on
// confirmation, through the same geometry write path the form uses.  Names follow config/fusion_param_map.yaml.
//
// Since 2026-10-15 these are actions of the Geometry tab's "Files" menu (components/geometry/FilesMenu.tsx): this module is the hook
// behind the Active items.
import { useState } from 'react';
import { useMotorStore } from '../../stores/motorStore';
import { formatApiErrorDetail } from '../../lib/apiErrorDetail';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export function useFusion360RoundTrip() {
  const { connectedToApi, fetchGeometryFromApi } = useMotorStore();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const doExport = async () => {
    setBusy(true); setMsg('exporting…');
    try {
      const r = await fetch(`${API}/api/fusion/params.csv`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const cd = r.headers.get('Content-Disposition') || '';
      const name = /filename="([^"]+)"/.exec(cd)?.[1] || 'fusion_params.csv';
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = name; a.click();
      URL.revokeObjectURL(a.href);
      setMsg('✓ CSV downloaded — in Fusion: Parameter I/O → Import');
    } catch (e: any) { setMsg('✗ export failed: ' + String(e?.message ?? e)); }
    setBusy(false);
  };

  const post = async (f: File, dry: boolean) => {
    const fd = new FormData(); fd.append('file', f);
    const r = await fetch(`${API}/api/fusion/import?dry_run=${dry ? 1 : 0}`, { method: 'POST', body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(formatApiErrorDetail(j?.detail) || `HTTP ${r.status}`);
    return j;
  };

  const doImport = async (f: File) => {
    setBusy(true); setMsg('reading…');
    try {
      const d = await post(f, true);
      // The dry run runs the SAME schema/value validation the real write
      // would (routes.geometry.check_geometry_submission): a CSV whose
      // values, applied together, do not describe a buildable machine comes
      // back `ok: false` here, before any confirm dialog — never "apply N
      // parameters?" for a machine that would then be refused on the second
      // click.
      if (d.ok === false) {
        setMsg(`✗ refused — ${formatApiErrorDetail({ error: d.error, invalid_parameters: d.invalid_parameters }) || d.note || 'invalid geometry'}`);
        setBusy(false); return;
      }
      const n = Object.keys(d.applied ?? {}).length;
      const lines = Object.entries(d.applied ?? {}).map(([k, v]) => `${k}: ${d.before?.[k]} → ${v}`);
      const extra = [
        d.unknown?.length ? `${d.unknown.length} unknown (Fusion-only) skipped` : '',
        Object.keys(d.refused ?? {}).length ? `${Object.keys(d.refused).length} refused (formulas / units)` : '',
      ].filter(Boolean).join('; ');
      if (!n) { setMsg(`✓ nothing to apply — geometry already matches${extra ? ' (' + extra + ')' : ''}`); setBusy(false); return; }
      if (!window.confirm(`Apply ${n} parameter(s) from Fusion?\n\n${lines.slice(0, 20).join('\n')}${n > 20 ? '\n…' : ''}${extra ? '\n\n' + extra : ''}`)) {
        setMsg('cancelled — nothing written'); setBusy(false); return;
      }
      const a = await post(f, false);
      if (a.ok === false) {
        // Should not happen — the dry run above already refused a bad
        // combination — but the write path is the one that must never be
        // wrong, so it is still checked rather than assumed.
        setMsg(`✗ refused — ${formatApiErrorDetail({ error: a.error, invalid_parameters: a.invalid_parameters }) || a.note || 'invalid geometry'}`);
        setBusy(false); return;
      }
      await fetchGeometryFromApi();
      setMsg(`✓ applied ${Object.keys(a.applied ?? {}).length}, unchanged ${a.unchanged?.length ?? 0}, skipped ${a.unknown?.length ?? 0}`);
    } catch (err: any) { setMsg('✗ import failed: ' + formatApiErrorDetail(err?.message ?? err)); }
    setBusy(false);
  };

  return { connectedToApi, busy, msg, setMsg, doExport, doImport };
}
