/**
 * DemagMap — Ansys-style demagnetisation map (per-element, in %).
 *
 * Renders ONLY the magnet triangles, coloured by the local irreversible
 * demagnetisation:  % = (1 − Br_factor)·100  (0 % = full strength / safe,
 * 100 % = the element lost all its remanence).  The view zooms to the magnet
 * bounding box so the spokes fill the frame.  Fed by the transient solve's
 * `demag_field` payload (only present when the Demagnetisation option is on).
 */
import React from 'react';
import { Box, Paper, Typography, Tooltip } from '@mui/material';

export interface DemagField {
  vertices: [number, number][];
  triangles: [number, number, number][];
  domain_per_tri: number[];
  demag_coef_per_tri: number[];
  mag_domains: number[];
  extent: [number, number, number, number];
}

// % demag → colour.  0 % green (safe) → 50 % amber → 100 % red (irreversible),
// matching the "danger" reading an engineer expects from an Ansys demag plot.
function demagColor(pct: number): string {
  const t = Math.max(0, Math.min(100, pct)) / 100;
  let r: number, g: number, b: number;
  if (t < 0.5) {                       // green → amber
    const u = t / 0.5;
    r = 34 + (250 - 34) * u; g = 197 + (204 - 197) * u; b = 94 + (21 - 94) * u;
  } else {                             // amber → red
    const u = (t - 0.5) / 0.5;
    r = 250 + (239 - 250) * u; g = 204 + (68 - 204) * u; b = 21 + (68 - 21) * u;
  }
  return `rgb(${r | 0},${g | 0},${b | 0})`;
}

const DemagMap: React.FC<{ field: DemagField }> = ({ field }) => {
  const { vertices, triangles, domain_per_tri, demag_coef_per_tri, mag_domains } = field;
  const magSet = new Set(mag_domains);

  // Collect magnet triangles + the magnet-only bounding box (zoom to spokes).
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  const tris: { v: [number, number][]; pct: number }[] = [];
  let worst = 0, nDemag = 0;
  for (let i = 0; i < triangles.length; i++) {
    if (!magSet.has(domain_per_tri[i])) continue;
    const coef = demag_coef_per_tri[i] ?? 1;
    const pct = Math.max(0, Math.min(100, (1 - coef) * 100));
    if (pct > worst) worst = pct;
    if (pct > 0.5) nDemag++;
    const vs = triangles[i].map(vi => vertices[vi]) as [number, number][];
    for (const [x, y] of vs) {
      if (x < xmin) xmin = x; if (x > xmax) xmax = x;
      if (y < ymin) ymin = y; if (y > ymax) ymax = y;
    }
    tris.push({ v: vs, pct });
  }
  if (!tris.length) return null;

  const W = Math.max(xmax - xmin, 1e-6), H = Math.max(ymax - ymin, 1e-6);
  const pad = 0.06 * Math.max(W, H);
  // SVG y is flipped (screen y grows downward): svgY = ymax − y.
  const poly = (vs: [number, number][]) =>
    vs.map(([x, y]) => `${(x - xmin).toFixed(3)},${(ymax - y).toFixed(3)}`).join(' ');

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
          Demagnetisation map — % of Br lost (per element)
          <Tooltip title="Irreversible demagnetisation at every magnet element: % = (1 − Br_factor)·100, where Br_factor comes from the recoil line at the worst demagnetising field the element saw over the electrical period. 0 % = full strength, 100 % = fully demagnetised. This is the same de-rating applied to the magnets in the torque / back-EMF above." placement="top">
            <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
          </Tooltip>
        </Typography>
        <Typography sx={{ fontSize: 11, color: worst > 0.5 ? '#f87171' : '#34d399', fontWeight: 600 }}>
          worst {worst.toFixed(0)}% · {nDemag} elems
        </Typography>
      </Box>

      <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'stretch' }}>
        <Box sx={{ flexGrow: 1, bgcolor: 'var(--panel-2)', border: '1px solid var(--app-bg)',
          borderRadius: 1, minHeight: 300, display: 'flex' }}>
          <svg viewBox={`${-pad} ${-pad} ${W + 2 * pad} ${H + 2 * pad}`}
            width="100%" height="320" preserveAspectRatio="xMidYMid meet"
            style={{ display: 'block' }}>
            {tris.map((t, i) => (
              <polygon key={i} points={poly(t.v)} fill={demagColor(t.pct)}
                stroke="var(--panel-2)" strokeWidth={W / 600} />
            ))}
          </svg>
        </Box>
        {/* Vertical 0–100 % colour legend */}
        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'space-between', py: 0.5, width: 54 }}>
          <Typography sx={{ fontSize: 9, color: 'var(--text-2)' }}>100%</Typography>
          <Box sx={{ flexGrow: 1, width: 14, my: 0.5, borderRadius: 1,
            border: '1px solid var(--line-soft)',
            background: `linear-gradient(to top,
              ${demagColor(0)} 0%, ${demagColor(25)} 25%, ${demagColor(50)} 50%,
              ${demagColor(75)} 75%, ${demagColor(100)} 100%)` }} />
          <Typography sx={{ fontSize: 9, color: 'var(--text-2)' }}>0%</Typography>
          <Typography sx={{ fontSize: 8, color: 'var(--text-4)', mt: 0.5, textAlign: 'center' }}>
            Br lost
          </Typography>
        </Box>
      </Box>
    </Paper>
  );
};

export default DemagMap;
