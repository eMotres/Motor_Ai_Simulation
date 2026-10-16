// Admin · configuration-level access — the pure logic behind the configuration rows of
// Admin · Motors access and the per-user config-grant picker (Users · Motors dialog).
// No runtime imports, so the node tests exercise it directly.
//
// Server contract (routes/admin.py, motor_access.py):
//   PUT /api/admin/dies/{die}/configs/{cfg}/access  {status: 'inherit'|'private'|'public'|'selected', clients?: string[]}
//   GET /api/admin/dies     rows carry `config_access: [{name, status, clients}]` (+ `private_configs: string[]`)
//   GET /api/admin/motors   rows carry `private_configs: string[]`
//   PUT /api/admin/users/{email}/motors             body `configs: ["<die>/<config>", ...]`
//                                                   (key absent = keep, [] = clear)

/** inherit = follows the die ("As motor"); selected = private + the accounts holding its grant. */
export type CfgStatus = 'inherit' | 'private' | 'public' | 'selected';

/** One configuration of a die row with its own access. */
export interface ConfigAccess {
  name: string;
  status: CfgStatus;
  /** accounts holding the "<die>/<config>" grant */
  clients: string[];
}

/** A die row as the two admin lists return it (only what this module reads). */
export interface DieWithConfigs {
  name: string;
  config_names?: string[];
  private_configs?: string[];
  config_access?: ConfigAccess[];
}

/** The grant key the server stores: "<die>/<config>". */
export const grantKey = (die: string, config: string): string => `${die}/${config}`;

/** Inverse of grantKey (the config is the part after the LAST slash). */
export const splitGrantKey = (key: string): { die: string; config: string } | null => {
  const i = key.lastIndexOf('/');
  if (i <= 0 || i === key.length - 1) return null;
  return { die: key.slice(0, i), config: key.slice(i + 1) };
};

/** Every configuration of the die with its access (an older server without `config_access`: derived from `private_configs`). */
export const configAccessOf = (die: DieWithConfigs): ConfigAccess[] => {
  if (die.config_access) return die.config_access;
  const priv = new Set(die.private_configs ?? []);
  return (die.config_names ?? []).map((name) => ({ name, status: priv.has(name) ? 'private' : 'inherit', clients: [] }));
};

/** The configurations open beyond their die: Public, or Selected clients. */
export const openConfigs = (die: DieWithConfigs): ConfigAccess[] =>
  configAccessOf(die).filter((c) => c.status === 'public' || c.status === 'selected');

/** The PUT body for one configuration (clients only for Selected, trimmed, lower-cased, unique). */
export const configPutBody = (status: CfgStatus, clients: Iterable<string>) =>
  status === 'selected'
    ? { status, clients: [...new Set([...clients].map((c) => c.trim().toLowerCase()).filter(Boolean))].sort() }
    : { status };

/** A Selected configuration needs at least one client; every other status saves as is. */
export const canSaveConfig = (status: CfgStatus, clients: readonly string[]): boolean =>
  status !== 'selected' || clients.some((c) => c.trim() !== '');

/** A copy of `dies` with one configuration's access replaced (optimistic UI update); `private_configs` follows. */
export const withConfigAccess = <T extends DieWithConfigs>(
  dies: T[], die: string, config: string, status: CfgStatus, clients: string[],
): T[] =>
  dies.map((d) => {
    if (d.name !== die) return d;
    const access = configAccessOf(d).map((c) => (c.name === config ? { name: config, status, clients: [...clients] } : c));
    const rest = (d.private_configs ?? []).filter((c) => c !== config);
    const priv = status === 'private' || status === 'selected' ? [...rest, config] : rest;
    return { ...d, config_access: access, private_configs: priv.sort() };
  });

/** One line of the config-grant picker. */
export interface GrantRow {
  key: string;
  die: string;
  config: string;
  /** false = the account holds the grant but the configuration is not private any more (or gone): shown so it can be revoked */
  isPrivate: boolean;
  /** false = no such die / configuration in the catalog any more */
  exists: boolean;
}

/**
 * The rows of the picker: every PRIVATE configuration of the catalog, plus every
 * grant the account already holds (even for a configuration that is no longer
 * private or has disappeared) so nothing it holds is invisible and nothing cannot be revoked.
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
