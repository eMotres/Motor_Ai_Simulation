// The magnet's TEMPERATURE, as this project encodes it: one library record per
// (grade, operating temperature) — `F52SH_80C`, `F52SH_120C`, `N52UH_150C`
// (one rule since 2026-09-08: `<grade>_<T>C`, no maker prefix).
// The solver does not temperature-correct magnets (materials_library.yaml says so
// in as many words); the record IS the temperature.
//
// A machine's magnet is a MACHINE property (the materials assignment, shared
// config / client overlay).  Its temperature is not: a continuous duty runs the
// rotor at ~80 °C while the peak duty of the same motor saturates it at 120–150.
// So a duty may pick ANOTHER TEMPERATURE RECORD OF THE SAME GRADE, and that
// choice travels per-request (?mat=) — it never rewrites the assignment.
//
// This module is the one place that knows the naming convention, so the picker,
// the ?mat= getter and the badge cannot disagree about what a duty is solving.

/** `F52SH_120C` → { grade: 'F52SH', tempC: 120 }.  A name that does not carry a
 *  `_<T>C` suffix (`Fe16N2_lab_best`) has no temperature family and therefore no
 *  variants — it gets no picker at all. */
export function parseMagnetName(name: string | null | undefined,
                                ): { grade: string; tempC: number } | null {
  const m = /^(.+)_(\d+(?:\.\d+)?)C$/.exec(String(name ?? ''));
  if (!m) return null;
  const t = Number(m[2]);
  return Number.isFinite(t) ? { grade: m[1], tempC: t } : null;
}

/** The grade family a record belongs to — its name minus the `_<T>C` suffix. */
export function magnetFamily(name: string | null | undefined): string | null {
  return parseMagnetName(name)?.grade ?? null;
}

/** Every library record of the SAME grade as `machine`, coldest first (the
 *  machine's own record included).  Fewer than two → there is nothing to pick
 *  between and the caller shows the plain badge. */
export function magnetVariants(machine: string | null | undefined,
                               libraryNames: readonly string[]): string[] {
  const fam = magnetFamily(machine);
  if (!fam) return [];
  return libraryNames
    .filter(n => magnetFamily(n) === fam)
    .sort((a, b) => (parseMagnetName(a)!.tempC - parseMagnetName(b)!.tempC));
}

/** What the solve must actually use.
 *
 *  `stored` is the ACTIVE DUTY's remembered temperature record (or null/'' for
 *  "the machine's own").  It is honoured only when it is a real library record
 *  of the SAME grade as the machine's magnet — so changing the machine's magnet
 *  in Materials (a genuine grade change) always wins over a duty's stale
 *  temperature pick, and a record that has since left the library is ignored
 *  instead of being sent to the solver.
 *
 *  Returns the machine's own name when there is no usable override, which is
 *  what makes the ?mat= payload byte-identical to today in that case. */
export function effectiveMagnet(machine: string | null | undefined,
                                stored: string | null | undefined,
                                libraryNames: readonly string[]): string {
  const base = String(machine ?? '');
  const want = String(stored ?? '');
  if (!want || want === base) return base;
  if (!libraryNames.includes(want)) return base;
  const fam = magnetFamily(base);
  return (fam && magnetFamily(want) === fam) ? want : base;
}
