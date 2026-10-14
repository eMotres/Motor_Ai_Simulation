/**
 * Geometry of the concept diagram on the landing page (pure, no React).
 *
 * The diagram is one inline SVG.  Inner pentagon = what the visitor does (five
 * steps), centre = the AeroStator Core machine, outer ring = what is open to
 * everyone.  The pentagon is open on the left: "Idea" enters at Design and
 * "Product" leaves Production, so the slogan reads straight off the picture.
 *
 * Everything is computed here so a node test can prove that no two labelled
 * boxes overlap and that all of them sit inside the viewBox.
 */

export const VIEW = { w: 1200, h: 760, cx: 600, cy: 380 } as const;
export const NODE = { w: 244, h: 74 } as const;
export const END = { w: 88, h: 34 } as const;
export const TAG = { w: 196, h: 56 } as const;
export const RING = { rx: 490, ry: 345 } as const;
/** Radii of the ellipse the five steps sit on. */
export const STEP_RX = 250;
export const STEP_RY = 215;
/** Radius of the picture of the machine in the middle. */
export const CORE_R = 76;

export const STEP_IDS = ['design', 'simulation', 'configure', 'model3d', 'production'] as const;
export type StepId = (typeof STEP_IDS)[number];
export const TAG_IDS = ['open', 'agents', 'modules', 'compute'] as const;
export type TagId = (typeof TAG_IDS)[number];

/** Angle (deg, clockwise from +x, screen coordinates) of each step. */
const STEP_ANGLE: Record<StepId, number> = {
  design: 216, simulation: 288, configure: 360, model3d: 432, production: 504,
};

export interface Box { id: string; x: number; y: number; w: number; h: number }
export interface Arrow { id: string; d: string; chevron: string }

const rad = (deg: number) => (deg * Math.PI) / 180;
const r1 = (v: number) => Math.round(v * 10) / 10;

function onCircle(angle: number): { x: number; y: number } {
  return {
    x: r1(VIEW.cx + STEP_RX * Math.cos(rad(angle))),
    y: r1(VIEW.cy + STEP_RY * Math.sin(rad(angle))),
  };
}

/** Top-left anchored box centred on (x, y). */
function box(id: string, x: number, y: number, w: number, h: number): Box {
  return { id, x: r1(x - w / 2), y: r1(y - h / 2), w, h };
}

export function stepBoxes(): Box[] {
  return STEP_IDS.map((id) => {
    const p = onCircle(STEP_ANGLE[id]);
    return box(id, p.x, p.y, NODE.w, NODE.h);
  });
}

function tangentDeg(angle: number): number {
  return (Math.atan2(STEP_RY * Math.cos(rad(angle)), -STEP_RX * Math.sin(rad(angle))) * 180) / Math.PI;
}

/** Arcs between consecutive steps, with a chevron at the middle of each. */
export function stepArrows(): Arrow[] {
  const out: Arrow[] = [];
  for (let i = 0; i < STEP_IDS.length - 1; i++) {
    const a = STEP_ANGLE[STEP_IDS[i]];
    const b = STEP_ANGLE[STEP_IDS[i + 1]];
    const p1 = onCircle(a);
    const p2 = onCircle(b);
    const mid = onCircle((a + b) / 2);
    out.push({
      id: `${STEP_IDS[i]}-${STEP_IDS[i + 1]}`,
      d: `M ${p1.x} ${p1.y} A ${STEP_RX} ${STEP_RY} 0 0 1 ${p2.x} ${p2.y}`,
      // Tangent of the clockwise ellipse at parameter t: (-RX sin t, RY cos t).
      chevron: `translate(${mid.x} ${mid.y}) rotate(${r1(tangentDeg((a + b) / 2))})`,
    });
  }
  return out;
}

/** "Idea" (enters Design) and "Product" (leaves Production), in the gap. */
export function endBoxes(): { idea: Box; product: Box } {
  return {
    idea: box('idea', VIEW.cx - 290, VIEW.cy - 40, END.w, END.h),
    product: box('product', VIEW.cx - 290, VIEW.cy + 40, END.w, END.h),
  };
}

/** The four open-ecosystem tags, centred on the ring at N, E, S, W. */
export function ringTags(): Box[] {
  const pts: Record<TagId, { x: number; y: number }> = {
    open: { x: VIEW.cx, y: VIEW.cy - RING.ry },
    agents: { x: VIEW.cx + RING.rx, y: VIEW.cy },
    modules: { x: VIEW.cx, y: VIEW.cy + RING.ry },
    compute: { x: VIEW.cx - RING.rx, y: VIEW.cy },
  };
  return TAG_IDS.map((id) => box(id, pts[id].x, pts[id].y, TAG.w, TAG.h));
}

/** Every labelled box in the diagram (for the overlap / bounds test). */
export function allBoxes(): Box[] {
  const e = endBoxes();
  return [...stepBoxes(), e.idea, e.product, ...ringTags(),
    box('core', VIEW.cx, VIEW.cy, CORE_R * 2, CORE_R * 2)];
}
