/**
 * Build-time entry: renders the landing's concept diagram to static markup.
 *
 * `scripts/prerender-landing.mjs` bundles this file with esbuild (CommonJS, packages
 * external) and calls `renderDiagram()` once per locale and theme.  It renders the SAME
 * component the browser renders (`ConceptDiagramView`, the same `conceptLayout.ts`
 * geometry, the same colours from `buildAppTheme`), so the pre-rendered picture cannot
 * drift from the live one.  Only the words come from the locale file.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ConceptDiagramView, diagramColors } from '../src/components/landing/ConceptDiagram';
import { buildAppTheme } from '../src/theme';

function lookup(tree: unknown, dotted: string): string {
  const v = dotted.split('.').reduce<unknown>(
    (o, k) => (o == null ? o : (o as Record<string, unknown>)[k]), tree);
  if (typeof v !== 'string') throw new Error(`landing.json: missing string at "${dotted}"`);
  return v;
}

export function renderDiagram(landing: unknown, mode: 'light' | 'dark', idSuffix = ''): string {
  const colors = diagramColors(buildAppTheme(mode));
  return renderToStaticMarkup(
    <ConceptDiagramView t={(k) => lookup(landing, k)} colors={colors} idSuffix={idSuffix} />,
  );
}
