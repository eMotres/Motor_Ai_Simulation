# Open and private data sets

## Boundary: what may live in git

| Store | Contents |
|---|---|
| Public repo `eMotres/Motor_Ai_Simulation` (`data/open/dies/`) | made-up demo dies and MOTRES reference data we chose to publish |
| Private repo `eMotres/motor-ai-sim-private` (`config/dies/`) | curated MOTRES reference data only: our own products, validation data |
| Application storage (workspaces, shared catalog, runs on the server) | **all customer data**: customer dies, configurations, duties, results |

Customer projects and their results never go to either git repository. This
is enforced, not just a convention:

- A die can be moved into or between the repositories only when its
  `die.yaml` declares `owner_org: MOTRES` (or another organisation listed in
  `DATA_REFERENCE_ORGS`). No declaration = blocked. Another owner = blocked.
- A die, configuration, duty or result tagged `customer`, marked
  `confidential: customer`, or naming a `customer:` is blocked for **both**
  repositories.
- Workspace and shared-catalog dies cannot be moved at all: only dies already
  in one of the two checkouts are movable.
- `nda` / `confidential` tags block publication (open). An NDA-covered MOTRES
  die may stay in the private repository.

The data set is **not** the same thing as *visibility* (Admin → Motors access:
Private / Public / Selected clients). Visibility decides who can see a die on
this server; the data set decides which git repository holds its files.

## Precedence: one rule for every catalog kind

Dies, materials, device cards and bearings follow the same rule
(`motor_ai_sim.catalog_sources`):

1. Each item has an ID (die name, `<category>/<material>`, device part,
   bearing name) and comes from one named source (`open`, `private`,
   `shared`, `public`, `global`).
2. One ID must come from **exactly one** source. The same ID in two sources
   is a **clash**: a loud error, and the item does not resolve. A calculation
   that needs it fails and names the clash. There is no silent "private wins"
   or "public wins".
3. The only way through a clash is an explicit override record in
   `<config dir>/catalog_overrides.yaml` (or the file named by
   `MOTOR_AI_SIM_CATALOG_OVERRIDES`):

   ```yaml
   overrides:
     - {kind: devices, id: IMZA65R020M2H, source: private, by: admin, reason: "vendor erratum"}
   ```

   An unreadable override file counts as no overrides, so the clash stays an
   error.
4. Every result records which items it used, as ID + source + revision
   (sha256 of the content): `catalog_refs` in each duty record, and
   `provenance.catalog_ref` in each controller result. A same-named item from
   another source cannot silently swap the engineering data behind a stored
   number.

Where each kind applies the rule:

| Kind | Sources | Notes |
|---|---|---|
| dies | `open`, `private`, and on a layered server with `MOTOR_AI_SIM_DATA_SOURCES=1` also `shared` | a user's workspace copy is the user's own edit (copy-on-write), not a catalog source; its ref says `source: workspace` |
| devices | `public` (or `shared` when `shared/devices` exists), `private` overlay | a clashing part is left out of the catalog and `get_device` raises |
| materials | one library file (`shared` copy, else the `public` repo file) | the admin global layer editing a built-in counts as an explicit override (admin action, recorded `source: global`); no private overlay is read |
| bearings | one library file (`shared` copy, else `public`) | no private overlay is read |

## Loader

`motor_ai_sim.data_sources` reads both checkouts and merges them by die name
under the rule above. The Admin table marks a clashing die with ⚠ and the
error.

- **Multi-user (server):** the source layers are active only with
  `MOTOR_AI_SIM_DATA_SOURCES=1`.
- **Single-user (workstation):** active when that variable is set, or when
  `config/dies` holds no die (a fresh public clone).

## Moving a die (Admin → Motors access → die drawer → Source)

**Publication is the first push to the public repository.** A branch in a
public repository is public the moment it is pushed. The pull request is only
for review and merge into `main`; it is not a confidentiality gate. So every
check and the admin's confirmation happen before that push.

1. **Preview.** `GET /api/admin/dies/{die}/source/preview?target=open|private`
   validates the die and returns the export (file list with sha256 hashes),
   geometry, configurations, materials, devices, results, blockers, the
   warning, and a `snapshot` id over all of it.
2. **Confirm.** `POST /api/admin/dies/{die}/source` with
   `{"target": ..., "confirm": true, "snapshot": "<id from the preview>"}`.
   If the die changed since the preview, the snapshot does not match and the
   move is refused. For a publish, confirming pushes to the public repository
   immediately. The dialog says so, and its button is "Publish now".
3. **Journal, then act.** The move writes an intent record to
   `<config dir>/data_moves.json` before any git action: id, die, source,
   target, branch, snapshot, file list with hashes, planned steps. Then it runs
   the steps and records each one:

   `commit:private` → `commit:open` → `push:private` → `push:open` →
   `pr:private` → `pr:open`

   Local commits come first. The private repository always goes before the
   public one. The commit copies exactly the confirmed files and re-checks
   every hash. Every step is idempotent: on a retry it recognises a commit
   already on the branch, a branch already on the remote at the recorded
   commit, or a PR already opened. Moves are serialized across threads and
   processes with a lock file.
