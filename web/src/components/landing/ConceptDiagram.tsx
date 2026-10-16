/**
 * The concept diagram: the AeroStator Core machine in the middle (a static,
 * pre-rendered isometric image of the CIANO14 40 L12 model,
 * `public/landing/core-l12.webp`; never the 3D file itself), the five steps from
 * idea to product around it, and the open ecosystem as the outer ring.
 *
 * One inline SVG that scales with its container.  Colours come from the MUI
 * theme, so both themes are right by construction.  The geometry is computed in
 * `conceptLayout.ts` (pure, tested).  Below ~720 px the labels would be too
 * small, so the caller hides this and shows the step cards instead.
 *
 * ONE SOURCE FOR THE PAGE AND FOR THE PRE-RENDER.  `ConceptDiagramView` is a pure
 * function of (translate, colours): `ConceptDiagram` feeds it from the MUI theme and
 * i18next at runtime, and `scripts/prerender-landing.mjs` renders the very same view
 * with `renderToStaticMarkup` into the static HTML, so a visitor whose JavaScript has
 * not arrived (or never does, as on a slow cross-border link) still sees the diagram.
 * The machine picture is a plain HTML `<img>` laid over the SVG (an HTML element is
 * found by the browser's preload scanner and needs no script), placed with percentages
 * of the viewBox so it scales with the SVG.
 */
import React from 'react';
import { useTheme, type Theme } from '@mui/material';
import { useTranslation } from 'react-i18next';
import {
  CORE_R, END, NODE, RING, TAG, VIEW, endBoxes, ringTags, stepArrows, stepBoxes,
  type StepId, type TagId,
} from './conceptLayout';

// 512 px square, transparent, the motor centred and spanning ~93 % of the width.
export const CORE_PIC = { src: '/landing/core-l12.webp', size: 2 * (CORE_R - 1) };

export interface DiagramColors {
  paper: string; ink: string; soft: string; line: string; brand: string; open: string;
  openInk: string; coreFill: string;
}

/** The diagram's colours, from a MUI theme (runtime and pre-render use the same function). */
export function diagramColors(theme: Theme): DiagramColors {
  const dark = theme.palette.mode === 'dark';
  return {
    paper: theme.palette.background.paper,
    ink: theme.palette.text.primary,
    soft: theme.palette.text.secondary,
    line: theme.palette.divider,
    brand: theme.palette.primary.main,
    open: theme.palette.secondary.main,
    openInk: dark ? theme.palette.secondary.light : theme.palette.secondary.dark,
    coreFill: dark ? '#1c2433' : '#f3f6fb',
  };
}

const FONT = '"Inter", "Roboto", "Helvetica", "Arial", "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif';

const pct = (v: number, of: number) => `${Math.round((v / of) * 1e4) / 100}%`;

export interface DiagramViewProps {
  /** Translate a key of the `landing` namespace. */
  t: (key: string) => string;
  colors: DiagramColors;
  /** Suffix for the SVG ids, so two renderings on one page (light + dark) do not collide. */
  idSuffix?: string;
  className?: string;
}

