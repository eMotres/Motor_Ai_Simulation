// Source text of the specification view for the source-level tests: DevelopmentPage keeps the data logic, the wizard
// components (components/specWizard/*.tsx) draw it.  The spec renderers were moved verbatim, so the assertions that pinned
// them in DevelopmentPage.tsx now read the concatenation.
import { readFileSync, readdirSync } from 'node:fs';

const dir = new URL('../components/specWizard/', import.meta.url);
export const wizardFiles = readdirSync(dir).filter((f) => f.endsWith('.tsx')).sort();
export const readWizard = (name) => readFileSync(new URL(name, dir), 'utf8');
export const pageOnly = () => readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');
/** DevelopmentPage.tsx + every wizard .tsx (the whole specification view). */
export const specViewSource = () => [pageOnly(), ...wizardFiles.map(readWizard)].join('\n');
/** The field renderers (moved verbatim from DevelopmentPage). */
export const rendererSource = () => readWizard('useSpecFields.tsx');
