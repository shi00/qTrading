#ifndef MyAppVersion
; x-release-please-version
#define MyAppVersion "0.6.5"
#endif

#ifndef TargetVariant
#define TargetVariant "unknown"
#endif

[Setup]
; Stable AppId for in-place upgrades. Do not change after release.
AppId={{7E6D0B44-BD44-4E46-8B5C-3A5F2D237C1A}
AppName=AStockScreener
AppVersion={#MyAppVersion}
AppPublisher=QTrading
; Force user-level installation to prevent UAC / access denied issues
DefaultDirName={localappdata}\Programs\AStockScreener
DefaultGroupName=AStockScreener
DisableProgramGroupPage=yes
OutputBaseFilename=AStockScreener-Setup-{#TargetVariant}
OutputDir=Output
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
SetupIconFile=assets\icon.ico
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "assets\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\AStockScreener\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AStockScreener"; Filename: "{app}\AStockScreener.exe"
Name: "{autodesktop}\AStockScreener"; Filename: "{app}\AStockScreener.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AStockScreener.exe"; Description: "启动 AStockScreener"; Flags: nowait postinstall skipifsilent
