"""Tests de los helpers puros de exportación (nombres, concat, filtros)."""

from pathlib import Path

from utils.ffmpeg_wrapper import (
    VERTICAL_HEIGHT,
    VERTICAL_WIDTH,
    build_concat_list,
    build_transition_graph,
    vertical_filter,
)
from utils.file_manager import clip_filename


def test_clip_filename_with_suffix():
    assert clip_filename(3, 1062) == "highlight_03_00-17-42.mp4"
    assert clip_filename(3, 1062, suffix="_vertical") == "highlight_03_00-17-42_vertical.mp4"


def test_concat_list_uses_posix_paths_and_quotes():
    clips = [Path(r"C:\videos\con espacios\clip 1.mp4"), Path(r"C:\videos\clip2.mp4")]
    content = build_concat_list(clips)
    lines = content.strip().split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("file '")
    assert "con espacios/clip 1.mp4'" in lines[0]
    assert "\\" not in lines[0]  # rutas posix para el concat demuxer


def test_concat_list_escapes_single_quotes():
    content = build_concat_list([Path(r"C:\videos\o'brien.mp4")])
    assert "o'\\''brien" in content


def test_transition_graph_offsets_accumulate():
    # 3 clips de 8 s con crossfade de 0.35 s:
    # offsets en 7.65 y 15.30; duración total 24 - 2*0.35
    graph, v_label, a_label, total = build_transition_graph(
        [8.0, 8.0, 8.0], prep="format=yuv420p", fps=60.0
    )
    assert "offset=7.650" in graph
    assert "offset=15.300" in graph
    assert graph.count("xfade") == 2
    assert graph.count("acrossfade") == 2
    assert v_label == "vout" and a_label == "ax2"
    assert "[vx2]format=yuv420p[vout]" in graph
    assert abs(total - 23.3) < 1e-6


def test_transition_shorter_than_shortest_clip():
    # con clips muy cortos el crossfade se acorta a la mitad del clip mínimo
    graph, _v, _a, _total = build_transition_graph(
        [0.4, 8.0], prep="format=yuv420p", fps=60.0
    )
    assert "duration=0.200" in graph


def test_vertical_filter_crop_targets_9_16():
    filt = vertical_filter("crop")
    assert "crop=ih*9/16:ih" in filt
    assert f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}" in filt


def test_vertical_filter_zoom_crops_3_4_at_75_percent_height():
    filt = vertical_filter("zoom")
    assert "crop=ih*3/4:ih" in filt
    assert f"scale={VERTICAL_WIDTH}:1440" in filt  # 75% de 1920
    assert "[bg][fg]overlay" in filt


def test_vertical_filter_blur_composites_fg_over_blurred_bg():
    filt = vertical_filter("blur")
    assert "boxblur" in filt
    assert "[bg][fg]overlay" in filt
    assert f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}" in filt  # bg ampliado a 9:16
