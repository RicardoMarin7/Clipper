; Instalador de Clipper para Windows (Inno Setup 6).
; 1. Construye primero el exe:  .\build.ps1
; 2. Instala Inno Setup:        winget install JRSoftware.InnoSetup
; 3. Compila este script:       abre installer.iss en Inno Setup y pulsa Compile
;    (o por consola: ISCC.exe installer.iss)
; Resultado: installer_output\ClipperSetup-0.1.0.exe

#define AppName "Clipper"
#define AppVersion "0.1.0"

[Setup]
AppId={{7E2B6C1A-9C44-4E7B-B7D2-CLIPPERBF6}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Bonum
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ClipperSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\Clipper.exe

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
; La carpeta completa generada por PyInstaller, incluido bin\ con ffmpeg
Source: "dist\Clipper\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Clipper.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Clipper.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Run]
Filename: "{app}\Clipper.exe"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent
