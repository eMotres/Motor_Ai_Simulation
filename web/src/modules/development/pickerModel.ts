import type { ProjectMotorPickerItem } from './types';

export function filterPickerCatalog(items: ProjectMotorPickerItem[], query: string): ProjectMotorPickerItem[] {
  const needle = query.trim().toLocaleLowerCase();
  return items.filter((item) => !needle || `${item.die} ${item.config} ${item.label ?? ''} ${item.diameter_mm ?? ''} ${item.slots ?? ''} ${item.poles ?? ''} ${item.stack_mm ?? ''}`.toLocaleLowerCase().includes(needle));
}

export function groupPickerCatalog(items: ProjectMotorPickerItem[]): Array<[string, ProjectMotorPickerItem[]]> {
  const groups = new Map<string, ProjectMotorPickerItem[]>();
  for (const item of items) groups.set(item.die, [...(groups.get(item.die) ?? []), item]);
  return [...groups.entries()];
}

export function pickerDisplayLabel(item: ProjectMotorPickerItem, t: (key: string, args?: Record<string, unknown>) => string): string {
  if (item.diameter_mm != null || item.slots != null || item.poles != null || item.stack_mm != null) {
    return t('picker.dimensions', { diameter: item.diameter_mm ?? '—', slots: item.slots ?? '—', poles: item.poles ?? '—', stack: item.stack_mm ?? '—' });
  }
  return item.label ?? `${item.die} / ${item.config}`;
}

export function pickerReasonKey(reason?: string): string {
  const value = (reason ?? '').trim().toLowerCase().replace(/[^a-z]+/g, '_').replace(/^_|_$/g, '');
  if (value.includes('already_linked') || value.includes('already_linked_to_another_project') || value.includes('linked_elsewhere')) return 'picker.reason.linkedElsewhere';
  if (value.includes('motor_unavailable') || value.includes('no_longer_available')) return 'picker.reason.motorUnavailable';
  if (value.includes('published') || value.includes('share_authority') || value.includes('not_shareable')) return 'picker.reason.shareRequired';
  return 'picker.reason.unavailable';
}

export function snapshotSourceKey(source: string): string | undefined {
  const value = source.toLowerCase().replace(/[^a-z]+/g, '_').replace(/^_|_$/g, '');
  const known: Record<string, string> = {
    linked_motor: 'snapshots.sourceLinkedMotor',
    unlinked_motor: 'snapshots.sourceUnlinkedMotor',
    saved_from_configure: 'snapshots.sourceConfigure',
    saved_from_simulation: 'snapshots.sourceSimulation',
    applied_optimization_point: 'snapshots.sourceOptimization',
  };
  return known[value];
}
