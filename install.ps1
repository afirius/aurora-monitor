param(
    [string]$InstallDir = (Split-Path -Parent $PSScriptRoot),
    [string]$SourceExe = (Join-Path $PSScriptRoot 'dist\AuroraMonitor.exe'),
    [switch]$NoAutostart,
    [switch]$NoShortcuts,
    [switch]$NoStart,
    [switch]$UserMode
)

$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $env:TEMP "AuroraMonitor-install-$stamp.log"

function Write-Log([string]$message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
    Write-Host $message
}

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-Administrator {
    if (-not (Test-Administrator)) {
        throw 'Запустите install.ps1 от имени администратора.'
    }
}

function Remove-StartupValue {
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    Remove-ItemProperty -Path $runKey -Name 'AuroraMonitor' -ErrorAction SilentlyContinue
}

function Register-AuroraTask([string]$target, [string]$workingDirectory) {
    $user = if ($env:USERDOMAIN) {
        '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME
    } else {
        $env:USERNAME
    }
    $action = New-ScheduledTaskAction -Execute $target -Argument '--autostart' `
        -WorkingDirectory $workingDirectory
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = 'PT20S'
    $principal = New-ScheduledTaskPrincipal -UserId $user `
        -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName 'AuroraMonitor' -Action $action `
        -Trigger $trigger -Principal $principal -Settings $settings `
        -Description 'АВРОРА — монитор компьютера' -Force | Out-Null
}

function New-Shortcut([string]$path, [string]$target, [string]$workingDirectory) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = $workingDirectory
    $shortcut.IconLocation = "$target,0"
    $shortcut.Description = 'АВРОРА — монитор компьютера и USB-экрана'
    $shortcut.Save()
}

