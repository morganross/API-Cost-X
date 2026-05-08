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
SetupIconFile=..\assets\apicostx.ico

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

[Code]
function IsWebView2RuntimeInstalledInRoot(RootKey: Integer): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  if Result then begin
    Result := (Version <> '') and (Version <> '0.0.0.0');
  end;
end;

function NeedsWebView2: Boolean;
begin
  Result := not (
    IsWebView2RuntimeInstalledInRoot(HKCU) or
    IsWebView2RuntimeInstalledInRoot(HKLM) or
    IsWebView2RuntimeInstalledInRoot(HKLM32) or
    IsWebView2RuntimeInstalledInRoot(HKLM64)
  );
end;

[Run]
Filename: "{app}\runtime\webview2\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 Runtime..."; Flags: runhidden waituntilterminated; Check: NeedsWebView2
Filename: "{app}\{#MyAppExeName}"; Description: "Launch APICostX"; WorkingDir: "{localappdata}\APICostX"; Flags: nowait postinstall skipifsilent
