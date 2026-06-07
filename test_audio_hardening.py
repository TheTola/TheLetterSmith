from __future__ import annotations

import json
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import generate
import sound_tab
from Template import TEMPLATE_JS


class AudioHardeningTests(unittest.TestCase):
    def test_export_apple_safe_mp3_uses_expected_encoding_and_atomic_replace(self) -> None:
        try:
            audio_export = importlib.import_module("audio_export")
        except ModuleNotFoundError as exc:
            self.fail(f"audio_export module is missing: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source.mp3"
            dst = root / "music.mp3"
            src.write_bytes(b"source")
            calls: list[tuple[str, object]] = []

            class FakeAudio:
                def set_frame_rate(self, rate: int) -> "FakeAudio":
                    calls.append(("frame_rate", rate))
                    return self

                def set_channels(self, channels: int) -> "FakeAudio":
                    calls.append(("channels", channels))
                    return self

                def export(self, out_f: Path, **kwargs: object) -> None:
                    calls.append(("export_path", Path(out_f).name))
                    calls.append(("export_kwargs", kwargs))
                    Path(out_f).write_bytes(b"encoded")

            class FakeSegment:
                @staticmethod
                def from_file(path: str) -> FakeAudio:
                    calls.append(("from_file", Path(path).name))
                    return FakeAudio()

            class FakeEffects:
                @staticmethod
                def normalize(audio: FakeAudio) -> FakeAudio:
                    calls.append(("normalize", True))
                    return audio

            with mock.patch.object(audio_export, "_load_pydub_tools", return_value=(FakeSegment, FakeEffects)):
                audio_export._export_apple_safe_mp3(src, dst)

            self.assertEqual(dst.read_bytes(), b"encoded")
            self.assertFalse((root / "music.mp3.tmp").exists())
            self.assertIn(("from_file", "source.mp3"), calls)
            self.assertIn(("frame_rate", 44100), calls)
            self.assertIn(("channels", 2), calls)
            self.assertIn(("normalize", True), calls)
            export_kwargs = dict(next(value for name, value in calls if name == "export_kwargs"))
            self.assertEqual(export_kwargs["format"], "mp3")
            self.assertEqual(export_kwargs["bitrate"], "192k")
            self.assertEqual(export_kwargs["parameters"], ["-write_xing", "0"])

    def test_export_apple_safe_mp3_falls_back_to_ffmpeg_when_pydub_cannot_load(self) -> None:
        audio_export = importlib.import_module("audio_export")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source.mp3"
            dst = root / "music.mp3"
            src.write_bytes(b"source")
            commands: list[list[str]] = []

            def fake_find_audio_tool(_env_name: str, _exe_name: str) -> str:
                return str(root / "ffmpeg.exe")

            def fake_run(cmd: list[str], **_kwargs: object) -> object:
                commands.append([str(part) for part in cmd])
                Path(cmd[-1]).write_bytes(b"encoded by ffmpeg")
                return object()

            with (
                mock.patch.object(audio_export, "_load_pydub_tools", side_effect=RuntimeError("pydub broken")),
                mock.patch.object(audio_export, "_find_audio_tool", fake_find_audio_tool),
                mock.patch("subprocess.run", fake_run),
            ):
                try:
                    audio_export._export_apple_safe_mp3(src, dst)
                except RuntimeError as exc:
                    self.fail(f"ffmpeg fallback was not used: {exc}")

            self.assertEqual(dst.read_bytes(), b"encoded by ffmpeg")
            self.assertFalse((root / "music.mp3.tmp").exists())
            command = commands[0]
            self.assertIn("-ar", command)
            self.assertIn("44100", command)
            self.assertIn("-ac", command)
            self.assertIn("2", command)
            self.assertIn("-b:a", command)
            self.assertIn("192k", command)
            self.assertIn("-write_xing", command)
            self.assertIn("0", command)

    def _write_project_assets(self, root: Path, *, include_music: bool = True) -> None:
        pages = root / "gallery" / "user" / "pages"
        controls = root / "gallery" / "user" / "card" / "controls"
        message = root / "gallery" / "user" / "message"
        user_sounds = root / "gallery" / "user" / "sounds"
        app_sounds = root / "gallery" / "app" / "sounds"

        pages.mkdir(parents=True)
        controls.mkdir(parents=True)
        message.mkdir(parents=True)
        user_sounds.mkdir(parents=True)
        app_sounds.mkdir(parents=True)

        for name in ("cover.png", "letter.png", "wall.png", "back.png"):
            Image.new("RGBA", (8, 8), (240, 240, 240, 255)).save(pages / name)

        for name in (
            "npage.png",
            "ppage.png",
            "cleft.png",
            "cright.png",
            "R_cleft.png",
            "R_cright.png",
            "volon.png",
            "voloff.png",
            "showmessageicon.png",
        ):
            Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(controls / name)

        if include_music:
            (user_sounds / "music.mp3").write_bytes(b"user music")

        for name in ["glissando.mp3", *[f"flip{i}.mp3" for i in range(1, 11)]]:
            (app_sounds / name).write_bytes(name.encode("ascii"))

        (root / "settings.json").write_text(
            json.dumps({"recipient_name": "Test", "recipient_title": "Audio"}),
            encoding="utf-8",
        )

    def test_generate_exports_every_runtime_sound_and_injects_build_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project_assets(root)
            calls: list[tuple[str, str]] = []

            def fake_export(src: Path, dst: Path) -> None:
                calls.append((Path(src).name, Path(dst).name))
                Path(dst).parent.mkdir(parents=True, exist_ok=True)
                Path(dst).write_bytes(b"apple safe")

            with mock.patch.object(generate, "_export_apple_safe_mp3", fake_export, create=True):
                play_dir = generate.generate_play_bundle(str(root), message_html="", seed_sfx=True)

            exported = [dst for _, dst in calls]
            self.assertEqual(
                exported,
                ["music.mp3", "glissando.mp3", *[f"flip{i}.mp3" for i in range(1, 11)]],
            )

            html = (play_dir / "index.html").read_text(encoding="utf-8")
            script = (play_dir / "script.js").read_text(encoding="utf-8")
            self.assertIn("?v=", html)
            self.assertIn("const BUILD_ID =", script)
            self.assertNotIn("{{BUILD_ID}}", html + script)

    def test_template_centralizes_audio_creation_and_safe_playback(self) -> None:
        self.assertIn("function makeAudio", TEMPLATE_JS)
        self.assertIn("function safePlay", TEMPLATE_JS)
        self.assertIn("function primeAudioOnGesture", TEMPLATE_JS)
        self.assertIn("Tap to enable music", TEMPLATE_JS)
        self.assertEqual(TEMPLATE_JS.count("new Audio("), 1)
        self.assertNotIn("playOneShot", TEMPLATE_JS)
        self.assertNotIn(".play().catch(()=>{})", TEMPLATE_JS)

    def test_sound_archive_processes_mp3_input_and_current_music_through_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "song.mp3"
            source.write_bytes(b"raw mp3")
            manager = sound_tab.SoundArchiveManager(root)
            calls: list[tuple[str, str]] = []

            def fake_export(src: Path, dst: Path) -> None:
                calls.append((Path(src).name, Path(dst).name))
                Path(dst).parent.mkdir(parents=True, exist_ok=True)
                Path(dst).write_bytes(b"apple safe")

            with mock.patch.object(sound_tab, "_export_apple_safe_mp3", fake_export, create=True):
                result = manager.add_song_from_path(source)

            self.assertNotEqual(result.action, "error", result.message)
            self.assertEqual(calls, [("song.mp3", "song.mp3"), ("song.mp3", "music.mp3")])
            self.assertEqual((root / "gallery" / "user" / "sounds" / "music.mp3").read_bytes(), b"apple safe")


if __name__ == "__main__":
    unittest.main()
