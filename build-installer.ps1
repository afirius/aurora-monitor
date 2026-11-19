param(
    [switch]$SkipBuild,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$version = '0.4.2'

if (-not $SkipBuild) {
    & (Join-Path $root 'build.ps1') -Clean:$Clean
    if ($LASTEXITCODE -ne 0) { throw 'Сборка AuroraMonitor.exe завершилась с ошибкой.' }
} elseif (-not (Test-Path (Join-Path $root 'dist\AuroraMonitor.exe'))) {
    throw 'Нет dist\AuroraMonitor.exe. Запустите build.ps1 или сборку без -SkipBuild.'
}

$candidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$iscc = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    throw ('Не найден ISCC.exe. Установите Inno Setup 6: ' +
        'winget install --id JRSoftware.InnoSetup -e')
}
$pawnio = Join-Path $root 'vendor\PawnIO_setup.exe'
if (-not (Test-Path -LiteralPath $pawnio)) {
    throw ('Не найден vendor\PawnIO_setup.exe. Скачайте PawnIO 2.2.0 и положите ' +
        'установщик в vendor (см. README, раздел «Разработка»).')
}

Write-Host "Компилирую установщик: $iscc"
$process = Start-Process -FilePath $iscc -ArgumentList @('installer.iss') `
    -NoNewWindow -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Inno Setup завершился с ошибкой.' }

$setup = Join-Path $root "dist\AuroraMonitor-Setup-$version.exe"
if (-not (Test-Path -LiteralPath $setup)) {
    throw "Inno Setup не создал $setup."
}
$size = [math]::Round((Get-Item -LiteralPath $setup).Length / 1MB, 1)
Write-Host "Готово: $setup ($size МБ)"
