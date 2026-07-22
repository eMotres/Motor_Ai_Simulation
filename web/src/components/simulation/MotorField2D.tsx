/**
 * MotorField2D — 2D field visualisation of the motor cross-section.
 *
 * Renders analytical field maps on a canvas:
 *   |B|    — magnetic flux density magnitude  (heatmap, viridis)
 *   A_z    — vector potential  (heatmap, RdBu)  +  flux lines (contours)
 *   J_z    — current density   (heatmap, RdBu)
 *   domain — material regions  (discrete colour)
 *
 * Geometry overlay: stator outline, rotor, magnets, slot positions.
 * Flux lines are iso-contours of A_z (classic FEA look).
 *
 * Rotor angle slider animates the PM field rotation.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box, Paper, Typography, Chip, CircularProgress,
  ToggleButton, ToggleButtonGroup, Slider, Tooltip,
  IconButton,
} from '@mui/material';
import RefreshIcon    from '@mui/icons-material/Refresh';
import DownloadIcon   from '@mui/icons-material/Download';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ── colour maps ───────────────────────────────────────────────────────────────
// Viridis LUT (sampled at 256 values)
function viridis(t: number): [number,number,number] {
  const c0=[68,1,84], c1=[59,82,139], c2=[33,145,140], c3=[94,201,98], c4=[253,231,37];
  const pts = [c0,c1,c2,c3,c4];
  const s  = Math.max(0, Math.min(1, t)) * (pts.length - 1);
  const i  = Math.min(Math.floor(s), pts.length - 2);
  const f  = s - i;
  return [
    Math.round(pts[i][0] + f*(pts[i+1][0]-pts[i][0])),
    Math.round(pts[i][1] + f*(pts[i+1][1]-pts[i][1])),
    Math.round(pts[i][2] + f*(pts[i+1][2]-pts[i][2])),
  ];
}

// RdBu diverging
function rdbu(t: number): [number,number,number] {
  const c0=[178,24,43], c1=[244,165,130], c2=[255,255,255], c3=[146,197,222], c4=[33,102,172];
  const pts = [c0,c1,c2,c3,c4];
  const s   = Math.max(0, Math.min(1, t)) * (pts.length - 1);
  const i   = Math.min(Math.floor(s), pts.length - 2);
  const f   = s - i;
  return [
    Math.round(pts[i][0] + f*(pts[i+1][0]-pts[i][0])),
    Math.round(pts[i][1] + f*(pts[i+1][1]-pts[i][1])),
    Math.round(pts[i][2] + f*(pts[i+1][2]-pts[i][2])),
  ];
}

// Domain colour map
const DOMAIN_COLORS: Record<number,[number,number,number,number]> = {
  0:  [6,  13, 23,  0],      // outside — transparent
  1:  [72, 85, 99,  240],    // stator steel 20SW1200 — slate
  2:  [251,191,36,  240],    // slot winding copper — amber
  3:  [8,  18, 38,  200],    // air gap — deep dark blue
  4:  [59, 130,246, 240],    // magnet N+ — blue
  44: [239,68, 68,  240],    // magnet S− — red
  5:  [55, 68, 82,  230],    // rotor back-iron — lighter slate
  6:  [180,180,190, 210],    // shaft Al6061 — grey
  7:  [8,  18, 38,  150],    // slot opening / jumper — dark (part of air gap)
  8:  [120,100,60,  200],    // insulation / slot liner — brown
};

// ── types ─────────────────────────────────────────────────────────────────────
type FieldKey = 'B_mag' | 'A_z' | 'J_z' | 'domain';

interface Magnet {
  index: number;
  polarity: number;  // +1 = N pole, -1 = S pole
  x_m: number;       // center X [m]
  y_m: number;       // center Y [m]
  r_inner_m: number;
  r_outer_m: number;
  angle_rad: number;
}

interface PolyRing {
  exterior: [number, number][];
  holes:    [number, number][][];
}
interface MagnetPoly {
  polarity: number;
  rings:    PolyRing[];
}
interface Polygons {
  stator:  PolyRing[];
  rotor:   PolyRing[];
  shaft:   PolyRing[];
  coils:   PolyRing[];
  magnets: MagnetPoly[];
}

interface FieldData {
  grid_size:   number;
  extent:      [number,number,number,number];  // xmin,xmax,ymin,ymax [m]
  A_z:         number[];
  B_x:         number[];
  B_y:         number[];
  B_mag:       number[];
  J_z:         number[];
  domain:      number[];
  stats: { A_z_min: number; A_z_max: number; B_mag_max: number;
           B_mag_mean: number; J_z_max: number };
  geometry:    Record<string,number>;
  magnets?:    Magnet[];
  polygons?:   Polygons;
  rotor_angle_deg: number;
  gamma_deg:   number;
  note:        string;
}

interface Props {
  gamma_deg: number;
}

// ── iso-contour drawing ───────────────────────────────────────────────────────
function drawContours(
  ctx: CanvasRenderingContext2D,
  data: number[], gs: number, minVal: number, maxVal: number,
  nLevels: number, canvasW: number, canvasH: number,
  domain: number[],
) {
  const levels: number[] = [];
  for (let k = 1; k < nLevels; k++)
    levels.push(minVal + (k / nLevels) * (maxVal - minVal));

  const scaleX = canvasW / gs;
  const scaleY = canvasH / gs;

  ctx.lineWidth = 0.8;
  ctx.strokeStyle = 'rgba(255,255,255,0.35)';

  // Marching squares (simplified — draw line segments per cell)
  for (let r = 0; r < gs - 1; r++) {
    for (let c = 0; c < gs - 1; c++) {
      const dom = domain[r * gs + c];
      if (dom === 0) continue;   // skip outside

      const v00 = data[r * gs + c];
      const v01 = data[r * gs + c + 1];
      const v10 = data[(r+1) * gs + c];
      const v11 = data[(r+1) * gs + c + 1];

      for (const level of levels) {
        // Build 4-bit case
        const b = ((v00 >= level ? 8 : 0) | (v01 >= level ? 4 : 0) |
                   (v11 >= level ? 2 : 0) | (v10 >= level ? 1 : 0));
        if (b === 0 || b === 15) continue;

        // Linear interpolation helpers
        const lerp = (a: number, b2: number) =>
          a === b2 ? 0 : (level - a) / (b2 - a);

        const top    = lerp(v00, v01);
        const right  = lerp(v01, v11);
        const bottom = lerp(v10, v11);
        const left   = lerp(v00, v10);

        // Convert cell corner pixel positions
        const x0 = c * scaleX, x1 = (c+1) * scaleX;
        const y0 = r * scaleY, y1 = (r+1) * scaleY;

        const pts: Record<string,[number,number]> = {
          T: [x0 + top   *(x1-x0), y0],
          R: [x1,                  y0 + right *(y1-y0)],
          B: [x0 + bottom*(x1-x0), y1],
          L: [x0,                  y0 + left  *(y1-y0)],
        };

        // Segments for each case
        const segs: Record<number,[string,string][]> = {
          1: [['L','B']], 2: [['B','R']], 3: [['L','R']],
          4: [['T','R']], 5: [['T','L'],['B','R']], 6: [['T','B']],
          7: [['T','L']], 8: [['T','L']], 9: [['T','B']],
          10:[['T','R'],['L','B']], 11:[['T','R']], 12:[['L','R']],
          13:[['B','R']], 14:[['L','B']], 15:[],
        };
        for (const [s,e] of (segs[b]??[])) {
          ctx.beginPath();
          ctx.moveTo(pts[s][0], pts[s][1]);
          ctx.lineTo(pts[e][0], pts[e][1]);
          ctx.stroke();
        }
      }
    }
  }
}

// ── main component ─────────────────────────────────────────────────────────────
const MotorField2D: React.FC<Props> = ({ gamma_deg }) => {
  const canvasRef  = useRef<HTMLCanvasElement>(null);
  const [data,    setData]    = useState<FieldData | null>(null);
  const [loading, setLoading] = useState(false);
  const [field,   setField]   = useState<FieldKey>('B_mag');
  const [showFlux,    setShowFlux]    = useState(true);
  const [showGeom,    setShowGeom]    = useState(true);
  const [rotorAng, setRotorAng] = useState(0);

  const CANVAS_SIZE = 500;

  // ── fetch ──────────────────────────────────────────────────────────────────
  const fetchField = useCallback(() => {
    setLoading(true);
    fetch(`${API}/api/simulation/physics/field2d?grid_size=150&rotor_angle_deg=${rotorAng}&gamma_deg=${gamma_deg}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [rotorAng, gamma_deg]);

  useEffect(() => { fetchField(); }, [fetchField]);

  // ── render canvas ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!data || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const ctx    = canvas.getContext('2d')!;
    const gs     = data.grid_size;
    const W      = CANVAS_SIZE, H = CANVAS_SIZE;
    canvas.width = W; canvas.height = H;

    // Choose field array + colour map
    let arr: number[];
    let vMin = 0, vMax = 1;
    let cmap: (t: number) => [number,number,number];

    if (field === 'B_mag') {
      arr = data.B_mag; vMax = data.stats.B_mag_max; cmap = viridis;
    } else if (field === 'A_z') {
      arr = data.A_z;
      vMin = data.stats.A_z_min; vMax = data.stats.A_z_max;
      cmap = rdbu;
    } else if (field === 'J_z') {
      arr = data.J_z;
      vMin = -data.stats.J_z_max; vMax = data.stats.J_z_max;
      cmap = rdbu;
    } else {
      arr = data.domain.map(Number); vMax = 1; cmap = viridis;
    }

    const imgData = ctx.createImageData(W, H);
    const pix     = imgData.data;

    // Pixel fill loop — map grid[row][col] → canvas pixel
    const scX = gs / W, scY = gs / H;

    for (let py = 0; py < H; py++) {
      for (let px = 0; px < W; px++) {
        const gr = Math.min(Math.floor(py * scY), gs - 1);
        const gc = Math.min(Math.floor(px * scX), gs - 1);
        const idx = gr * gs + gc;
        const dom = data.domain[idx];

        let r=0, g=0, b=0, a=0;
        if (field === 'domain') {
          const dc = DOMAIN_COLORS[dom] ?? [30,30,30,200];
          [r,g,b,a] = dc;
        } else {
          if (dom === 0) { a = 0; }
          else {
            const v   = arr[idx];
            const t   = vMax !== vMin
              ? (v - vMin) / (vMax - vMin)
              : 0.5;
            [r,g,b] = cmap(Math.max(0, Math.min(1, t)));
            // Darken steel slightly for domain distinction
            if (dom === 1 || dom === 5) { r*=0.7; g*=0.7; b*=0.7; }
            a = dom === 0 ? 0 : 255;
          }
        }
        const pidx = (py * W + px) * 4;
        pix[pidx]   = r; pix[pidx+1] = g;
        pix[pidx+2] = b; pix[pidx+3] = a;
      }
    }
    ctx.putImageData(imgData, 0, 0);

    // Background
    ctx.fillStyle = '#060d17';
    // Draw the outside region as background
    ctx.globalCompositeOperation = 'destination-over';
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'source-over';

    // ── Flux lines (contours of A_z) ──────────────────────────────────────
    if (showFlux && field !== 'domain') {
      drawContours(ctx, data.A_z, gs,
        data.stats.A_z_min, data.stats.A_z_max,
        20, W, H, data.domain);
    }

    // ── Geometry overlay — REAL CadQuery polygons (T-teeth, magnets, coils) ──
    if (showGeom) {
      const [xMin,,yMin] = data.extent;
      const span   = data.extent[1] - data.extent[0];
      const m2px   = W / span;

      const cx = (x: number) => (x - xMin) * m2px;
      const cy = (y: number) => H - (y - yMin) * m2px;  // flip Y

      const tracePoly = (ring: PolyRing) => {
        ctx.beginPath();
        ring.exterior.forEach((pt, i) => {
          const px = cx(pt[0]), py = cy(pt[1]);
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        });
        ctx.closePath();
        for (const hole of ring.holes) {
          hole.forEach((pt, i) => {
            const px = cx(pt[0]), py = cy(pt[1]);
            if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
          });
          ctx.closePath();
        }
      };

      const drawRings = (rings: PolyRing[], fill: string | null, stroke: string, lw = 1) => {
        for (const ring of rings) {
          tracePoly(ring);
          if (fill) { ctx.fillStyle = fill;   ctx.fill('evenodd'); }
          ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke();
        }
      };

      const polys = data.polygons;
      if (polys) {
        // For the field views, draw outlines only (let the heatmap show through).
        // For the domain view, fill polygons with category colours.
        const isDomain = field === 'domain';

        // Stator (T-teeth)
        drawRings(polys.stator,
          isDomain ? 'rgba(72,85,99,0.95)'   : null,
          'rgba(220,220,235,0.7)', 1.2);

        // Rotor back-iron
        drawRings(polys.rotor,
          isDomain ? 'rgba(55,68,82,0.95)'   : null,
          'rgba(180,180,200,0.55)', 1);

        // Shaft
        drawRings(polys.shaft,
          isDomain ? 'rgba(180,180,190,0.85)' : null,
          'rgba(220,220,230,0.7)', 1);

        // Coils (winding bars)
        for (const ring of polys.coils) {
          tracePoly(ring);
          if (isDomain) { ctx.fillStyle = 'rgba(251,191,36,0.95)'; ctx.fill('evenodd'); }
          ctx.strokeStyle = 'rgba(251,191,36,0.85)';
          ctx.lineWidth = 0.6;
          ctx.stroke();
        }

        // Magnets — fill always (N=blue, S=red) so polarity is visible on every view
        for (const mag of polys.magnets) {
          const fillCol = mag.polarity > 0
            ? (isDomain ? 'rgba(59,130,246,0.95)' : 'rgba(59,130,246,0.45)')
            : (isDomain ? 'rgba(239,68,68,0.95)'  : 'rgba(239,68,68,0.45)');
          const strokeCol = mag.polarity > 0 ? 'rgba(96,165,250,0.95)' : 'rgba(248,113,113,0.95)';
          for (const ring of mag.rings) {
            tracePoly(ring);
            ctx.fillStyle = fillCol;   ctx.fill('evenodd');
            ctx.strokeStyle = strokeCol; ctx.lineWidth = 1; ctx.stroke();
          }
        }
      } else {
        // Fallback: draw radii circles if polygon payload missing
        const geo = data.geometry;
        const circ = (r: number, col: string, lw=1) => {
          ctx.beginPath();
          ctx.arc(cx(0), cy(0), r * m2px, 0, 2*Math.PI);
          ctx.strokeStyle = col; ctx.lineWidth = lw; ctx.stroke();
        };
        circ(geo.r_stator_out, 'rgba(200,200,200,0.7)', 1.5);
        circ(geo.r_stator_in,  'rgba(150,150,180,0.5)', 0.8);
        circ(geo.r_rotor_out,  'rgba(150,150,180,0.5)', 0.8);
        circ(geo.r_rotor_in,   'rgba(100,100,150,0.4)', 0.8);
        circ(geo.r_shaft_in,   'rgba(200,200,200,0.5)', 0.8);
      }
    }

    // Magnet polarization is encoded in the magnet fill colour (blue=N, red=S)
    // and is consumed by the backend as a tangential bound current on the
    // bottom and top radial faces of each magnet. No vector overlay here —
    // the picture shows only the field (A_z / |B| / J_z / domain).
  }, [data, field, showFlux, showGeom]);

  // ── Download PNG ───────────────────────────────────────────────────────────
  const downloadPng = () => {
    const a = document.createElement('a');
    a.href = canvasRef.current?.toDataURL('image/png') ?? '';
    a.download = `motor_field_${field}_theta${rotorAng}.png`;
    a.click();
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>

      {/* ── Header ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6',
          textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          2D Field Map
        </Typography>
        <Chip label="analytical Green's function" size="small"
          sx={{ fontSize: 9, height: 16, bgcolor: '#2d1e0f', color: '#fb923c' }}/>
        {data && (
          <>
            <Chip label={`|B|_max = ${data.stats.B_mag_max.toFixed(3)} T`}
              size="small" sx={{ fontSize: 9, height: 16, bgcolor: 'var(--line-accent)', color: '#93c5fd' }}/>
            <Chip label={`θ = ${rotorAng}° mech`}
              size="small" sx={{ fontSize: 9, height: 16, bgcolor: 'var(--panel)', color: 'var(--text-3)' }}/>
          </>
        )}
        <Box sx={{ flex: 1 }}/>
        <IconButton size="small" onClick={fetchField} sx={{ color: 'var(--text-4)' }}>
          <RefreshIcon fontSize="small"/>
        </IconButton>
        <IconButton size="small" onClick={downloadPng} sx={{ color: 'var(--text-4)' }}>
          <DownloadIcon fontSize="small"/>
        </IconButton>
      </Box>

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>

        {/* ── Canvas ── */}
        <Box sx={{ position: 'relative', flex: '0 0 auto' }}>
          {loading && (
            <Box sx={{
              position: 'absolute', inset: 0, bgcolor: 'rgba(6,13,23,0.7)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              zIndex: 2, borderRadius: 1,
            }}>
              <CircularProgress size={32} sx={{ color: '#3b82f6' }}/>
            </Box>
          )}
          <canvas
            ref={canvasRef}
            width={CANVAS_SIZE} height={CANVAS_SIZE}
            style={{
              width: CANVAS_SIZE, height: CANVAS_SIZE,
              borderRadius: 4, border: '1px solid var(--line-soft)',
              display: 'block',
            }}
          />
        </Box>

        {/* ── Controls + Colour bar ── */}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, flex: 1, minWidth: 180 }}>

          {/* Field selector */}
          <Box>
            <Typography sx={{ fontSize: 9, color: 'var(--text-4)', textTransform: 'uppercase',
              letterSpacing: 1, mb: 0.5 }}>Field</Typography>
            <ToggleButtonGroup size="small" value={field} exclusive
              onChange={(_, v) => v && setField(v)}
              sx={{ flexDirection: 'column', alignItems: 'flex-start', gap: 0.25,
                '& .MuiToggleButton-root': {
                  width: '100%', justifyContent: 'flex-start', py: 0.4, px: 1,
                  fontSize: 11, color: 'var(--text-3)', borderColor: 'var(--panel)', textTransform: 'none',
                  '&.Mui-selected': { color: 'var(--text-0)', bgcolor: 'var(--line-accent)', borderColor: '#3b82f6' },
                },
              }}>
              <ToggleButton value="B_mag">|B| flux density [T]</ToggleButton>
              <ToggleButton value="A_z"> A_z vector potential [Wb/m]</ToggleButton>
              <ToggleButton value="J_z"> J_z current density [A/m²]</ToggleButton>
              <ToggleButton value="domain">Material domains</ToggleButton>
            </ToggleButtonGroup>
          </Box>

          {/* Overlays */}
          <Box>
            <Typography sx={{ fontSize: 9, color: 'var(--text-4)', textTransform: 'uppercase',
              letterSpacing: 1, mb: 0.5 }}>Overlays</Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
              {[
                { label: 'Flux lines (A_z contours)', val: showFlux, set: setShowFlux },
                { label: 'Geometry boundaries',       val: showGeom, set: setShowGeom },
              ].map(({ label, val, set }) => (
                <Box key={label} onClick={() => set(!val)}
                  sx={{ display: 'flex', alignItems: 'center', gap: 1, cursor: 'pointer',
                    p: 0.5, borderRadius: 1, bgcolor: val ? 'var(--panel)' : 'transparent',
                    border: '1px solid', borderColor: val ? 'var(--line)' : 'transparent' }}>
                  <Box sx={{ width: 12, height: 12, borderRadius: 0.5, flexShrink: 0,
                    bgcolor: val ? '#3b82f6' : 'var(--panel)', border: '1px solid var(--line)' }}/>
                  <Typography sx={{ fontSize: 10, color: val ? 'var(--text-0)' : 'var(--text-4)' }}>
                    {label}
                  </Typography>
                </Box>
              ))}
            </Box>
          </Box>

          {/* Rotor angle slider */}
          <Box>
            <Typography sx={{ fontSize: 9, color: 'var(--text-4)', textTransform: 'uppercase',
              letterSpacing: 1, mb: 0.5 }}>
              Rotor angle: {rotorAng.toFixed(1)}° mech
              <Typography component="span" sx={{ fontSize: 9, color: 'var(--line)', ml: 1 }}>
                ({(rotorAng * 14).toFixed(0)}° elec)
              </Typography>
            </Typography>
            <Slider
              value={rotorAng}
              onChange={(_, v) => setRotorAng(v as number)}
              onChangeCommitted={() => fetchField()}
              min={0} max={25.71} step={0.43}
              sx={{ color: '#3b82f6', py: 0.5,
                '& .MuiSlider-thumb': { width: 12, height: 12 } }}/>
            <Typography sx={{ fontSize: 9, color: 'var(--line)' }}>
              One electrical period = 25.71° mech (14 pole pairs)
            </Typography>
          </Box>

          {/* Colour bar */}
          {data && field !== 'domain' && (
            <Box>
              <Typography sx={{ fontSize: 9, color: 'var(--text-4)', textTransform: 'uppercase',
                letterSpacing: 1, mb: 0.5 }}>Colour scale</Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Typography sx={{ fontSize: 9, color: 'var(--text-3)', width: 40 }}>
                  {field === 'B_mag' ? '0' :
                   field === 'A_z'   ? data.stats.A_z_min.toExponential(1) :
                   (-data.stats.J_z_max/1e6).toFixed(0)+'M'}
                </Typography>
                <Box sx={{
                  flex: 1, height: 12, borderRadius: 1,
                  background: field === 'J_z' || field === 'A_z'
                    ? 'linear-gradient(to right, #b21821, #f4a582, #fff, #92c5de, #2166ac)'
                    : 'linear-gradient(to right, #440154, #3b528b, #21918c, #5dc962, #fde725)',
                }}/>
                <Typography sx={{ fontSize: 9, color: 'var(--text-3)', width: 40, textAlign: 'right' }}>
                  {field === 'B_mag' ? data.stats.B_mag_max.toFixed(2)+'T' :
                   field === 'A_z'   ? data.stats.A_z_max.toExponential(1) :
                   (data.stats.J_z_max/1e6).toFixed(0)+'M'}
                </Typography>
              </Box>
            </Box>
          )}

          {/* Domain legend */}
          {field === 'domain' && (
            <Box>
              <Typography sx={{ fontSize: 9, color: 'var(--text-4)', textTransform: 'uppercase',
                letterSpacing: 1, mb: 0.75 }}>Real Geometry Domains</Typography>
              {[
                { col: 'rgb(72,85,99)',    label: 'Stator steel (20SW1200)' },
                { col: 'rgb(251,191,36)',  label: 'Winding copper (Cu)' },
                { col: 'rgb(120,100,60)',  label: 'Slot insulation' },
                { col: 'rgb(8,18,38)',     label: 'Air gap' },
                { col: 'rgb(59,130,246)',  label: 'Magnet N+ (tapered)' },
                { col: 'rgb(239,68,68)',   label: 'Magnet S− (tapered)' },
                { col: 'rgb(55,68,82)',    label: 'Rotor back-iron' },
                { col: 'rgb(180,180,190)', label: 'Shaft (Al6061)' },
              ].map(({ col, label }) => (
                <Box key={label} sx={{ display:'flex', alignItems:'center', gap:0.75, mb:0.25 }}>
                  <Box sx={{ width:10, height:10, bgcolor:col, borderRadius:'2px', flexShrink:0 }}/>
                  <Typography sx={{ fontSize:9, color:'var(--text-2)' }}>{label}</Typography>
                </Box>
              ))}
            </Box>
          )}

          {/* Stats */}
          {data && (
            <Box sx={{ bgcolor:'var(--panel-2)', p:1, borderRadius:1, border:'1px solid var(--line-soft)' }}>
              <Typography sx={{ fontSize:9, fontWeight:700, color:'var(--text-4)',
                textTransform:'uppercase', letterSpacing:1, mb:0.5 }}>Field stats</Typography>
              {[
                ['|B|_max',  `${data.stats.B_mag_max.toFixed(3)} T`],
                ['|B|_mean', `${data.stats.B_mag_mean.toFixed(3)} T`],
                ['J_z_max',  `${(data.stats.J_z_max/1e6).toFixed(2)} MA/m²`],
                ['A_z range',`${data.stats.A_z_min.toExponential(2)} … ${data.stats.A_z_max.toExponential(2)}`],
                ['Grid',     `${data.grid_size}×${data.grid_size}`],
              ].map(([k,v])=>(
                <Box key={k as string} sx={{ display:'flex', justifyContent:'space-between', py:0.15 }}>
                  <Typography sx={{ fontSize:9, color:'var(--text-4)' }}>{k}</Typography>
                  <Typography sx={{ fontSize:9, fontWeight:600, color:'var(--text-2)' }}>{v}</Typography>
                </Box>
              ))}
            </Box>
          )}

          {/* Note */}
          <Box sx={{ p:1, bgcolor:'var(--panel-2)', borderRadius:1, border:'1px solid var(--line-soft)' }}>
            <Typography sx={{ fontSize:8, color:'var(--line)', lineHeight:1.4 }}>
              {data?.note ?? ''}
              <br/><br/>
              PINN solves the exact nonlinear PDE with μ(B), giving correct B in steel regions.
            </Typography>
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default MotorField2D;
