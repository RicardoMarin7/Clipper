# Clipper — Battlefield 6 Highlight Extractor

Aplicación de escritorio 100 % local que procesa grabaciones largas de partidas
y exporta clips de highlights a partir del **audio**. Sin nube, sin recodificar
(por defecto). Dos señales de detección, combinables:

- **Kills**: template matching espectral (correlación cruzada normalizada)
  contra los sonidos de confirmación de kill del juego
  (`assets/kill_sounds/*.wav`). Timestamp exacto, inmune al volumen de mezcla.
- **Intensidad**: picos de energía RMS (disparos, explosiones, rachas),
  gobernados por el slider de sensibilidad.

## Comandos rápidos

```powershell
# Instalar dependencias (una vez)
winget install Gyan.FFmpeg
pip install -r requirements.txt

# Ejecutar en modo desarrollo
python main.py

# Correr los tests
pip install pytest
python -m pytest -q

# Construir el ejecutable (dist\Clipper\Clipper.exe, autocontenido)
pip install pyinstaller
.\build.ps1

# Generar el instalador (requiere Inno Setup)
winget install JRSoftware.InnoSetup
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

## Requisitos

- **Python 3.10+**
- **FFmpeg** (ffmpeg + ffprobe) en el `PATH`, o copiados en una carpeta `bin/`
  dentro del proyecto. En Windows: `winget install Gyan.FFmpeg`

## Instalación y uso

```powershell
pip install -r requirements.txt
python main.py
```

1. Selecciona el video de la partida (`.mp4`, `.mkv`, `.mov`, `.avi`).
2. La carpeta de salida se autocompleta (`<carpeta_del_video>/highlights/`).
3. Elige el modo de detección (**Solo kills** / **Kills + intensidad** /
   **Solo intensidad**), la sensibilidad (solo aplica a intensidad) y el
   padding antes/después de cada evento.
4. Opcional: marca **Corte exacto** para clips que empiezan exactamente en el
   momento detectado (recodifica con NVENC si tienes GPU NVIDIA; si no, x264).
   Sin marcar, el corte es instantáneo y sin pérdida de calidad (stream copy),
   con inicio ajustado al keyframe anterior (~±2 s, compensado por el padding).
5. Pulsa **Extraer Highlights**.

## Arquitectura (resumen del SDD)

```
ui/      Presentación (CustomTkinter). No conoce OpenCV/FFmpeg.
core/    Dominio: análisis RMS, detección de segmentos, orquestación.
utils/   Infraestructura: wrapper de FFmpeg, config, logging, filesystem.
```

- Dependencias unidireccionales: `ui → core → utils`.
- El procesamiento corre en un hilo worker; la UI consume `ProgressEvent`s
  vía `queue.Queue` drenada con `.after(100)`. Cancelación cooperativa con
  `threading.Event` (nunca quedan clips corruptos).
- El análisis visual con OpenCV (`core/video_analyzer.py`) está fuera del MVP
  por decisión de diseño; el detector ya está preparado para fusionar esa
  señal cuando se active.

## Construir el .exe (sin necesitar Python)

```powershell
pip install pyinstaller
.\build.ps1
```

Genera `dist\Clipper\Clipper.exe` con FFmpeg incluido (carpeta `bin/`):
autocontenido, se puede copiar/comprimir y llevar a cualquier PC con Windows.

### Instalador (opcional)

Con [Inno Setup](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup`), compila `installer.iss` para obtener
`installer_output\ClipperSetup-0.1.0.exe` — instala en Archivos de programa,
crea acceso directo y desinstalador.

## Tests

```powershell
pip install pytest
python -m pytest -q
```

## Ajustes persistentes

Se guardan al cerrar en `%APPDATA%/Clipper/settings.json`. El log técnico está
en `%APPDATA%/Clipper/clipper.log`.
