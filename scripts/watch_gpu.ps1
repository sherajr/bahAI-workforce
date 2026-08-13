# Live GPU watcher — plain-language view of what the graphics card is doing.
#
# Written for a non-technical read: the two numbers that matter on an 8GB card
# are how BUSY the GPU is and how much VRAM is FREE. Video generation needs
# roughly 7GB free for Wan 2.2; when free VRAM drops near zero the model spills
# into system RAM and everything crawls.
#
# Run:  powershell -ExecutionPolicy Bypass -File scripts\watch_gpu.ps1
# Stop: Ctrl+C

$ErrorActionPreference = "Stop"

function Get-Bar([double]$pct, [int]$width = 24) {
    $filled = [Math]::Round(($pct / 100) * $width)
    if ($filled -lt 0) { $filled = 0 }
    if ($filled -gt $width) { $filled = $width }
    return ("#" * $filled) + ("." * ($width - $filled))
}

Write-Host ""
Write-Host "  Watching your graphics card. Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

while ($true) {
    $raw = & nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu `
                        --format=csv,noheader,nounits 2>$null
    if (-not $raw) {
        Write-Host "  Could not read the GPU (is the NVIDIA driver present?)" -ForegroundColor Red
        Start-Sleep -Seconds 5
        continue
    }
    $p = $raw -split ',' | ForEach-Object { $_.Trim() }
    $busy = [double]$p[0]; $used = [double]$p[1]; $total = [double]$p[2]; $temp = [double]$p[3]
    $free = $total - $used
    $memPct = ($used / $total) * 100

    # Colour by how much room is left for a video model, not by raw usage.
    $memColor = if ($free -lt 700) { "Red" } elseif ($free -lt 2000) { "Yellow" } else { "Green" }
    $verdict = if ($free -lt 700) { "TOO FULL for video generation" }
               elseif ($free -lt 2000) { "tight - close other AI apps first" }
               else { "room to work" }

    Clear-Host
    Write-Host ""
    Write-Host "  GPU activity   [$(Get-Bar $busy)] $([int]$busy)%"
    Write-Host "  Memory in use  [$(Get-Bar $memPct)] $([int]$used) / $([int]$total) MB" -ForegroundColor $memColor
    Write-Host "  Free for video $([int]$free) MB  -  $verdict" -ForegroundColor $memColor
    Write-Host "  Temperature    $([int]$temp) C"
    Write-Host ""
    Write-Host "  Apps holding graphics memory:" -ForegroundColor DarkGray

    $apps = & nvidia-smi --query-compute-apps=process_name --format=csv,noheader 2>$null
    if ($apps) {
        foreach ($a in $apps) {
            $name = Split-Path $a.Trim() -Leaf
            $label = switch -Wildcard ($name) {
                "*LM Studio*"    { "LM Studio (local LLM app)" }
                "llama-server*"  { "Ollama (your local Qwen model)" }
                "python*"        { "ComfyUI (the video engine)" }
                "*Omen*"         { "Omen Command Center (HP utility)" }
                default          { $name }
            }
            Write-Host "    - $label" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "    (none)" -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "  Refreshing every 2s. Ctrl+C to stop." -ForegroundColor DarkGray
    Start-Sleep -Seconds 2
}
