param(
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$stdoutLog = Join-Path $env:TEMP "sultaan-ems-cloudflared-out.log"
$stderrLog = Join-Path $env:TEMP "sultaan-ems-cloudflared-err.log"

if (-not (Test-Path $python)) {
    throw "The EMS virtual environment was not found at $python"
}
if (-not (Test-Path $cloudflared)) {
    throw "cloudflared was not found. Install Cloudflare.cloudflared with winget first."
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if (-not $listener) {
    $serverCommand = "from app import create_app; create_app().run(host='0.0.0.0', port=$Port, debug=False, use_reloader=False)"
    Start-Process -FilePath $python -ArgumentList "-c", $serverCommand -WorkingDirectory $projectRoot -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
    throw "EMS did not start on port $Port. Check the application logs and retry."
}

Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item -LiteralPath $stdoutLog -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $stderrLog -Force -ErrorAction SilentlyContinue

Start-Process -FilePath $cloudflared -ArgumentList "tunnel", "--url", "http://127.0.0.1:$Port" -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

$url = $null
for ($i = 0; $i -lt 30 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $stderrLog) {
        $match = [regex]::Match(
            (Get-Content -LiteralPath $stderrLog -Raw),
            'https://[a-z0-9-]+\.trycloudflare\.com'
        )
        if ($match.Success) {
            $url = $match.Value
        }
    }
}

if (-not $url) {
    Get-Content $stdoutLog, $stderrLog -ErrorAction SilentlyContinue
    throw "Cloudflare Tunnel did not provide a public URL."
}

Write-Host "SULTAAN EMS is available temporarily at:" -ForegroundColor Green
Write-Host $url -ForegroundColor Cyan
Write-Host "Keep this PowerShell window/session available while testing." -ForegroundColor Yellow
