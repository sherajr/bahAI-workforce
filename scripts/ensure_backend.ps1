# Makes sure the bahAI Workforce API is really ANSWERING on :8765, and starts
# it the sanctioned way if it is not. Called by `npm run dev` (via
# dashboard/scripts/dev.mjs) so the dashboard never opens against a dead API,
# and safe to run on its own when the backend needs reviving.
#
# Why "answering" and not "is a process alive": on 2026-08-24 the API process
# was still running with its LISTENING SOCKET GONE -- uvicorn's proactor accept
# loop had died with WinError 64 ("the specified network name is no longer
# available", logs/api.err.log) while the process stayed up. Nothing was on the
# port, the Scheduled Task said "Ready" because it only triggers at logon, and
# the dashboard just said the backend was not running. A liveness check by PID
# or by port would BOTH have reported everything was fine. Only /health tells
# the truth, so /health is the only thing trusted here.
#
# /health needs no key: it is one of the three endpoints outside the owner gate
# (AGENTS.md rule 70), so this script never has to touch private/api_key.txt.
#
# Never `python agents/api.py` -- that binds 0.0.0.0 with --reload (AGENTS.md).
# Starting goes through the managed Scheduled Task, falling back to
# start_secretary_server.ps1, which is the same command the task itself runs.

param([int]$TimeoutSec = 90)

$ErrorActionPreference = 'Stop'

$root      = Split-Path -Parent $PSScriptRoot
$port      = 8765
$healthUrl = "http://127.0.0.1:$port/health"
$taskName  = 'bahAI Secretary API'

function Test-Api {
    try {
        $r = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Every process that could be holding :8765 or half-running the API: whatever
# is listening on the port, PLUS any uvicorn started against agents.api:app
# whose socket has died (the case above, invisible to a port check).
#
# BOTH halves of that are needed, and so is killing every match rather than
# just the listener: the venv's pythonw.exe is a launcher that runs the real
# interpreter as a CHILD, so one API instance is always TWO processes (verified
# 2026-08-24: pythonw 18060 -> python 32700, and only the child holds the
# socket). Killing the child alone leaves the parent behind; killing the parent
# alone can leave an orphan still on the port. Also true if a second instance
# ever does start -- Windows lets a wildcard bind and a loopback bind coexist,
# which is why AGENTS.md warns against starting the API by hand.
function Get-StaleApiProcess {
    $ids = New-Object System.Collections.Generic.List[int]
    try {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { $ids.Add([int]$_.OwningProcess) }
    } catch { }
    try {
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like '*uvicorn*agents.api:app*' } |
            ForEach-Object { $ids.Add([int]$_.ProcessId) }
    } catch { }
    return ($ids | Sort-Object -Unique)
}

if (Test-Api) {
    Write-Host "     already running and answering on port $port"
    exit 0
}

$stale = @(Get-StaleApiProcess)
if ($stale.Count -gt 0) {
    $word = if ($stale.Count -eq 1) { 'process' } else { 'processes' }
    Write-Host "     not answering; clearing $($stale.Count) stopped $word ($($stale -join ', '))"
    foreach ($procId in $stale) {
        try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch { }
    }
    # Windows can leave the socket in TIME_WAIT for a moment after the kill.
    for ($i = 0; $i -lt 20; $i++) {
        $held = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($held.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    }
}

$how = ''
try {
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $how = "scheduled task `"$taskName`""
} catch {
    & (Join-Path $PSScriptRoot 'start_secretary_server.ps1')
    $how = 'scripts/start_secretary_server.ps1'
}
Write-Host "     starting it ($how)..." -NoNewline

$sw = [Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    if (Test-Api) {
        Write-Host (" ready in {0:N1}s" -f $sw.Elapsed.TotalSeconds)
        exit 0
    }
    Write-Host '.' -NoNewline
    Start-Sleep -Milliseconds 700
}

Write-Host ''
Write-Host "     STILL NOT ANSWERING after $TimeoutSec seconds." -ForegroundColor Red
$errLog = Join-Path $root 'logs\api.err.log'
if (Test-Path $errLog) {
    Write-Host "     Last lines of logs/api.err.log:" -ForegroundColor Red
    Get-Content $errLog -Tail 12 | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
} else {
    Write-Host "     No logs/api.err.log to read." -ForegroundColor Red
}
exit 1
