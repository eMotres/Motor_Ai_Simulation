#requires -Version 5.1
<#
  Coordinated release for Motor AI Simulator.

  Stamps the version (from /VERSION) + git sha into BOTH the frontend and the
  backend and deploys them together, so prod is never left half-deployed
  (the "version skew" the in-app header badge warns about). See docs/RELEASES.md.

  Usage (from anywhere — it cd's to the repo root):
    ./scripts/release.ps1 0.2.0     # set new version, then release
    ./scripts/release.ps1           # release the version already in VERSION
#>
param([string]$Version)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($Version) {
  if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must be SemVer X.Y.Z, got '$Version'" }
  Set-Content -Path 'VERSION' -Value $Version -NoNewline -Encoding ascii
}
$Version = (Get-Content 'VERSION' -Raw).Trim()
$sha     = (git rev-parse --short HEAD).Trim()
$builtAt = (Get-Date).ToUniversalTime().ToString('o')
Write-Host "==> Releasing v$Version  ($sha)  $builtAt" -ForegroundColor Cyan

# 1) commit any VERSION/CHANGELOG change + tag
git add VERSION CHANGELOG.md
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { git commit -m "Release v$Version" }
$tagExists = git tag -l "v$Version"
if (-not $tagExists) { git tag -a "v$Version" -m "Release v$Version" } else { Write-Host "   tag v$Version already exists" }

# 2) frontend (vite stamps the version from VERSION) -> Firebase Hosting
Write-Host "==> Building + deploying frontend" -ForegroundColor Cyan
Push-Location web
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "frontend build failed" }
Pop-Location
firebase deploy --only hosting

# 3) backend -> Cloud Run, stamping git sha + build time.
#    --update-env-vars MERGES (preserves AUTH_ENFORCE/ADMIN_EMAILS/secrets).
Write-Host "==> Building + deploying backend (Cloud Run)" -ForegroundColor Cyan
gcloud run deploy aerostator-backend --source . --region europe-west1 `
  --cpu=4 --memory=8Gi --timeout=900 --max-instances=3 `
  --update-env-vars "APP_GIT_SHA=$sha,APP_BUILT_AT=$builtAt" --quiet

Write-Host ""
Write-Host "==> Released v$Version. Verify the header badge shows v$Version (no warning)" -ForegroundColor Green
Write-Host "    and GET /api/version matches. Then push:  git push; git push origin v$Version" -ForegroundColor Green
