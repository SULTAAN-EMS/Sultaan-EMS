Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "Cloudflare Tunnel stopped." -ForegroundColor Yellow
