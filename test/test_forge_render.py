from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

import generate
from forge_render import (DISPLAY_MANIFEST, RenderValidationError, StaleRenderError,
                          build_render_to, capture_render, copy_display_output,
                          generate_render, export_render, render_is_current, validate_render)
from project_model import create_project, MAX_PAGES, ProjectModel
from project_repository import ProjectRepository
from project_session import ProjectSession, get_project_session, release_project_session
from test_project_model import five_page_project, canvas_layout


class ForgeRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ls-forge-")
        self.root = Path(self.temporary.name).resolve()
        self.project = five_page_project()
        for index, page in enumerate(self.project.pages):
            self.write_image(page["artwork"]["asset"], (index * 45, 80, 150))
            self.project.rename_page(page["id"], "Repeated name")
            if page["message"] is not None:
                self.project.update_message(page["id"], {**page["message"], "layout": {},
                    "settings": {"message_overlay_preset": "black" if index == 1 else "paper"}})
        for filename in generate.CONTROL_FILES:
            self.write_image(str(Path(generate.USER_CONTROLS_DIR) / filename), "gray")
        self.write_image(str(generate.APP_BANNER_PATH), "gray")
        self.session = ProjectSession(self.project, project_root=self.root)

    def tearDown(self):
        self.session.close()
        release_project_session(self.root)
        self.temporary.cleanup()

    def write_image(self, relative, color):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 48), color).save(path)
        return path

    def build(self, name="display", mode="display"):
        return build_render_to(capture_render(self.session, mode), self.root / name, seed_sfx=False)

    def seed_app_sounds(self):
        source = generate.application_paths(Path(__file__).resolve().parents[1]).app_resource_path(generate.APP_SOUNDS_DIR)
        destination = self.root / "resources/app/sounds"
        destination.mkdir(parents=True, exist_ok=True)
        for name in generate._sfx_names():
            shutil.copy2(source / name, destination / name)

    def test_long_saved_title_staging_preserves_relative_display_resources(self):
        from forge_render import validate_display_output
        from transactional_io import _remove_path
        destination = self.root / ("recipient-" + "r" * 80) / ("title-" + "t" * 85)
        try:
            output = generate_render(self.session, destination=destination, seed_sfx=False)
            verified = validate_display_output(output)
            manifest = json.loads((verified / DISPLAY_MANIFEST).read_text(encoding="utf-8"))
            self.assertTrue(manifest["files"])
            self.assertTrue(all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in manifest["files"]))
        finally:
            self.assertTrue(destination.parent.resolve().is_relative_to(self.root.resolve()))
            _remove_path(destination.parent)

    def test_saved_preview_uses_app_controls_without_active_artwork_fallback(self):
        from forge_render import SavedProjectPreviewAdapter
        stored = ProjectRepository(self.root / "saved").save(self.project, asset_root=self.root)
        isolated = ProjectSession(stored.project, project_root=stored.asset_root, asset_root=stored.asset_root)
        adapter = SavedProjectPreviewAdapter(self.root)
        try:
            render = adapter.capture_render(isolated)
            output = adapter.build_render_to(render, self.root / "saved-preview", seed_sfx=False)
            self.assertTrue((output / "gallery/controls/npage.png").is_file())
            self.assertNotEqual(isolated.session_id, self.session.session_id)
            missing = stored.project.pages[0]["artwork"]["asset"]
            (stored.asset_root / missing).unlink()
            # An identically named file in the active workspace must not fill it.
            active_file = self.root / missing
            active_file.parent.mkdir(parents=True, exist_ok=True)
            self.write_image(missing, "pink")
            issues = adapter.validate_render(adapter.capture_render(isolated))
            self.assertTrue(any(issue.code == "missing_artwork" and issue.page_id == stored.project.pages[0]["id"] for issue in issues))
            preview = adapter.build_render_to(adapter.capture_render(isolated), self.root / "missing-preview", seed_sfx=False)
            manifest = json.loads((preview / DISPLAY_MANIFEST).read_text(encoding="utf-8"))
            self.assertNotEqual((preview / manifest["pages"][0]["source"]).read_bytes(), active_file.read_bytes())
        finally:
            isolated.close()

    def test_real_five_pages_order_messages_reorder_and_repository_reload(self):
        output = self.build()
        content = (output / "index.html").read_text(encoding="utf-8")
        sections = re.findall(r'<section class="slide.*?</section>', content, re.DOTALL)
        self.assertEqual(len(sections), 5)
        overlays = re.findall(r'<div class="text-wall".*?</div>\s*</div>', content, re.DOTALL)
        self.assertIn('data-page-id="' + self.project.pages[1]["id"] + '"', overlays[0])
        self.assertIn('data-page-id="' + self.project.pages[3]["id"] + '"', overlays[1])
        self.assertIn("Message A", overlays[0])
        self.assertNotIn("Message B", overlays[0])
        self.assertIn("Message B", overlays[1])
        self.assertNotIn("Message A", overlays[1])
        self.assertIn("--message-ink:#f1eee8", overlays[0])
        self.assertIn("--message-ink:#2b1c12", overlays[1])
        self.assertEqual(len(set(re.findall(r'<section[^>]+id="([^"]+)"', content))), 5)
        order = [page["id"] for page in self.project.pages]
        order[1], order[3] = order[3], order[1]
        self.project.reorder_pages(order)
        repository = ProjectRepository(self.root / "saved")
        repository.save(self.project, asset_root=self.root)
        restored = repository.load()
        self.session.replace(restored.project, asset_root=restored.asset_root)
        second = self.build("reordered")
        overlays = re.findall(r'<div class="text-wall".*?</div>\s*</div>', (second / "index.html").read_text(encoding="utf-8"), re.DOTALL)
        self.assertIn("Message B", overlays[0])
        self.assertIn("Message A", overlays[1])
        manifest = json.loads((second / DISPLAY_MANIFEST).read_text())
        self.assertEqual([page["id"] for page in manifest["pages"]], order)
        for page, original in zip(manifest["pages"], self.session.project.pages):
            self.assertEqual((second / page["source"]).read_bytes(), (restored.asset_root / original["artwork"]["asset"]).read_bytes())

    def test_distinct_resolved_layouts_survive_reorder_save_and_public_copy(self):
        from html import unescape
        first, second = self.project.pages[1], self.project.pages[3]
        self.project.update_shared_settings({"message_layout": canvas_layout()})
        self.project.update_message(second["id"], {**second["message"], "layout": {"box": {"x": 80, "y": 330, "width": 280, "height": 190}, "text_align": "right"}})
        self.project.update_artwork(second["id"], {"placement": {"version": 1, "fit": "cover", "position": {"x": .8}}})
        fingerprint = capture_render(self.session).source_fingerprint
        self.project.update_shared_settings({"message_layout": {**canvas_layout(), "padding": {"left": 20}}})
        self.assertNotEqual(capture_render(self.session).source_fingerprint, fingerprint)
        order = [page["id"] for page in self.project.pages]
        order[1], order[3] = order[3], order[1]
        self.project.reorder_pages(order)
        repository = ProjectRepository(self.root / "saved")
        repository.save(self.project, asset_root=self.root)
        stored = repository.load()
        self.session.replace(stored.project, asset_root=stored.asset_root)
        public = copy_display_output(self.build(), self.root / "public")
        content = (public / "index.html").read_text(encoding="utf-8")
        layouts = [json.loads(unescape(value)) for value in re.findall(r'data-layout="([^"]+)"', content)]
        self.assertEqual([layout["message"]["box"]["y"] for layout in layouts], [330, 130])
        self.assertEqual(layouts[0]["artwork"]["fit"], "cover")
        self.assertEqual(content.count('class="text-wall canvas-message"'), 2)
        self.assertEqual(generate.validate_play_bundle(public), public)

    def test_classic_workspace_migrates_and_renders_actual_type_meanings(self):
        from image_animation import install_image_asset
        from settings_store import SettingsStore
        legacy = self.root / "legacy"
        for index, slot in enumerate(("cover", "letter", "wall", "back")):
            self.write_image(f"legacy/gallery/user/pages/{slot}.png", (index * 50, 90, 120))
        for filename in generate.CONTROL_FILES:
            self.write_image(str(Path("legacy") / generate.USER_CONTROLS_DIR / filename), "gray")
        self.write_image(str(Path("legacy") / generate.APP_BANNER_PATH), "gray")
        message = legacy / generate.MESSAGE_HTML_FILE
        message.parent.mkdir(parents=True, exist_ok=True)
        message.write_text('<p style="color:#bb2244">Classic migrated message</p>', encoding="utf-8")
        SettingsStore(legacy).update_fields({"recipient_name": "Synthetic Friend", "recipient_title": "Migrated fixture",
                                           "message_overlay_preset": "black"})
        frames = [Image.new("RGB", (24, 36), color) for color in ("red", "blue")]
        source = legacy / "original.gif"
        frames[0].save(source, save_all=True, append_images=frames[1:], duration=[70, 130], loop=0)
        install_image_asset(legacy / generate.USER_PAGES_DIR, "cover", source)
        session = get_project_session(legacy)
        try:
            self.assertEqual([page["type"] for page in session.project.pages], ["cover", "illustration", "letter", "end_page"])
            output = generate.build_play_bundle_to(legacy, legacy / "display", seed_sfx=False)
            content = (output / "index.html").read_text(encoding="utf-8")
            overlays = re.findall(r'<div class="text-wall".*?</div>\s*</div>', content, re.DOTALL)
            self.assertEqual(len(overlays), 1)
            self.assertIn('data-page-id="' + session.project.pages[2]["id"] + '"', overlays[0])
            self.assertIn("Classic migrated message", overlays[0])
            self.assertIn("--message-ink:#f1eee8", overlays[0])
            self.assertEqual(content.count("Classic migrated message"), 1)
            manifest = json.loads((output / DISPLAY_MANIFEST).read_text())
            self.assertEqual(manifest["animations"]["0"]["durations_ms"], [70, 130])
            self.assertEqual((output / manifest["animations"]["0"]["source"]).read_bytes(), source.read_bytes())
        finally:
            release_project_session(legacy)

    def test_no_letter_middle_end_and_bounds(self):
        for count in (2, MAX_PAGES):
            project = create_project(["end_page"] + ["illustration"] * (count - 1))
            for page in project.pages:
                project.update_artwork(page["id"], {"asset": "assets/background-0.png"})
            self.session.replace(project)
            output = self.build(str(count))
            content = (output / "index.html").read_text()
            self.assertEqual(content.count('data-canonical="true"'), count)
            self.assertNotIn('class="text-wall"', content)
        imported = project.to_dict()
        extra = create_project(["cover"]).pages[0]
        imported["pages"].append(extra)
        self.session.replace(ProjectModel.from_dict(imported))
        issues = validate_render(capture_render(self.session))
        self.assertIn("page_count", {issue.code for issue in issues})
        self.assertEqual(len(self.session.project.pages), MAX_PAGES + 1)

    def test_retyped_message_and_unused_private_files_never_in_output(self):
        page = self.project.pages[1]
        self.project.change_page_type(page["id"], "illustration")
        (self.root / "secret.txt").write_text("SYNTHETIC_PRIVATE_MARKER")
        output = self.build()
        (output / "editable-private.json").write_text("SYNTHETIC_PRIVATE_MARKER")
        public = copy_display_output(output, self.root / "public")
        text = "\n".join(path.read_text(encoding="utf-8") for path in public.rglob("*") if path.suffix in (".html", ".json", ".js", ".css"))
        self.assertNotIn("Message A", text)
        self.assertNotIn("Prompt A", text)
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", text)
        self.assertNotIn(str(self.root), text)
        self.assertFalse((public / generate.BUILD_STATE_FILE).exists())
        self.assertFalse((public / "editable-private.json").exists())
        self.assertEqual(generate.validate_play_bundle(public), public)

    def test_publish_staging_freezes_the_original_display_revision(self):
        from Forge_Tab import _stage_publish_display
        captured = capture_render(self.session)
        output = generate_render(self.session, render_input=captured, destination=self.root / "active", seed_sfx=False)
        (output / "lettersmith-metadata.json").write_text('{"private":"SYNTHETIC_PRIVATE_MARKER"}')
        (output / "prompt_writer_state.json").write_text('{"prompt":"SYNTHETIC_PRIVATE_MARKER"}')
        staged = _stage_publish_display(self.root, output, captured)
        try:
            frozen = Path(staged.name) / "display"
            original_html = (frozen / "index.html").read_bytes()
            original_manifest = json.loads((frozen / DISPLAY_MANIFEST).read_text())
            self.assertEqual(original_manifest["source_fingerprint"], captured.source_fingerprint)
            self.assertIn(b"Message A", original_html)
            self.assertFalse((frozen / "lettersmith-metadata.json").exists())
            self.assertFalse((frozen / "prompt_writer_state.json").exists())
            self.assertFalse((frozen / generate.BUILD_STATE_FILE).exists())
            page = self.project.pages[1]
            self.project.update_message(page["id"], {**page["message"], "html": "<p>Later edited message</p>"})
            generate_render(self.session, destination=output, seed_sfx=False)
            self.assertIn("Later edited message", (output / "index.html").read_text())
            self.assertEqual((frozen / "index.html").read_bytes(), original_html)
            self.assertEqual(json.loads((frozen / DISPLAY_MANIFEST).read_text()), original_manifest)
            self.assertEqual(generate.validate_play_bundle(frozen), frozen)
            with self.assertRaises(RuntimeError):
                _stage_publish_display(self.root, output, captured)
        finally:
            staged.cleanup()

    def test_only_active_message_resource_and_security_are_packaged(self):
        self.write_image("attachments/active.png", "red")
        self.write_image("attachments/inactive.png", "blue")
        page = self.project.pages[1]
        self.project.update_message(page["id"], {**page["message"], "layout": {}, "resources": ["attachments/active.png", "attachments/inactive.png"],
            "html": '<p>Message A {{CSP_NONCE}}</p><img src="attachments/active.png"><script>alert(1)</script>'})
        output = self.build()
        content = (output / "index.html").read_text()
        self.assertNotIn("alert(1)", content)
        self.assertIn("{{CSP_NONCE}}", content)
        assets = list((output / "gallery/message").rglob("*.png"))
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].read_bytes(), (self.root / "attachments/active.png").read_bytes())

    def test_shared_artwork_and_message_image_survives_save_and_portable_render(self):
        page = self.project.pages[1]
        reference = page["artwork"]["asset"]
        original = (self.root / reference).read_bytes()
        self.project.update_message(page["id"], {
            **page["message"], "resources": [reference],
            "html": f'<p>Message A</p><img src="{reference}">',
        })
        repository = ProjectRepository(self.root / "saved")
        repository.save(self.project, asset_root=self.root)
        restored = repository.load()
        self.session.replace(restored.project, asset_root=restored.asset_root)
        # The canonical repository must satisfy both owners without fallback
        # to the original development workspace.
        (self.root / reference).unlink()
        output = self.build()
        public = copy_display_output(output, self.root / "public")
        manifest = json.loads((public / DISPLAY_MANIFEST).read_text())
        artwork = next(item for item in manifest["pages"] if item["id"] == page["id"])
        self.assertEqual((public / artwork["source"]).read_bytes(), original)
        attachments = list((public / "gallery/message").rglob("*.png"))
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].read_bytes(), original)
        self.assertEqual(generate.validate_play_bundle(public), public)

    def test_portable_exports_allow_only_display_files_and_preserve_inline_nonce(self):
        from portable_export import create_zip_package, create_publish_package, create_single_html
        output = self.build()
        (output / "private-draft.json").write_text('{"prompt":"PRIVATE_EXPORT_MARKER"}')
        (output / "gallery/private-draft.png").write_bytes(b"PRIVATE_EXPORT_MARKER")
        manifest = json.loads((output / DISPLAY_MANIFEST).read_text())
        expected = {*manifest["files"], DISPLAY_MANIFEST}
        for exporter, rooted in ((create_zip_package, True), (create_publish_package, False)):
            archive_path = exporter(output, self.root / "exports")
            with zipfile.ZipFile(archive_path) as archive:
                prefix = output.name + "/" if rooted else ""
                actual = {name.removeprefix(prefix) for name in archive.namelist()}
                self.assertEqual(actual, expected if rooted else expected | {".nojekyll", "_headers"})
                self.assertIn(b"Message A", archive.read(prefix + "index.html"))
                self.assertIn(b"Message B", archive.read(prefix + "index.html"))
        document = create_single_html(output, self.root / "exports").read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_EXPORT_MARKER", document)
        self.assertNotIn('src="script.js"', document)
        self.assertNotIn('href="styles.css"', document)
        nonce = re.search(r"script-src 'nonce-([^']+)'", document).group(1)
        scripts = re.findall(r"<script\b([^>]*)>", document)
        self.assertEqual(len(scripts), 2)
        self.assertTrue(all(f'nonce="{nonce}"' in attrs for attrs in scripts))
        self.assertIn("media-src 'self' data:", document)

    def test_real_export_service_preserves_previous_file_on_edit_replace_and_cancel(self):
        self.seed_app_sounds()
        from portable_export import PortableExportError
        destination = self.root / "exports/letter.zip"
        destination.parent.mkdir()
        destination.write_bytes(b"previous usable export")
        real_build = build_render_to
        for change in (lambda: self.project.rename_page(self.project.pages[1]["id"], "Edited"),
                       lambda: self.session.replace(self.project, asset_root=self.root)):
            def build_then_change(*args, **kwargs):
                result = real_build(*args, **kwargs)
                change()
                return result
            with mock.patch("forge_render.build_render_to", side_effect=build_then_change), self.assertRaises(StaleRenderError):
                export_render(self.session, destination)
            self.assertEqual(destination.read_bytes(), b"previous usable export")
        with self.assertRaises((StaleRenderError, PortableExportError)):
            export_render(self.session, destination, cancelled=lambda: True)
        self.assertEqual(destination.read_bytes(), b"previous usable export")
        export_render(self.session, destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertIn("letter/index.html", archive.namelist())

    def test_forge_export_action_runs_the_real_snapshot_export(self):
        self.seed_app_sounds()
        from types import SimpleNamespace
        from Forge_Tab import ForgeTab
        session = get_project_session(self.root)
        session.replace(self.project, asset_root=self.root)
        destination = self.root / "exports/from-forge.html"
        completed = mock.Mock()
        forge = SimpleNamespace(project_root=self.root, _busy=False, _publication_operation="",
                                _flush_prompt_writer_state=lambda: True, _required_gate=lambda **kwargs: True,
                                _export_completed=completed, _set_status=mock.Mock())
        forge._start_operation = lambda _status, task, success, _error: success(task())
        with mock.patch("Forge_Tab.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(destination), "HTML")):
            ForgeTab.export_letter(forge, "html")
        self.assertIn("Message A", destination.read_text(encoding="utf-8"))
        self.assertIn("Message B", destination.read_text(encoding="utf-8"))
        self.assertEqual(completed.call_args.args[0][0], destination)

    def test_static_and_independent_real_gif_frames(self):
        frames = [Image.new("RGB", (20, 30), color) for color in ("red", "blue")]
        source = self.root / "moving.gif"
        frames[0].save(source, save_all=True, append_images=frames[1:], duration=[80, 140], loop=2)
        for page, speed in zip(self.project.pages[:2], (50, 200)):
            self.project.update_artwork(page["id"], {"asset": "moving.gif", "animation": {"speed_percent": speed, "playback_mode": "ping_pong"}})
        output = self.build()
        manifest = json.loads((output / DISPLAY_MANIFEST).read_text())
        first, second = manifest["animations"]["0"], manifest["animations"]["1"]
        self.assertEqual(first["speed_percent"], 50)
        self.assertEqual(second["speed_percent"], 200)
        self.assertNotEqual(first["frames"], second["frames"])
        self.assertEqual(first["durations_ms"], [80, 140])
        self.assertEqual((output / first["source"]).read_bytes(), source.read_bytes())
        self.assertNotEqual((output / first["frames"][0]).read_bytes(), (output / first["frames"][1]).read_bytes())

    def test_playlist_uses_only_selected_order_and_one_player(self):
        tracks = []
        for index in range(3):
            source = self.root / f"track-{index}.mp3"
            source.write_bytes(b"synthetic-audio-" + str(index).encode())
            tracks.append({"track_id": str(index), "asset": source.name})
        self.project.update_project_data("sound", {"tracks": tracks, "mode": "playlist", "playlist": ["2", "0"], "volume": 23, "crossfade_ms": 750})
        output = self.build()
        self.assertEqual((output / "gallery/sounds/music-001.mp3").read_bytes(), b"synthetic-audio-2")
        self.assertEqual((output / "gallery/sounds/music-002.mp3").read_bytes(), b"synthetic-audio-0")
        self.assertFalse((output / "gallery/sounds/music-003.mp3").exists())
        content = (output / "index.html").read_text()
        self.assertEqual(content.count('id="bg-music"'), 1)
        self.assertEqual(content.count('id="bg-music-standby"'), 1)
        self.assertIn("750", content)

    def test_immutable_and_external_change_fingerprints_ignore_prompt_recovery(self):
        render = capture_render(self.session)
        with self.assertRaises(TypeError):
            render.snapshot["pages"][0]["name"] = "mutated"
        self.project.update_prompt(self.project.pages[0]["id"], {"text": "private changed prompt"})
        self.assertEqual(capture_render(self.session).source_fingerprint, render.source_fingerprint)
        self.write_image("assets/background-0.png", "yellow")
        self.assertNotEqual(capture_render(self.session).source_fingerprint, render.source_fingerprint)

    def test_unchanged_saves_and_resource_normalization_keep_visible_fingerprint(self):
        self.write_image("attachments/active.png", "red")
        page = self.project.pages[1]
        self.project.update_message(page["id"], {**page["message"], "html": '<p>Message A</p><img src="attachments/active.png">', "resources": ["attachments/active.png"]})
        fingerprint = capture_render(self.session).source_fingerprint
        repository = ProjectRepository(self.root / "saved")
        revision = None
        for _ in range(2):
            stored = repository.save(self.session.project, asset_root=self.session.asset_root, expected_revision=revision)
            revision = stored.revision
            restored = repository.load()
            self.session.replace(restored.project, asset_root=restored.asset_root)
            self.assertEqual(capture_render(self.session).source_fingerprint, fingerprint)

    def test_failed_cancelled_and_stale_build_preserve_previous_output(self):
        target = self.root / "active"
        generate_render(self.session, destination=target, seed_sfx=False)
        original = (target / "index.html").read_bytes()
        with self.assertRaises(StaleRenderError):
            generate_render(self.session, destination=target, seed_sfx=False, cancelled=lambda: True)
        self.assertEqual((target / "index.html").read_bytes(), original)
        real_build = build_render_to
        def change_during_build(*args, **kwargs):
            result = real_build(*args, **kwargs)
            self.project.rename_page(self.project.pages[0]["id"], "Changed during build")
            return result
        with mock.patch("forge_render.build_render_to", side_effect=change_during_build):
            with self.assertRaises(StaleRenderError):
                generate_render(self.session, destination=target, seed_sfx=False)
        self.assertEqual((target / "index.html").read_bytes(), original)
        (self.root / "assets/background-0.png").write_bytes(b"corrupt synthetic artwork")
        with self.assertRaises(RenderValidationError):
            generate_render(self.session, destination=target, seed_sfx=False)
        self.assertEqual((target / "index.html").read_bytes(), original)
        render = capture_render(self.session)
        self.session.replace(self.project)
        self.assertFalse(render_is_current(self.session, render))

    def test_missing_artwork_preview_warning_and_final_block(self):
        (self.root / "assets/background-0.png").unlink()
        output = self.build(mode="preview")
        manifest = json.loads((output / DISPLAY_MANIFEST).read_text())
        self.assertEqual(manifest["warnings"][0]["page_id"], self.project.pages[0]["id"])
        self.assertTrue((output / manifest["pages"][0]["source"]).is_file())
        with self.assertRaises(RenderValidationError):
            self.build("blocked")

    def test_unknown_layout_and_unsafe_resource_report_original_page(self):
        page = self.project.pages[1]
        self.project.update_message(page["id"], {**page["message"], "layout": {"width": 100}})
        self.project.update_artwork(page["id"], {"asset": "../outside.png"})
        issues = validate_render(capture_render(self.session))
        self.assertEqual({item.page_id for item in issues}, {page["id"]})
        self.assertEqual({item.code for item in issues}, {"unsupported_layout", "invalid_artwork"})

    def test_newer_same_revision_generation_wins(self):
        target = self.root / "active"
        real_build = build_render_to
        newer_output = []
        def build_newer_after_old(*args, **kwargs):
            result = real_build(*args, **kwargs)
            with mock.patch("forge_render.build_render_to", real_build):
                newer_output.append(generate_render(self.session, destination=target, output_mode="display", seed_sfx=False))
            return result
        with mock.patch("forge_render.build_render_to", side_effect=build_newer_after_old):
            with self.assertRaises(StaleRenderError):
                generate_render(self.session, destination=target, output_mode="preview", seed_sfx=False)
        self.assertEqual(newer_output, [target])
        self.assertEqual(json.loads((target / DISPLAY_MANIFEST).read_text())["output_mode"], "display")

    def test_message_only_change_reuses_checked_gif_frames(self):
        frames = [Image.new("RGB", (20, 30), color) for color in ("red", "blue")]
        frames[0].save(self.root / "moving.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
        self.project.update_artwork(self.project.pages[0]["id"], {"asset": "moving.gif"})
        output = self.build()
        page = self.project.pages[1]
        self.project.update_message(page["id"], {**page["message"], "html": "<b>Updated Message A</b>"})
        import image_animation
        with mock.patch("image_animation._extract_gif_frames", wraps=image_animation._extract_gif_frames) as extract:
            second = build_render_to(capture_render(self.session), self.root / "second", seed_sfx=False, reuse_play_directory=output)
        extract.assert_not_called()
        self.assertIn("Updated Message A", (second / "index.html").read_text())
        manifest = json.loads((second / DISPLAY_MANIFEST).read_text())
        self.assertTrue(manifest["animations"]["0"]["source"].endswith(".gif"))

    def test_existing_generate_entrypoint_dispatches_to_real_dynamic_renderer(self):
        shared = get_project_session(self.root)
        shared.replace(self.project, asset_root=self.root)
        render = capture_render(shared)
        output, built = generate.ensure_play_bundle(self.root, render_input=render, seed_sfx=False)
        self.assertTrue(built)
        self.assertTrue((output / DISPLAY_MANIFEST).is_file())
        _, built = generate.ensure_play_bundle(self.root, seed_sfx=False)
        self.assertFalse(built)

    def test_two_distinct_fonts_share_one_embedding_pass(self):
        from font_export import inspect_font_family
        if not all(inspect_font_family(self.root, name).faces for name in ("Arial", "Times New Roman")):
            self.skipTest("Required installed fixture fonts unavailable")
        for index, family in ((1, "Arial"), (3, "Times New Roman")):
            page = self.project.pages[index]
            self.project.update_message(page["id"], {**page["message"], "html": f'<p style="font-family:{family}">{family}</p>'})
        output = self.build()
        state = json.loads((output / generate.BUILD_STATE_FILE).read_text())
        self.assertEqual(set(state["font_export"]["embedded"]), {"Arial", "Times New Roman"})
        self.assertEqual(len(state["font_export"]["files"]), len(list((output / "gallery/fonts").iterdir())))
        content = (output / "index.html").read_text()
        self.assertIn("LetterSmithFont1", content)
        self.assertIn("LetterSmithFont2", content)


if __name__ == "__main__":
    unittest.main()
