; Inno Setup 6 script for AURORA: wraps the PyInstaller folder dist\AURORA into dist\AURORA-<version>-windows-x64-setup.exe
; (Start-menu entry, optional desktop shortcut, optional 'Open in AURORA' folder verb, uninstaller). Compile after
; PyInstaller, from the repository root:
;
;     ISCC.exe /DMyAppVersion=<version> packaging\aurora.iss
;
; packaging\build_win.bat does the whole chain and takes the version from packaging\specutil.py, so this file never
; carries a version number: compiling without /DMyAppVersion stops with an error.
;
; Per-user by default: no administrator rights needed on a lab or university computer (the app goes to
; %LOCALAPPDATA%\Programs\AURORA). The wizard offers an all-users install when the account can elevate.
; Silent install for IT:
;     AURORA-<version>-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER [/TASKS="desktopicon"]
; Settings (registry HKCU\Software\AURORA), logs (%LOCALAPPDATA%\AURORA\Logs) and beamtime folders are never touched by
; the installer or the uninstaller.

#ifndef MyAppVersion
  #error Pass the version: ISCC /DMyAppVersion=<x.y.z> packaging\aurora.iss (packaging\build_win.bat does)
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
; the AppId is AURORA's own and never changes: a newer setup finds and upgrades the installed copy
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
PrivilegesRequiredOverridesAllowed=commandline dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-windows-x64-setup
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
; upgrades: same AppId, so the setup keeps the installed folder, privileges and tasks, closes a running AURORA and
; replaces the files ([InstallDelete] wipes the previous _internal first)
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
Name: "folderverb"; Description: "Add 'Open in AURORA' to the right-click menu of folders"; GroupDescription: "Explorer:"; Flags: unchecked

[InstallDelete]
; the previous version's runtime
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; dist\AURORA holds AURORA.exe, aurora-cli.exe, _internal\ and LICENSE, CREDITS.md, THIRD_PARTY_LICENSES.txt, LICENSES\
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{userdocs}"; Comment: "Soft X-ray XAS and XRF stacks (SGM, CLS)"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{userdocs}"; Tasks: desktopicon

[Registry]
; Explorer: right-click a folder > Open in AURORA (a beamtime folder, a loose folder of scans). HKA is HKCU for the
; default per-user install and HKLM for an all-users install.
Root: HKA; Subkey: "Software\Classes\Directory\shell\AURORA"; ValueType: string; ValueName: ""; ValueData: "Open in AURORA"; Flags: uninsdeletekey; Tasks: folderverb
Root: HKA; Subkey: "Software\Classes\Directory\shell\AURORA"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#MyAppExeName}"",0"; Tasks: folderverb
Root: HKA; Subkey: "Software\Classes\Directory\shell\AURORA\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: folderverb

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
