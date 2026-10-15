// Admin · configuration-level visibility — the pure logic behind the "Private" toggle
// (Motors access drawer) and the per-user config-grant picker (Users · Motors dialog).
// No runtime imports, so the node tests exercise it directly.
//
// Server contract (routes/admin.py, motor_access.py):
//   PUT /api/admin/dies/{die}/configs/{cfg}/access  {visibility: 'private' | 'public'}
//   GET /api/admin/dies, /api/admin/motors          rows carry `private_configs: string[]`
//   PUT /api/admin/users/{email}/motors             body `configs: ["<die>/<config>", ...]`
//                                                   (key absent = keep, [] = clear)

export type CfgVisibility = 'private' | 'public';

/** A die row as the two admin lists return it (only what this module reads). */
export interface DieWithConfigs {
  name: string;
  config_names?: string[];
  private_configs?: string[];
}

/** The grant key the server stores: "<die>/<config>". */
export const grantKey = (die: string, config: string): string => `${die}/${config}`;

/** Inverse of grantKey (the config is the part after the LAST slash). */
export const splitGrantKey = (key: string): { die: string; config: string } | null => {
  const i = key.lastIndexOf('/');
  if (i <= 0 || i === key.length - 1) return null;
  return { die: key.slice(0, i), config: key.slice(i + 1) };
};

/** True when the die row lists this configuration as private. */
export const isPrivateConfig = (die: DieWithConfigs, config: string): boolean =>
  (die.private_configs ?? []).includes(config);

/** A copy of `dies` with one configuration switched private / public (optimistic UI update). */
export const withConfigVisibility = <T extends DieWithConfigs>(
  dies: T[], die: string, config: string, visibility: CfgVisibility,
): T[] =>
  dies.map((d) => {
    if (d.name !== die) return d;
    const rest = (d.private_configs ?? []).filter((c) => c !== config);
    const next = visibility === 'private' ? [...rest, config] : rest;
    return { ...d, private_configs: next.sort() };
  });

/** One line of the config-grant picker. */
export interface GrantRow {
  key: string;
  die: string;
  config: string;
  /** false = the account holds the grant but the configuration is public now (or gone): shown so it can be revoked */
  isPrivate: boolean;
  /** false = no such die / configuration in the catalog any more */
  exists: boolean;
}

/**
 * The rows of the picker: every PRIVATE configuration of the catalog, plus every
 * grant the account already holds (even for a configuration that has since turned
 * public or disappeared) so nothing it holds is invisible and nothing cannot be revoked.
 * Sorted by die, then configuration.
 */
export const grantRows = (dies: DieWithConfigs[] | null, granted: Iterable<string>): GrantRow[] => {
  const byKey = new Map<string, GrantRow>();
  for (const d of dies ?? []) {
    for (const c of d.private_configs ?? []) {
      const key = grantKey(d.name, c);
      byKey.set(key, { key, die: d.name, config: c, isPrivate: true, exists: true });
    }
  }
  for (const key of granted) {
    if (byKey.has(key)) continue;
    const parts = splitGrantKey(key);
    if (!parts) continue;
    const d = (dies ?? []).find((x) => x.name === parts.die);
    const exists = !!d && (d.config_names ?? []).includes(parts.config);
    byKey.set(key, { key, ...parts, isPrivate: false, exists });
  }
  return [...byKey.values()].sort((a, b) =>
    a.die === b.die ? a.config.localeCompare(b.config) : a.die.localeCompare(b.die));
};

/** The set with `key` toggled (a new Set; the argument is never mutated). */
export const toggleKey = (set: ReadonlySet<string>, key: string): Set<string> => {
  const n = new Set(set);
  if (n.has(key)) n.delete(key); else n.add(key);
  return n;
};

/** The `configs` value of the PUT body: sorted, unique, only well-formed keys. */
export const configsBody = (granted: Iterable<string>): string[] =>
  [...new Set([...granted].filter((k) => splitGrantKey(k) !== null))].sort();

/** The whole PUT /users/{email}/motors body. `configs` is always sent, so the dialog's picture is the stored one. */
export const motorsPutBody = (args: {
  all: boolean; dies: Iterable<string>; configs: Iterable<string>;
  defaultMotor: { die: string; config: string } | null;
}) => ({
  all: args.all,
  dies: [...args.dies],
  configs: configsBody(args.configs),
  default: args.defaultMotor,
});

/** Count of private configurations (for a chip on the die row). */
export const privateCount = (die: DieWithConfigs): number => (die.private_configs ?? []).length;
