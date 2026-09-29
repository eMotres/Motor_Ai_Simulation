# Stage-2 GPU benchmark, owner's PC (RTX 5070), NIGHT WINDOW ONLY (22:00-06:00).
#
#   powershell -ExecutionPolicy Bypass -File scripts\bench\night_gpu_bench.ps1 [-Force] [-SkipAB]
#
# Prerequisites (owner installs, see docs/GPU_TDM_STUDY_2026-09-29.md section 6):
#   NVIDIA driver with CUDA 12.9+ support, venv C:\Users\vadim\venvs\gpu_bench with
#   cupy-cuda12x, nvmath-python[cu12], pypardiso and the repo requirements.
# Reads matrices from C:\Users\vadim\Downloads\solver_matrices (read-only), writes to
# C:\Users\vadim\Downloads\solver_matrices\results_<date>. Never touches the live API,
# config\motor_config.yaml or any workspace: each A/B run gets a copied config.
param([switch]$Force, [switch]$SkipAB,
      [string]$Python = 'C:\Users\vadim\venvs\gpu_bench\Scripts\python.exe',
      [string]$Repo = 'C:\Users\vadim\Projects\motor_ai_sim',
      [string]$Matrices = 'C:\Users\vadim\Downloads\solver_matrices',
      [int]$Threads = 8)

$h = (Get-Date).Hour
if (-not $Force -and $h -lt 22 -and $h -ge 6) { Write-Host "Not in the night window (22:00-06:00); exiting."; exit 1 }
if (-not (Test-Path $Python)) { throw "venv python not found: $Python" }

$bench = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $Matrices ("results_" + (Get-Date -Format 'yyyyMMdd_HHmm'))
New-Item -ItemType Directory -Force -Path $out | Out-Null
$env:MKL_NUM_THREADS = "$Threads"; $env:OMP_NUM_THREADS = "$Threads"
$env:OPENBLAS_NUM_THREADS = "$Threads"; $env:SB_NO_WARM_CACHE = '1'; $env:MPLBACKEND = 'Agg'
Remove-Item Env:WORKSPACES_ROOT -ErrorAction SilentlyContinue

& $Python -c "import cupy, nvmath; print('GPU', cupy.cuda.runtime.getDeviceProperties(0)['name'], 'CUDA rt', cupy.cuda.runtime.runtimeGetVersion(), 'nvmath', nvmath.__version__)" 2>&1 | Tee-Object (Join-Path $out 'env.txt')

# 1) matrix benchmark: every exported system, every backend, 7 repeats
& $Python (Join-Path $bench 'gpu_solver_bench.py') --matrices $Matrices `
  --backends 'pardiso,pardiso_spd,superlu,cudss_fp64,cudss_fp64_spd,cudss_fp64_sym,cudss_mixed,cudss_fp32,cupy_qr,cupy_gmres_jac,amgx' `
  --repeat 7 --out (Join-Path $out 'gpu_bench.json') 2>&1 | Tee-Object (Join-Path $out 'gpu_bench.log')

# 2) Ginkgo driver, if built (scripts\bench\ginkgo\build\Release\ginkgo_bench.exe)
$gk = Join-Path $bench 'ginkgo\build\Release\ginkgo_bench.exe'
if (Test-Path $gk) {
  Get-ChildItem $Matrices -Filter '*_A.mtx' | ForEach-Object {
    $b = $_.FullName -replace '_A\.mtx$', '_b.mtx'
    foreach ($m in 'gmres_bj', 'gmres_parilu', 'direct_lu') {
      & $gk $_.FullName $b cuda $m 5 2>&1 | Add-Content (Join-Path $out 'ginkgo.jsonl')
    }
  }
}

# 3) engineering A/B: full runs, CPU PARDISO FP64 vs cuDSS FP64 vs cuDSS mixed
if (-not $SkipAB) {
  $env:PYTHONPATH = Join-Path $Repo 'src'
  $dies = Join-Path $Repo 'config\dies'
  foreach ($case in 'd40_rated', 'l13_rated', 'l155_rated') {
    foreach ($mode in 'static', 'eddy') {
      foreach ($be in 'pardiso', 'pardiso_spd', 'cudss_fp64', 'cudss_fp64_spd', 'cudss_mixed') {
        $tag = "ab_${case}_${mode}_${be}"
        $cfg = Join-Path $out "cfg_$tag"
        New-Item -ItemType Directory -Force -Path $cfg | Out-Null
        foreach ($f in 'motor_config.yaml', 'materials_library.yaml', 'wire_stock.yaml', 'end_effect_3d.json') {
          $src = Join-Path $Repo "config\$f"; if (Test-Path $src) { Copy-Item $src $cfg }
        }
        $env:MOTOR_AI_SIM_CONFIG = Join-Path $cfg 'motor_config.yaml'
        & $Python (Join-Path $bench 'profile_fem_run.py') --dies $dies --case $case --mode $mode `
          --backend $be --out (Join-Path $out "$tag.json") 2> (Join-Path $out "$tag.err")
        if ((Get-Date).Hour -ge 6 -and (Get-Date).Hour -lt 22 -and -not $Force) { Write-Host 'Night window over; stopping.'; exit 0 }
      }
      foreach ($be in 'pardiso_spd', 'cudss_fp64', 'cudss_fp64_spd', 'cudss_mixed') {
        & $Python (Join-Path $bench 'compare_engineering.py') (Join-Path $out "ab_${case}_${mode}_pardiso.json") `
          (Join-Path $out "ab_${case}_${mode}_${be}.json") 2>&1 | Tee-Object (Join-Path $out "cmp_${case}_${mode}_${be}.txt")
      }
    }
  }
}
Write-Host "done: $out"
