param([int]$HealthTimeoutSeconds = 180)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

& "$PSScriptRoot\check_docker_windows.ps1"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Создан .env. Перед публичным запуском замените JWT, БД и S3 секреты." -ForegroundColor Yellow
}

& docker.exe compose up --build -d
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up завершился с кодом $LASTEXITCODE."
}
& docker.exe compose ps

$health = $null
$started = Get-Date
while (((Get-Date) - $started).TotalSeconds -lt $HealthTimeoutSeconds) {
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:8000/healthz" -TimeoutSec 5
        if ($health.status -eq "ok") {
            break
        }
    } catch {
        Start-Sleep -Seconds 3
    }
}
if (-not $health -or $health.status -ne "ok") {
    & docker.exe compose logs --tail 80 api worker
    throw "Сервис не прошел healthcheck за $HealthTimeoutSeconds секунд."
}
Write-Host "SupplyMargin AI: $($health.status), mode=$($health.mode)" -ForegroundColor Green
Write-Host "Dashboard: http://localhost:8000/"
Write-Host "Swagger:   http://localhost:8000/docs"
