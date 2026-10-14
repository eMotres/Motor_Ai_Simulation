# Rules for contributing modules (people and their AI agents)

The [Manifesto](../MANIFESTO.md) says the portal is a core plus modules from
different people. This document is how that works in practice: you build a
module (a propeller, a gearbox, a belt, later a battery or a vehicle), we check it,
and the checked ones are connected to our motors. Technical contract:
[COMPONENT_SDK.md](COMPONENT_SDK.md) (drive-train components) and
[PLUGIN_SDK.md](PLUGIN_SDK.md) (geometry). General rules for any change:
[CONTRIBUTING.md](../CONTRIBUTING.md). Decisions the owner has taken are marked
**Decided**; what still waits for him is marked **Open** and collected in section 11.

The short version: you develop your module in your own pull request; we read it,
run the gates, compare it with data, and decide how far it is trusted. You are never
asked to sign away anything, and we never see or need our motors' design data in
your module, nor you in ours.

## 1. What a module is allowed to know

* A module couples to a motor **only through performance**: the motor passport,
  torque-speed-efficiency maps, thermal limits, mass and inertia, shaft interface
  dimensions (`MotorPerformanceIR`). Never stator or rotor geometry, windings,
  materials or AeroStator Core design data. Motor design data is private by default.
* A module describes itself in typed IR JSON and does not call the portal's internals.
  It imports only `motor_ai_sim.contracts` and `ModuleManifest`.
* Physics is honest: no smoothing, no silent clamping, loud validation, units
  explicit, provenance on every number. The conformance gates enforce what they can;
  the review enforces the rest.

## 2. Licence and certification

* **Apache-2.0, under the DCO.** Your contribution is licensed under the same
  terms as the project (Apache License section 5), you **keep your copyright**, and
  there is no contributor licence agreement. Every commit carries
  `Signed-off-by: Real Name <email>` (`git commit -s`), matching the commit author;
  the DCO check fails the pull request otherwise ([DCO.md](../DCO.md)).
* **Patents.** Section 3 of the Apache License applies to you as to everybody: each
  contributor grants a licence under the patent claims they could assert that are
  necessarily infringed by *their own contribution*. It does not reach your other
  work. The same section ends the licence of anyone who sues over the Work, which
  protects every user and contributor alike.
* Every new file starts with the SPDX header (`Apache-2.0`, "and contributors" or your
  own name). Keep the project's `NOTICE` and the licence with redistributions.
* If your employer has rights in what you write, make sure you may contribute it
  under Apache-2.0 before you sign off.
* Third-party data and code: only what you may redistribute under Apache-2.0
  (state the licence of every dataset in `validation/SOURCE.md`). No GPL or AGPL
  code in the module's process, no non-commercial or field-of-use licences (the rule
  in CONTRIBUTING.md, "Dependencies").

## 3. The status ladder

Every module carries a `status` in its manifest and in `GET /api/modules`. **The
maintainers of MOTRES d.o.o. decide the status**; a pull request, a module's own
manifest or a contributor cannot raise it. A status can be lowered at any time, with
the reason published, if a defect or a better measurement shows the module wrong.

| Status | What it means | What it requires | Who decides | Where it shows |
|---|---|---|---|---|
| `reference` | in-tree example for authors, outside the ladder | written and reviewed by maintainers; says "example values" in its summary | maintainers | core repo, listed in `/api/modules` |
| `community` | published, **clearly labelled unverified** | the pull-request requirements of section 5 (gate passed, tests, data and source stated, provenance file), a maintainer has read the code (section 7); nothing is claimed about accuracy | a maintainer, on review | `aerostator-modules`; listed as unverified; never in Configure |
| `verified` | our physics review **and** validation against measurements or published data | a `PROVENANCE.md` with a validation table (model vs reference, deviation, fitted vs held-out points); the data in `validation/`; a physics review by a second person who did not write it; the deviation inside the stated validity range; the module still passes the gates on the current core | maintainers, after the review is recorded in the pull request | listed as verified with its validity range and the largest deviation; usable in published studies |
| `integrated` | offered in **Configure** next to our motors | `verified`; Apache-2.0 and open; the integration work (UI, defaults, tests in the core) done and reviewed; a maintainer committed to keep it working across core releases | maintainers (the owner has the last word) | Configure |

Notes:

* A module with `validated_against = null` cannot be `verified`. A module whose
  deviation from its data exceeds what the review accepts stays `community` with the
  numbers printed.
* Only **open** modules reach `integrated` (section 8).
* Statuses are per module **version**; a new version starts again at `community`
  unless the maintainers confirm that the review still applies (a change of a
  parameter default does not need a new review; a change of a loss model does).
