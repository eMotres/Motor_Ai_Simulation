# Backend watchdog — keeps the FEM API on :8001 alive.
#
# The uvicorn worker occasionally dies mid-solve (LLVM JIT crash on heavy FEM);
# the UI then shows "mesh not loading / failed to fetch" until someone restarts
# it by hand.  This script polls the PORT (not the HTTP health endpoint!) and
# restarts the backend only when NOTHING is listening on 8001 any more.
#
# Why the port and not /api/health: a long synchronous FEM solve blocks the
# single worker for minutes, so health-check timeouts are NORMAL while busy.
# Killing on a slow health check would murder live runs.  A dead process
# releases the port — that is the unambiguous "it crashed" signal.
#
# Usage (detached):
#   Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\backend_watchdog.ps1' -WindowStyle Hidden
# Stop: create file scripts\watchdog.stop (checked every cycle) or kill the process.

$ErrorActionPreference = 'SilentlyContinue'
$Root    = Split-Path -Parent $PSScriptRoot          # repo root (script lives in scripts\)
$Port    = 8001
$LogFile = Join-Path $PSScriptRoot 'watchdog.log'
$StopFile = Join-Path $PSScriptRoot 'watchdog.stop'
$PollSec = 20

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

# single-instance guard: bail if another watchdog already runs
$mutex = New-Object System.Threading.Mutex($false, 'Global\motor_ai_sim_backend_watchdog')
if (-not $mutex.WaitOne(0)) { exit 0 }

Log "watchdog started (root=$Root, poll=${PollSec}s)"
$restarts = 0
while ($true) {
    if (Test-Path $StopFile) { Log "stop file found - exiting"; Remove-Item $StopFile -Force; break }
    $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) {
        $restarts++
        Log "port $Port has no listener - restarting backend (restart #$restarts)"
        Start-Process -FilePath 'python' `
            -ArgumentList '-m','uvicorn','motor_ai_sim.api:app','--port',"$Port",'--host','127.0.0.1' `
            -WorkingDirectory $Root -WindowStyle Hidden
        # give it time to boot before the next poll
        Start-Sleep -Seconds 15
        $ok = $false
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$Port/api/health" -UseBasicParsing -TimeoutSec 8
            $ok = ($r.StatusCode -eq 200)
        } catch {}
        Log ("restart #{0} -> health={1}" -f $restarts, $ok)
    }
    Start-Sleep -Seconds $PollSec
}
