#define MyAppName "APICostX"
#ifndef MyAppVersion
#define MyAppVersion "0.0.0-local"
#endif
#define MyAppPublisher "APICostX Contributors"
#define MyAppExeName "APICostX.exe"

[Setup]
AppId={{8DA4323E-3B41-49C7-8A5E-58C36AF2C03C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\APICostX
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist\installer
OutputBaseFilename=APICostX-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\APICostX\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{localappdata}\APICostX"
Name: "{localappdata}\APICostX\data"
Name: "{localappdata}\APICostX\logs"

[Icons]
Name: "{autoprograms}\APICostX"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{localappdata}\APICostX"
Name: "{autodesktop}\APICostX"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{localappdata}\APICostX"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch APICostX"; WorkingDir: "{localappdata}\APICostX"; Flags: nowait postinstall skipifsilent