* Promotion is a pull request in `aerostator-modules` changing the `status` in
  `module.json` and the review record; it is made by a maintainer, not by the author.

## 4. Where modules live

**Decided (owner, 2026-10-14): a separate public repository `aerostator-modules`.**
(The repository itself is created by the owner; this PR describes it and adds
[module_template/](module_template/).)

* The core repository keeps the contracts, the conformance gates, the system solve,
  the registry and **one reference module**. Community modules do not go into a
  `contrib/` folder of the core.
* `aerostator-modules` holds one folder per module under `modules/`, each with
  `module.py`, `module.json`, `test_module.py`, `PROVENANCE.md`, `validation/`
  (data and `SOURCE.md`) and `requirements.lock`. Its CI installs the **pinned core
  release** and runs the conformance gate and the module tests on every pull request.
* Why separate: the core's history, release cadence and review load stay about the
  contracts and the physics; a module can have its own maintainers (CODEOWNERS for its
  folder) without commit rights to the core; the core never imports third-party code
  by accident; a module can be pinned to the core release it was validated on; and the
  trust ladder is visible in the repository itself instead of buried in a folder.
* A module reaches the portal by a maintainer **vendoring a reviewed release** into
  `modules/component_plugins/` of the core (or, once it exists, by the isolated runner
  loading the released package). The core's discovery still runs the conformance gate
  before it registers anything.

## 5. What a pull request must contain

**One module per pull request.** The CI of `aerostator-modules` rejects a pull
request that touches more than one module folder. In addition:

1. **Tests and a conformance pass.** `test_module.py` runs the provider gate
   (`assert_transmission_provider` / `assert_load_provider`) and at least one
   physical check against your data. Run only your own tests; do not run the core's
   full suite or any solver campaign.
2. **A validation dataset with its source** in `validation/` (`SOURCE.md`: origin,
   licence, method, conditions). Text files only (CSV with a unit header).
3. **Units and validity ranges.** Units are the family's (`SI-rpm-v1`); the map axes are
   your validity range; the notes say what the data cover (ambient, speeds, torques).
4. **`PROVENANCE.md`**: where every number comes from (measured, published, datasheet,
   assumed), and which points were used to fit and which were held out. Assumptions
   are allowed and are printed as assumptions.
5. **No network or filesystem access outside the sandbox at runtime.** `run()` is a
   pure description: no sockets, no reading or writing files, no environment lookups,
   no subprocesses, no randomness, no clock. Data are in the IR or in constants.
6. **No binary blobs**: no compiled files, archives, spreadsheets, images or model
   binaries; text only, so every line can be read. The CI checks the diff for binaries.
7. **Pinned dependencies with free licences only** in `requirements.lock`
   (MIT, BSD, Apache-2.0, ISC, PSF). The best dependency list is empty.
8. **The DCO sign-off** on every commit, and the PR description filled in
   (what the module models, which data, which validity range, how it was checked).
9. Requested status `community`. Ask for more in the description if you have the data.

## 6. AI agents are welcome contributors

Agents may write modules, tests and data tables. The rules exist so that a human
stays accountable and the numbers stay traceable.

* **Declare the agent and model in the pull request** (for example
  "written by Claude Sonnet 5.5 with the author's review") and in `module.json`
  (`agent`). Do not hide it; it is not a reason to reject a module.
* **A named human signs the DCO and is accountable.** The sign-off certifies that the
  human read and understood the change and may submit it. An agent is never the
  author of record and never signs.
* **Provenance of every number.** An agent must not invent a coefficient, a
  datasheet value or a citation. A number comes from a source the human can open, or
  is marked *assumed*. A reference that cannot be opened is a defect; the review
  checks a sample of the citations.
* **No secrets.** No keys, tokens, passwords or private URLs in code, tests, data or
  the PR text. An agent that is handed a credential must not write it anywhere.
* **Test against real performance data through the MCP server where access is
  granted.** The AeroStator MCP server serves motor *performance* (never geometry) to
  signed-in agents; where the owner of a machine has granted access, an agent may use
  it to size and check its module against that motor's real performance data. Access
  is per grant; an agent never asks for more than it needs and never stores the data in
  the repository (the data belong to the owner of the machine).
* An agent follows the same limits as anybody: one module per pull request, tests for
  its own module only, no sweeps or long computations on the maintainers' machines.
* The review treats agent-written text and numbers as unverified input, exactly like
  any other contribution.

## 7. Security and review before integration

