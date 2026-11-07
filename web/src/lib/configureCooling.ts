// The cooling a CONFIGURATION declares (its `thermal_defaults:` block), as Configure's temperature row uses it
// (2026-11-03: a water-glycol jacketed generator, h = 1060 W/m²K at 41 °C, was shown in the page's default 8 m/s air,
// every temperature red).  The server reduces the block to `preset.cooling_defaults`
// (motor_ai_sim.thermal_settings.configure_cooling); here it becomes the film the lumped estimate reads.
// A preset without it keeps the page's own typed cooling, unchanged.
//
// Pure, no imports: web/src/lib/__tests__/configureCooling.test.mjs imports this file.

/** `preset.cooling_defaults` as the server sends it. */
export interface ConfigCoolingSpec {
  kind: 'film' | 'liquid' | 'air';
  /** film: the stated film coefficient [W/m²K] */
  h_W_m2K?: number;
  /** liquid: the coolant flow [L/min] (the page's jacket law gives h) */
  flow_lpm?: number;
  fluid?: string;
  /** air: the air speed over the housing [m/s] */
  air_speed_ms?: number;
  /** the air, or the coolant inlet, the film works against [°C] */
  ambient_C: number;
  source?: string;
}

/** The cooling behind Configure's temperature row. */
export interface PageCooling {
  h_Wm2K: number;
  ambient_C: number;
  /** null = no air over the housing (a liquid jacket / a stated film) */
  air_speed_ms: number | null;
  /** true = a coolant film: the row shows "Coolant film h, T" instead of the air speed */
  film: boolean;
}

const pos = (v: unknown): number | null => {
  const x = Number(v);
  return Number.isFinite(x) && x > 0 ? x : null;
};

/** The configuration's own cooling at housing diameter `D_m`, or null when it states none Configure can use.
 *  `airH` / `liqH` are the page's own laws (components/thermal/api), passed in so this file stays pure. */
export function configCooling(spec: ConfigCoolingSpec | null | undefined, D_m: number,
                              airH: (v: number, D: number) => number, liqH: (q: number) => number): PageCooling | null {
  if (!spec || typeof spec !== 'object') return null;
  const amb = Number(spec.ambient_C);
  if (!Number.isFinite(amb)) return null;
  if (spec.kind === 'film') {
    const h = pos(spec.h_W_m2K);
    return h == null ? null : { h_Wm2K: h, ambient_C: amb, air_speed_ms: null, film: true };
  }
  if (spec.kind === 'liquid') {
    const q = pos(spec.flow_lpm);
    return q == null ? null : { h_Wm2K: liqH(q), ambient_C: amb, air_speed_ms: null, film: true };
  }
  if (spec.kind === 'air') {
    const v = Math.max(0, Number(spec.air_speed_ms) || 0);
    return { h_Wm2K: airH(v, D_m), ambient_C: amb, air_speed_ms: v, film: false };
  }
  return null;
}
