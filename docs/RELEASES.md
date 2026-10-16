# Releasing Motor AI Simulator

**Single source of version truth:** the `VERSION` file at the repo root (SemVer `MAJOR.MINOR.PATCH`).

A release deploys the **frontend and backend together**, stamps the version + git sha
into both, and tags git — so prod is never left half-deployed (the *version skew*
failure mode the in-app header badge warns about with a ⚠).

## Cut a release

1. Pick the new version (SemVer):
   - **PATCH** — fixes only · **MINOR** — backward-compatible features · **MAJOR** — breaking.
2. Update `CHANGELOG.md`: move items from `[Unreleased]` into a new `## [X.Y.Z] — YYYY-MM-DD`.
3. From the repo root (Windows PowerShell):
   ```powershell
   ./scripts/release.ps1 X.Y.Z      # or omit the arg to (re)deploy the current VERSION
   ```
   The script: writes `VERSION`, commits + tags `vX.Y.Z`, builds the frontend (version
   stamped by vite), `firebase deploy --only hosting`, then `gcloud run deploy` the
   backend with `APP_GIT_SHA` + `APP_BUILT_AT` stamped in.
4. Push the tag it prints: `git push origin vX.Y.Z` (and `git push`).

## Verify

- Header chip shows `vX.Y.Z` with **no ⚠** (frontend & backend agree at major.minor).
- `GET /api/version` → `{version, gitSha, builtAt}` matches `VERSION`.

## Rollback

- **Backend** (Cloud Run keeps every revision):
  ```
  gcloud run revisions list  --service aerostator-backend --region europe-west1
  gcloud run services update-traffic aerostator-backend --region europe-west1 --to-revisions <PREV>=100
  ```
- **Frontend** (Firebase Hosting): `firebase hosting:rollback` (or Console → Hosting → release history).
- **Enforcement** (instant, no rebuild): `gcloud run services update aerostator-backend --region europe-west1 --update-env-vars AUTH_ENFORCE=0`

## Notes / gotchas

- The backend image **bakes `config/motor_config.yaml`** (the active/default motor). A
  backend redeploy resets the Cloud Run instance's active config to the baked one — make
  sure `config/motor_config.yaml` holds the intended motor before releasing (prod config
  is volatile; see the multi-user memory).
- Cloud Run env vars (`AUTH_ENFORCE`, `ADMIN_EMAILS`, secrets) are **preserved** across
  `--source` redeploys; the script uses `--update-env-vars` (merge), never `--set-env-vars`.
- If `gcloud` says *"Reauthentication failed, cannot prompt"* mid-release, run
  `gcloud auth login` and re-run the script.