if (-not $UserMode -and -not (Test-Administrator)) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
    foreach ($name in $PSBoundParameters.Keys) {
        if ($name -eq 'UserMode') {
            continue
        }
        $value = $PSBoundParameters[$name]
        if ($value -is [switch]) {
            if ($value.IsPresent) {
                $arguments += "-$name"
            }
        } else {
            $arguments += "-$name"
            $arguments += $value
        }
    }
    $argumentLine = ($arguments | ForEach-Object {
        if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ }
    }) -join ' '
    try {
        $process = Start-Process -FilePath 'powershell.exe' -Verb RunAs `
            -ArgumentList $argumentLine -Wait -PassThru
        exit $process.ExitCode
    } catch {
        Write-Host ('Не удалось получить права администратора: ' +
            $_.Exception.Message) -ForegroundColor Red
        Write-Host ('Запустите PowerShell от имени администратора и повторите ' +
            'install.ps1.') -ForegroundColor Yellow
        exit 1
    }
}

try {
    if (-not $UserMode) {
        Assert-Administrator
        Write-Log "Установка АВРОРЫ в $InstallDir (с правами администратора)"
    } else {
        Write-Log "Установка АВРОРЫ в $InstallDir (режим пользователя)"
    }
    if (-not (Test-Path -LiteralPath $SourceExe)) {
        throw "Не найден собранный файл: $SourceExe. Сначала запустите build.ps1."
    }
    if (-not (Test-Path -LiteralPath $InstallDir)) {
        throw "Не найден каталог установки: $InstallDir"
    }

    $chineseItems = @(
        'UsbMonitor.exe', 'app', 'BouncyCastle.Crypto.dll',
        'Microsoft.Win32.TaskScheduler.dll', 'msvcp140_1d.dll',
        'msvcr110d.dll', 'msvcr120d.dll', 'NAudio.dll',
        'Newtonsoft.Json.dll', 'code.ini', 'DriverinStallationGuide.txt',
        'NET_download.txt', 'Theme Notes.jpg', 'Troubleshooting.txt',
        'guide.jpg', 'config', '3.5', '%SystemDrive%'
    )

    Write-Log 'Останавливаю старый монитор и АВРОРУ...'
    & schtasks.exe /End /TN 'UsbMonitor' 2>$null | Out-Null
    Get-Process -Name 'UsbMonitor', 'AuroraMonitor' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500

    $stage = Join-Path $env:TEMP "AuroraMonitor-chinese-$stamp"
    $backup = Join-Path $env:TEMP "AuroraMonitor-chinese-backup-$stamp.zip"
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    Write-Log "Сохраняю китайскую версию в резервную копию: $backup"
    foreach ($item in $chineseItems) {
        $source = Join-Path $InstallDir $item
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination $stage -Recurse -Force
        }
    }
    $taskXml = & schtasks.exe /Query /TN 'UsbMonitor' /XML 2>$null
    if ($LASTEXITCODE -eq 0 -and $taskXml) {
        $taskXml | Out-File -LiteralPath (Join-Path $stage 'UsbMonitor-task.xml') `
            -Encoding Unicode
    }
    if (Get-ChildItem -LiteralPath $stage -Force) {
        Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $backup `
            -CompressionLevel Optimal -Force
        Write-Log "Резервная копия создана."
    }
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue

    if (-not $UserMode) {
        $oldTask = Get-ScheduledTask -TaskName 'UsbMonitor' -ErrorAction SilentlyContinue
        if ($oldTask) {
            Unregister-ScheduledTask -TaskName 'UsbMonitor' -Confirm:$false
            Write-Log 'Задача автозапуска UsbMonitor удалена.'
        }
    } else {
        Write-Log ('Без прав администратора задача UsbMonitor не удаляется; ' +
            'она больше не запустит китайскую программу, потому что EXE удалён.')
    }

    Write-Log 'Удаляю файлы китайской версии...'
    foreach ($item in $chineseItems) {
        $target = Join-Path $InstallDir $item
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }

    $targetExe = Join-Path $InstallDir 'AuroraMonitor.exe'
    Copy-Item -LiteralPath $SourceExe -Destination $targetExe -Force
    Write-Log "Установлен $targetExe"

    $oldLauncher = Join-Path $InstallDir 'Запустить Аврору.cmd'
    if (Test-Path -LiteralPath $oldLauncher) {
        Remove-Item -LiteralPath $oldLauncher -Force
    }

    if (-not $NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $desktop = [Environment]::GetFolderPath('Desktop')
        $programs = [Environment]::GetFolderPath('Programs')
        New-Shortcut (Join-Path $desktop 'АВРОРА.lnk') $targetExe $InstallDir
        New-Shortcut (Join-Path $programs 'АВРОРА.lnk') $targetExe $InstallDir
        New-Shortcut (Join-Path $InstallDir 'Запустить Аврору.lnk') $targetExe $InstallDir
        Write-Log 'Ярлыки созданы на рабочем столе и в меню «Пуск».'
    }

    $settingsDir = Join-Path $env:LOCALAPPDATA 'AuroraMonitor'
    New-Item -ItemType Directory -Force -Path $settingsDir | Out-Null
    $settingsPath = Join-Path $settingsDir 'settings.json'
    if (Test-Path -LiteralPath $settingsPath) {
        try {
            # Windows PowerShell 5.1 reads BOM-less files as ANSI without this;
            # that would turn «Москва» into mojibake on every reinstall.
            $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 |
                ConvertFrom-Json
            $settings | Add-Member -NotePropertyName autostart `
                -NotePropertyValue (-not $NoAutostart) -Force
            $json = $settings | ConvertTo-Json -Depth 6
            [IO.File]::WriteAllText($settingsPath, $json,
                [Text.UTF8Encoding]::new($false))
        } catch {
            Write-Log "Не удалось обновить settings.json: $($_.Exception.Message)"
        }
    } else {
        $json = @{ autostart = (-not $NoAutostart) } | ConvertTo-Json
        [IO.File]::WriteAllText($settingsPath, $json,
            [Text.UTF8Encoding]::new($false))
    }

    if ($UserMode) {
        Remove-StartupValue
        if (-not $NoAutostart) {
            $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
            New-Item -Path $runKey -Force | Out-Null
            Set-ItemProperty -Path $runKey -Name 'AuroraMonitor' `
                -Value ('"{0}" --autostart' -f $targetExe)
            Write-Log 'Пользовательский автозапуск АВРОРЫ включён.'
        } else {
            Write-Log 'Автозапуск отключён параметром -NoAutostart.'
        }
    } else {
        $existingTask = Get-ScheduledTask -TaskName 'AuroraMonitor' -ErrorAction SilentlyContinue
        if ($existingTask) {
            Unregister-ScheduledTask -TaskName 'AuroraMonitor' -Confirm:$false
        }
        if (-not $NoAutostart) {
            Register-AuroraTask $targetExe $InstallDir
            Remove-StartupValue
            Write-Log 'Автозапуск АВРОРЫ при входе в Windows включён.'
        } else {
            Remove-StartupValue
            Write-Log 'Автозапуск отключён параметром -NoAutostart.'
        }
    }

    if (-not $NoStart) {
        Start-Process -FilePath $targetExe -WorkingDirectory $InstallDir
        Write-Log 'АВРОРА запущена.'
    }
    Write-Log "Готово. Журнал: $log"
} catch {
    Write-Log "ОШИБКА: $($_.Exception.Message)"
    Write-Host "Установка не завершена. Журнал: $log" -ForegroundColor Red
    exit 1
}
