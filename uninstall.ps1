param(
    [string]$InstallDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$RemoveSettings
)

$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $env:TEMP "AuroraMonitor-uninstall-$stamp.log"

function Write-Log([string]$message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
    Write-Host $message
}

try {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Запустите uninstall.ps1 от имени администратора.'
    }

    & schtasks.exe /End /TN 'AuroraMonitor' 2>$null | Out-Null
    Get-Process -Name 'AuroraMonitor' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500

    foreach ($taskName in 'AuroraMonitor', 'UsbMonitor') {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            Write-Log "Задача $taskName удалена."
        }
    }
    Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
        -Name 'AuroraMonitor' -ErrorAction SilentlyContinue

    $exe = Join-Path $InstallDir 'AuroraMonitor.exe'
    if (Test-Path -LiteralPath $exe) {
        Remove-Item -LiteralPath $exe -Force
        Write-Log "Удалён $exe"
    }
    foreach ($shortcut in @(
            (Join-Path ([Environment]::GetFolderPath('Desktop')) 'АВРОРА.lnk'),
            (Join-Path ([Environment]::GetFolderPath('Programs')) 'АВРОРА.lnk'),
            (Join-Path $InstallDir 'Запустить Аврору.lnk'))) {
        if (Test-Path -LiteralPath $shortcut) {
            Remove-Item -LiteralPath $shortcut -Force
        }
    }
    if ($RemoveSettings) {
        $settingsDir = Join-Path $env:LOCALAPPDATA 'AuroraMonitor'
        if (Test-Path -LiteralPath $settingsDir) {
            Remove-Item -LiteralPath $settingsDir -Recurse -Force
            Write-Log 'Настройки и журналы удалены.'
        }
    }
    Write-Log "АВРОРА удалена. Журнал: $log"
} catch {
    Write-Log "ОШИБКА: $($_.Exception.Message)"
    exit 1
}
