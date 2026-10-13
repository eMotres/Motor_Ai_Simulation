// Configure presets = CONFIGURATION x MODE (owner 2026-10-13): "L12 . rated", "L12 . peak",
// "L20 . rated"...  A button sets the configuration's build (stack, turns, wire, connection)
// AND the mode's operating point (current, speed).  Only modes that have data are offered -
// the same rule as the drive variants: a mode exists when the configuration SAVED it with a
// usable point, nothing is invented.  The same template for every motor; no die-specific code.
//
// Pure, imports types only: web/src/lib/__tests__/configuratorModes.test.mjs imports this file.
import type { Preset, PresetDutyPoint } from './configuratorPresets';

/** A Preset narrowed to one mode: `duty` names the mode, `knobs` carry its point. */
export interface ModePreset extends Preset {
  duty: string;
  /** `${config}|${duty}` - the toggle value */
  key: string;
  /** "L12 · rated" */
  label: string;
  gamma_deg: number | null;
}

export const modeKey = (config: string, duty: string): string => `${config}|${duty}`;
export const modeLabel = (config: string, duty: string): string => `${config} · ${duty}`;

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Mandatory modes sort first: rated, then peak, then the rest in saved order. */
const rank = (name: string): number => {
  const n = name.trim().toLowerCase();
  return n.startsWith('rated') ? 0 : n.startsWith('peak') ? 1 : 2;
};

/** The modes of one configuration that have a usable operating point. */
export function modesOf(pr: Preset): PresetDutyPoint[] {
  const pts = (pr.duty_points ?? []).filter(
    (d) => d.name && finite(d.current_A) && finite(d.rpm) && d.rpm > 0);
  return pts.map((d, i) => ({ d, i }))
    .sort((a, b) => rank(a.d.name) - rank(b.d.name) || a.i - b.i)
    .map((x) => x.d);
}

/** One button per (configuration, mode) with data.  A configuration with no saved mode keeps
 *  its single configuration-level button (its legacy preset point), labelled by the name alone. */
export function buildModePresets(presets: readonly Preset[]): ModePreset[] {
  const out: ModePreset[] = [];
  for (const pr of presets) {
    const modes = modesOf(pr);
    if (!modes.length) {
      out.push({ ...pr, duty: pr.duty ?? '', key: modeKey(pr.config, pr.duty ?? ''),
                 label: pr.config, gamma_deg: null });
      continue;
    }
    for (const m of modes) {
      out.push({
        ...pr,
        duty: m.name,
        knobs: { ...pr.knobs, I_A: m.current_A, rpm: m.rpm },
        key: modeKey(pr.config, m.name),
        label: modeLabel(pr.config, m.name),
        gamma_deg: finite(m.gamma_deg) ? m.gamma_deg : null,
      });
    }
  }
  return out;
}

/** The "changed from ..." wording: the mode button the state started from. */
export function baseModeLabel(modePresets: readonly ModePreset[], config: string | null,
                              duty: string | null): string | null {
  if (!config) return null;
  const hit = modePresets.find((m) => m.config === config && (duty == null || m.duty === duty));
  return hit ? hit.label : config;
}

/** The mode of `config` whose point the knobs sit on (current and speed), or null.  Used when a
 *  freshly loaded machine picks its base configuration, so "changed from ..." names the mode. */
export function modeOfPoint(modePresets: readonly ModePreset[], config: string,
                            I_A: number, rpm: number): string | null {
  const near = (a: number | null, b: number) =>
    a != null && Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(b));
  const hit = modePresets.find((m) => m.config === config && m.duty
    && near(m.knobs.I_A, I_A) && near(m.knobs.rpm, rpm));
  return hit ? hit.duty : null;
}
