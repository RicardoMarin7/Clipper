"""Tests de los helpers puros de exportación (nombres, concat, filtros)."""

from pathlib import Path

from utils.ffmpeg_wrapper import VERTICAL_HEIGHT, VERTICAL_WIDTH, build_concat_list, vertical_filter
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


def test_vertical_filter_crop_targets_9_16():
    filt = vertical_filter("crop")
    assert "crop=ih*9/16:ih" in filt
    assert f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}" in filt


def test_vertical_filter_blur_composites_fg_over_blurred_bg():
    filt = vertical_filter("blur")
    assert "boxblur" in filt
    assert "[bg][fg]overlay" in filt
    assert f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}" in filt  # bg ampliado a 9:16
