// Configure PRESETS — the real configurations of the machine's die (owner 2026-10-05).
//
// One preset per configuration the die has (CIANO14 40 new today: L12 and L20; a die with one
// or five configurations has one or five).  Each is read from the configuration itself by the
// server (GET /api/catalog/{id}/configure_context -> `presets`), so no name and no number lives
// here.  Choosing one restores EVERY knob to that real configuration — stack, turns, wire,
// connection, current, speed — its saved battery pack and its default drive (the first PWM
// variant computed for it, else Sine).  The tuner then says "modified from <name> preset" as
// soon as anything differs.
//
// Pure, no imports: web/src/lib/__tests__/configuratorPresets.test.mjs imports this file.

/** The knobs the server states for a configuration (`null` = the configuration does not say). */
export interface PresetKnobs {
  L_mm: number | null; N: number | null; wireH_mm: number | null;
  split: number | null; nP: number | null; I_A: number | null; rpm: number | null;
}

/** A pack as the configuration saved it (the shape `batteryFromPack` reads). */
export interface PresetPack {
  cells?: number | null; chemistry?: string | null;
  v_min?: number | null; v_nom?: number | null; v_max?: number | null;
  v_cell_min?: number | null; v_cell_nom?: number | null; v_cell_max?: number | null;
}

export interface Preset {
  config: string;
  die: string;
  knobs: PresetKnobs;
  duty?: string | null;
  battery: PresetPack | null;
  device?: string | null;
  pwm_variants: { id: string; device?: string; carrier_hz?: number }[];
  /** the variant the preset opens on; null = Sine */
  drive_variant: string | null;
  /** date of this configuration's FULL passport card; null/absent = none */
  card_date?: string | null;
}

/** The slice of the tuner's knobs the presets speak about. */
export interface KnobsLike {
  N: number; L_mm: number; wireH_mm: number; nP: number; I_A: number; rpm: number;
  split?: number; drive?: 'sine' | 'pwm'; drive_variant?: string; drive_device?: string; drive_carrier_hz?: number;
}

/** `base` with every knob the preset states laid over it, and the preset's own drive. */
export function presetKnobs<K extends KnobsLike>(base: K, pr: Preset): K {
  const k = pr.knobs;
  const out: K = { ...base };
  const put = <F extends 'N' | 'L_mm' | 'wireH_mm' | 'nP' | 'I_A' | 'rpm'>(f: F, v: number | null) => {
    if (v != null && Number.isFinite(v)) (out as KnobsLike)[f] = v;
  };
  put('L_mm', k.L_mm); put('N', k.N); put('wireH_mm', k.wireH_mm);
  put('nP', k.nP); put('I_A', k.I_A); put('rpm', k.rpm);
  if (k.split != null && Number.isFinite(k.split)) out.split = k.split;
  if (pr.drive_variant) {
    out.drive = 'pwm'; out.drive_variant = pr.drive_variant;
    // the transistor + frequency the preset opens on (the pair is what the two dropdowns show)
    const v = pr.pwm_variants.find((x) => x.id === pr.drive_variant);
    if (v?.device && Number(v.carrier_hz) > 0) { out.drive_device = v.device; out.drive_carrier_hz = Number(v.carrier_hz); }
    else { delete (out as KnobsLike).drive_device; delete (out as KnobsLike).drive_carrier_hz; }
  } else {
    out.drive = 'sine';
    delete (out as KnobsLike).drive_variant; delete (out as KnobsLike).drive_device; delete (out as KnobsLike).drive_carrier_hz;
  }
  return out;
}

const close = (a: number, b: number) => Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(b));

export interface BatteryCmp { cells: number; nom: number; max: number; min: number; }

/** What differs between the tuner's current state and a preset — empty = exactly the preset.
 *  Names are field keys (`build`, `current`, `speed`, `battery`, `drive`), not sentences. */
export function presetDiff(pr: Preset, k: KnobsLike, battery: BatteryCmp | null,
                           presetBattery: BatteryCmp | null,
                           /** the propeller in use and the one this preset opens on — given only for a
                            *  propeller-cooled machine; a different one makes the state "modified" */
                           prop?: { current: string | null; wanted: string | null }): string[] {
  // the drive is the (transistor, frequency) PAIR: two ids of one pair are the same drive
  const out: string[] = [];
  const p = pr.knobs;
  const diff = (a: number, b: number | null) => b != null && !close(a, b);
  if (diff(k.L_mm, p.L_mm) || diff(k.N, p.N) || diff(k.wireH_mm, p.wireH_mm)
      || diff(k.nP, p.nP) || (p.split != null && diff(k.split ?? 1, p.split))) out.push('build');
  if (diff(k.I_A, p.I_A)) out.push('current');
  if (diff(k.rpm, p.rpm)) out.push('speed');
  if (presetBattery && battery) {
    if (battery.cells !== presetBattery.cells || !close(battery.nom, presetBattery.nom)
        || !close(battery.max, presetBattery.max) || !close(battery.min, presetBattery.min)) out.push('battery');
  }
  const wantPwm = !!pr.drive_variant;
  const isPwm = k.drive === 'pwm';
  const pv = pr.pwm_variants.find((x) => x.id === pr.drive_variant);
  const same = pv?.device && Number(pv.carrier_hz) > 0 && k.drive_device
    ? k.drive_device === pv.device && Math.abs(Number(k.drive_carrier_hz) - Number(pv.carrier_hz)) < 1e-6 * Number(pv.carrier_hz)
    : k.drive_variant === pr.drive_variant;
  if (wantPwm !== isPwm || (wantPwm && !same)) out.push('drive');
  if (prop && (prop.current ?? '') !== (prop.wanted ?? '')) out.push('propeller');
  return out;
}

/** The preset whose BUILD the knobs are — the base a freshly loaded machine starts from
 *  (stack, turns, wire and connection all equal); `null` when no configuration is that build. */
export function presetOfBuild(presets: Preset[], k: Pick<KnobsLike, 'N' | 'L_mm' | 'wireH_mm' | 'nP'>): Preset | null {
  return presets.find((pr) => {
    const p = pr.knobs;
    const eq = (a: number, b: number | null) => b == null || close(a, b);
    return p.L_mm != null && eq(k.L_mm, p.L_mm) && eq(k.N, p.N) && eq(k.wireH_mm, p.wireH_mm) && eq(k.nP, p.nP);
  }) ?? null;
}
