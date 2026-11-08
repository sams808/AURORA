; Inno Setup script for AURORA: wraps the PyInstaller folder dist\AURORA into dist\AURORA-<version>-windows-setup.exe
; (Start-menu entries, optional desktop shortcut, uninstaller). Compile after PyInstaller, from the repository root:
;
;     ISCC.exe /DMyAppVersion=0.4.0 packaging\aurora.iss
;
; packaging\build_win.bat does the whole chain. The version comes from the command line, so this file never carries
; a stale number.
;
; Per-user by default: no administrator rights needed on a lab or university computer (the app goes to
; %LOCALAPPDATA%\Programs\AURORA). The wizard offers an all-users install when the account can elevate.
; Silent install for IT:  AURORA-<v>-windows-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART [/TASKS="desktopicon"]

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "AURORA"
#define MyAppPublisher "Sami Soudani, Washington State University"
#define MyAppURL "https://github.com/sams808/AURORA"
#define MyAppExeName "AURORA.exe"
#ifndef SourceDir
  #define SourceDir "..\dist\AURORA"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{E4B9D82B-3D95-4DE5-805B-887FAFE5BFA1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
AppComments=Soft X-ray XAS and XRF stacks from the SGM beamline, Canadian Light Source. Version 2 of John Bussey's SGM XAS GUI, based on James J. Dynes' SGM notebooks.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-windows-setup
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
; upgrades: the AppId never changes, so a newer setup finds the installed copy, keeps its folder, privileges and
; tasks, closes a running AURORA and replaces the files ([InstallDelete] wipes the previous _internal first).
UsePreviousAppDir=yes
UsePreviousPrivileges=yes
UsePreviousTasks=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
SetupIconFile=..\branding\aurora.ico
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; the previous version's runtime; settings (registry), logs (%LOCALAPPDATA%\AURORA) and the beamtime folders are never touched
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CREDITS.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Soft X-ray XAS and XRF stacks (SGM, CLS)"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
