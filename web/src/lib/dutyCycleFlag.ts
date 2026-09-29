/**
 * THE DUTY-CYCLE FEATURE FLAG — off by default.
 *
 * Owner, 2026-09-17: «давай пока уберём duty cycle из Thermal, оставим только
 * стандартный каплинг» — for now the Thermal tab shows the cooling and the
 * coupled loop, and nothing about S2/S3 cycles.  It is a "for now": not one line
 * of the cycle was deleted, it is all behind this one switch, so the feature
 * comes back with a build flag and no code archaeology.
 *
 * ONE MECHANISM, NOT A THIRD.  The project has no feature-flag service and no
 * runtime flag list on `/api/version` or `/api/modules` (both were read before
 * this file was written), so the flag is what Vite already gives every build:
 * `VITE_DUTY_CYCLE=1`.  Its backend twin is the env var `DUTY_CYCLE_ENABLED`,
 * read by `coupled_duty_cycle.enabled()`; the two are independent on purpose —
 * turning the UI on against a server that ignores cycles must be possible while
 * the feature is being brought back, and each side says what IT does.
 *
 * WHAT THE FLAG HIDES when it is off:
 *   • the Duty cycle block on the Thermal tab (`ThermalPanel` does not mount
 *     `DutyCycleEditor`, so no rows, no lines, no tooltips, no RUN CYCLE);
 *   • the S1/S3 chip in the family catalog (`gatedDutyCycleChip`);
 *   • every ED term a coupled answer could print — the summary line, the run
 *     notice and the tooltip rows (`components/simulation/coupledApi`).
 *
 * WHAT IT DOES NOT TOUCH: the stored `duty_cycle:` blocks on the duties, the
 * standalone `POST /api/thermal/duty_cycle` tool, the robotics cooling mode and
 * every duty-cycle record already filed.  Hiding a feature is not deleting the
 * evidence it produced.
 *
 * Dependency-free apart from `import.meta.env`, and the reading of it is a pure
 * function so node's own type stripping can test it (the repo's convention for
 * the modules beside it — see `dutyCycleRegime.ts`).
 */

/** PURE.  What counts as "on" in a build env: `1`, `true`, `on`, `yes`.
 *
 *  Anything else — an unset variable, an empty string, the `0` a deployment
 *  writes to turn it off — is OFF.  A flag that is on by accident is worse than
 *  one that will not come on: this one hides a feature the owner asked to have
 *  out of the way. */
export function dutyCycleEnabled(raw: unknown): boolean {
  const v = String(raw ?? '').trim().toLowerCase();
  return v === '1' || v === 'true' || v === 'on' || v === 'yes';
}

/** The build's answer, resolved once.  `import.meta.env` is replaced by Vite at
 *  build time and is simply absent under plain node, which is exactly the
 *  default this flag wants. */
export const DUTY_CYCLE_ENABLED: boolean =
  dutyCycleEnabled(import.meta.env?.VITE_DUTY_CYCLE);

/** PURE.  A duty's cycle chip, gated: `null` whenever the feature is off.
 *
 *  `dutyCycleChip` itself stays exactly what it was — it is copied verbatim
 *  into its own test and is about the BLOCK, not about whether this build shows
 *  cycles — so the gate is a wrapper the catalog calls instead. */
export function gatedDutyCycleChip(chip: string | null | undefined,
                                   enabled: boolean = DUTY_CYCLE_ENABLED):
    string | null {
  return enabled ? (chip ?? null) : null;
}
