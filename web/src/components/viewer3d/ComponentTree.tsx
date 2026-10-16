/**
 * ComponentTree — the 3-D viewport's binding of the shared tree.
 *
 * Legacy MotorScene host binding for the shared minimal tree. The unified
 * model hosts use UnifiedViewer, which owns these visibility controls itself.
 */
import React, { useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useUIStore, type CompKey } from '../../stores/motorStore';
import { useMotorMeshExtruded } from './ApiMotorMesh';
import { PART_COLORS } from '../../lib/partColors';
import { PartTree, type PartTreeProps } from '../../modules/viewer3d';
import { usePartStates } from '../materials/usePartStates';

interface TreeRow { key:string; label:string; colour:string; visible:boolean; present?:boolean; group?:TreeRow[] }
interface TreeModel { rows:TreeRow[]; selected?:string|null; onToggle:(key:string)=>void; onSelect?:(key:string)=>void }

export function useActiveTreeModel(): TreeModel {
  const { t } = useTranslation('geometry');
  const {
    componentVisibility,
    coilVisibility,
    magnetVisibility,
    toggleComponentVisibility,
    toggleCoilVisibility,
    toggleMagnetVisibility,
    selectedPart,
    setSelectedPart,
  } = useUIStore();

  // Read mesh data to know how many coils / magnets exist
  const meshData = useMotorMeshExtruded();
  const { states } = usePartStates();
  const hasMesh = useCallback((key: string) => !!(meshData as Record<string, { vertices?: number[] }> | null)?.[key]?.vertices?.length, [meshData]);
  const hasSleeve = hasMesh('sleeve') && states?.sleeve !== 'excluded';

  const coilCount = useMemo(() => {
    if (!meshData) return 0;
    return Object.keys(meshData).filter(k => k.startsWith('coil_')).length;
  }, [meshData]);

  const magnetCount = useMemo(() => {
    if (!meshData) return 0;
    return Object.keys(meshData).filter(k => k.startsWith('magnet_')).length;
  }, [meshData]);

  const model: TreeModel = useMemo(() => {
    const rows: TreeRow[] = [
      { key: 'stator', label: t('activeTree.stator'), colour: PART_COLORS.statorIron,
        visible: componentVisibility.stator, present: hasMesh('stator_core') && states?.stator_core !== 'excluded' },
      { key: 'rotor', label: t('activeTree.rotor'), colour: PART_COLORS.rotorIron,
        visible: componentVisibility.rotor, present: hasMesh('rotor_core') && states?.rotor_core !== 'excluded' },
      { key: 'coils', label: t('activeTree.coils'), colour: PART_COLORS.copper,
        visible: componentVisibility.coils, present: coilCount > 0 && states?.slot !== 'excluded',
        group: Array.from({ length: coilCount }, (_, i) => ({
          key: `coil:${i}`, label: t('activeTree.coil', { n: i + 1 }), colour: PART_COLORS.copper,
          visible: coilVisibility[i] ?? true, present: hasMesh(`coil_${i}`),
        })) },
      { key: 'magnets', label: t('activeTree.magnets'), colour: PART_COLORS.magnetN,
        visible: componentVisibility.magnets, present: magnetCount > 0 && states?.magnet !== 'excluded',
        group: Array.from({ length: magnetCount }, (_, i) => ({
          key: `magnet:${i}`,
          label: t('activeTree.magnet', { n: i + 1, pole: i % 2 === 0 ? 'N' : 'S' }),
          colour: i % 2 === 0 ? PART_COLORS.magnetN : PART_COLORS.magnetS,
          visible: magnetVisibility[i] ?? true, present: hasMesh(`magnet_${i}`),
        })) },
      { key: 'shaft', label: t('activeTree.shaft'), colour: PART_COLORS.shaft,
        visible: componentVisibility.shaft, present: hasMesh('shaft') && states?.shaft !== 'excluded' },
      // Retaining sleeve — only when the machine actually has one, so the row
      // cannot appear for a part that is not built (and the server drops the
      // mesh key for an EXCLUDED sleeve, which hides the row too).
      ...(hasSleeve ? [{ key: 'sleeve', label: t('activeTree.sleeve'),
        colour: PART_COLORS.sleeve, visible: componentVisibility.sleeve, present: hasSleeve,
        } as TreeRow] : []),
      { key: 'slot_insulation', label: t('activeTree.insulation'), colour: PART_COLORS.slotLiner,
        visible: componentVisibility.slot_insulation, present: hasMesh('slot_insulation') },
      { key: 'wire_insulation', label: t('activeTree.enamel'), colour: PART_COLORS.enamel,
        visible: componentVisibility.wire_insulation, present: hasMesh('wire_insulation') },
      // Sliding-band air rings: rotor-side, then stator-side.
      { key: 'in_band', label: t('activeTree.inBand'), colour: PART_COLORS.inBand,
        visible: componentVisibility.in_band, present: hasMesh('in_band') },
      { key: 'out_band', label: t('activeTree.outBand'), colour: PART_COLORS.outBand,
        visible: componentVisibility.out_band, present: hasMesh('out_band') },
    ];
    return {
      rows,
      selected: selectedPart,
      onToggle: (key) => {
        if (key.startsWith('coil:')) return toggleCoilVisibility(Number(key.slice(5)));
        if (key.startsWith('magnet:')) return toggleMagnetVisibility(Number(key.slice(7)));
        toggleComponentVisibility(key as CompKey);
      },
      // instance rows (coil:3, magnet:1) are not selectable parts; only the component rows are
      onSelect: (key) => { if (key.includes(':')) return; setSelectedPart(selectedPart === key ? null : key as CompKey); },
    };
  }, [componentVisibility, coilVisibility, magnetVisibility, coilCount,
      magnetCount, hasSleeve, hasMesh, states, selectedPart, toggleComponentVisibility,
      toggleCoilVisibility, toggleMagnetVisibility, setSelectedPart, t]);

  return model;
}

const ComponentTree: React.FC = () => {
  const model = useActiveTreeModel();
  const nodes: PartTreeProps['nodes'] = model.rows.map((r) => ({ key:r.key,label:r.label,colour:r.colour,present:r.present,
    ...(r.group ? {children:r.group.map((c) => ({key:c.key,label:c.label,colour:c.colour,present:c.present}))} : {}) }));
  const hidden = new Set<string>();
  const visit = (rows: TreeRow[]) => rows.forEach((r) => { if (!r.visible || r.present === false) hidden.add(r.key); if (r.group) visit(r.group); });
  visit(model.rows);
  return <PartTree nodes={nodes} hiddenKeys={hidden} onToggle={model.onToggle} selectedKey={model.selected} onSelect={model.onSelect} />;
};
export default ComponentTree;
