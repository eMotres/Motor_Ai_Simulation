// The magnet's TEMPERATURE, as this project encodes it.
//
// Since 2026-10-30 (owner: «в каталоге магниты все с одинаковой температурой
// 20 °C, а в моделировании просто вводить нужную температуру») the library holds
// ONE 20 °C card per grade — `N52UH`, `F52SH` — and the temperature is the
// Simulation tab's magnet temperature (`sim.magnetTempC`, saved with the duty),
// capped at the grade's maximum working temperature.  Until then it held one
// record per (grade, temperature) — `F52SH_80C`, `F52SH_120C`, `N52UH_150C` —
// and a duty picked ANOTHER TEMPERATURE RECORD OF THE SAME GRADE through ?mat=.
// Those former names still resolve on the server (the grade derived at that
// temperature); this module is the one place the web knows them, so a pick
// remembered in this browser can be turned into grade + temperature.

/** `F52SH_120C` → { grade: 'F52SH', tempC: 120 }.  A name that does not carry a
 *  `_<T>C` suffix (`F52SH`, `Fe16N2_lab_best`) → null. */
export function parseMagnetName(name: string | null | undefined,
                                ): { grade: string; tempC: number } | null {
  const m = /^(.+)_(\d+(?:\.\d+)?)C$/.exec(String(name ?? ''));
  if (!m) return null;
  const t = Number(m[2]);
  return Number.isFinite(t) ? { grade: m[1], tempC: t } : null;
}

/** The grade a card belongs to: a former `<grade>_<T>C` name's grade, else the
 *  name itself (a 20 °C grade card is its own family). */
export function magnetFamily(name: string | null | undefined): string | null {
  const n = String(name ?? '');
  if (!n) return null;
  return parseMagnetName(n)?.grade ?? n;
}

/** A FORMER per-temperature pick that is no longer a library card, but whose
 *  grade is: `F52SH_80C` with `F52SH` in the library → { grade, tempC: 80 }.
 *  Null for anything else (a current card, an unknown name). */
export function legacyMagnetPick(name: string | null | undefined,
                                 libraryNames: readonly string[],
                                 ): { grade: string; tempC: number } | null {
  const p = parseMagnetName(name);
  if (!p || libraryNames.includes(String(name))) return null;
  return libraryNames.includes(p.grade) ? p : null;
}

/** Every library card of the SAME grade as `machine` (the machine's own card
 *  included), coldest first.  One card per grade since 2026-10-30, so this is
 *  at most one entry and the caller shows the plain badge; kept for a library
 *  (a private overlay) that still holds several. */
export function magnetVariants(machine: string | null | undefined,
                               libraryNames: readonly string[]): string[] {
  const fam = magnetFamily(machine);
  if (!fam) return [];
  const t = (n: string) => parseMagnetName(n)?.tempC ?? 20;
  return libraryNames
    .filter(n => magnetFamily(n) === fam)
    .sort((a, b) => t(a) - t(b));
}

/** What the solve must actually use.
 *
 *  `stored` is the ACTIVE DUTY's remembered card (or null/'' for "the
 *  machine's own").  It is honoured only when it is a real library card of the
 *  SAME grade as the machine's magnet — so changing the machine's magnet in
 *  Materials (a genuine grade change) always wins over a duty's stale pick, and
 *  a card that has since left the library (a former `_<T>C` record) is ignored
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
