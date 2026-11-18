param(
    [switch]$SkipInstall,
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
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

function Get-DockerDesktopPath {
    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\Docker\Docker\Docker Desktop.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

function Test-DockerDaemon {
    Add-DockerCliToPath
    $docker = Get-Command docker.exe -ErrorAction SilentlyContinue
    if (-not $docker) {
        return $false
    }
    & $docker.Source info --format "{{.ServerVersion}}" 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Wait-DockerDaemon {
    param([int]$Seconds)
    $started = Get-Date
    $nextMessage = 0
    while (((Get-Date) - $started).TotalSeconds -lt $Seconds) {
        if (Test-DockerDaemon) {
            return $true
        }
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        if ($elapsed -ge $nextMessage) {
            Write-Host "Ожидаю Docker Engine... $elapsed/$Seconds сек." -ForegroundColor DarkGray
            $nextMessage += 15
        }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Stop-WithMessage {
    param([string]$Message, [int]$Code = 1)
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    Write-Host "Инструкция: docs\windows_docker_ru.md" -ForegroundColor Yellow
    exit $Code
}

Write-Host "SupplyMargin AI — установка Docker и запуск проекта" -ForegroundColor Cyan

Add-DockerCliToPath
$installationNeeded = (
    -not (Get-Command wsl.exe -ErrorAction SilentlyContinue) -or
    (-not (Get-DockerDesktopPath) -and
        -not (Get-Command docker.exe -ErrorAction SilentlyContinue))
)
if (-not $SkipInstall -and $installationNeeded -and -not (Test-Administrator)) {
    Write-Host "Запрашиваю права администратора для WSL/Docker Desktop..." -ForegroundColor Yellow
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"'),
        "-TimeoutSeconds", $TimeoutSeconds
    )
    $elevated = Start-Process powershell.exe `
        -Verb RunAs `
        -ArgumentList $arguments `
        -Wait `
        -PassThru
    exit $elevated.ExitCode
}

$virtualization = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty VirtualizationFirmwareEnabled
if ($virtualization -eq $false) {
    Stop-WithMessage (
        "Аппаратная виртуализация выключена. Включите Intel VT-x/AMD-V " +
        "в BIOS/UEFI и повторите запуск."
    ) 10
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    if ($SkipInstall) {
        Stop-WithMessage "WSL 2 не установлен." 11
    }
    Write-Host "Включаю компоненты WSL 2..." -ForegroundColor Yellow
    & dism.exe /online /enable-feature `
        /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
    $wslFeatureCode = $LASTEXITCODE
    & dism.exe /online /enable-feature `
        /featurename:VirtualMachinePlatform /all /norestart
    $vmFeatureCode = $LASTEXITCODE
    if ($wslFeatureCode -ne 0 -or $vmFeatureCode -ne 0) {
        Stop-WithMessage "Не удалось включить системные компоненты WSL 2." 12
    }
    Stop-WithMessage (
        "Компоненты WSL 2 включены. Перезагрузите Windows и снова запустите " +
        "INSTALL_AND_RUN_WINDOWS.cmd."
    ) 20
}

Write-Host "Обновляю WSL 2..." -ForegroundColor Cyan
& wsl.exe --update
if ($LASTEXITCODE -ne 0) {
    Write-Host "WSL не обновился автоматически; продолжаю с установленной версией." -ForegroundColor Yellow
}
& wsl.exe --set-default-version 2
if ($LASTEXITCODE -ne 0) {
    Write-Host "Не удалось назначить WSL 2 по умолчанию." -ForegroundColor Yellow
}

Add-DockerCliToPath
$dockerDesktop = Get-DockerDesktopPath
if (-not $dockerDesktop -and -not (Get-Command docker.exe -ErrorAction SilentlyContinue)) {
    if ($SkipInstall) {
        Stop-WithMessage "Docker Desktop не установлен." 13
    }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Host "WinGet не найден. Открываю официальную страницу Docker Desktop." -ForegroundColor Yellow
        Start-Process "https://docs.docker.com/desktop/setup/install/windows-install/"
        Stop-WithMessage (
            "Установите Docker Desktop с открывшейся страницы, затем снова " +
            "запустите INSTALL_AND_RUN_WINDOWS.cmd."
        ) 14
    }
    Write-Host "" 
    Write-Host "Docker Desktop устанавливается по лицензии Docker:" -ForegroundColor Yellow
    Write-Host "https://www.docker.com/legal/docker-subscription-service-agreement/"
    Write-Host "WinGet также запросит принятие условий источника пакетов."
    $consent = Read-Host "Введите YES, если согласны продолжить установку"
    if ($consent.Trim().ToUpperInvariant() -ne "YES") {
        Stop-WithMessage "Установка отменена пользователем: условия не приняты." 19
    }
    Write-Host "Устанавливаю Docker Desktop через WinGet..." -ForegroundColor Yellow
    & $winget.Source install --exact --id Docker.DockerDesktop `
        --source winget `
        --accept-package-agreements `
        --accept-source-agreements `
        --silent
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "WinGet не смог установить Docker Desktop (код $LASTEXITCODE)." 15
    }
    Add-DockerCliToPath
    $dockerDesktop = Get-DockerDesktopPath
}

if (-not (Test-DockerDaemon)) {
    if (-not $dockerDesktop) {
        $dockerDesktop = Get-DockerDesktopPath
    }
    if (-not $dockerDesktop) {
        Stop-WithMessage "Docker Desktop установлен, но Docker Desktop.exe не найден." 16
    }
    Write-Host "Запускаю Docker Desktop: $dockerDesktop" -ForegroundColor Cyan
    Start-Process $dockerDesktop
    Write-Host (
        "Если открылось лицензионное соглашение Docker Desktop, прочитайте " +
        "его и нажмите Accept. Вход в аккаунт Docker для локального запуска не нужен."
    ) -ForegroundColor Yellow
}

if (-not (Wait-DockerDaemon -Seconds $TimeoutSeconds)) {
    Write-Host "Перезапускаю WSL и Docker Desktop один раз..." -ForegroundColor Yellow
    & wsl.exe --shutdown
    Start-Sleep -Seconds 3
    $dockerDesktop = Get-DockerDesktopPath
    if ($dockerDesktop) {
        Start-Process $dockerDesktop
    }
    if (-not (Wait-DockerDaemon -Seconds 180)) {
        Stop-WithMessage (
            "Docker daemon не запустился. Возможна необходимость перезагрузки " +
            "Windows после установки WSL/Docker Desktop."
        ) 17
    }
}

Add-DockerCliToPath
$osType = (& docker.exe info --format "{{.OSType}}").Trim()
if ($osType -ne "linux") {
    Stop-WithMessage (
        "Сейчас выбран режим Windows containers. В меню Docker Desktop " +
        "выберите Switch to Linux containers и повторите запуск."
    ) 18
}

Write-Host "Docker daemon готов. Запускаю SupplyMargin AI..." -ForegroundColor Green
& "$PSScriptRoot\start_windows.ps1"
exit $LASTEXITCODE
