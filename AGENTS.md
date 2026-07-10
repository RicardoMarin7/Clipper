# AGENTS.md — Contexto para agentes que continúen este proyecto

## Qué es Clipper

App de escritorio Windows 100 % local (CustomTkinter + FFmpeg + NumPy, sin
OpenCV) que procesa grabaciones de partidas de Battlefield 6 y exporta clips
de highlights. **Estado: funcional y validada con partidas reales del usuario.**
El usuario trabaja en español; prefiere diseño por fases con aprobación
expresa antes de implementar, y valida los cambios procesando partidas reales
y reportando resultados (p. ej. "hice 27 kills y detectó 20").

## Arquitectura (regla inquebrantable: dependencias `ui → core → utils`)

- `main.py` — entry point.
- `ui/` — CustomTkinter. `app.py` (ventana, máquina de estados IDLE/RUNNING/
  CANCELLING/DONE/ERROR, polling de queue cada 100 ms con `.after()`),
  `widgets/`, `theme.py`. La UI jamás toca el core directamente: consume
  `ProgressEvent`s de una `queue.Queue`.
- `core/` — dominio. `pipeline.py` (orquestador, corre en UN hilo worker
  daemon), `sound_matcher.py`, `video_analyzer.py`, `highlight_detector.py`,
  `audio_analyzer.py`, `clip_extractor.py`, `models.py` (contratos:
  `JobConfig`, `HighlightSegment`, `ProgressEvent` y todas las constantes de
  modos).
- `utils/` — `ffmpeg_wrapper.py` (subprocess a ffmpeg/ffprobe, progreso real
  vía `-progress pipe:1`, cancelación cooperativa con `threading.Event`,
  borra archivos parciales), `config.py` (settings en
  `%APPDATA%/Clipper/settings.json`), `file_manager.py`, `logger.py`
  (log técnico en `%APPDATA%/Clipper/clipper.log`).
- `assets/` — `kill_sounds/*.wav` (plantillas de audio 16 kHz mono, derivadas
  de los .ogg del juego que aportó el usuario) y `kill_skull.npy` (plantilla
  visual 40×36 float32 de la calavera de la UI de kill).
- `tests/` — 23 tests de la lógica pura (detector, matchers, helpers de
  export). `python -m pytest -q`.

## Detección de kills: híbrida audio + video (la parte más calibrada)

1. **Audio propone** (`sound_matcher.py`): ZNCC entre espectrogramas
   log-magnitud (ventanas 32 ms, salto 10 ms, chunked) de las plantillas de
   sonido de kill y el audio de la partida (WAV 16 kHz extraído con ffmpeg).
   Umbral de candidatos = slider "Umbral de kills" (default **0.45**, recall
   alto a propósito). Supresión de vecinos a 0.6 s.
2. **Video confirma** (`video_analyzer.py`): por candidato extrae ~8 frames
   (2.5 s a 3 fps, UNA invocación de ffmpeg) de la región central-baja
   (frame normalizado a 1920 de ancho, región 800×200 en (560,600)) y busca
   la calavera con ZNCC 2D (FFT + imagen integral float64 con suelo de
   contraste — en float32 hay cancelación catastrófica que dispara scores>1).
   Regla de fusión calibrada: `visual ≥ 0.80` confirma solo; `visual ≥ 0.72
   AND audio ≥ 0.60` rescata trade-kills/UI tardía. **0.72 y no menos**: la
   pantalla de muerte propia contiene una calavera que puntúa ~0.68 y el
   sonido de "te mataron" es de la misma familia (falso positivo confirmado
   por el usuario).

Datos de calibración (partida real 6:43, 27 kills según el usuario, verificada
frame a frame): kills reales puntúan audio 0.45–0.75 y visual 0.90–0.97;
no-kills visual 0.58–0.77. Las multikills (vi una CUÁDRUPLE y varias x2)
suenan como UN evento. "EQUIPAMIENTO DESTRUIDO" es el falso de audio típico
(~0.50). Sin plantilla de calavera la app cae a solo-audio y el log recomienda
subir el umbral a ~0.55.