/** Pure view: the same markup in the browser and in the build-time pre-render. */
export const ConceptDiagramView: React.FC<DiagramViewProps> = ({ t, colors: c, idSuffix = '', className }) => {
  const steps = stepBoxes();
  const ends = endBoxes();
  const tags = ringTags();
  const arrowId = `arrow-brand${idSuffix}`;
  // The picture is clipped to a circle of radius CORE_R - 3; the image itself is
  // 2 * (CORE_R - 1) wide, so it is inset by 2 viewBox units inside that circle.
  const clipR = CORE_R - 3;
  const inset = (CORE_PIC.size - 2 * clipR) / 2;

  return (
    <div className={className} data-concept-diagram
      style={{ position: 'relative', width: '100%', lineHeight: 0 }}>
      <svg viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} role="img" aria-label={t('concept.diagramAlt')}
        style={{ width: '100%', height: 'auto', display: 'block', fontFamily: FONT }}>
        <defs>
          <marker id={arrowId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7"
            markerHeight="7" orient="auto-start-reverse">
            <path d="M1 1 9 5 1 9z" fill={c.brand} />
          </marker>
        </defs>

        {/* outer ring: the open ecosystem */}
        <ellipse cx={VIEW.cx} cy={VIEW.cy} rx={RING.rx} ry={RING.ry} fill="none"
          stroke={c.open} strokeWidth={3} strokeDasharray="2 9" strokeLinecap="round" />

        {/* arcs between the steps, chevrons on top of them */}
        {stepArrows().map((a) => (
          <g key={a.id}>
            <path d={a.d} fill="none" stroke={c.brand} strokeWidth={3} opacity={0.55} />
            <path d="M-7 -9 7 0 -7 9z" fill={c.brand} transform={a.chevron} />
          </g>
        ))}

        {/* Idea -> Design and Production -> Product, in the open side of the pentagon */}
        <path d={`M${steps[0].x + 30} ${ends.idea.y - 2} V${steps[0].y + NODE.h + 2}`}
          stroke={c.brand} strokeWidth={3} fill="none" markerEnd={`url(#${arrowId})`} />
        <path d={`M${steps[4].x + 30} ${steps[4].y - 2} V${ends.product.y + END.h + 2}`}
          stroke={c.brand} strokeWidth={3} fill="none" markerEnd={`url(#${arrowId})`} />

        {/* centre: the machine (the picture itself is the <img> below the SVG) */}
        <circle cx={VIEW.cx} cy={VIEW.cy} r={CORE_R + 10} fill={c.paper} stroke={c.brand}
          strokeWidth={3} />
        <circle cx={VIEW.cx} cy={VIEW.cy} r={clipR} fill={c.coreFill} />
        <text x={VIEW.cx} y={VIEW.cy + CORE_R + 30} textAnchor="middle" fontSize={19}
          fontWeight={700} fill={c.ink}>{t('concept.core')}</text>

        {/* the five steps */}
        {steps.map((b, i) => {
          const id = b.id as StepId;
          return (
            <g key={id}>
              <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={14} fill={c.paper}
                stroke={c.brand} strokeWidth={2.5} />
              <circle cx={b.x + 26} cy={b.y + b.h / 2} r={15} fill={c.brand} />
              <text x={b.x + 26} y={b.y + b.h / 2 + 6} textAnchor="middle" fontSize={17}
                fontWeight={700} fill="#ffffff">{i + 1}</text>
              <text x={b.x + 54} y={b.y + 33} fontSize={25} fontWeight={700} fill={c.ink}>
                {t(`chain.${id}`)}
              </text>
              <text x={b.x + 54} y={b.y + 57} fontSize={17} fill={c.soft}>
                {t(`concept.${id}.sub`)}
              </text>
            </g>
          );
        })}

        {/* idea / product */}
        {[ends.idea, ends.product].map((b) => (
          <g key={b.id}>
            <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={b.h / 2} fill={c.brand} />
            <text x={b.x + b.w / 2} y={b.y + b.h / 2 + 6.5} textAnchor="middle" fontSize={19}
              fontWeight={700} fill="#ffffff">{t(`chain.${b.id}`)}</text>
          </g>
        ))}

        {/* open ecosystem tags on the ring */}
        {tags.map((b) => {
          const id = b.id as TagId;
          return (
            <g key={id}>
              <rect x={b.x} y={b.y} width={TAG.w} height={TAG.h} rx={TAG.h / 2} fill={c.paper}
                stroke={c.open} strokeWidth={2.5} />
              <text x={b.x + TAG.w / 2} y={b.y + 26} textAnchor="middle" fontSize={22}
                fontWeight={700} fill={c.ink}>{t(`concept.${id}.title`)}</text>
              <text x={b.x + TAG.w / 2} y={b.y + 47} textAnchor="middle" fontSize={16.5}
                fill={c.openInk}>{t(`concept.${id}.sub`)}</text>
            </g>
          );
        })}
      </svg>
      {/* The machine: a plain same-origin <img> over the centre circle, sized and placed in
          percentages of the viewBox (the SVG fixes the box's height), clipped to the circle. */}
      <span aria-hidden="true" style={{
        position: 'absolute', display: 'block', overflow: 'hidden', borderRadius: '50%',
        left: pct(VIEW.cx - clipR, VIEW.w), top: pct(VIEW.cy - clipR, VIEW.h),
        width: pct(2 * clipR, VIEW.w), height: pct(2 * clipR, VIEW.h),
      }}>
        <img src={CORE_PIC.src} alt="" width={512} height={512} decoding="async"
          style={{
            position: 'absolute', display: 'block', maxWidth: 'none',
            left: pct(-inset, 2 * clipR), top: pct(-inset, 2 * clipR),
            width: pct(CORE_PIC.size, 2 * clipR), height: pct(CORE_PIC.size, 2 * clipR),
          }} />
      </span>
    </div>
  );
};

const ConceptDiagram: React.FC = () => {
  const { t } = useTranslation('landing');
  const theme = useTheme();
  return <ConceptDiagramView t={(k) => t(k)} colors={diagramColors(theme)} />;
};

export default ConceptDiagram;
