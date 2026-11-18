$ErrorActionPreference = "Continue"

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Add-DockerCliToPath {
    $candidateDirectories = @(
        "$env:ProgramFiles\Docker\Docker\resources\bin",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin",
        "$env:LOCALAPPDATA\Programs\Docker\Docker\resources\bin"
    )
    foreach ($directory in $candidateDirectories) {
        if ((Test-Path "$directory\docker.exe") -and
            (($env:Path -split ";") -notcontains $directory)) {
            $env:Path = "$directory;$env:Path"
        }
    }
}

Write-Host "SupplyMargin AI: проверка Docker на Windows" -ForegroundColor Cyan
$failed = $false

$windows = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
Write-Host "Windows: $($windows.ProductName), build $($windows.CurrentBuildNumber)"

$virtualization = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty VirtualizationFirmwareEnabled
if ($virtualization -eq $false) {
    Write-Host "[FAIL] Виртуализация отключена в BIOS/UEFI." -ForegroundColor Red
    $failed = $true
} elseif ($virtualization -eq $true) {
    Write-Host "[OK] Аппаратная виртуализация включена" -ForegroundColor Green
}

if (-not (Test-Command "wsl.exe")) {
    Write-Host "[FAIL] WSL не найден. Выполните от имени администратора: wsl --install" -ForegroundColor Red
    $failed = $true
} else {
    Write-Host "[OK] WSL найден" -ForegroundColor Green
    & wsl.exe --status
    & wsl.exe --version
}

Add-DockerCliToPath
if (-not (Test-Command "docker.exe")) {
    Write-Host "[FAIL] Docker CLI не найден." -ForegroundColor Red
    Write-Host "Установите Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/"
    $failed = $true
} else {
    Write-Host "[OK] Docker CLI найден" -ForegroundColor Green
    & docker.exe version
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Docker daemon не отвечает." -ForegroundColor Red
        Write-Host "Запустите Docker Desktop и дождитесь статуса Engine running."
        $failed = $true
    } else {
        $osType = (& docker.exe info --format "{{.OSType}}").Trim()
        & docker.exe info --format "Docker daemon: {{.ServerVersion}} / {{.OSType}}"
        if ($osType -ne "linux") {
            Write-Host "[FAIL] Нужен режим Linux containers." -ForegroundColor Red
            Write-Host "В меню Docker Desktop выберите Switch to Linux containers."
            $failed = $true
        }
    }
    & docker.exe compose version
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Docker Compose v2 недоступен." -ForegroundColor Red
        $failed = $true
    }
}

if ($failed) {
    Write-Host "Проверка не пройдена. См. docs/windows_docker_ru.md" -ForegroundColor Yellow
    exit 1
}

Write-Host "Все проверки пройдены: Docker daemon готов." -ForegroundColor Green
