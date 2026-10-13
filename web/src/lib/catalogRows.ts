// Motors catalog = ONE ROW PER MOTOR (die) - owner 2026-10-13.  Its configurations are small
// buttons inside the row; outside, only each configuration's DEFAULT mode is shown, and the
// numbers (power, torque, mass, rpm) are that mode's.  The server states the default
// (`default_duty`, resolved by routes/family.config_default_duty); the same rule is repeated
// here only for trees from an older server that does not send it.
//
// Pure, no imports: web/src/lib/__tests__/catalogRows.test.mjs imports this file.

export interface RowDuty {
  name: string; mode?: string; power_kw?: number | null; torque_nm?: number | null;
  rpm?: number | null; current_arms?: number | null;
  result?: { mass_kg?: number | null } | null;
}
export interface RowCfg {
  name: string; default_duty?: string | null; duties: RowDuty[];
  card_date?: string | null; role?: string;
}
export interface RowDie { name: string; configs: RowCfg[]; }

/** Same rule as the server: the flag, else "rated", else "rated ...", else the first duty. */
export function defaultDutyOf(c: RowCfg): RowDuty | null {
  const ds = c.duties ?? [];
  if (!ds.length) return null;
  const by = (n?: string | null) => (n ? ds.find((d) => d.name === n) : undefined);
  return by(c.default_duty) ?? by('rated')
    ?? ds.find((d) => String(d.name).toLowerCase().startsWith('rated')) ?? ds[0];
}

export interface ConfigButton {
  config: string; duty: string | null;
  power_kw: number | null; torque_nm: number | null; mass_kg: number | null; rpm: number | null;
  card_date: string | null; modes: number;
}

const num = (v: unknown): number | null =>
  (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The die's configuration buttons, in the die's order, each with its default mode's numbers. */
export function configButtons(die: RowDie): ConfigButton[] {
  return (die.configs ?? []).map((c) => {
    const d = defaultDutyOf(c);
    return {
      config: c.name, duty: d ? d.name : null,
      power_kw: num(d?.power_kw), torque_nm: num(d?.torque_nm),
      mass_kg: num(d?.result?.mass_kg), rpm: num(d?.rpm),
      card_date: c.card_date ?? null, modes: (c.duties ?? []).length,
    };
  });
}

/** Which button is selected: a pick that still exists, else the loaded configuration of this
 *  die, else the first. */
export function selectedConfig(buttons: readonly ConfigButton[], active: string | null,
                               picked: string | null): string | null {
  const has = (n: string | null) => !!n && buttons.some((b) => b.config === n);
  if (has(picked)) return picked;
  if (has(active)) return active;
  return buttons[0]?.config ?? null;
}

const modeRank = (name: string): number => {
  const n = name.trim().toLowerCase();
  return n.startsWith('rated') ? 0 : n.startsWith('peak') ? 1 : 2;
};

/** The modes (duties) of one configuration for the header dropdown: rated, peak, then the
 *  rest in saved order.  Empty when the die or configuration is not in the tree. */
export function modeNames(dies: readonly RowDie[], die: string | null | undefined,
                          config: string | null | undefined): string[] {
  const c = dies.find((d) => d.name === die)?.configs.find((x) => x.name === config);
  const names = (c?.duties ?? []).map((d) => d.name);
  return names.map((n, i) => ({ n, i }))
    .sort((a, b) => modeRank(a.n) - modeRank(b.n) || a.i - b.i).map((x) => x.n);
}
