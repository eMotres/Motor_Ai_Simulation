# PWM lives in the Controller (2026-09-24)

Owner, 2026-09-24, on a screenshot of the Controller tab's greyed "Carrier
20,000 Hz" placeholder: *«Это значение нужно задавать в контроллере; PWM нужно
выкинуть из Electromagnetic.»*

The PWM drive (carrier, V_dc source, dead time, modulation) is now defined in
the **Controller tab only**. The Electromagnetic/Simulation tab keeps **Sine
current** and **Target T / P** (plus the two non-PWM current sources, BLDC 120°
and Custom I). Branch `feat/pwm-in-controller`. This follows on from
`CONTROLLER_MODULE_2026-09-22.md` §7 and §7a, which covered the Stage 2 coupling
and the first redirect of the PWM button.

## 1 · One resolution: `inverter/drive_source.py`

Every consumer resolves the carrier and the DC link through this module. Each
answer carries `origin` (`request` | `controller` | `legacy` | `default`) and
`source` (the words that records and tooltips print).

**Carrier:**

1. the request. This is an explicit API field: a carrier study,
   `inverter.f_carrier_hz` of a coupled `alt_carrier` run, or the Controller
   tab's own Solve body.
2. the Controller block sent by reference (`body.controller.f_carrier_hz`,
   which the Coupled panel sends).
3. **the saved Controller settings** (`controller.f_carrier_hz`, written by
   `PATCH /api/family/config/{die}/{cfg}/controller`).
4. **migration (legacy)**, in this order:
   - the duty's stored PWM record (`inverter.f_carrier_hz`, where the caller
     holds it);
   - the duty's saved `mesh['sim.fSwitch']`;
   - the first other duty of the configuration that has one;
   - the configuration's `simulation.f_switch`;
   - the process-global `simulation.f_switch`. This tier is read only when the
     machine named is the one loaded, or when nothing is catalogued.
5. `DEFAULT_CARRIER_HZ = 20 kHz`. This tier is used only where a carrier is
   required: a bridge run, a controller solve or the PWM calculator. The report
   and the mechanical excitation tables pass `default=False`. When nothing names
   a carrier they draw no carrier line, rather than a made-up one.

**DC link:**

1. the request;
2. the Controller's manual `v_dc_V`;
3. the configuration's battery (nominal, then the min/max midpoint, then the
   minimum);
4. the loaded machine's battery, for a machine with no catalog document;
5. the duty's legacy PWM bus, only when there is no battery at all.

This changes the Controller solve in two ways:

- The saved Controller carrier now outranks the duty's PWM record. Before this
  change the record won.
- The battery now outranks a record's PWM bus.

On L155 and L180 both values are the same (24 kHz, 750.4 V), so no stored
number moves.

## 2 · Migration rule

- **No carrier saved** (no `controller` block, or `f_carrier_hz: null`, which is
  what every block saved before today holds):
  - the resolver answers with the legacy Simulation-tab carrier and
    `origin: legacy`;
  - the Controller tab writes that value into the Carrier field as a real,
    editable number, with one line under it: *"from the old Simulation-tab PWM —
    Save to keep"*;
  - if there is no legacy carrier either, the line reads *"default — Save to
    keep"* and the value is 20 kHz.
- The value becomes the Controller's own on the first save:
  - Controller → Save settings;
  - "Save to duty" (the `ctrl.settings` mirror, `ActiveFamilyStrip`);
  - the Coupled panel's auto-save when Drive = inverter.
- After that save, `origin` is `controller` and the legacy keys are never read
  again for this configuration.
- Nothing is written on a GET. The migration is persisted only by a save the
  owner makes (the "no silent state mutation" rule).
- The duty-settings snapshot keeps `sim.vBus/fSwitch/fSwGroup/fSwCustom` in
  `DUTY_OP_KEYS` as legacy pass-through. No panel writes them any more, but a
  duty save must not drop the value the migration reads.

## 3 · What moved where

