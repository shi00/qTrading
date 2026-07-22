#ifndef MyAppVersion
#define MyAppVersion "0.9.0" ; x-release-please-version
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
; common: 递归收集 PyInstaller 产物，排除 sidecar binary（仅 embedded variant 收集）。
; maintenance 脚本位于 resources/ 不在 dist/AStockScreener/ 下，无需 Excludes。
Source: "dist\AStockScreener\*"; DestDir: "{app}"; Excludes: "\_internal\sidecars\"; Flags: ignoreversion recursesubdirs createallsubdirs
#if TargetVariant == "embedded"
; embedded variant: 收集 sidecar binary（PyInstaller 产物中存在时，避免无 sidecar 构建时 iscc 报错）
#ifexist "dist\AStockScreener\_internal\sidecars\qtrading-pg-sidecar.exe"
Source: "dist\AStockScreener\_internal\sidecars\*"; DestDir: "{app}\_internal\sidecars"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
; embedded variant: 收集离线维护脚本（pg_plan §16.2）
Source: "resources\maintenance\*"; DestDir: "{app}\resources\maintenance"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

[Icons]
Name: "{group}\AStockScreener"; Filename: "{app}\AStockScreener.exe"
Name: "{autodesktop}\AStockScreener"; Filename: "{app}\AStockScreener.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AStockScreener.exe"; Description: "启动 AStockScreener"; Flags: nowait postinstall skipifsilent
