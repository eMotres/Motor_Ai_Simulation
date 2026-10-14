/**
 * The approved AeroStator logo (owner decision 2026-10-07): a six-lobed navy stator
 * outline, twelve red winding strokes and ten alternating red / blue magnets.  The
 * artwork is the raster master, only resized and made transparent outside the
 * outline (public/brand/, built by scripts/make_brand_assets.py).  Colours are never
 * altered; in dark mode a very thin light edge keeps the navy outline readable.
 */
import React from 'react';
import { Box } from '@mui/material';

const SRC = {
  '512': '/brand/aerostator-logo-512.png',
  '192': '/brand/aerostator-logo-192.png',
  '64': '/brand/aerostator-logo-64.png',
};

const BrandLogo: React.FC<{ size?: number }> = ({ size = 44 }) => {
  // The nearest asset that is at least 2x the CSS size (sharp on retina screens).
  const src = size * 2 <= 64 ? SRC['64'] : size * 2 <= 192 ? SRC['192'] : SRC['512'];
  return (
    <Box component="img" src={src} width={size} height={size} alt="" aria-hidden="true"
      draggable={false}
      sx={(theme) => ({
        flexShrink: 0, display: 'block', width: size, height: size,
        filter: theme.palette.mode === 'dark'
          ? 'drop-shadow(0 0 0.5px rgba(255,255,255,0.9)) drop-shadow(0 0 1px rgba(255,255,255,0.45))'
          : 'none',
      })} />
  );
};

export default BrandLogo;
