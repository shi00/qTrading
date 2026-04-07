#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif

#ifndef TargetVariant
#define TargetVariant "unknown"
#endif

[Setup]
AppId={{B1A2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName=AStockScreener
AppVersion={#MyAppVersion}
AppPublisher=QTrading
; Force user-level installation to prevent UAC / access denied issues
DefaultDirName={localappdata}\Programs\AStockScreener
DefaultGroupName=AStockScreener
DisableProgramGroupPage=yes
OutputBaseFilename=AStockScreener-Setup-{#TargetVariant}
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
SetupIconFile=assets\icon.ico
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
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
