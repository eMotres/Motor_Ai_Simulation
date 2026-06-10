# Backend watchdog - keeps the FEM API on :8001 alive.
#
# The uvicorn worker dies on heavy FEM (LLVM JIT crash) OR occasionally wedges
# (port stays open but the single sync worker is blocked forever).  Either way
# the UI shows "mesh not loading / failed to fetch".  This watchdog recovers
# BOTH cases:
#
#   CRASH  - nothing listens on 8001  -> restart immediately.
#   HANG   - port IS listening but /api/health has failed continuously for
#            longer than HANG_TIMEOUT (well past any legit solve)  -> restart.
#
# Why a long HANG_TIMEOUT: a real FEM solve blocks the single worker for tens of
# seconds (transient ~70 s, eddy view ~40 s), so short health timeouts are
# NORMAL while busy.  Only a block far longer than that is a true hang.
#
# Usage (detached):
#   Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\backend_watchdog.ps1' -WindowStyle Hidden
# Stop: create scripts\watchdog.stop (checked each cycle) or kill the process.

$ErrorActionPreference = 'SilentlyContinue'
$Root        = Split-Path -Parent $PSScriptRoot      # repo root (script in scripts\)
$Port        = 8001
$LogFile     = Join-Path $PSScriptRoot 'watchdog.log'
$StopFile    = Join-Path $PSScriptRoot 'watchdog.stop'
$PollSec     = 15
$HangTimeout = 240          # seconds of continuous health failure = a hang

function Log($msg) {
    Add-Content -Path $LogFile -Encoding utf8 `
        -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
}
function Healthy {
    try { return (Invoke-WebRequest -Uri "http://localhost:$Port/api/health" `
                  -UseBasicParsing -TimeoutSec 4).StatusCode -eq 200 } catch { return $false }
}
function PortOpen {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}
function KillBackend {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -Expand OwningProcess -Unique |
        ForEach-Object { try { Stop-Process -Id $_ -Force -ErrorAction Stop } catch {} }
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'uvicorn.*motor_ai_sim' } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
}
function StartBackend {
    Start-Process -FilePath 'python' `
        -ArgumentList '-m','uvicorn','motor_ai_sim.api:app','--port',"$Port",'--host','127.0.0.1' `
        -WorkingDirectory $Root -WindowStyle Hidden
    # patient health wait - cold import of skfem/gmsh/numpy can take ~20 s
    for ($i = 0; $i -lt 20; $i++) { Start-Sleep -Seconds 2; if (Healthy) { return $true } }
    return $false
}

# single-instance guard.  An AbandonedMutexException means a PRIOR watchdog
# died holding the mutex - the wait still SUCCEEDS (we now own it), so treat it
# as acquired rather than crashing on startup.
$mutex = New-Object System.Threading.Mutex($false, 'Global\motor_ai_sim_backend_watchdog')
$acquired = $false
try { $acquired = $mutex.WaitOne(0) }
catch [System.Threading.AbandonedMutexException] { $acquired = $true }
if (-not $acquired) { exit 0 }

Log "watchdog started (poll=${PollSec}s, hang_timeout=${HangTimeout}s)"
$restarts = 0
$unhealthySince = $null          # timestamp of first failure in the current streak
while ($true) {
    if (Test-Path $StopFile) { Log 'stop file -> exit'; Remove-Item $StopFile -Force; break }

    if (Healthy) {
        $unhealthySince = $null
    }
    else {
        $reason = $null
        if (-not (PortOpen)) {
            $reason = 'crash (no listener)'
        }
        else {
            if ($null -eq $unhealthySince) { $unhealthySince = Get-Date }
            $stuckFor = ((Get-Date) - $unhealthySince).TotalSeconds
            if ($stuckFor -ge $HangTimeout) {
                $reason = "hang (unresponsive ${HangTimeout}s+, port still open)"
            }
            # else: probably a long solve in progress - wait, do not touch.
        }
        if ($reason) {
            $restarts++
            Log "restarting backend - $reason (restart #$restarts)"
            KillBackend
            Start-Sleep -Seconds 1
            $ok = StartBackend
            Log "restart #$restarts -> health=$ok"
            $unhealthySince = $null
        }
    }
    Start-Sleep -Seconds $PollSec
}
