; Inno Setup 6 script for a strictly per-user SightVault installer (no administrator rights).
; Invoked by build_windows.ps1 with /DAppVersion, /DSourceDir and /DOutputDir.
; The user's library, models and logs live in %LOCALAPPDATA%\SightVault and are kept on uninstall.
#ifndef AppVersion
  #error AppVersion must be defined
#endif
#ifndef SourceDir
  #error SourceDir must be defined
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{6F1D3C2A-8B4E-4F7A-9C21-5E3B7A9D0C14}
AppName=SightVault
AppVersion={#AppVersion}
AppPublisher=SightVault
; Per-user install: no administrator rights, no machine-wide files.
DefaultDirName={autopf}\SightVault
DefaultGroupName=SightVault
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 version 2004 (10.0.19041) or later. The WebView2 runtime is required;
; Windows 11 includes it, older Windows 10 hosts may need the Microsoft runtime.
MinVersion=10.0.19041
SetupIconFile=icons\SightVault.ico
OutputDir={#OutputDir}
OutputBaseFilename=SightVault-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\SightVault.exe
CloseApplications=yes

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SightVault"; Filename: "{app}\SightVault.exe"
Name: "{autodesktop}\SightVault"; Filename: "{app}\SightVault.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\SightVault.exe"; Description: "Open SightVault"; Flags: nowait postinstall skipifsilent

; The library, index, stored evidence, models and logs live in the per-user data folder
; (%LOCALAPPDATA%\SightVault or SIGHTVAULT_HOME), never in {app}. Uninstalling removes
; only the installed program files and intentionally leaves that data in place. No
; [UninstallDelete] entry may be added for it.