| was (Simulation tab) | now |
|---|---|
| "PWM inverter" button | removed. A persisted or restored `pwm_voltage` run shows one line: *"Stored PWM run — carrier … and DC link from Controller"*, an **Open** button and a HelpTip |
| V bus field + battery prefill (`battery.busSeed`) | removed. The Controller's DC link is blank = the battery, nominal, or a typed override |
| f switch picker (SiC / IGBT / ESC classes, custom) | removed. The Controller's **Carrier** is a normal field saved with the controller |
| `sim.vBus` / `sim.fSwitch` PATCHed into `simulation:` | no longer sent. The backend still accepts them and logs a deprecation |
| step rule, modulation gate, charging row, cost estimate | read the Controller's resolved point (`GET /api/controller/point`, refreshed on `family-changed`, `sim-design-applied` and the new `controller-settings-saved` event) |
| "Generate from PWM model" (Custom I) | sends no `v_bus` / `f_switch`; `/api/simulation/pwm_waveform` resolves both from the Controller |
| `emRunPayload.driveFields` | `pwm_voltage` sends no `v_bus` / `f_switch` |

## 4 · Consumers of the carrier, and their new source

Found with `grep f_switch|f_carrier|carrier_hz|pwm_freq|fSwitch|vBus` over
`src/` and `web/src`.

### Backend

| consumer | new source |
|---|---|
| `routes/controller.py::_build_request` (`POST /solve`, `GET /point`) | `drive_source.resolve_carrier` / `resolve_v_dc`; `origins` in the response, `carrier_origin` / `v_dc_origin` on `/point` |
| `routes/controller.py::_duty_defaults` | still reads the record's `inverter.f_carrier_hz` / `v_dc_V`, but only as the migration tier |
| `routes/coupled.py::_inverter_settings` (drive `pwm` and `inverter`) | `_drive_carrier(default=True)` and `_drive_v_dc`. An explicit `inverter.f_carrier_hz` / `v_dc_V` still wins (alt-carrier studies). The record keeps `inverter.carrier_origin` |
| `routes/coupled.py::_effective_f_switch` (modal and critical-speed excitation tables of a coupled run) | `_drive_carrier(default=False)`. A body `f_switch(_hz)` is accepted below the Controller and logged as deprecated |
| `routes/coupled.py::_controller_settings` | new tier: the saved Controller block (device, topology, N, dead time, V_GS off, R_G, cooling), between the request and the duty's last Stage-1 solve |
| `_pwm_run_kwargs`, `_ControllerLoop.snap_excitation`, `_snap_excitation` | unchanged; they read the resolved inverter block |
| thermal PWM loss map (the coupled `_em_map` of a `pwm` / `inverter` pass) | follows the inverter block, so it follows the Controller |
| `routes/simulation.py::get_fem_transient`, drive `pwm_voltage` | a missing `v_bus` / `f_switch` is filled from the Controller (a `battery` payload is the last V_dc fallback), recorded as `pwm.drive_sources`. A sent value is obeyed: coupled, passport, restore |
| `routes/simulation.py` `GET /pwm_waveform` | `v_bus` / `f_switch` are optional and default to the Controller; the response carries `drive_sources` |
| `routes/simulation.py` `PATCH /config` | `v_bus` / `f_switch` accepted, with a deprecation log |
| `modules/solvers.py::EmTransientSolver` (`POST /api/kernel/run`) | `drive_source.pwm_request_fields`: on `pwm_voltage` the Controller's saved values win over sent ones, with a deprecation log. Restore and ledger probes pass through |
| `routes/mechanical.py::_f_switch(None)` (Mechanical tab modes and critical speeds) | `resolve_carrier_for(default=False)`. An explicit value (0 included) still wins |
| `report.py::_warning_context` (ring mode vs carrier) | `carrier_hz = duty_carrier_hz(cfg_doc, duty)`: the Controller carrier, else the legacy `sim.fSwitch`. A stored PWM record's own effective carrier still wins, and the captions now say "the Controller asked for" / "the Controller's PWM carrier" |
| `report.py::gather_report_data["modes_f_switch_hz"]` → `report_docx.py` modal table | `duty_carrier_hz` |
| `report.py` PWM-influence tables, carrier rows, alt-carrier columns, Fig. 6 annotation | unchanged; each prints the record's own `inverter.f_carrier_hz` / `f_carrier_eff_hz` |
| `datasheet.py::_ctrl` rows | unchanged; they read the controller record, whose carrier now comes from the Controller |
| `optimization/refine_proc.py` (`OPT_ALLOW_PWM=1`) | `drive_source`, with the retired `simulation.*` as fallback |
| `passport_pwm.py` / `passport.py` | unchanged. The carrier is the study's measurement axis and is passed explicitly. The other agent is editing `passport_pwm.py` |
| `duty_results.py`, `duty_refile.py`, `routes/family.py::_run_rows` | storage and display of a record's own carrier; unchanged |
| `inverter/losses.py`, `waveforms.py`, `coupling.py`, `simulation/pwm.py`, `excitation.py`, `fem_solver_2d.py`, `mechanical/modal.py`, `rotordynamics.py` | consume the value handed in; not touched |

