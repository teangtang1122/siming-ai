#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#ifndef SourceDir
  #define SourceDir "..\release\Siming"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

#define MyAppName "司命"
#define MyAppExeName "Siming.exe"
#define MyAppPublisher "teangtang1122"
#define MyAppURL "https://github.com/teangtang1122/siming-ai"

[Setup]
AppId={{9D10D4A4-29F8-4F11-A88A-534A50F96D55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases/latest
DefaultDirName={localappdata}\Programs\Siming
DefaultGroupName={#MyAppName}
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.10240
OutputDir={#OutputDir}
OutputBaseFilename=Siming-Setup
SetupIconFile=..\backend\Siming.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd,*.chm
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
ChangesEnvironment=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "installed.marker"; DestDir: "{app}"; DestName: ".siming-installed"; Flags: ignoreversion

[InstallDelete]
; PyInstaller's runtime is immutable application code. Recreate it on every
; install so files removed by a newer release cannot survive an in-place update.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent; Check: ShouldLaunchSiming
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Flags: nowait skipifnotsilent; Check: ShouldLaunchSiming

[Code]
{ Inno Setup 6 has a 32-bit scripting host, including x64 install mode. }
function OpenRuntimeFile(FileName: String; Access, ShareMode: Cardinal;
  Security: LongWord; Creation, Attributes: Cardinal; Template: LongWord): LongWord;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseRuntimeFile(Handle: LongWord): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

procedure RequireReplaceableFile(const FileName: String);
var
  Handle: LongWord;
  ErrorCode: Integer;
begin
  { OPEN_EXISTING, write/delete access, all sharing allowed. This does not
    change the file, but detects loaded DLLs and handles preventing replacement. }
  Handle := OpenRuntimeFile(FileName, $40010000, 7, 0, 3, $80, 0);
  if Handle = $FFFFFFFF then begin
    ErrorCode := DLLGetLastError;
    { A file which disappeared while another installation exited is harmless. }
    if (ErrorCode = 2) or (ErrorCode = 3) then
      Exit;
    Log(Format('Runtime replacement blocked: %s (Windows error %d)', [FileName, ErrorCode]));
    RaiseException(Format(
      '安装已停止，尚未清理旧版程序。文件被占用或无法写入：'#13#10 +
      '%s'#13#10#13#10 +
      '请先保存工作并彻底退出司命（包括桌宠、后台及 MCP 进程），再重新安装。' +
      '若仍失败，请重启电脑后安装，并检查安装目录权限或安全软件拦截记录。' +
      ''#13#10'Windows 错误 %d：%s', [FileName, ErrorCode, SysErrorMessage(ErrorCode)]));
  end;
  CloseRuntimeFile(Handle);
end;

procedure RequireReplaceableDirectory(const Directory: String);
var
  Item: TFindRec;
  FileName: String;
begin
  if not DirExists(Directory) then
    Exit;
  if not FindFirst(AddBackslash(Directory) + '*', Item) then
    RaiseException('安装已停止：无法读取旧版运行库，请检查目录权限：' + Directory);
  try
    repeat
      if (Item.Name <> '.') and (Item.Name <> '..') then begin
        FileName := AddBackslash(Directory) + Item.Name;
        if (Item.Attributes and $400) <> 0 then
          RaiseException('安装已停止：运行库内存在目录链接，请检查后重新安装：' + FileName);
        if (Item.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          RequireReplaceableDirectory(FileName)
        else
          RequireReplaceableFile(FileName);
      end;
    until not FindNext(Item);
  finally
    FindClose(Item);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Attempt: Integer;
begin
  { The in-app updater exits after flushing its HTTP response. Give it a brief
    chance to release files, then require a manual exit before any cleanup.
    An InstallDelete Check is unsafe: Inno treats its exception as "skip" and
    can continue copying files into the old runtime. PrepareToInstall instead
    blocks the entire installation and lets the author retry after closing. }
  for Attempt := 1 to 11 do begin
    try
      RequireReplaceableFile(ExpandConstant('{app}\{#MyAppExeName}'));
      RequireReplaceableDirectory(ExpandConstant('{app}\_internal'));
      Result := '';
      Exit;
    except
      Result := GetExceptionMessage;
    end;
    if Attempt < 11 then
      Sleep(250);
  end;
end;

function ShouldLaunchSiming(): Boolean;
begin
  Result := CompareText(ExpandConstant('{param:SIMINGNOLAUNCH|0}'), '1') <> 0;
end;
