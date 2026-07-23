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

#if TargetVariant == "embedded"
[UninstallRun]
; Phase 5 失败注入 #32 配套：卸载前先 stop sidecar（防止文件占用）；Check 确保开发环境无 sidecar binary 时不报错
; --data-dir 路径与 resources/maintenance/README-maintenance.md 一致（platformdirs 默认）
Filename: "{app}\_internal\sidecars\qtrading-pg-sidecar.exe"; Parameters: "stop --data-dir ""{localappdata}\qTrading\postgres\17\data"""; Flags: runhidden; RunOnceId: "StopSidecar"; Check: SidecarExists
#endif

#if TargetVariant == "embedded"
[Code]
// Phase 5 失败注入 #32 + P1-11：qTrading 主进程或 sidecar 运行中启动安装器被拒绝
// 使用 tasklist + find（Windows 原生，无需 PowerShell，Inno Setup Exec 调用 cmd 更稳定）
// find 返回 0 表示找到匹配（进程存在），非 0 表示未找到
function IsProcessRunning(const ProcessName: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  if Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq ' + ProcessName + '" | find "' + ProcessName + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
      Result := True;
  end;
end;

function InitializeSetup(): Boolean;
begin
  if IsProcessRunning('AStockScreener.exe') then
  begin
    MsgBox('AStockScreener 正在运行，请先关闭后再升级。', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  // P1-11: sidecar 运行中（用户手动启动维护实例或异常残留）也拒绝安装，
  // 避免升级期间 sidecar 持有 PGDATA 文件锁导致 [UninstallRun] stop 失败
  if IsProcessRunning('qtrading-pg-sidecar.exe') then
  begin
    MsgBox('qTrading 数据库服务正在运行，请先关闭 qTrading 后再升级。', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  Result := True;
end;

// Check 函数：sidecar binary 存在时才执行 [UninstallRun] 中的 stop 命令
// 开发环境或 standard variant 升级到 embedded（无 sidecar binary）时不报错
function SidecarExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\_internal\sidecars\qtrading-pg-sidecar.exe'));
end;
#endif
