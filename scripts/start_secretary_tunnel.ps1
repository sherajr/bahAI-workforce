# Starts the Cloudflare Tunnel that exposes ONLY /whatsapp/webhook and
# /whatsapp/privacy (see C:\Users\Sheraj\.cloudflared\config.yml) at Windows
# logon, so the Secretary's WhatsApp connection survives a restart without
# manual intervention.

$root = "C:\Users\Sheraj\Documents\bahAI-workforce"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$logDir = Join-Path $root "logs"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Start-Process -FilePath $cloudflared `
    -ArgumentList "tunnel", "run", "2d2df423-bad1-44d6-8d9d-10aeb126ba27" `
    -RedirectStandardOutput (Join-Path $logDir "tunnel.out.log") `
    -RedirectStandardError (Join-Path $logDir "tunnel.err.log") `
    -WindowStyle Hidden
