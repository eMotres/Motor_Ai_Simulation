/**
 * Materials tab "Active | Full" (owner 2026-10-14) - the pure logic, testable without React.
 *
 *   Active  the tab exactly as it was: the parts the electromagnetic simulation uses (stator core, rotor core,
 *           magnets, winding copper, insulation, shaft), assigned in the 3-D viewer and the library tree.
 *   Full    the whole motor: those parts PLUS every other part of the assembled motor (holders, rings, carrier,
 *           bearings, screws, the phase board ...) with a material and a mass each.  The extra parts are NOT used
 *           by the electromagnetic simulation; their choice is saved with the die (PUT .../part_materials).
 *
 * The part list comes from the server (GET /api/family/die/{die}/part_materials): the CAD bill of materials of the
 * motor where one has been built, never an invented part.
 */

export type MaterialsMode = 'active' | 'full';
export const MATERIALS_MODES: ReadonlyArray<{ id: MaterialsMode; labelKey: string }> = [
  { id: 'active', labelKey: 'materialsModeActive' },
  { id: 'full', labelKey: 'materialsModeFull' },
];
export const MODE_STORAGE_KEY = 'materials.mode';

/** Anything but 'full' is Active: the tab opens as it always did. */
export function parseMode(raw: unknown): MaterialsMode {
  return raw === 'full' ? 'full' : 'active';
}

export interface FullPartRow {
  key: string;
  name: string;
  role: string;
  em: boolean;                       // true = used by the electromagnetic simulation (the active assignment)
  material_id: string;
  default_material_id: string | null;
  user_set: boolean;
  mass_kg: number | null;
  counted: boolean;
  status: string;
  basis: string;
}

export interface MaterialChoice {
  id: string;
  name: string;
  group: string;
  origin: string;
  density_kg_m3: number | null;
}

export interface FullPartsPayload {
  die: string;
  config: string;
  available: boolean;
  reason?: string;
  source?: 'cad_bom' | 'active_only' | 'active_only+config';
  /** true = no CAD model yet: the mass is the active mass only ("partial mass (no CAD model yet)"). */
  partial?: boolean;
  can_write?: boolean;
  /** What `total_kg` is the mass OF: "full motor" or "frameless kit" (owner 2026-10-15). */
  scope?: 'full motor' | 'frameless kit';
  /** true = the frameless kit (stator + coils + phase board + rotor + magnets): the total is the kit mass. */
  kit?: boolean;
  /** THE motor mass: the full assembled mass (partial: the active mass). */
  total_kg?: number;
  active_kg?: number;
  rotating_kg?: number;
  parts: FullPartRow[];
  choices?: MaterialChoice[];
  non_active_keys?: string[];
}

/** Active shows only the EM parts; Full shows the whole motor, EM parts first. */
export function rowsForMode(rows: FullPartRow[], mode: MaterialsMode): FullPartRow[] {
  if (mode === 'active') return rows.filter((r) => r.em);
  return [...rows.filter((r) => r.em), ...rows.filter((r) => !r.em)];
}

/** Only a part the EM simulation does not use can be re-assigned here; the active parts keep their own assignment. */
export function isEditable(row: FullPartRow): boolean {
  return !row.em;
}

/** The tag of a row: the small "not used by EM" label of the non-active parts. */
export function rowTag(row: FullPartRow): 'em' | 'notEm' {
  return row.em ? 'em' : 'notEm';
}

/**
 * A client-side overlay (an ordinary user on an enforced backend keeps choices in the browser, like the active
 * assignment does) is merged over the server rows - non-active parts only.  The CAD volume is fixed, so a part given
 * another material changes its mass by the density ratio (when both densities are known).
 */
export function mergeOverlay(rows: FullPartRow[], overlay: Record<string, string>,
                             density?: (id: string) => number | null): FullPartRow[] {
  return rows.map((r) => {
    const m = r.em ? undefined : overlay[r.key];
    if (!m || m === r.material_id) return r;
    return { ...r, material_id: m, user_set: m !== r.default_material_id,
             mass_kg: density ? scaledMass(r, density, m) : r.mass_kg };
  });
}

/** Mass of a part under another material: the CAD volume is fixed, so the mass follows the density. */
export function scaledMass(row: FullPartRow, density: (id: string) => number | null, material: string): number | null {
  if (row.mass_kg == null) return null;
  const from = density(row.material_id);
  const to = density(material);
  return from && to ? row.mass_kg * (to / from) : row.mass_kg;
}

export const GROUP_ORDER = ['metal', 'steel', 'magnet', 'insulator', 'other'] as const;
export const GROUP_LABEL_KEYS: Record<string, string> = {
  metal: 'materialsGroupMetal', steel: 'materialsGroupSteel', magnet: 'materialsGroupMagnet',
  insulator: 'materialsGroupInsulator', other: 'materialsGroupOther',
};

/** Choices grouped for the picker, in a fixed group order; unknown groups go last under "other". */
export function groupChoices(choices: MaterialChoice[]): { group: string; items: MaterialChoice[] }[] {
  const by = new Map<string, MaterialChoice[]>();
  for (const c of choices) {
    const g = (GROUP_ORDER as readonly string[]).includes(c.group) ? c.group : 'other';
    if (!by.has(g)) by.set(g, []);
    by.get(g)!.push(c);
  }
  return GROUP_ORDER.filter((g) => by.has(g)).map((g) => ({
    group: g, items: by.get(g)!.sort((a, b) => a.id.localeCompare(b.id)),
  }));
}

/** "12.3 g" below a kilogram, "0.397 kg" above; unknown mass is a dash, never 0. */
export function formatMass(kg: number | null | undefined): string {
  if (kg == null || !Number.isFinite(kg)) return '-';
  return kg < 1 ? `${(kg * 1000).toFixed(kg < 0.01 ? 2 : 1)} g` : `${kg.toFixed(3)} kg`;
}

export const OVERLAY_KEY = 'materials.partOverlay.v1';

/** Overlay storage key per die (the choice belongs to the die). */
export function overlayKey(die: string): string {
  return `${OVERLAY_KEY}.${die}`;
}

/** Mass of the parts that count in the motor mass (the stand-in shaft tube of a carrier motor does not). */
export function countedMassKg(rows: FullPartRow[]): number {
  return rows.reduce((t, r) => t + (r.counted && r.mass_kg != null ? r.mass_kg : 0), 0);
}
