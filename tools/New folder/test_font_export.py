from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PIL import Image

import generate
from config import CONTROL_FILES, REQUIRED_SLIDES
from font_export import FontExportError, build_embedded_font_payload


def _write_test_font(path: Path, *, fs_type: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack(">IHHHH", 0x00010000, 1, 0, 0, 0)
    table_offset = 28
    record = struct.pack(">4sIII", b"OS/2", 0, table_offset, 10)
    os2_table = b"\0" * 8 + struct.pack(">H", fs_type)
    path.write_bytes(header + record + os2_table)


def _write_minimal_project(root: Path) -> None:
    pages = root / "gallery/user/pages"
    controls = root / "gallery/user/card/controls"
    pages.mkdir(parents=True)
    controls.mkdir(parents=True)
    for name in REQUIRED_SLIDES:
        Image.new("RGBA", (8, 8), (240, 240, 240, 255)).save(pages / name)
    for name in CONTROL_FILES:
        Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(controls / name)


def test_embeds_and_rewrites_project_font(tmp_path: Path) -> None:
    _write_test_font(tmp_path / "gallery/user/fonts/Arcane_Font.ttf", fs_type=0x0008)
    fonts_dir = tmp_path / "output/fonts"

    result = build_embedded_font_payload(
        tmp_path,
        "<span style=\"font-family:'Arcane', serif\">Letter</span>",
        fonts_dir,
    )

    assert result.report["embedded"] == ("Arcane",)
    assert result.report["fallback"] == ()
    assert len(result.report["files"]) == 1
    assert (fonts_dir / result.report["files"][0]).is_file()
    assert "font-family:'LetterSmithFont1', 'Arcane', serif" in result.html
    assert "@font-face" in result.css


def test_rejects_font_marked_embedding_restricted(tmp_path: Path) -> None:
    _write_test_font(tmp_path / "gallery/user/fonts/Secret_Font.ttf", fs_type=0x0002)

    with pytest.raises(FontExportError) as error:
        build_embedded_font_payload(
            tmp_path,
            "<span style=\"font-family:'Secret'\">Letter</span>",
            tmp_path / "output/fonts",
        )

    assert error.value.report["restricted"] == ("Secret",)


def test_generate_wires_embedded_font_into_viewer(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    _write_test_font(tmp_path / "gallery/user/fonts/Arcane_Font.ttf", fs_type=0x0008)

    play_dir = generate.generate_play_bundle(
        str(tmp_path),
        message_html="<span style=\"font-family:'Arcane'\">Letter</span>",
        seed_sfx=False,
    )

    styles = (play_dir / "styles.css").read_text(encoding="utf-8")
    index = (play_dir / "index.html").read_text(encoding="utf-8")
    report = generate.get_last_font_export_report()
    assert "@font-face" in styles
    assert "LetterSmithFont1" in styles
    assert "font-family:'LetterSmithFont1', 'Arcane'" in index
    assert report["embedded"] == ("Arcane",)
    assert len(tuple((play_dir / "gallery/fonts").glob("ls-font-*"))) == 1
