// Pure model of the controller-product list (ControllerProducts.tsx): what each cell and detail row says.
// A value the catalogue does not state is empty (the view prints "-"); nothing here estimates anything.
import type { ControllerProduct, ProvCell } from './controllerApi';

export type Translate = (key: string, opts?: Record<string, unknown>) => string;
export interface ProductCell { text: string; tip?: string }

const present = (c: ProvCell | null | undefined): c is ProvCell => !!c && c.value !== undefined && c.value !== null && c.value !== '';
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? String(Number(v.toFixed(3))) : String(v ?? ''));
/** "page 10, stated" - where a value comes from. */
export function provTip(c: ProvCell | null | undefined, t: Translate): string {
  if (!c) return '';
  const parts = [c.page ? t('controllers.page', { page: c.page }) : '', c.basis ? t(`controllers.basis.${c.basis}`) : ''].filter(Boolean);
  return [parts.join(', '), c.note ?? ''].filter(Boolean).join(' - ');
}
const withUnit = (c: ProvCell | null | undefined, unit: string) => (present(c) ? `${num(c.value)} ${unit}`.trim() : '');
const kilo = (c: ProvCell | null | undefined) => (present(c) && typeof c.value === 'number' ? `${num(c.value / 1000)} kHz` : '');

export function controllerCells(row: ControllerProduct, t: Translate): Record<string, ProductCell> {
  const sup = row.supply_voltage_V ?? {};
  const pc = row.phase_current ?? { continuous: [], peak: [] };
  const peakRms = (pc.peak ?? []).find((p) => p.unit === 'Arms');
  const fsw = row.switching_frequency_Hz ?? {};
  const range = (a: ProvCell | null | undefined, b: ProvCell | null | undefined, unit: string) => present(a) && present(b) ? `${num(a.value)} - ${num(b.value)} ${unit}` : '';
  const dev = row.power_devices as { device_type?: string | null; voltage_class_V?: number | null } | undefined;
  return {
    brand: { text: row.brand },
    model: { text: row.model, tip: row.manufacturer ?? undefined },
    revision: { text: row.revision ?? '' },
    // ONE value per cell: the supply maximum; minimum and absolute maximum are in the tooltip
    supplyMax: { text: withUnit(sup.max, 'V'), tip: [provTip(sup.max, t), present(sup.min) ? `${t('controllers.min')}: ${num(sup.min.value)} V` : '', present(sup.abs_max) ? `${t('controllers.absMax')}: ${num(sup.abs_max.value)} V` : ''].filter(Boolean).join(' | ') },
    continuous: { text: withUnit(pc.continuous_Arms_at_supply_max, 'A rms'), tip: [provTip(pc.continuous_Arms_at_supply_max, t), ...(pc.continuous ?? []).map((c) => `${num(c.value)} ${c.unit} @ ${c.dc_V} V (${t('controllers.page', { page: c.page })})`)].filter(Boolean).join(' | ') },
    peak: { text: peakRms ? withUnit(peakRms, 'A rms') : '', tip: (pc.peak ?? []).map((p) => `${num(p.value)} ${p.unit} (${t('controllers.page', { page: p.page })})`).join(' | ') },
    peakTime: { text: withUnit(pc.peak_duration_s, 's'), tip: provTip(pc.peak_duration_s, t) },
    switching: { text: kilo(fsw.rating_point), tip: [provTip(fsw.rating_point, t), range(fsw.min, fsw.max, 'Hz')].filter(Boolean).join(' | ') },
    devices: { text: row.power_devices_known ? [dev?.device_type, dev?.voltage_class_V ? `${dev.voltage_class_V} V` : ''].filter(Boolean).join(', ') : t('controllers.unknown'), tip: row.power_devices_known ? undefined : String((row.power_devices as { note?: string } | undefined)?.note ?? '') },
    sensors: { text: (row.position_sensors ?? []).map((s) => s.spec_choice ?? s.type).join(', '), tip: (row.position_sensors ?? []).map((s) => `${s.type} (${t('controllers.page', { page: s.page })})`).join(' | ') },
    mass: { text: withUnit(row.mass_kg, 'kg'), tip: provTip(row.mass_kg, t) },
  };
}

/** The open row: every stated value of the entry with its page. */
export function controllerDetails(row: ControllerProduct, t: Translate): Array<{ label: string; value: string }> {
  const sup = row.supply_voltage_V ?? {};
  const pc = row.phase_current ?? { continuous: [], peak: [] };
  const withPage = (text: string, c: ProvCell | null | undefined) => (text ? `${text} (${t('controllers.page', { page: c?.page ?? '-' })})` : '');
  const fsw = row.switching_frequency_Hz ?? {};
  const dim = row.dimensions_mm as { height?: number; width?: number; length?: number; page?: number } | null | undefined;
  const cooling = row.cooling as { type?: string; coolant?: string; max_inlet_C?: ProvCell; flow_L_per_min?: ProvCell; page?: number } | null | undefined;
  const items: Array<[string, string]> = [
    [t('controllers.detail.manufacturer'), row.manufacturer ?? ''],
    [t('controllers.detail.supply'), [['min', 'min'], ['nominal', 'nominal'], ['max', 'max'], ['abs_max', 'absMax']].map(([k, label]) => present(sup[k]) ? `${t(`controllers.${label}`)} ${withPage(withUnit(sup[k], 'V'), sup[k])}` : '').filter(Boolean).join('; ')],
    [t('controllers.detail.dcCurrent'), withPage(withUnit(row.dc_input_current_max_A, 'A'), row.dc_input_current_max_A)],
    [t('controllers.detail.continuous'), (pc.continuous ?? []).map((c) => withPage(`${num(c.value)} ${c.unit} @ ${c.dc_V} V`, c)).join('; ')],
    [t('controllers.detail.peak'), (pc.peak ?? []).map((c) => withPage(`${num(c.value)} ${c.unit}`, c)).join('; ')],
    [t('controllers.detail.peakTime'), withPage(withUnit(pc.peak_duration_s, 's'), pc.peak_duration_s)],
    [t('controllers.detail.switching'), present(fsw.min) && present(fsw.max) ? withPage(`${num(fsw.min.value)} - ${num(fsw.max.value)} Hz`, fsw.min) : ''],
    [t('controllers.detail.devices'), row.power_devices_known ? controllerCells(row, t).devices.text : t('controllers.unknown')],
    [t('controllers.detail.sensors'), (row.position_sensors ?? []).map((s) => withPage(s.type, s)).join('; ')],
    [t('controllers.detail.communication'), (row.communication ?? []).map((c) => withPage(c.name, c)).join('; ')],
    [t('controllers.detail.cooling'), cooling ? [cooling.type, cooling.coolant, present(cooling.max_inlet_C) ? `${num(cooling.max_inlet_C.value)} °C` : '', present(cooling.flow_L_per_min) ? `${num(cooling.flow_L_per_min.value)} L/min` : ''].filter(Boolean).join(', ') : ''],
    [t('controllers.detail.dimensions'), dim && dim.length ? withPage(`${dim.length} x ${dim.width} x ${dim.height} mm`, { page: dim.page } as ProvCell) : ''],
    [t('controllers.detail.mass'), withPage(withUnit(row.mass_kg, 'kg'), row.mass_kg)],
    [t('controllers.detail.protection'), (row.protection as { ip?: string } | null | undefined)?.ip ?? ''],
    [t('controllers.detail.source'), row.source?.document ?? ''],
    [t('controllers.detail.notes'), (row.notes ?? []).join(' | ')],
  ];
  return items.filter(([, v]) => v !== undefined).map(([label, value]) => ({ label, value }));
}
