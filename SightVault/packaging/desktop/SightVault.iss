; Inno Setup 6 script for the per-user SightVault installer (no administrator rights).
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
DefaultDirName={autopf}\SightVault
DefaultGroupName=SightVault
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
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