4. **Failure → incomplete.** If a step fails, the move becomes `incomplete`,
   with the failed step and the error. The admin drawer shows **Resume**
   (`POST /api/admin/dies/source/moves/{id}/resume`), which re-runs from the
   first unfinished step. It also shows **Rollback**
   (`POST .../moves/{id}/rollback` with `{"confirm": true}`), which deletes
   the pushed branches, closes the PRs and drops the local branches. Rollback
   is allowed until a PR is merged. It cannot unpublish a branch that was
   already public.
5. **Complete.** After a human merges both PRs,
   `POST /api/admin/dies/source/reconcile` (the "Check merged PRs" button) runs
   `merged` → `ff:private` → `ff:open` → `verify`. It fast-forwards each
   server checkout, which must be on its base branch, and checks the files on
   disk against the journal hashes. A checkout that cannot be updated leaves
   the move `incomplete`, never `done`.

`GET /api/admin/dies/source/moves` lists the journal. An unreadable journal
refuses every move until it is fixed by hand.

### Validation (fail closed)

Any of these is a blocker, and the move is refused with each error listed:

- a file that cannot be read or parsed: `die.yaml`, a configuration, a result
  archive, the public materials library;
- the wrong shape: not a mapping, `duties` not a list, `materials` not a
  mapping;
- a file outside the export whitelist: `die.yaml`, `<config>.yaml`,
  `runs/<config>/*.json.gz`, `runs/<config>/<duty>/fields/*.npz`. Backups,
  notes, attachments, PDFs and anything else must be removed first;
- a symlink or a path that escapes the die folder;
- a file over its size limit (YAML 1–2 MiB, results 20 MiB, fields 50 MiB,
  200 MiB in total, 400 files), or an archive that unpacks too large or holds
  pickled objects;
- a secret (private key, GitHub/AWS/Google/API tokens, a `password:`/`token:`
  field) or an e-mail address anywhere in the content;
- a customer tag, a missing or foreign `owner_org` (see Boundary);
- for publication only: an NDA/confidential tag, or materials and devices
  missing from the public library.

**Audit:** every intent, request, refusal, failure, resume, rollback and
completion is written to `<config dir>/data_moves_audit.jsonl` and to the admin
event log (`die_source_move`).

## Environment (`/etc/motres/api.env`; never commit these values)

| Variable | Meaning |
|---|---|
| `MOTOR_AI_SIM_DATA_SOURCES=1` | turn the open and private layers on |
| `OPEN_DATA_REPO_DIR` | checkout of the public repo. Default: the source tree |
| `MOTOR_AI_SIM_PRIVATE_DATA` | checkout of the private repo (PR #41) |
| `PRIVATE_DATA_REPO_DIR` | overrides the private checkout used for moves |
| `OPEN_DATA_REPO_SLUG` / `PRIVATE_DATA_REPO_SLUG` | `owner/name`. Defaults: `eMotres/Motor_Ai_Simulation`, `eMotres/motor-ai-sim-private` |
| `OPEN_DATA_BASE_BRANCH` / `PRIVATE_DATA_BASE_BRANCH` | the branch PRs target. Default: `main` |
| `OPEN_DATA_DEPLOY_KEY` / `PRIVATE_DATA_DEPLOY_KEY` | SSH deploy key with **write** access, used for pushes |
| `OPEN_DATA_GITHUB_TOKEN` / `PRIVATE_DATA_GITHUB_TOKEN` | fine-grained token, *Pull requests: read/write* on that one repository. Without it, the admin opens and closes PRs by hand from the compare URL |
| `DATA_GIT_AUTHOR_NAME` / `DATA_GIT_AUTHOR_EMAIL` | commit identity (and `Signed-off-by`) |
| `DATA_REFERENCE_ORGS` | organisations whose data may enter the repos. Default: `MOTRES` |
| `MOTOR_AI_SIM_CATALOG_OVERRIDES` | path of the override records. Default: `<config dir>/catalog_overrides.yaml` |

## Server setup

1. Deploy keys. Run `ssh-keygen -t ed25519 -N '' -f /etc/motres/keys/open_data`
   and repeat for `private_data`. Add each `.pub` as a deploy key with *Allow
   write access* in its repository. Then `chmod 600` and `chown` to the API user.
2. Checkouts, owned by the API user and left on `main`:
   `GIT_SSH_COMMAND='ssh -i /etc/motres/keys/open_data' git clone git@github.com:eMotres/Motor_Ai_Simulation.git /srv/motres/data/open`,
   and the same for the private repo into `/srv/motres/data/private`. In each
   checkout, set `git config core.sshCommand 'ssh -i <key> -o IdentitiesOnly=yes'`.
3. Two fine-grained tokens, each scoped to one repository with *Pull requests:
   read/write* only.
4. Put the variables above in `/etc/motres/api.env`, then restart the API.
5. Seed the private repo's `config/dies/` with **MOTRES reference dies only**,
   each with `owner_org: MOTRES`, through a PR in eMotres/motor-ai-sim-private.
   Customer dies stay in the application storage (workspaces or
   `SHARED_ROOT/dies`), are never copied into either repository, and cannot be
   moved from the Admin UI.
6. Branch protection on `main` in both repos: PRs required, no direct pushes.
   The deploy keys only ever push `data/*` branches.
