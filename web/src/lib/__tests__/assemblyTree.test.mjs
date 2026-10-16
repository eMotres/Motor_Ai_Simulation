import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const A = await import('../assemblyTree.ts');

const part = (id, group, name, glb) => ({ id, group, name, names:{en:name,zh:`${name}(zh)`}, em:group==='winding', glb, order:0 });
const tree = {
  groups:[
    {id:'stator',name:{en:'Stator','zh-CN':'定子'},parts:['stator_core']},
    {id:'winding',name:{en:'Winding','zh-CN':'绕组'},parts:['slot']},
    {id:'magnets',name:{en:'Magnets','zh-CN':'磁钢'},parts:['magnet']},
    {id:'housing',name:{en:'Housing & holders','zh-CN':'外壳与支架'},parts:['holder']},
    {id:'bearings',name:{en:'Bearings','zh-CN':'轴承'},parts:['bearings']},
    {id:'fasteners',name:{en:'Fasteners','zh-CN':'紧固件'},parts:['screws']},
  ],
  parts:[
    part('stator_core','stator','Stator core',['Core']), part('slot','winding','Winding copper',['Coils']),
    part('magnet','magnets','Magnets',['Magnets','Magnets_S']), part('holder','housing','Stator holder',['Holder_front','Holder_back']),
    part('bearings','bearings','Bearings',['Bearings_front','Bearings_front_balls','Bearings_back']),
    part('screws','fasteners','Screws',['Screws']),
  ],
};
const numbered=(prefix,n)=>Array.from({length:n},(_,i)=>`${prefix}_${String(i+1).padStart(2,'0')}`);
const nodes=[
  {name:'Core',family:'Core',explode:0,children:['Core'],colour:'#999'},
  {name:'Coils',family:'Coils',explode:0,children:numbered('Coil',6),colour:'#e0821a'},
  {name:'Magnets',family:'Magnets',explode:0,children:numbered('Magnet',14).filter((_,i)=>i%2===0),colour:'#e02718'},
  {name:'Magnets_S',family:'Magnets',explode:0,children:numbered('Magnet',14).filter((_,i)=>i%2===1),colour:'#2e86ff'},
  ...['Holder_front','Holder_back','Bearings_front','Bearings_front_balls','Bearings_back'].map(name=>({name,family:name,explode:0,children:[name],colour:'#abc'})),
  {name:'Screws',family:'Screws',explode:0,children:[1,2,3,4,5,6].map(i=>`Screw_${i}`),colour:'#ccc'},
];
const fmt=(_key,_vars,fallback)=>fallback;

test('host adapter builds neutral group, part and instance names with scene mappings', () => {
  const rows=A.buildAssemblyRows(tree,nodes,{lang:'en',fmt});
  assert.deepEqual(rows.map(r=>r.key),['g:stator','g:winding','g:magnets','g:housing','g:bearings','g:fasteners']);
  assert.deepEqual(rows.map(r=>r.label),['Stator','Winding','Magnets','Housing & holders','Bearings','Fasteners']);
  const zh=A.buildAssemblyRows(tree,nodes,{lang:'zh-CN',fmt});
  assert.equal(zh[0].children[0].label,'Stator core(zh)');
  assert.deepEqual(rows[1].children[0].children.map(r=>r.label),['Coil 1','Coil 2','Coil 3','Coil 4','Coil 5','Coil 6']);
  const magnetRows=rows[2].children[0].children;
  assert.equal(magnetRows.length,14);
  assert.deepEqual(magnetRows.slice(0,4).map(r=>r.label),['Magnet 1 N','Magnet 2 S','Magnet 3 N','Magnet 4 S']);
  assert.equal(magnetRows[0].colour,'#e02718'); assert.equal(magnetRows[1].colour,'#2e86ff');
  assert.deepEqual(rows[5].children[0].children.map(r=>r.label),['Screw 1','Screw 2','Screw 3','Screw 4','Screw 5','Screw 6']);
  assert.deepEqual(rows[4].children[0].children.map(r=>r.label),['Bearing, front','Balls, front','Bearing, rear']);
  assert.deepEqual(rows[3].children[0].children.map(r=>r.label),['Holder, front','Holder, rear']);
  assert.equal(rows[0].children[0].children,undefined);
  assert.equal(rows[0].children[0].colour,'#999');
  for (const row of [...rows,...rows.flatMap(r=>r.children)]) {
    assert.deepEqual(Object.keys(row).sort(),Object.keys(row).filter(k=>['key','label','colour','sceneNames','defaultExpanded','children','present'].includes(k)).sort());
    assert.ok(!('mass' in row) && !('tag' in row) && !('visible' in row));
  }
});

test('no loaded nodes keeps server rows but marks leaves absent; kit filtering invents no rows',()=>{
  const absent=A.buildAssemblyRows(tree,[],{lang:'en',fmt});
  assert.equal(absent.length,6); assert.equal(absent[1].children[0].present,false);
  const kit={groups:tree.groups.slice(0,3),parts:tree.parts.slice(0,3)};
  const kitRows=A.buildAssemblyRows(kit,nodes,{lang:'en',fmt});
  assert.deepEqual(kitRows.map(r=>r.key),['g:stator','g:winding','g:magnets']);
  const walk=(rows)=>rows.flatMap(row=>[row.key,...walk(row.children??[])]);
  const keys=walk(kitRows);
  assert.equal(new Set(keys).size,keys.length);
  assert.equal(A.instanceLabel('PhaseBoard_pins_003',fmt),'Pin 3');
  assert.equal(A.instanceLabel('Prop_fix',fmt),'Prop fix');
});

test('one public viewer delegates to its minimal PartTree and all four hosts use that public entry', () => {
  const root=join(dirname(fileURLToPath(import.meta.url)),'..','..');
  const read=(p)=>readFileSync(join(root,p),'utf8');
  const publicIndex=read('modules/viewer3d/index.ts');
  const viewer=read('modules/viewer3d/internal/UnifiedViewer.tsx');
  assert.ok(publicIndex.includes('UnifiedViewer')&&publicIndex.includes('PartTree'));
  assert.ok(viewer.includes("from './PartTree'") && viewer.includes('<PartTree'));
  for(const p of ['components/model3d/Model3DPanel.tsx','components/geometry/GeometryFullPanel.tsx','components/materials/FullPartsPanel.tsx','components/viewer3d/UnifiedActiveMotorScene.tsx'])
    assert.match(read(p),/from ['"][^'"]*modules\/viewer3d['"]/ ,p);
  assert.ok(read('components/common/FieldViewer.tsx').includes("from '../../modules/viewer3d'"));
  assert.ok(read('components/viewer3d/ComponentTree.tsx').includes("from '../../modules/viewer3d'"));
  assert.equal(existsSync(join(root,'components/viewer3d/ComponentTreeView.tsx')),false);
  for(const old of ['ModelCanvas.tsx','modelViewer.ts','NavigationCube.tsx','UnifiedModelViewer.tsx','UnifiedViewerToolbar.tsx'])
    assert.equal(existsSync(join(root,'components/model3d',old)),false,old);
});
