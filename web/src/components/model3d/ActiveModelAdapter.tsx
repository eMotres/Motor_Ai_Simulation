import { useMemo } from 'react';
import { PART_COLORS } from '../../lib/partColors';
import { useMotorStore } from '../../stores/motorStore';
import { useMotorMeshExtruded } from '../viewer3d/ApiMotorMesh';
import { usePartStates } from '../materials/usePartStates';
import type { PartTreeProps, UnifiedViewerProps } from '../../modules/viewer3d';
import { useTranslation } from 'react-i18next';

const familyOf = (key:string):string => key === 'stator_core' ? 'stator' : key === 'rotor_core' ? 'rotor'
  : key.startsWith('magnet_') ? 'magnets' : key.startsWith('coil_') ? 'coils' : key;
const stateKey = (key:string):string|null => key==='stator_core'?'stator_core':key==='rotor_core'?'rotor_core':key==='shaft'?'shaft':key==='sleeve'?'sleeve'
  :key.startsWith('magnet_')?'magnet':key.startsWith('coil_')?'slot':null;
const colourOf = (key:string):string => key==='stator_core'?PART_COLORS.statorIron:key==='rotor_core'?PART_COLORS.rotorIron:key==='shaft'?PART_COLORS.shaft:key==='sleeve'?PART_COLORS.sleeve
  :key.startsWith('magnet_')?(Number(key.slice(7))%2===0?PART_COLORS.magnetN:PART_COLORS.magnetS):key.startsWith('coil_')?PART_COLORS.copper
  :key==='slot_insulation'?PART_COLORS.slotLiner:key==='wire_insulation'?PART_COLORS.enamel:key==='in_band'?PART_COLORS.inBand:key==='out_band'?PART_COLORS.outBand:'#94a3b8';

export function useActiveViewerAdapter() {
  const data = useMotorMeshExtruded();
  const geometry = useMotorStore((s) => s.geometry);
  const { states } = usePartStates();
  const { t } = useTranslation('geometry');
  const excluded = useMemo(() => new Set(Object.entries(states ?? {}).filter(([,v])=>v==='excluded').map(([k])=>k)),[states]);
  const parts = useMemo(() => {
    if (!data) return null;
    const parts: NonNullable<UnifiedViewerProps['activeParts']> = [];
    for (const [key,mesh] of Object.entries(data)) {
      const state=stateKey(key); if (state&&excluded.has(state)) continue;
      if (!mesh?.vertices?.length) continue;
      const translucent=key==='in_band'||key==='out_band';
      parts.push({key,family:familyOf(key),colour:colourOf(key),vertices:mesh.vertices,faces:mesh.faces,opacity:translucent ? 0.12 : 1});
    }
    return parts;
  },[data,excluded]);
  const tree = useMemo(() => {
    if (!parts) return null;
    const names=(family:string) => parts.filter(p=>p.family===family);
    const tree: PartTreeProps['nodes'] = [];
    for (const [key,label,colour] of [['stator_core',t('activeTree.stator'),PART_COLORS.statorIron],['rotor_core',t('activeTree.rotor'),PART_COLORS.rotorIron],
      ['shaft',t('activeTree.shaft'),PART_COLORS.shaft],['sleeve',t('activeTree.sleeve'),PART_COLORS.sleeve],['slot_insulation',t('activeTree.insulation'),PART_COLORS.slotLiner],
      ['wire_insulation',t('activeTree.enamel'),PART_COLORS.enamel],['in_band',t('activeTree.inBand'),PART_COLORS.inBand],['out_band',t('activeTree.outBand'),PART_COLORS.outBand]] as const) {
      const p=parts.find(x=>x.key===key); if (p) tree.push({key:key==='stator_core'?'stator':key==='rotor_core'?'rotor':key,label,colour,sceneNames:[p.key],defaultVisible:!translucentKey(key)});
    }
    const coils=names('coils'), magnets=names('magnets');
    if (coils.length) tree.splice(2,0,{key:'coils',label:t('activeTree.coils'),colour:PART_COLORS.copper,sceneNames:coils.map(p=>`coil:${Number(p.key.slice(5))}`),children:coils.map(p=>({key:p.key,label:t('activeTree.coil',{n:Number(p.key.slice(5))+1}),colour:p.colour,sceneNames:[`coil:${Number(p.key.slice(5))}`]}))});
    if (magnets.length) tree.splice(coils.length?3:2,0,{key:'magnets',label:t('activeTree.magnets'),colour:PART_COLORS.magnetN,sceneNames:magnets.map(p=>`magnet:${Number(p.key.slice(7))}`),children:magnets.map(p=>({key:p.key,label:t('activeTree.magnet',{n:Number(p.key.slice(7))+1,pole:Number(p.key.slice(7))%2===0?'N':'S'}),colour:p.colour,sceneNames:[`magnet:${Number(p.key.slice(7))}`]}))});
    return tree;
  },[parts,t]);
  return {parts,tree,sourceKey:`${JSON.stringify(geometry)}:${parts?.map(p=>p.key).join(',') ?? ''}:${[...excluded].sort().join(',')}`};
}
function translucentKey(key:string):boolean { return key==='in_band'||key==='out_band'||key==='wire_insulation'||key==='slot_insulation'; }
