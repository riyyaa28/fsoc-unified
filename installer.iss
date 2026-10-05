; Windows installer for the PyInstaller build in dist\FSOC Control Center.
; Build: "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
; Output: installer\FSOC_Control_Center_Setup.exe

#define AppName "FSOC Control Center"
#define AppVersion "1.0"
#define AppExe "FSOC Control Center.exe"

[Setup]
AppId={{6E0B2C4A-3F7D-4C1E-9B8A-2D5F7A9C1E34}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install: no administrator rights needed.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=FSOC_Control_Center_Setup
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