### Web

| consumer | new source |
|---|---|
| `ControllerPanel.tsx` Carrier | a real value: saved, else `carrierPrefill` from `/point`, with its origin line. No placeholder |
| `ControllerPanel.tsx` DC link | blank = the battery; placeholder "battery N"; HelpTip says so |
| `controllerApi.ts` | `ResolvedPoint.carrier_origin` / `v_dc_origin`; `carrierPrefill`, `carrierOriginLine` |
| `SimulationPanel.tsx` | PWM controls and state removed; `ctrlDrive` from `/point` |
| `emRunPayload.ts`, `TransientCharts.tsx`, `PhysicsDashboard.tsx` | `vBus` / `fSwitch` removed |
| `coupledApi.ts::runCoupled` | unchanged. `body.controller` (by reference) carries `f_carrier_hz`, which is tier 2 above |
| `dutySettings.ts` | legacy keys kept (see §2) |
| `dutyRuns.ts`, `StoredRunSelector.tsx`, `FamilyCatalog.tsx`, `TransientCharts` result card, `compareRows.ts` | display a record's own carrier; unchanged |
| `ConfiguratorPanel.tsx` / `motorScaling.ts` | the passport's measured PWM deltas with a what-if carrier knob (client Configure). Unchanged; see §6 |

## 5 · Backward compatibility

- **Old sessions and scripts are accepted.** They may still send `v_bus` /
  `f_switch` to `PATCH /api/simulation/config`, to `POST /api/kernel/run` with
  `drive: pwm_voltage`, or to `POST /api/coupled/run`. Where the Controller has
  a saved value, that value is used, and a `DEPRECATED:` warning is logged once
  per distinct message.
- **Stored records keep loading and rendering.** This covers `drive: "pwm"`
  records, their `inverter` blocks, `reference_sine` and `alt_carriers`. The
  report judges each one against the record's own effective carrier.
- **A stored `pwm_voltage` run is still accepted.** The panel still restores it
  and can re-run it, now with the Controller's carrier and bus.

## 6 · Open, for the owner

1. **BLDC 120° and Custom I stay on the Simulation tab.** They are current
   sources, not PWM. The brief's "Sine current + Target T/P only" could be read
   as removing them too, so the owner should confirm.
2. **The Configure (client) carrier knob is still separate.** It scales the
   passport's measured PWM deltas and defaults to the passport's reference
   carrier, not to the machine's Controller carrier. Should it default to the
   Controller's?
3. **The DC link field still shows a placeholder.** It reads "battery 750",
   which is the configuration's own pack, not another tab's value. If a real
   value is wanted there too, the field needs a "battery / manual" selector so
   that saving does not freeze a manual override.
