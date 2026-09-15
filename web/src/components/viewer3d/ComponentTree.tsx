/**
 * ComponentTree — the 3-D viewport's binding of the shared tree.
 *
 * The LOOK lives in `ComponentTreeView` (user 2026-09-06: "используй то же
 * самое дерево, которое у нас уже есть, чтобы всё было универсально" — the
 * field viewer now drives the same view with its own model).  This file is only
 * the wiring to the global `useUIStore`: which rows the machine has, what each
 * eye toggles, what "isolate" and "Show All" mean here.  Rows are listed in the
 * order they always were, so the 3-D tab is unchanged down to the connectors.
 */
import React, { useMemo } from 'react';
import { useUIStore, useMotorStore, type CompKey } from '../../stores/motorStore';
import { useMotorMesh } from './ApiMotorMesh';
import { PART_COLORS } from '../../lib/partColors';
import ComponentTreeView, { type TreeRow, type TreeModel } from './ComponentTreeView';

const ComponentTree: React.FC = () => {
  const {
    componentVisibility,
    coilVisibility,
    magnetVisibility,
    toggleComponentVisibility,
    toggleCoilVisibility,
    toggleMagnetVisibility,
    isolateComponent,
    showAllComponents,
    selectedPart,
    setSelectedPart,
  } = useUIStore();

  // Read mesh data to know how many coils / magnets exist
  const meshData = useMotorMesh();
  // The sleeve row keys on the GEOMETRY, not only on the CadQuery mesh: in the
  // flat / extruded viewer modes that mesh is never fetched, so the row was
  // missing there while the ring was drawn (user 2026-09-05: "не вижу sleeve
  // в дереве").  The geometry says whether the machine has one.
  const sleeveT = Number(useMotorStore(s => (s.geometry as Record<string, unknown> | null)?.sleeve_thickness ?? 0));
  const hasSleeve = sleeveT > 0 || !!(meshData && (meshData as Record<string, unknown>).sleeve);

  const coilCount = useMemo(() => {
    if (!meshData) return 0;
    return Object.keys(meshData).filter(k => k.startsWith('coil_')).length;
  }, [meshData]);

  const magnetCount = useMemo(() => {
    if (!meshData) return 0;
    return Object.keys(meshData).filter(k => k.startsWith('magnet_')).length;
  }, [meshData]);

  const hiddenCoils   = Object.values(coilVisibility).filter(v => !v).length;
  const hiddenMagnets = Object.values(magnetVisibility).filter(v => !v).length;

  const allVisible =
    componentVisibility.stator &&
    componentVisibility.rotor &&
    componentVisibility.magnets &&
    componentVisibility.coils &&
    componentVisibility.shaft &&
    hiddenCoils === 0 &&
    hiddenMagnets === 0;

  // Child keys are prefixed so one flat `onToggle(key)` can route them: the
  // model is a list of strings, not a union of component kinds.
  const model: TreeModel = useMemo(() => {
    const rows: TreeRow[] = [
      { key: 'stator', label: 'Stator Core', colour: PART_COLORS.statorIron,
        visible: componentVisibility.stator, partKey: 'stator_core' },
      { key: 'rotor', label: 'Rotor Core', colour: PART_COLORS.rotorIron,
        visible: componentVisibility.rotor, partKey: 'rotor_core' },
      { key: 'coils', label: 'Windings', colour: PART_COLORS.copper,
        visible: componentVisibility.coils, partKey: 'slot',
        group: Array.from({ length: coilCount }, (_, i) => ({
          key: `coil:${i}`, label: `Coil ${i + 1}`, colour: PART_COLORS.copper,
          visible: coilVisibility[i] ?? true,
        })) },
      { key: 'magnets', label: 'Magnets', colour: PART_COLORS.magnetN,
        visible: componentVisibility.magnets, partKey: 'magnet',
        group: Array.from({ length: magnetCount }, (_, i) => ({
          key: `magnet:${i}`,
          label: `Magnet ${i + 1}${i % 2 === 0 ? ' N' : ' S'}`,
          colour: i % 2 === 0 ? PART_COLORS.magnetN : PART_COLORS.magnetS,
          visible: magnetVisibility[i] ?? true,
        })) },
      { key: 'shaft', label: 'Shaft', colour: PART_COLORS.shaft,
        visible: componentVisibility.shaft, partKey: 'shaft' },
      // Retaining sleeve — only when the machine actually has one, so the row
      // cannot appear for a part that is not built (and the server drops the
      // mesh key for an EXCLUDED sleeve, which hides the row too).
      ...(hasSleeve ? [{ key: 'sleeve', label: 'Retaining sleeve',
        colour: PART_COLORS.sleeve, visible: componentVisibility.sleeve,
        partKey: 'sleeve' } as TreeRow] : []),
      { key: 'slot_insulation', label: 'Insulation', colour: PART_COLORS.slotLiner,
        visible: componentVisibility.slot_insulation },
      { key: 'wire_insulation', label: 'Wire enamel', colour: PART_COLORS.enamel,
        visible: componentVisibility.wire_insulation },
      // Sliding-band air rings: rotor-side, then stator-side.
      { key: 'in_band', label: 'In Band (rotating air)', colour: PART_COLORS.inBand,
        visible: componentVisibility.in_band },
      { key: 'out_band', label: 'Out Band (static air)', colour: PART_COLORS.outBand,
        visible: componentVisibility.out_band },
    ];
    return {
      rows,
      selected: selectedPart,
      allVisible,
      onToggle: (key) => {
        if (key.startsWith('coil:')) return toggleCoilVisibility(Number(key.slice(5)));
        if (key.startsWith('magnet:')) return toggleMagnetVisibility(Number(key.slice(7)));
        toggleComponentVisibility(key as CompKey);
      },
      onIsolate: (key) => isolateComponent(key as CompKey),
      onShowAll: showAllComponents,
      onSelect: (key) => setSelectedPart(selectedPart === key ? null : key as CompKey),
    };
  }, [componentVisibility, coilVisibility, magnetVisibility, coilCount,
      magnetCount, hasSleeve, allVisible, selectedPart, toggleComponentVisibility,
      toggleCoilVisibility, toggleMagnetVisibility, isolateComponent,
      showAllComponents, setSelectedPart]);

  return <ComponentTreeView model={model} />;
};

export default ComponentTree;
