; packaging/windows/kurulum.iss  (UTF-8 with BOM: Inno Setup 6 reads the
; Turkish text correctly only with it)
;
; The Windows installer of Göz Faresi. Built by derle.ps1:
;   ISCC.exe /DSurum=1.3.0 /DKaynak=<PyInstaller onedir> /DCikti=<dist dir>
;            /DSimge=<icon.ico> kurulum.iss
;
; - Per user, no administrator rights (spec 6.1): %LOCALAPPDATA%\Programs\
;   GozFaresi. The program's data (config.yaml, calibration, models) lives in
;   %LOCALAPPDATA%\GozFaresi - a different folder, created by the program.
; - Uninstalling removes only the program files. The data folder (the
;   calibration took this user real physical effort) is kept unless the
;   separate "also delete" box - OFF by default - is ticked; a silent
;   uninstall never deletes it.
; - The installed copy has no "tasinabilir" marker, so it runs in installed
;   mode (eye_gaze.paths); the portable zip is the one with the marker.

#ifndef Surum
  #error Surum tanimli degil: ISCC /DSurum=1.3.0 ...
#endif
#ifndef Kaynak
  #error Kaynak tanimli degil: ISCC /DKaynak=<onedir> ...
#endif
#ifndef Cikti
  #define Cikti "."
#endif

[Setup]
; Fixed forever: Windows recognises updates of the same program by it.
AppId={{6E0D8E61-3C4B-4C55-9C1A-6A1F0B7E2F31}
AppName=Göz Faresi
AppVersion={#Surum}
AppVerName=Göz Faresi {#Surum}
AppPublisher=eye-track project
VersionInfoVersion={#Surum}
VersionInfoProductName=Göz Faresi
VersionInfoDescription=Göz Faresi kurulumu
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\GozFaresi
DefaultGroupName=Göz Faresi
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
OutputDir={#Cikti}
OutputBaseFilename=GozFaresi-{#Surum}-Windows-Kurulum
#ifdef Simge
SetupIconFile={#Simge}
#endif
UninstallDisplayIcon={app}\GozFaresi.exe
UninstallDisplayName=Göz Faresi
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; The program may be running while it is updated or removed.
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "masaustu"; Description: "Masaüstüne kısayol koy"; GroupDescription: "Kısayollar:"

; An upgrade must not keep files the new version no longer ships: old
; _internal\*.dist-info folders (importlib.metadata could see two
; versions), removed modules, licence folders of dropped packages.
; Only these two program folders: never {app}\* - a config.yaml or
; "tasinabilir" a user put in {app} is portable-mode data.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\lisanslar"

[Files]
Source: "{#Kaynak}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Göz Faresi"; Filename: "{app}\GozFaresi.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Göz Faresi - Ayarlar"; Filename: "{app}\GozFaresi.exe"; Parameters: "--ayarlar"; WorkingDir: "{app}"
Name: "{autoprograms}\Göz Faresi - Kurulum Okuma Notu"; Filename: "{app}\BENI-OKU-KURULUM.txt"
Name: "{autodesktop}\Göz Faresi"; Filename: "{app}\GozFaresi.exe"; WorkingDir: "{app}"; Tasks: masaustu

[Run]
Filename: "{app}\GozFaresi.exe"; Description: "Göz Faresi'ni şimdi başlat"; Flags: nowait postinstall skipifsilent

[Code]
const
  DataDirName = 'GozFaresi';
  StartupLink = 'GozFaresi.lnk';

var
  DeleteData: Boolean;

function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\') + DataDirName;
end;

{ The confirmation: its own window with one box, unticked by default. }
function AskDeleteData(): Boolean;
var
  Form: TSetupForm;
  Info: TNewStaticText;
  Box: TNewCheckBox;
  OkButton: TNewButton;
begin
  Result := False;
  Form := CreateCustomForm(ScaleX(460), ScaleY(210), False, True);
  try
    Form.Caption := 'Göz Faresi kaldırılıyor';
    Info := TNewStaticText.Create(Form);
    Info.Parent := Form;
    Info.Left := ScaleX(16);
    Info.Top := ScaleY(16);
    Info.Width := Form.ClientWidth - ScaleX(32);
    Info.WordWrap := True;
    Info.AutoSize := True;
    Info.Caption := 'Program dosyaları silinecek. Ayarlarınız, kalibrasyonunuz ' +
      've indirilen modeller (' + DataDir() + ') KORUNACAK: programı yeniden ' +
      'kurarsanız kalibrasyonu tekrar yapmanız gerekmez.';
    Box := TNewCheckBox.Create(Form);
    Box.Parent := Form;
    Box.Left := ScaleX(16);
    Box.Top := ScaleY(110);
    Box.Width := Form.ClientWidth - ScaleX(32);
    Box.Height := ScaleY(22);
    Box.Caption := 'Ayarları ve kalibrasyonu da sil (geri alınamaz)';
    Box.Checked := False;
    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Caption := 'Devam';
    OkButton.Width := ScaleX(90);
    OkButton.Height := ScaleY(26);
    OkButton.Left := Form.ClientWidth - OkButton.Width - ScaleX(16);
    OkButton.Top := Form.ClientHeight - OkButton.Height - ScaleY(14);
    OkButton.ModalResult := mrOk;
    OkButton.Default := True;
    Form.ActiveControl := OkButton;
    if Form.ShowModal() = mrOk then
      Result := Box.Checked;
  finally
    Form.Free();
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  DeleteData := False;
  if (not UninstallSilent()) and DirExists(DataDir()) then
    DeleteData := AskDeleteData();
end;

{ The settings window's "start with Windows" shortcut, only when it starts
  THIS copy: a portable copy elsewhere may have made the same-named one, and
  the user cannot start the program by hand. }
procedure RemoveOwnStartupLink();
var
  Shell, Link: Variant;
  Path, Target: String;
begin
  Path := ExpandConstant('{userstartup}\') + StartupLink;
  if not FileExists(Path) then
    Exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Link := Shell.CreateShortcut(Path);
    Target := Link.TargetPath;
  except
    Exit;
  end;
  if CompareText(Target, ExpandConstant('{app}\GozFaresi.exe')) = 0 then
    DeleteFile(Path);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveOwnStartupLink();
  if (CurUninstallStep = usPostUninstall) and DeleteData then
    DelTree(DataDir(), True, True, True);
end;
