# Starts the bahAI Workforce API (Secretary + everything else) at Windows
# logon, without uvicorn's --reload (see CLAUDE.md Gotchas: --reload was
# observed serving a stale .env value even across apparent restarts).
# Bound to 127.0.0.1 only — the Cloudflare Tunnel reaches it via localhost;
# nothing here needs to be reachable from the wider network.

$root = "C:\Users\Sheraj\Documents\bahAI-workforce"
$python = "C:\Users\Sheraj\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"
$logDir = Join-Path $root "logs"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Set-Location $root
Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "agents.api:app", "--host", "127.0.0.1", "--port", "8765" `
    -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $logDir "api.out.log") `
    -RedirectStandardError (Join-Path $logDir "api.err.log") `
    -WindowStyle Hidden
