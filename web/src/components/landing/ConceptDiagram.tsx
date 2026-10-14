/**
 * The concept diagram: the AeroStator Core machine in the middle (the real
 * flux-density picture from `public/landing/em-field.png`), the five steps from
 * idea to product around it, and the open ecosystem as the outer ring.
 *
 * One inline SVG that scales with its container.  Colours come from the MUI
 * theme, so both themes are right by construction.  The geometry is computed in
 * `conceptLayout.ts` (pure, tested).  Below ~720 px the labels would be too
 * small, so the caller hides this and shows the step cards instead.
 */
import React from 'react';
import { useTheme } from '@mui/material';
import { useTranslation } from 'react-i18next';
import {
  CORE_R, END, NODE, RING, TAG, VIEW, endBoxes, ringTags, stepArrows, stepBoxes,
  type StepId, type TagId,
} from './conceptLayout';

const EM_PIC = { src: '/landing/em-field.png', w: 884, h: 766, mx: 390, my: 383, mr: 375 };

const ConceptDiagram: React.FC = () => {
  const { t } = useTranslation('landing');
  const theme = useTheme();
  const c = {
    paper: theme.palette.background.paper,
    ink: theme.palette.text.primary,
    soft: theme.palette.text.secondary,
    line: theme.palette.divider,
    brand: theme.palette.primary.main,
    open: theme.palette.secondary.main,
    openInk: theme.palette.mode === 'dark' ? theme.palette.secondary.light : theme.palette.secondary.dark,
  };
  const steps = stepBoxes();
  const ends = endBoxes();
  const tags = ringTags();
  const scale = CORE_R / EM_PIC.mr;
  const font = '"Inter", "Roboto", "Helvetica", "Arial", sans-serif';

  return (
    <svg viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} role="img" aria-label={t('concept.diagramAlt')}
      style={{ width: '100%', height: 'auto', display: 'block', fontFamily: font }}>
      <defs>
        <clipPath id="core-clip"><circle cx={VIEW.cx} cy={VIEW.cy} r={CORE_R - 3} /></clipPath>
        <marker id="arrow-brand" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7"
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
        stroke={c.brand} strokeWidth={3} fill="none" markerEnd="url(#arrow-brand)" />
      <path d={`M${steps[4].x + 30} ${steps[4].y - 2} V${ends.product.y + END.h + 2}`}
        stroke={c.brand} strokeWidth={3} fill="none" markerEnd="url(#arrow-brand)" />

      {/* centre: the machine */}
      <circle cx={VIEW.cx} cy={VIEW.cy} r={CORE_R + 10} fill={c.paper} stroke={c.brand}
        strokeWidth={3} />
      <circle cx={VIEW.cx} cy={VIEW.cy} r={CORE_R - 3} fill="#ffffff" />
      <image href={EM_PIC.src} clipPath="url(#core-clip)"
        x={VIEW.cx - EM_PIC.mx * scale} y={VIEW.cy - EM_PIC.my * scale}
        width={EM_PIC.w * scale} height={EM_PIC.h * scale} aria-hidden="true" />
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
  );
};

export default ConceptDiagram;
