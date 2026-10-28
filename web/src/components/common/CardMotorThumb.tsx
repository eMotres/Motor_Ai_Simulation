/** The small motor picture in the bottom-right corner of an overview tile: the catalog's MotorThumbnail, name as alt text and tooltip. */
import React, { useEffect, useState } from 'react';
import { Box, Tooltip } from '@mui/material';
import MotorThumbnail from '../catalog/MotorThumbnail';
import { fetchFamilyTree } from '../../lib/familyTree';
import { indexDies, type CardMotor, type DieIndex } from '../../lib/cardMotor';

/** The dies of the catalog, read once through the shared (memoised) tree request while `enabled`; null until it answers or when it cannot be read. */
export function useDieIndex(enabled: boolean): DieIndex | null {
  const [index, setIndex] = useState<DieIndex | null>(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    fetchFamilyTree().then((t) => { if (alive) setIndex(indexDies(t.dies)); }).catch(() => { /* signed out or offline - no pictures */ });
    return () => { alive = false; };
  }, [enabled]);
  return index;
}

export const CARD_THUMB_SIZE = 64;

/** Positioned inside a tile that has `position: relative`; it floats, so it never changes the tile's text layout. */
const CardMotorThumb: React.FC<{ motor: CardMotor }> = ({ motor }) => (
  <Tooltip title={motor.label}>
    <Box sx={{ position: 'absolute', right: 10, bottom: 8, lineHeight: 0, pointerEvents: 'auto' }}>
      <MotorThumbnail slots={motor.slots} poles={motor.poles} size={CARD_THUMB_SIZE} thumbSvg={motor.thumbSvg} alt={motor.label} lazy />
    </Box>
  </Tooltip>
);

export default CardMotorThumb;
