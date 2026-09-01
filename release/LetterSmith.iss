#define MyAppName "Letter Smith"
#define MyAppVersion "1.0.0-beta.1"
#define MyAppPublisher "Infini Works"
#define MyAppExeName "LetterSmith.exe"
#define MyInstallerBaseName "LetterSmith-Beta-Setup-1.0.0"

[Setup]
AppId={{44498786-B309-5F44-9BDB-E2B7250E6304}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=Copyright Infini Works
DefaultDirName={autopf}\Infini Works\Letter Smith
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename={#MyInstallerBaseName}
SetupIconFile=..\gallery\app\icons\folder\lsmith.ico
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.10240
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
CreateUninstallRegKey=yes
Uninstallable=yes
SetupLogging=yes
ChangesAssociations=no
ChangesEnvironment=no
VersionInfoVersion=1.0.0.0
VersionInfoProductVersion=1.0.0.0
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoTextVersion={#MyAppVersion}
#ifdef LetterSmithSignedRelease
SignTool=lettersmith
SignedUninstaller=yes
SignToolRetryCount=3
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\LetterSmith\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; User data is deliberately outside {app}. Upgrades and uninstall leave
; Local AppData\Infini Works\Letter Smith and Documents\Letter Smith intact.
