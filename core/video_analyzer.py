"""Análisis visual con OpenCV — FUERA DEL MVP (decisión de SDD Fase 1).

Reservado para una versión futura: detección de killfeed / cambios bruscos
entre frames como señal secundaria. Cuando se active, este módulo expondrá
una función que devuelva (timestamps, señal) con la misma forma que
audio_analyzer.compute_rms, y highlight_detector fusionará ambas señales
sin que ninguna otra capa cambie.
"""
