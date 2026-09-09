# Clears Vite dev servers left over from earlier `npm run dev` sessions, so the
# dashboard always comes back on its usual port 5173.
#
# Why this is not tidiness: on 2026-08-24 four Vite processes from 2026-08-13
# and -08-14 were still holding ports 5173, 5174, 5175 and 5176, and NONE of
# them answered an HTTP request -- the sockets were listening, the servers were
# gone. So the bookmarked http://localhost:5173 just spun for ever ("it takes
# forever to load"), while a freshly started dev server had quietly moved to
# 5177 and was working fine. Vite's port hunting hides this: it reports "port
# in use, trying another one" and carries on, which is the right default for a
# library and the wrong one for the single dashboard on this machine.
#
# Scoped hard: only node processes whose command line names THIS repo's
# dashboard/node_modules Vite binary are touched. VS Code, Claude Code and every
# other node process on the machine can never match.

$ErrorActionPreference = 'Stop'

$root      = Split-Path -Parent $PSScriptRoot
$dashboard = Join-Path $root 'dashboard'
# The path as it appears in a real command line, e.g.
#   "node" "...\dashboard\node_modules\.bin\..\vite\bin\vite.js"
$needle    = Join-Path $dashboard 'node_modules'

$ours = @(
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*$needle*" -and
            $_.CommandLine -like '*vite*'
        }
)

if ($ours.Count -eq 0) {
    exit 0
}

# Report the ports too -- that is what Sheraj recognises, not a PID.
$ports = New-Object System.Collections.Generic.List[int]
foreach ($proc in $ours) {
    try {
        Get-NetTCPConnection -State Listen -OwningProcess $proc.ProcessId -ErrorAction SilentlyContinue |
            ForEach-Object { $ports.Add([int]$_.LocalPort) }
    } catch { }
}

$word    = if ($ours.Count -eq 1) { 'dev server' } else { 'dev servers' }
$portStr = if ($ports.Count -gt 0) { " on port $(($ports | Sort-Object -Unique) -join ', ')" } else { '' }
Write-Host "     clearing $($ours.Count) leftover $word$portStr"

foreach ($proc in $ours) {
    try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop } catch { }
}

# Give Windows a moment to release the sockets, or Vite starts and still finds
# the port busy.
for ($i = 0; $i -lt 20; $i++) {
    $held = @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)
    if ($held.Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
}
exit 0
