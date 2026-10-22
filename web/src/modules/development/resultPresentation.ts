export type Translate = (key: string) => string;

export function resultKindLabel(kind: string, t: Translate): string {
  const key = `resultKind.${kind}`;
  const value = t(key);
  return value === key ? t('storedResult') : value;
}

export function temperatureKindForKey(key: string): string {
  const low = key.toLowerCase();
  if (low.includes('coil') || low.includes('winding')) return 'coil';
  if (low.includes('magnet')) return low.includes('max') ? 'magnetMax' : 'magnet';
  if (low.includes('bearing')) return 'bearing';
  if (low.includes('stator')) return 'stator';
  if (low.includes('rotor')) return 'rotor';
  if (low.includes('housing')) return 'housing';
  if (low.startsWith('t_max')) return 'maximum';
  if (low.startsWith('t_min')) return 'minimum';
  return 'other';
}

export function temperatureLabel(kind: string, t: Translate): string {
  const key = `temperature.${kind}`;
  const value = t(key);
  return value === key ? t('temperatures') : value;
}
