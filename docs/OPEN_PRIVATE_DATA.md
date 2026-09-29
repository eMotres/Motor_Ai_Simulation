# Open and private data sets

Every die belongs to exactly one data set. That covers the die itself, its
configurations and duties, and the results under `runs/`.

| Source    | Repository                                  | Path in the repo        | Audience                  |
|-----------|---------------------------------------------|-------------------------|---------------------------|
| `open`    | eMotres/Motor_Ai_Simulation (public, AGPL)  | `data/open/dies/<die>/` | everyone who clones it    |
| `private` | eMotres/motor-ai-sim-private                | `config/dies/<die>/`    | us only                   |

Any die that is not in the open set counts as **private**. This includes every
existing real product, and every workspace or shared-catalog die.

The open set is a few made-up demo motors (Ø30 spindle, Ø80 servo with a
controller and a battery, Ø160 generator). They let the public repository run
with no private data.

The data set is **not** the same thing as *visibility* (Admin → Motors access:
Private / Public / Selected clients). Visibility decides who can see a die on
this server. The data set decides which git repository holds the die's files.

## Loader

`motor_ai_sim.data_sources` reads both checkouts and merges them by die name.
If a name exists in both, the private copy is used and a WARNING is logged.
The Admin table marks that die with ⚠.

In the catalog, the two data sets sit below the shared catalog and are
read-only. As with shared dies, a user's save is copy-on-write into their own
workspace.

- **Multi-user (server):** the layers are active only when
  `MOTOR_AI_SIM_DATA_SOURCES=1` is set.
- **Single-user (workstation):** the layers are active when that variable is
  set, or automatically when `config/dies` holds no die (a fresh public clone).

## Moving a die (Admin → Motors access → die drawer → Source)

1. **Preview.** `GET /api/admin/dies/{die}/source/preview?target=open|private`
   returns the exact file list, geometry, configurations, materials, devices,
   results, blockers, and the warning.
2. **Move.** `POST /api/admin/dies/{die}/source` with
   `{"target": ..., "confirm": true}`. For each repository this:
   - creates a temporary worktree at `origin/<base>`,
   - makes a signed-off commit on a new branch `data/publish-…` or
     `data/withdraw-…`,
   - pushes that branch,
   - opens a PR through the GitHub API.

   The base branch is never pushed. Until a human merges the PRs, the die shows
   **pending publish** or **pending withdraw**.
3. **Complete.** `POST /api/admin/dies/source/reconcile` (the "Check merged PRs"
   button) sees that the die has arrived on the target base. It then
   fast-forwards both server checkouts, but only when they are on the base
   branch.

**Guard rails:**
- A die cannot go open while it references materials that are missing from the
  public `config/materials_library.yaml`, or devices whose cards are missing
  from the public `config/devices/`. The API returns these as blockers.
- A die whose `die.yaml` has `confidential: customer|nda`, `nda: true`,
  `customer: <name>`, or `tags: [customer|nda|confidential]` can never go open.
- Only admins can move dies.

**Audit:** every request, refusal, failure and completion is written to
`<config dir>/data_moves_audit.jsonl` and to the admin event log
(`die_source_move`). Pending state is kept in `<config dir>/data_moves.json`.

**Before you publish:** once merged, the files stay in the public git history
and in every fork. Moving a die back to private removes it from the repository
from that point on. It does not unpublish what is already in the history.

## Environment (`/etc/motres/api.env`; never commit these values)

| Variable | Meaning |
|---|---|
| `MOTOR_AI_SIM_DATA_SOURCES=1` | turn the open and private layers on |
| `OPEN_DATA_REPO_DIR` | checkout of the public repo that the open layer reads from and moves write to. Default: the source tree |
| `MOTOR_AI_SIM_PRIVATE_DATA` | checkout of the private repo (PR #41). The private layer reads `config/dies` from it |
| `PRIVATE_DATA_REPO_DIR` | overrides the private checkout used for moves. Default: `MOTOR_AI_SIM_PRIVATE_DATA` |
| `OPEN_DATA_REPO_SLUG` / `PRIVATE_DATA_REPO_SLUG` | `owner/name`. Defaults: `eMotres/Motor_Ai_Simulation`, `eMotres/motor-ai-sim-private` |
| `OPEN_DATA_BASE_BRANCH` / `PRIVATE_DATA_BASE_BRANCH` | the branch PRs target. Default: `main` |
| `OPEN_DATA_DEPLOY_KEY` / `PRIVATE_DATA_DEPLOY_KEY` | path of an SSH deploy key with **write** access, used for pushes |
| `OPEN_DATA_GITHUB_TOKEN` / `PRIVATE_DATA_GITHUB_TOKEN` | fine-grained token with *Pull requests: read/write* on that one repository. Without it, the admin gets a compare URL and opens the PR by hand |
| `DATA_GIT_AUTHOR_NAME` / `DATA_GIT_AUTHOR_EMAIL` | commit identity (and `Signed-off-by`) |

## Server setup

1. Deploy keys. Run `ssh-keygen -t ed25519 -N '' -f /etc/motres/keys/open_data`
   and repeat for `private_data`. Add each `.pub` as a deploy key with *Allow
   write access* in its GitHub repository. Then:
   `chmod 600`, `chown` to the API user.
2. Checkouts, owned by the API user:
   `GIT_SSH_COMMAND='ssh -i /etc/motres/keys/open_data' git clone git@github.com:eMotres/Motor_Ai_Simulation.git /srv/motres/data/open`
   and the same for the private repo into `/srv/motres/data/private`. In each
   checkout, set `git config core.sshCommand 'ssh -i <key> -o IdentitiesOnly=yes'`.
3. Two fine-grained tokens, each scoped to one repository with *Pull requests:
   read/write* only.
4. Put the variables above in `/etc/motres/api.env`, then restart the API.
5. Move the real dies into the private repo, same paths (`config/dies/<die>/`),
   through a PR in eMotres/motor-ai-sim-private. Until this is done they stay in
   `SHARED_ROOT/dies`, count as private, and cannot be moved from the Admin UI.
6. Branch protection on `main` in both repos: PRs required, no direct pushes.
   The deploy keys only ever push `data/*` branches.