* **Review before integration.** Components load in the API process. A maintainer
  reads the whole module (and its dependencies) before it is merged into the core's
  `component_plugins/`, and again when it changes. A module that cannot be read in
  one sitting is asked to be smaller.
* **Sandbox.** Unreviewed or third-party code must not run in the API process. The
  target is an isolated runner: a separate process per module with CPU, memory and wall
  time limits, no network, a read-only filesystem and only the IR on its input and
  output. **Status: not built yet** (the kernel isolates faults in-process only, see
  `modules/kernel.py`). Until it exists, `community` modules run on the author's and
  reviewers' machines, and only code a maintainer has read is merged into the hosted
  instance. The CI limits in `aerostator-modules` (time, no network) are a first line,
  not the sandbox.
* **Signed releases.** A module version that is vendored or loaded by the runner is a
  tagged, signed release (signed tag and signed artifact checksum); the portal records
  the version and checksum in the module's provenance stamp. **Open** (section 11): the
  signing mechanism.
* Report a vulnerability privately to the maintainers (GitHub private security
  advisory on the repository), not in a public issue.

## 8. Confidentiality

* A module never sees private motor geometry (section 1), and its IR has no field
  for it.
* **Decided (owner, 2026-10-14): a closed or proprietary module MAY connect through
  the API, but only open (Apache-2.0) modules are integrated into the project and
  offered in Configure.** Concretely:
  * A contributor may keep a private model outside every repository and run it
    as a service that speaks the contract: the portal sends a `MotorPerformanceIR`
    (performance only), the service returns an IR as JSON, and the answer must pass
    the same conformance gate.
  * Such a module is for the contributor's own use and for those they share it with.
    It has no status on the ladder, is not listed as verified, is not shipped, and is
    not offered in Configure; we do not host, mirror or review its code.
  * Nothing about a closed module changes its owner's rights or ours. Contributing an
    **open** module later is always possible and follows this document.
  * The remote-component endpoint is not built yet (COMPONENT_SDK.md, section 9).
* Validation data that belong to someone else (a customer's bench, a vendor's
  measurements) are used only with that party's written permission to publish them under
  Apache-2.0; otherwise send the comparison result and keep the data private, and the
  module stays `community`.

## 9. Review process and response times

1. Open an issue or a Discussion first for anything beyond a small module, so the
   approach and the contract fit are agreed before you write code.
2. Open the pull request in `aerostator-modules`. CI runs the gates. A red CI is
   fixed by the author.
3. A maintainer reads the code and the provenance. Findings are written in the pull
   request. The author answers; there is no hurry on the author's side.
4. For `verified`: the second person does the physics review against the data; the
   review is recorded in the pull request.
5. A maintainer merges, tags a release and sets the status.

**Response times (proposed, MOTRES is a small team and these are targets, not
guarantees; Open, section 11):** first human response within 10 working days; a
`community` review within 20 working days of a green CI; a `verified` review is
scheduled with the author and starts within 60 days; a security report is acknowledged
within 3 working days. A pull request with no author response for 60 days is closed
(it can be reopened).

## 10. Communication and conduct

* **GitHub Issues** for bugs, wrong physics, missing validation, contract problems;
  **GitHub Discussions** for ideas for modules, ports and formats; pull requests for
  code. Company contact: https://emotres.com.
* Be respectful and assume good faith. The project follows the
  [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
  A `CODE_OF_CONDUCT.md` with a conduct contact address has to be added to both
  repositories (**Open**, section 11: it needs an address the owner chooses).
* Disagreement about physics is settled by data, not by seniority.

## 11. Decisions

**Decided (owner, 2026-10-14)**

1. Community modules live in a **separate public repository `aerostator-modules`**,
   one folder per module, CI conformance against a pinned core release; the core keeps
   contracts, gates, the system solve and the reference module (section 4). The
   owner creates the repository; the template is [module_template/](module_template/).
2. **Closed or proprietary modules may connect through the API; only open
   (Apache-2.0) modules are integrated and offered in Configure** (section 8).

**Open (waiting for the owner)**

1. The response-time targets of section 9 (numbers proposed above).
2. The conduct contact address and `CODE_OF_CONDUCT.md` for both repositories.
3. The signing mechanism for module releases (signed git tags with a project key, or
   keyless signing in CI) and who holds the key.
4. When the isolated runner is built, and until then whether the hosted instance may
   list `community` modules at all or only `verified` ones (recommended: list
   `community` ones as unverified in the catalogue but never execute them on the hosted
   instance).
5. Whether a module's author may be made a maintainer of their own module folder
   (CODEOWNERS) after a verified release (recommended: yes).
