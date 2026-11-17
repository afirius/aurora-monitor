param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Создаю виртуальное окружение Python 3.12...'
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать .venv. Установите Python 3.12.' }
    $python = Join-Path $root '.venv\Scripts\python.exe'
    & $python -m pip install --disable-pip-version-check -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
}

& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Устанавливаю PyInstaller...'
    & $python -m pip install --disable-pip-version-check -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить PyInstaller.' }
}

$arguments = @('-m', 'PyInstaller', '--noconfirm')
if ($Clean) { $arguments += '--clean' }
$arguments += 'AuroraMonitor.spec'
# PyInstaller writes progress to stderr; Start-Process avoids PowerShell 5.1
# turning those lines into terminating NativeCommandError records.
$process = Start-Process -FilePath $python -ArgumentList $arguments `
    -NoNewWindow -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Сборка завершилась с ошибкой.' }

$exe = Join-Path $root 'dist\AuroraMonitor.exe'
if (-not (Test-Path $exe)) { throw 'PyInstaller не создал AuroraMonitor.exe.' }
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Готово: $exe ($size МБ)"
