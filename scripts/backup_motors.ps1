# Backup of the MOTOR DATA — everything that describes the machines and their
# results and lives only on this disk — to Google Drive (mounted as G:\My Drive).
#
# WHY: of the 295 files under config\dies only 22 are in git and config\ was
# last committed on 2026-08-20; three weeks of duties, materials, saved runs
# and field maps had no copy anywhere (user 2026-09-12: "а мы делаем бэкап
# данных по моторам?").
#
# WHAT: two layers, both under $Root —
#   latest\   an incremental mirror (robocopy /MIR — only changed files move,
#             seconds per run; a file deleted on disk disappears here too);
#   daily\    one dated zip per day, the last $Keep kept — the copy that
#             survives a wrong edit mirrored into latest\.
#
# The set is ~46 MB.  The API is never touched: everything read here is a file
# the backend rewrites atomically, so a copy taken mid-solve is at worst one
# write behind.
#
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_motors.ps1
#         (-Root / -Keep to override; -NoZip for the mirror only)
param(
  [string]$Root = "G:\My Drive\motor_ai_sim_backup",
  [int]$Keep = 30,
  [switch]$NoZip
)
$ErrorActionPreference = "Stop"
$Proj = Split-Path -Parent $PSScriptRoot
$Cfg  = Join-Path $Proj "config"
$Logs = Join-Path $Proj "logs"

if (-not (Test-Path (Split-Path -Parent $Root))) {
  Write-Error "Google Drive is not mounted at $(Split-Path -Parent $Root) — nothing copied."
  exit 2
}
$Latest = Join-Path $Root "latest"
$Daily  = Join-Path $Root "daily"
New-Item -ItemType Directory -Force $Latest, $Daily | Out-Null

# ── the set ──────────────────────────────────────────────────────────────
# Directories are mirrored whole; single files are listed by name so a new
# cache nobody wants (e.g. .static3d_cache, tens of MB of meshes) does not ride
# along by accident.  Add a name here when a new store appears.
$Dirs  = @("dies", ".history", ".descent_runs")
$Files = @(
  "motor_config.yaml", "motor_catalog.json", "motor_presets.json",
  "materials_library.yaml", "saved_simulations.json", "users.json",
  "end_effect_3d.json", "FINAL_candidates.json",
  ".scan_cache.jsonl", ".duty_results.json", ".bench_ldq.json",
  ".daxis_cache.json", ".family_context.json",
  ".last_transient.json", ".last_transient_field.pkl",
  ".last_thermal.pkl", ".last_mechanical.pkl",
  ".last_coupled.json", ".last_scan.json", ".last_descent.json"
)

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$n = 0
foreach ($d in $Dirs) {
  $src = Join-Path $Cfg $d
  if (Test-Path $src) {
    # /MIR mirror, /R:2 /W:2 short retries (Drive can hold a file briefly),
    # /NFL /NDL quiet, /XJ no junctions.  Exit codes < 8 are success for robocopy.
    robocopy $src (Join-Path $Latest "config\$d") /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS /XJ | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Error "robocopy failed on $d (code $LASTEXITCODE)" }
    $n += (Get-ChildItem -Recurse -File $src).Count
  }
}
New-Item -ItemType Directory -Force (Join-Path $Latest "config") | Out-Null
foreach ($f in $Files) {
  $src = Join-Path $Cfg $f
  if (Test-Path $src) { Copy-Item $src (Join-Path $Latest "config\$f") -Force; $n++ }
}
$rj = Join-Path $Logs "run_journal.jsonl"
if (Test-Path $rj) {
  New-Item -ItemType Directory -Force (Join-Path $Latest "logs") | Out-Null
  Copy-Item $rj (Join-Path $Latest "logs\run_journal.jsonl") -Force; $n++
}
# what this copy is, in the copy itself
@{ taken = $stamp; host = $env:COMPUTERNAME; project = $Proj; files = $n } |
  ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Latest "BACKUP_INFO.json")

# ── the dated archive ────────────────────────────────────────────────────
if (-not $NoZip) {
  $zip = Join-Path $Daily ("{0}.zip" -f (Get-Date -Format "yyyy-MM-dd"))
  if (Test-Path $zip) { Remove-Item $zip -Force }
  Compress-Archive -Path (Join-Path $Latest "*") -DestinationPath $zip -CompressionLevel Optimal
  # keep the last $Keep days
  Get-ChildItem $Daily -Filter "*.zip" | Sort-Object Name -Descending |
    Select-Object -Skip $Keep | Remove-Item -Force
}

$size = [math]::Round(((Get-ChildItem -Recurse -File $Latest | Measure-Object Length -Sum).Sum / 1MB), 1)
$zipNote = ""
if (-not $NoZip) { $zipNote = "; zip: $zip" }
Write-Output ("motor backup: {0} files, {1} MB -> {2}  ({3}){4}" -f $n, $size, $Latest, $stamp, $zipNote)
