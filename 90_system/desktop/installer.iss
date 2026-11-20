#ifndef SourceDir
  #error Pass /DSourceDir with the built portable directory
#endif
#ifndef OutputDir
  #error Pass /DOutputDir with the release directory
#endif
[Setup]
AppId={{EADE9014-8C72-4A7C-A0A8-9E2807E409F8}
AppName=精读笔记库
AppVersion={#AppVersion}
AppPublisher=Local Literature Workspace
DefaultDirName={localappdata}\Programs\LiteratureNotes
DefaultGroupName=精读笔记库
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=精读笔记库-{#AppVersion}-安装
SetupIconFile=app.ico
UninstallDisplayIcon={app}\精读笔记库.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupMutex=Local\LiteratureNotes.Setup.v1
AppMutex=Local\LiteratureNotes.Desktop.v1
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Icons]
Name: "{group}\精读笔记库"; Filename: "{app}\精读笔记库.exe"
Name: "{autodesktop}\精读笔记库"; Filename: "{app}\精读笔记库.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\精读笔记库.exe"; Description: "打开精读笔记库"; Flags: nowait postinstall skipifsilent unchecked

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  { Program files are separate from projects; no research path is written or removed. }
end;