**Fragilidad conocida**: la plantilla de calavera se recortó de la grabación
del usuario (2560×1440 16:9, HUD default) normalizada a 1920 de ancho. Es
invariante a resolución mientras sea 16:9 y no cambie la escala del HUD; si
cambian, recapturar (`np.save` de un crop 40×36 del icono) — proceso: extraer
frame en una kill con ffmpeg, localizar la calavera, recortar.

## El video del usuario es HDR (crítico)

ShadowPlay graba **HEVC 10-bit HDR10** (smpte2084). Toda recodificación pasa
por `sdr_prep_filter()`: tonemapping HDR→SDR con **libplacebo** (GPU; fallback
zscale CPU; fallback format=yuv420p) → salida H.264 Main 8-bit bt709 rango TV.
Sin esto: error 0x80004005 en Windows (H.264 High 10) y colores lavados.
El stream copy (clips normales) NO se toca: conserva el HEVC HDR original.
Los grafos con filtros terminan en `format=yuv420p` explícito o NVENC negocia
4:4:4 que los móviles no decodifican.

## Exportación

- **Clips horizontales**: stream copy (instantáneo, sin pérdida, inicio
  ajustado al keyframe anterior ±2 s — compensado por el padding 3 s/5 s) o
  "corte exacto" (recodifica: NVENC si `has_nvenc()` —prueba FUNCIONAL de
  codificación, no solo lista de encoders— si no libx264 veryfast crf 18).
- **Vertical 9:16** (1080×1920, sirve para TikTok/Reels IG/Reels FB): 3
  estilos — `blur` (video entero, 32 % de altura), `zoom` (recorte 3:4 al
  75 % de altura — el preferido del usuario), `crop` (pantalla completa).
  El fondo difuminado se genera a 270×480 + upscale (10× más barato).
- **Compilatorio**: radio buttons Solo clips / Clips + compilatorio / Solo
  compilatorio (este último borra los clips tras unirlos). Concat demuxer
  sin recodificar, o con **transiciones** (checkbox): crossfade 0.35 s
  (`xfade` + `acrossfade`, grafo generado por `build_transition_graph()`,
  offsets acumulativos `sum(dur) - k·td`) — recodifica todo el compilatorio.

## Comandos

```powershell
python main.py                 # desarrollo
python -m pytest -q            # tests (23)
.\build.ps1                    # construye dist\Clipper\Clipper.exe (PyInstaller
                               # + copia bin/ y assets/). CERRAR Clipper.exe
                               # ANTES o falla con Acceso denegado.
```

`installer.iss` (Inno Setup) está listo pero nunca se ha compilado.

## Entorno del usuario

- Windows 11, monitor 5120 px de ancho; la ventana se auto-coloca centrada a
  altura completa del área de trabajo (`_work_area()` vía SystemParametersInfo).
- GPU NVIDIA con **NVENC funcional** (el runtime lo detecta; en shells
  sandboxeadas la prueba funcional puede dar False — no fiarse de esa señal).
- FFmpeg 8.1.2 (gyan full, con libplacebo y zscale) instalado vía winget y
  copiado a `bin/` para el exe autocontenido.
- Graba con ShadowPlay a 2560×1440 60 fps HEVC HDR en
  `C:\Users\ricar\Videos\NVIDIA\Battlefield 6\`.

## Estado de git y pendientes

- Repo git iniciado pero **sin commits** (safe.directory ya configurado; el
  .git fue creado con permisos de admin).
- `.gitignore` excluye `bin/`, `dist/`, `build/`, `bf6_kill_sounds/` (los .ogg
  originales del juego). Ojo si el repo se hace público: los WAV de `assets/`
  derivan de audio propiedad de EA/DICE.
- Ideas discutidas no implementadas: plantilla para "equipamiento destruido"
  como highlight opcional; soporte AMF/QuickSync; duración de transición
  configurable; paralelizar la verificación visual (hoy secuencial, ~1 s por
  candidato).
- `video_analyzer` hoy solo verifica kills; el análisis de killfeed/escenas
  con OpenCV que se pospuso en el SDD original sigue sin ser necesario.
