# Stage-2 GPU benchmark, owner's PC (RTX 5070), NIGHT WINDOW ONLY (22:00-06:00) by default.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bench\night_gpu_bench.ps1 [-SkipMatrices] [-SkipAB]
#       [-Cases d40_rated,l13_rated,l155_rated] [-Modes static,eddy] [-Threads 6]
#
# DAYTIME OVERRIDE (the guard stays; this is the only documented way past it):
#   ... night_gpu_bench.ps1 -AllowDaytime -DaytimeReason "owner approved in chat 2026-09-29 19:00"
# The reason is written to env.txt in the results folder. Use it only on the owner's explicit word
# for that run; the process runs at BelowNormal priority with <= 6 CPU threads either way.
#
# Prerequisites (see docs/GPU_TDM_STUDY_2026-09-29.md section 6): NVIDIA driver R580+ (CUDA >= 12.8),
# venv C:\Users\vadim\venvs\gpu_bench with the repo requirements + cupy-cuda13x + nvmath-python[cu13]
# (or the cu12 pair). Reads matrices from C:\Users\vadim\Downloads\solver_matrices (read-only), writes
# to ...\solver_matrices\results_<date>. Never touches the live API, config\motor_config.yaml or any
# workspace: each A/B run gets a copied config.
param([switch]$AllowDaytime, [string]$DaytimeReason = '',
      [switch]$SkipMatrices, [switch]$SkipAB,
      [string[]]$Cases = @('d40_rated', 'l13_rated', 'l155_rated'),
      [string[]]$Modes = @('static', 'eddy'),
      [string[]]$Backends = @('pardiso', 'pardiso_spd', 'cudss_fp64', 'cudss_fp64_spd', 'cudss_mixed'),
      [string]$Python = 'C:\Users\vadim\venvs\gpu_bench\Scripts\python.exe',
      # code: the src of the checkout this script lives in (perf/profiling-gpu-tdm)
      [string]$Src = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'src'),
      # data only, read-only: config\dies and the config files
      [string]$Repo = 'C:\Users\vadim\Projects\motor_ai_sim',
      [string]$Matrices = 'C:\Users\vadim\Downloads\solver_matrices',
      [int]$Threads = 6)

function In-Night { $h = (Get-Date).Hour; return ($h -ge 22 -or $h -lt 6) }
if (-not (In-Night)) {
  if (-not $AllowDaytime) { Write-Host "Not in the night window (22:00-06:00); exiting. (-AllowDaytime -DaytimeReason '...' overrides on the owner's word.)"; exit 1 }
  if (-not $DaytimeReason) { throw "-AllowDaytime needs -DaytimeReason (who approved, when)" }
}
if (-not (Test-Path $Python)) { throw "venv python not found: $Python" }
if ($Threads -gt 6) { $Threads = 6 }   # owner's cap while he works on this PC
(Get-Process -Id $PID).PriorityClass = 'BelowNormal'   # children inherit it

$bench = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $Matrices ("results_" + (Get-Date -Format 'yyyyMMdd_HHmm'))
New-Item -ItemType Directory -Force -Path $out | Out-Null
$env:MKL_NUM_THREADS = "$Threads"; $env:OMP_NUM_THREADS = "$Threads"
$env:OPENBLAS_NUM_THREADS = "$Threads"; $env:NUMEXPR_MAX_THREADS = "$Threads"
$env:SB_NO_WARM_CACHE = '1'; $env:MPLBACKEND = 'Agg'
Remove-Item Env:WORKSPACES_ROOT -ErrorAction SilentlyContinue

"started $(Get-Date -Format s); threads $Threads; priority BelowNormal; daytime override: $([bool]$AllowDaytime) $DaytimeReason" | Set-Content (Join-Path $out 'env.txt')
& $Python -c "import cupy, nvmath; p=cupy.cuda.runtime.getDeviceProperties(0); print('GPU', p['name'], 'cc', p['major'], p['minor'], 'CUDA rt', cupy.cuda.runtime.runtimeGetVersion(), 'driver', cupy.cuda.runtime.driverGetVersion(), 'cupy', cupy.__version__, 'nvmath', nvmath.__version__)" 2>&1 | Add-Content (Join-Path $out 'env.txt')

# 1) matrix benchmark: every exported system, every backend
if (-not $SkipMatrices) {
  & $Python (Join-Path $bench 'gpu_solver_bench.py') --matrices $Matrices `
    --backends 'pardiso,pardiso_spd,superlu,cudss_fp64,cudss_fp64_spd,cudss_fp64_sym,cudss_mixed,cudss_fp32,cupy_qr,cupy_gmres_jac,amgx' `
    --repeat 7 --out (Join-Path $out 'gpu_bench.json') 2>&1 | Tee-Object (Join-Path $out 'gpu_bench.log')
}

# 2) Ginkgo driver, if built (scripts\bench\ginkgo\build\Release\ginkgo_bench.exe)
$gk = Join-Path $bench 'ginkgo\build\Release\ginkgo_bench.exe'
if ((-not $SkipMatrices) -and (Test-Path $gk)) {
  Get-ChildItem $Matrices -Filter '*_A.mtx' | ForEach-Object {
    $b = $_.FullName -replace '_A\.mtx$', '_b.mtx'
    foreach ($m in 'gmres_bj', 'gmres_parilu', 'direct_lu') {
      & $gk $_.FullName $b cuda $m 5 2>&1 | Add-Content (Join-Path $out 'ginkgo.jsonl')
    }
  }
}

# 3) engineering A/B: full runs, CPU PARDISO FP64 LU (reference) vs the other backends
if (-not $SkipAB) {
  if (-not (Test-Path (Join-Path $Src 'motor_ai_sim'))) { throw "src not found: $Src" }
  $env:PYTHONPATH = $Src
  $dies = Join-Path $Repo 'config\dies'
  foreach ($case in $Cases) {
    foreach ($mode in $Modes) {
      foreach ($be in $Backends) {
        $tag = "ab_${case}_${mode}_${be}"
        $cfg = Join-Path $out "cfg_$tag"
        New-Item -ItemType Directory -Force -Path $cfg | Out-Null
        foreach ($f in 'motor_config.yaml', 'materials_library.yaml', 'wire_stock.yaml', 'end_effect_3d.json') {
          $src = Join-Path $Repo "config\$f"; if (Test-Path $src) { Copy-Item $src $cfg }
        }
        $env:MOTOR_AI_SIM_CONFIG = Join-Path $cfg 'motor_config.yaml'
        & $Python (Join-Path $bench 'profile_fem_run.py') --dies $dies --case $case --mode $mode `
          --backend $be --out (Join-Path $out "$tag.json") 2> (Join-Path $out "$tag.err")
        if ((-not $AllowDaytime) -and (-not (In-Night))) { Write-Host 'Night window over; stopping.'; exit 0 }
      }
      foreach ($be in ($Backends | Where-Object { $_ -ne 'pardiso' })) {
        & $Python (Join-Path $bench 'compare_engineering.py') (Join-Path $out "ab_${case}_${mode}_pardiso.json") `
          (Join-Path $out "ab_${case}_${mode}_${be}.json") 2>&1 | Tee-Object (Join-Path $out "cmp_${case}_${mode}_${be}.txt")
      }
    }
  }
}
Write-Host "done: $out"
