; АВРОРА — установщик Windows
; Сборка: build-installer.ps1 (Inno Setup 6)

#define MyAppName "АВРОРА"
#define MyAppVersion "0.4.2"
#define MyAppPublisher "Aurora Monitor"
#define MyAppURL "https://github.com/afirius/aurora-monitor"
#define MyAppExeName "AuroraMonitor.exe"

[Setup]
AppId={{44A7D075-5BA3-4E16-BB2F-191CC792EB85}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=GPL-3.0-or-later
DefaultDirName={commonpf32}\35inchENG\35inchENG
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=dist
OutputBaseFilename=AuroraMonitor-Setup-{#MyAppVersion}
SetupIconFile=assets\aurora-icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
CloseApplicationsFilter=AuroraMonitor.exe,UsbMonitor.exe
RestartApplications=no
SetupLogging=yes
LicenseFile=LICENSE
VersionInfoVersion=0.4.2.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} — установщик
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "autostart"; Description: "Запускать АВРОРУ при входе в Windows"; GroupDescription: "Автозапуск:"; Flags: checkedonce

[Files]
Source: "dist\AuroraMonitor.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Driver\usbser\cdc.inf"; DestDir: "{app}\Driver\usbser"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "vendor\LibreHardwareMonitor-LICENSE.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "vendor\PawnIO-LICENSE.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "vendor\PawnIO_setup.exe"; Flags: dontcopy

[InstallDelete]
Type: files; Name: "{app}\UsbMonitor.exe"
Type: files; Name: "{app}\BouncyCastle.Crypto.dll"
Type: files; Name: "{app}\Microsoft.Win32.TaskScheduler.dll"
Type: files; Name: "{app}\msvcp140_1d.dll"
Type: files; Name: "{app}\msvcr110d.dll"
Type: files; Name: "{app}\msvcr120d.dll"
Type: files; Name: "{app}\NAudio.dll"
Type: files; Name: "{app}\Newtonsoft.Json.dll"
Type: files; Name: "{app}\code.ini"
Type: files; Name: "{app}\DriverinStallationGuide.txt"
Type: files; Name: "{app}\NET_download.txt"
Type: files; Name: "{app}\Theme Notes.jpg"
Type: files; Name: "{app}\Troubleshooting.txt"
Type: files; Name: "{app}\guide.jpg"
Type: files; Name: "{app}\Запустить Аврору.cmd"
Type: files; Name: "{app}\Запустить Аврору.lnk"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\config"
Type: filesandordirs; Name: "{app}\3.5"
Type: filesandordirs; Name: "{app}\%SystemDrive%"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{app}\Запустить Аврору"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--disable-autostart"; Flags: runhidden waituntilterminated; RunOnceId: "DisableAutostart"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""AuroraMonitor"" /F"; Flags: runhidden; RunOnceId: "DeleteAuroraTask"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""UsbMonitor"" /F"; Flags: runhidden; RunOnceId: "DeleteUsbMonitorTask"

[Code]
function PawnIOInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf}\PawnIO\PawnIOLib.dll')) or
            FileExists(ExpandConstant('{commonpf32}\PawnIO\PawnIOLib.dll'));
end;

procedure StopOldMonitors();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM AuroraMonitor.exe /F /T', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/IM UsbMonitor.exe /F /T', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('schtasks.exe', '/Delete /TN "UsbMonitor" /F', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopOldMonitors();
  Result := '';
end;

procedure InstallPawnIO();
var
  ResultCode: Integer;
begin
  if PawnIOInstalled() then
  begin
    Log('PawnIO уже установлен.');
    Exit;
  end;
  ExtractTemporaryFile('PawnIO_setup.exe');
  if Exec(ExpandConstant('{tmp}\PawnIO_setup.exe'), '-install -silent', '',
          SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
    Log('PawnIO установлен.')
  else
  begin
    Log('PawnIO не установлен, код ' + IntToStr(ResultCode) + '.');
    if not WizardSilent() then
      MsgBox('Не удалось установить PawnIO. Датчики CPU и ОЗУ могут быть недоступны. ' +
             'Установите PawnIO вручную и запустите АВРОРУ от имени администратора.',
             mbInformation, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Params: String;
begin
  if CurStep = ssPostInstall then
  begin
    InstallPawnIO();
    if WizardIsTaskSelected('autostart') then
      Params := '--install-autostart'
    else
      Params := '--disable-autostart';
    if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), Params,
                ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated,
                ResultCode) or (ResultCode <> 0) then
      if not WizardSilent() then
        MsgBox('Не удалось настроить автозапуск. Включите или выключите его в настройках АВРОРЫ.',
               mbInformation, MB_OK);
  end;
end;
