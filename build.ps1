# Construye Clipper.exe (carpeta autocontenida en dist/Clipper).
# Uso:  .\build.ps1
# Requisitos: pip install pyinstaller

$ErrorActionPreference = "Stop"

python -m PyInstaller `
    --noconfirm --clean `
    --windowed `
    --name Clipper `
    --collect-all customtkinter `
    main.py

# Si existe una carpeta bin/ con ffmpeg.exe y ffprobe.exe, se incluye junto
# al ejecutable para que la app sea 100% autocontenida en otros PCs.
if (Test-Path "bin") {
    Copy-Item -Recurse -Force "bin" "dist\Clipper\bin"
    Write-Output "bin/ (ffmpeg) incluido en dist\Clipper\bin"
}

# Plantillas de sonido de kill (assets/kill_sounds/*.wav)
if (Test-Path "assets") {
    Copy-Item -Recurse -Force "assets" "dist\Clipper\assets"
    Write-Output "assets/ (plantillas de kill) incluido en dist\Clipper\assets"
}

Write-Output ""
Write-Output "Listo: dist\Clipper\Clipper.exe"
Write-Output "Distribucion: comprime la carpeta dist\Clipper completa, o compila installer.iss con Inno Setup."
