/**
 * MotorThumbnail — the motor's REAL 2-D cross-section.
 *
 * Loads the snapshot generated from the actual CadQuery geometry
 * (public/motor-thumbs/<motorId>.svg — stator steel + slots, rotor, coils,
 * magnets by polarity, shaft). If that asset is missing it falls back to a
 * schematic drawn from the slot/pole counts so the card never breaks.
 */
import React, { useState } from 'react';

interface Props { slots: number; poles: number; size?: number; motorId?: string; thumbSvg?: string; }

const C = 60;            // centre
const R_STATOR = 57;     // stator outer
const R_BORE = 33;       // stator bore (tooth tips) / air-gap outer
const R_ROTOR = 30;      // rotor outer
const R_SHAFT = 10;      // shaft

// a radial bar (rect pointing "up" from the centre), rotated to `deg`
const bar = (deg: number, rIn: number, rOut: number, w: number, key: string, fill: string, extra: object = {}) => (
  <rect key={key} x={C - w / 2} y={C - rOut} width={w} height={rOut - rIn} rx={Math.min(w / 2, 1.6)}
    transform={`rotate(${deg} ${C} ${C})`} fill={fill} {...extra} />
);

const MotorThumbnail: React.FC<Props> = ({ slots, poles, size = 92, motorId, thumbSvg }) => {
  const [failed, setFailed] = useState(false);

  // Inline real-geometry SVG (stored on the card at save time) — the true
  // cross-section without needing a hosted asset, so user-saved motors render
  // their actual geometry everywhere (local + prod).
  if (thumbSvg) {
    return (
      <img src={`data:image/svg+xml;utf8,${encodeURIComponent(thumbSvg)}`}
        width={size} height={size} alt={`${slots}-slot / ${poles}-pole cross-section`}
        style={{ display: 'block' }} />
    );
  }

  // Real cross-section snapshot (generated from the actual geometry, hosted asset).
  if (motorId && !failed) {
    return (
      <img src={`${import.meta.env.BASE_URL}motor-thumbs/${motorId}.svg`}
        width={size} height={size} alt={`${slots}-slot / ${poles}-pole cross-section`}
        onError={() => setFailed(true)} style={{ display: 'block' }} />
    );
  }

  // Fallback: schematic from slot/pole counts.
  const s = Math.max(1, Math.round(slots));
  const p = Math.max(1, Math.round(poles));

  // stator slot openings (copper) — one per slot, sitting in the back-iron ring
  const slotW = Math.min(7, (2 * Math.PI * ((R_STATOR + R_BORE) / 2) / s) * 0.5);
  const slotEls = Array.from({ length: s }, (_, i) =>
    bar((360 / s) * i, R_BORE + 1.5, R_STATOR - 4, slotW, `s${i}`, 'url(#tn-copper)'));

  // rotor spoke magnets — alternating N (red) / S (blue)
  const magW = Math.min(6, (2 * Math.PI * (R_ROTOR / 2) / p) * 0.62);
  const magEls = Array.from({ length: p }, (_, i) =>
    bar((360 / p) * i, R_SHAFT + 1, R_ROTOR - 1.5, magW, `m${i}`,
      i % 2 === 0 ? '#e0556a' : '#5b8def'));

  return (
    <svg width={size} height={size} viewBox="0 0 120 120" role="img"
      aria-label={`${s}-slot / ${p}-pole cross-section`}>
      <defs>
        <radialGradient id="tn-copper" cx="50%" cy="35%" r="70%">
          <stop offset="0%" stopColor="#f0b259" /><stop offset="100%" stopColor="#a8631d" />
        </radialGradient>
        <radialGradient id="tn-steel" cx="50%" cy="35%" r="75%">
          <stop offset="0%" stopColor="#2b3a52" /><stop offset="100%" stopColor="#16202f" />
        </radialGradient>
        <radialGradient id="tn-rotor" cx="50%" cy="35%" r="75%">
          <stop offset="0%" stopColor="#33415a" /><stop offset="100%" stopColor="#1c2636" />
        </radialGradient>
      </defs>

      {/* stator iron */}
      <circle cx={C} cy={C} r={R_STATOR} fill="url(#tn-steel)" stroke="#3b4d68" strokeWidth={1.2} />
      {slotEls}
      {/* air gap */}
      <circle cx={C} cy={C} r={R_BORE} fill="var(--panel-2)" />
      {/* rotor iron */}
      <circle cx={C} cy={C} r={R_ROTOR} fill="url(#tn-rotor)" stroke="#3b4d68" strokeWidth={0.8} />
      {magEls}
      {/* shaft */}
      <circle cx={C} cy={C} r={R_SHAFT} fill="var(--text-4)" stroke="var(--text-3)" strokeWidth={0.8} />
      <circle cx={C} cy={C} r={R_SHAFT * 0.45} fill="var(--panel)" />
    </svg>
  );
};

export default MotorThumbnail;
