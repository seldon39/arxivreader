; Installateur Windows d'arXivReader (Inno Setup 6) : le dossier construit (dist\arXivReader) copié dans
; %LOCALAPPDATA%\Programs\arXivReader, sans droits d'administrateur ; raccourci dans le menu Démarrer (et sur le
; bureau si demandé) ; désinstallation depuis Paramètres → Applications. La bibliothèque (%LOCALAPPDATA%\arXivReader)
; n'est jamais touchée. Une version plus récente s'installe par-dessus (même AppId), arXivReader fermé au besoin.
; Construit par .github/workflows/windows.yml : iscc /DAppVersion=X.Y.Z windows\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8A9E4080-2830-406F-B8AC-DB57B505BE0F}
AppName=arXivReader
AppVersion={#AppVersion}
AppVerName=arXivReader {#AppVersion}
AppPublisher=arXivReader (open source)
AppPublisherURL=https://github.com/seldon39/arxivreader
AppSupportURL=https://github.com/seldon39/arxivreader/issues
AppUpdatesURL=https://github.com/seldon39/arxivreader/releases/latest
DefaultDirName={localappdata}\Programs\arXivReader
DisableDirPage=yes
DefaultGroupName=arXivReader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=arXivReader-setup
SetupIconFile=arXivReader.ico
UninstallDisplayIcon={app}\arXivReader.exe
UninstallDisplayName=arXivReader
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; mise à jour : l'ancien serveur retiré d'abord (pas de module d'une version précédente qui traînerait)
Type: filesandordirs; Name: "{app}\server"

[Files]
Source: "..\dist\arXivReader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\arXivReader"; Filename: "{app}\arXivReader.exe"
Name: "{autodesktop}\arXivReader"; Filename: "{app}\arXivReader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\arXivReader.exe"; Description: "{cm:LaunchProgram,arXivReader}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; fichiers que l'application crée elle-même dans son dossier (aucun aujourd'hui) ; la bibliothèque est ailleurs
Type: dirifempty; Name: "{app}"
