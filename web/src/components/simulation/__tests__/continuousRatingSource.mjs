// Execute the production functions, including their TypeScript syntax.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
const require = createRequire(import.meta.url);
const ts = require('typescript');
const text = readFileSync(new URL('../coupledApi.ts', import.meta.url), 'utf8');
const source = ts.createSourceFile('coupledApi.ts', text, ts.ScriptTarget.Latest, true);
const names = new Set(['continuousRatingLine', 'continuousRatingTip', 's1ResultsAtLine']);
const declarations = source.statements.filter(node => ts.isFunctionDeclaration(node)
  && names.has(node.name?.text));
if (declarations.length !== names.size) throw new Error('Missing production S1 function');
const js = ts.transpileModule(declarations.map(node => node.getText(source)).join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exports = {};
vm.runInNewContext(js, { exports });
export const { continuousRatingLine, continuousRatingTip, s1ResultsAtLine } = exports;
