import { readFileSync, readdirSync } from 'node:fs';
import { dirname, extname, join, posix, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

function tokens(source) {
  const out = []; let i = 0;
  while (i < source.length) {
    const c = source[i], n = source[i + 1];
    if (/\s/.test(c)) { i++; continue; }
    if (c === '/' && n === '/') { i += 2; while (i < source.length && source[i] !== '\n') i++; continue; }
    if (c === '/' && n === '*') { i += 2; while (i < source.length && !(source[i] === '*' && source[i + 1] === '/')) i++; i += 2; continue; }
    if (c === '"' || c === "'" || c === '`') {
      const quote = c; i++; let value = '';
      while (i < source.length && source[i] !== quote) {
        if (source[i] === '\\' && i + 1 < source.length) { value += source[i + 1]; i += 2; }
        else { value += source[i++]; }
      }
      i++; out.push({ type:'string', value, quote }); continue;
    }
    if (/[A-Za-z_$]/.test(c)) { let j=i+1; while (j<source.length && /[\w$]/.test(source[j])) j++; out.push({type:'id',value:source.slice(i,j)}); i=j; continue; }
    out.push({type:'punct',value:c}); i++;
  }
  return out;
}
export function importsIn(source) {
  const ts = tokens(source), found = [];
  for (let i=0;i<ts.length;i++) {
    const t=ts[i]; if (t.type!=='id') continue;
    if (t.value==='require' && ts[i+1]?.value==='(' && ts[i+2]?.type==='string') found.push(ts[i+2].value);
    if (t.value==='import') {
      if (ts[i+1]?.value==='(' && ts[i+2]?.type==='string') found.push(ts[i+2].value);
      else if (ts[i+1]?.type==='string') found.push(ts[i+1].value);
      else {
        for (let j=i+1;j<ts.length && ts[j].value!==';';j++) {
          if (ts[j].type==='id' && ts[j].value==='from' && ts[j+1]?.type==='string') { found.push(ts[j+1].value); break; }
        }
      }
    }
    if (t.value==='export') {
      for (let j=i+1;j<ts.length && ts[j].value!==';';j++) {
        if (ts[j].type==='id' && ts[j].value==='from' && ts[j+1]?.type==='string') { found.push(ts[j+1].value); break; }
      }
    }
  }
  return found;
}
function resolveSource(importer, spec) {
  const normalize = (p) => posix.normalize(p.replaceAll('\\','/')).replace(/^\.\//,'');
  if (spec.startsWith('@/')) return normalize(`src/${spec.slice(2)}`);
  if (spec.startsWith('src/')) return normalize(spec);
  if (spec.startsWith('modules/') || spec.startsWith('components/') || spec.startsWith('stores/') || spec.startsWith('lib/') || spec.startsWith('i18n/')) return normalize(`src/${spec}`);
  if (spec.startsWith('.')) return normalize(posix.join(posix.dirname(importer), spec));
  return null;
}
const stripExtension = (p) => p.replace(/\.(?:tsx?|jsx?|mjs|cjs)$/, '').replace(/\/index$/, '');
export function inspectBoundary(importer, source) {
  const inside = importer.replaceAll('\\','/').includes('src/modules/viewer3d/');
  const violations = [];
  for (const spec of importsIn(source)) {
    const resolved = resolveSource(importer, spec);
    if (!resolved) continue;
    const target = stripExtension(resolved);
    if (inside && /(^|\/)(components|stores)(\/|$)/.test(target)) violations.push({ importer, spec, rule:'module-no-components-or-stores' });
    const moduleTarget = target === 'src/modules/viewer3d' || target === 'src/modules/viewer3d/index';
    const privateTarget = target === 'src/modules/viewer3d/internal' || target.startsWith('src/modules/viewer3d/internal/') || (target.startsWith('src/modules/viewer3d/') && !moduleTarget);
    if (!inside && privateTarget) violations.push({ importer, spec, rule:'outside-imports-public-index-only' });
  }
  return violations;
}
function files(dir) { return readdirSync(dir,{withFileTypes:true}).flatMap((e)=>e.isDirectory()?files(join(dir,e.name)):[join(dir,e.name)]).filter((p)=>/\.[cm]?[jt]sx?$/.test(extname(p))); }
export function scanTree(srcRoot) {
  const root=resolve(srcRoot), violations=[];
  for (const file of files(root)) {
    const rel=relative(root,file).replaceAll('\\','/');
    violations.push(...inspectBoundary(`src/${rel}`,readFileSync(file,'utf8')));
  }
  return violations;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const src = resolve(process.argv[2] ?? join(dirname(fileURLToPath(import.meta.url)),'../src'));
  const violations=scanTree(src);
  if (violations.length) { process.stderr.write(`${JSON.stringify(violations,null,2)}\n`); process.exitCode=1; }
  else process.stdout.write('Viewer module boundaries: clean\n');
}
