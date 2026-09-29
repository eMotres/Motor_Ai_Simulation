/**
 * One `onCreated` for every <Canvas>: put WebGL context loss on the record and
 * redraw when the browser hands the context back.
 *
 * three.js already `preventDefault()`s `webglcontextlost` (so the browser may
 * restore the context) and rebuilds its GL state on `webglcontextrestored`;
 * what it does NOT do is tell anyone but the console, and in demand-mode
 * rendering nothing asks for a frame after the restore — the canvas would
 * stay blank until the next mouse move.  Two "Context Lost" lines were the
 * only trace the 2026-09-13 freeze left; now they are in `diag.log` with the
 * viewer's name and the time.
 */
import type { RootState } from '@react-three/fiber';
import { diagNote } from '../../lib/diag';

export function guardCanvas(label: string) {
  return (state: RootState) => {
    const el = state.gl.domElement;
    el.addEventListener('webglcontextlost', () => {
      // r3f ends every <Canvas> with `forceContextLoss()` on unmount, so a
      // tab switch away from Geometry fires this for the viewer AND the
      // viewcube — the exact "two Context Lost lines" that were read as a
      // GPU failure on 2026-09-13.  By then React has already detached the
      // element: only a loss on a LIVE canvas is worth a line.
      if (!el.isConnected) return;
      diagNote(`webgl context lost: ${label}`);
    }, false);
    el.addEventListener('webglcontextrestored', () => {
      diagNote(`webgl context restored: ${label}`);
      state.invalidate();
    }, false);
  };
}
