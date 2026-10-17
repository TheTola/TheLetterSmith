from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from PIL import Image

from project_model import create_project
from project_repository import ProjectRepository, ProjectConflictError, ProjectRepositoryError


class ProjectRepositoryTests(unittest.TestCase):
    def test_generation_commit_retries_sharing_violation_and_preserves_failed_save(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = ProjectRepository(root / "saved")
            project = create_project(())
            original_replace = os.replace
            attempts = []
            def transient_replace(source, destination):
                if Path(source).name.startswith(".s-"):
                    attempts.append(source)
                    if len(attempts) == 1:
                        raise PermissionError("Synthetic temporary sharing violation")
                return original_replace(source, destination)
            with mock.patch("transactional_io.os.replace", side_effect=transient_replace):
                saved = repository.save(project, asset_root=root)
            self.assertEqual(len(attempts), 2)
            project.update_project_data("metadata", {"title": "Not committed"})
            with mock.patch("project_repository._replace_directory_path", side_effect=PermissionError("Still locked")):
                with self.assertRaises(PermissionError):
                    repository.save(project, asset_root=root, expected_revision=saved.revision)
            self.assertEqual(repository.load().revision, saved.revision)
            self.assertNotEqual(repository.load().project.to_dict(), project.to_dict())

    def test_migration_report_follows_identity_or_explicit_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = ProjectRepository(root / "saved")
            project = create_project(())
            report = {"converted": True, "source_format": "legacy_active"}
            first = repository.save(project, asset_root=root, migration_report=report)
            same = repository.save(project, asset_root=root, expected_revision=first.revision)
            self.assertTrue(same.migration_report["converted"])
            fresh = repository.save(create_project(()), asset_root=root, expected_revision=same.revision)
            self.assertEqual(fresh.migration_report, {})
            copied = repository.save(project.independent_copy(), asset_root=root, expected_revision=fresh.revision,
                                     migration_report=same.migration_report)
            self.assertTrue(copied.migration_report["converted"])

    def test_activation_lock_reenters_for_listeners_but_excludes_other_processes(self):
        import subprocess
        import sys
        from project_repository import activation_lock
        from project_session import get_project_session
        from settings_store import SettingsStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with activation_lock(root), activation_lock(root):
                result = subprocess.run([sys.executable, "-c",
                    "import sys\nfrom project_repository import activation_lock, ProjectConflictError\n"
                    "try:\n with activation_lock(sys.argv[1]): sys.exit(2)\n"
                    "except ProjectConflictError as error:\n sys.exit(0 if error.code == 'writer_busy' else 3)",
                    str(root)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
            session = get_project_session(root)
            failures, notifications = [], []
            def synchronize_from_listener(event):
                if event.kind == "workspace":
                    notifications.append(event)
                    try:
                        session.synchronize_workspace()
                    except Exception as error:
                        failures.append(error)
            session.add_listener(synchronize_from_listener)
            SettingsStore(root).update_fields({"starting_volume": 13})
            session.save()
            self.assertTrue(notifications)
            self.assertEqual(failures, [])

    def test_five_pages_round_trip_independent_artwork_messages_and_exact_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = [Image.new("RGB", (12, 18), color) for color in ("red", "blue")]
            frames[0].save(root / "a.gif", save_all=True, append_images=frames[1:], duration=[90, 130], loop=0)
            Image.new("RGB", (12, 18), "green").save(root / "b.png")
            project = create_project(("cover", "letter", "illustration", "letter", "end_page"))
            pages = project.pages
            first, second = pages[1]["id"], pages[3]["id"]
            for page_id, label, asset in ((first, "A", "a.gif"), (second, "B", "b.png")):
                project.rename_page(page_id, "Repeated name")
                message = project.get_page(page_id)["message"]
                message.update(html=f"<p><b>Message {label}</b></p>", layout={"top": label})
                project.update_message(page_id, message)
                project.update_prompt(page_id, {"text": f"  Prompt {label}\nΔ 日本語 \n", "resolved_components": {"choice": label}})
                project.update_artwork(page_id, {"asset": asset, "media_type": "image/gif" if label == "A" else "image/png", "animation": {"start_delay_ms": 72 if label == "A" else 0}})
            order = [page["id"] for page in project.pages]
            order[1], order[3] = order[3], order[1]
            project.reorder_pages(order)
            duplicate = project.duplicate_page(first)
            document = project.get_page(duplicate)["message"]
            document["html"] = "<i>Only duplicate</i>"
            project.update_message(duplicate, document)
            repository = ProjectRepository(root / "saved")
            stored = repository.save(project, asset_root=root)
            loaded = repository.load()
            self.assertEqual(loaded.project.to_dict(), stored.project.to_dict())
            self.assertEqual([p["id"] for p in loaded.project.pages], [p["id"] for p in project.pages])
            self.assertEqual(loaded.project.get_page(first)["message"]["html"], "<p><b>Message A</b></p>")
            self.assertEqual(loaded.project.get_page(duplicate)["message"]["html"], "<i>Only duplicate</i>")
            self.assertEqual(loaded.project.get_page(second)["prompt"]["text"], "  Prompt B\nΔ 日本語 \n")
            for page_id, original in ((first, "a.gif"), (second, "b.png")):
                actual = loaded.asset_root / loaded.project.get_page(page_id)["artwork"]["asset"]
                self.assertEqual(actual.read_bytes(), (root / original).read_bytes())

    def test_shared_artwork_and_message_resource_survives_portable_save(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (8, 12), "red").save(root / "shared.png")
            original = (root / "shared.png").read_bytes()
            project = create_project(("cover", "letter"))
            cover_id, letter_id = [page["id"] for page in project.pages]
            project.update_artwork(cover_id, {"asset": "shared.png", "media_type": "image/png"})
            document = project.get_message(letter_id)
            document.update(html='<p><img src="shared.png"></p>', resources=["shared.png"])
            project.update_message(letter_id, document)
            repository = ProjectRepository(root / "saved")
            repository.save(project, asset_root=root)
            loaded = repository.load()
            portable = ProjectRepository(root / "portable")
            portable.save(loaded.project, asset_root=loaded.asset_root)
            for stored in (loaded, portable.load()):
                self.assertEqual(stored.project.get_message(letter_id), document)
                artwork = stored.project.get_page(cover_id)["artwork"]["asset"]
                self.assertTrue(artwork.startswith("a/"))
                self.assertEqual((stored.asset_root / artwork).read_bytes(), original)
                self.assertEqual((stored.asset_root / "shared.png").read_bytes(), original)

    def test_revision_conflict_and_interrupted_commit_leave_old_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = ProjectRepository(root / "saved")
            original = create_project(())
            stored = repository.save(original, asset_root=root)
            with self.assertRaises(ProjectConflictError):
                repository.save(original, asset_root=root)
            changed = create_project(("letter",))
            from project_repository import atomic_write_json
            def fail_pointer(path, payload, **kwargs):
                if Path(path) == repository.pointer:
                    raise PermissionError("Synthetic commit interruption")
                return atomic_write_json(path, payload, **kwargs)
            with mock.patch("project_repository.atomic_write_json", side_effect=fail_pointer):
                with self.assertRaises(PermissionError):
                    repository.save(changed, asset_root=root, expected_revision=stored.revision)
            self.assertEqual(repository.load().project.to_dict(), original.to_dict())
            self.assertEqual(repository.load().revision, stored.revision)

    def test_deleted_page_and_unassigned_resources_survive_portable_save(self):
        from project_model import DeletionRecord, ProjectModel
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (8, 12), "red").save(root / "art.gif")
            (root / "attached.png").write_bytes(b"preserved embedded resource")
            project = create_project(("cover", "letter", "end_page"))
            page_id = project.pages[1]["id"]
            project.update_artwork(page_id, {"asset": "art.gif", "media_type": "image/gif"})
            project.delete_page(page_id)
            payload = project.to_dict()
            payload["recovery"]["unassigned_message"] = {"format": "html", "html": '<img src="attached.png">', "resources": ["attached.png"], "layout": {}, "settings": {}}
            saved = ProjectRepository(root / "first").save(ProjectModel.from_dict(payload), asset_root=root)
            second = ProjectRepository(root / "portable")
            second.save(saved.project, asset_root=saved.asset_root)
            loaded = second.load()
            deleted = loaded.project.to_dict()["recovery"]["deleted_pages"][0]
            loaded.project.restore_page(DeletionRecord(loaded.project.project_id, deleted["page"], deleted["index"]))
            restored_asset = loaded.asset_root / loaded.project.get_page(page_id)["artwork"]["asset"]
            self.assertEqual(restored_asset.read_bytes(), (root / "art.gif").read_bytes())
            self.assertEqual((loaded.asset_root / "attached.png").read_bytes(), b"preserved embedded resource")

    def test_missing_resources_are_repairable_and_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = create_project(("letter",))
            page_id = project.pages[0]["id"]
            project.update_artwork(page_id, {"asset": "missing.gif", "media_type": "image/gif"})
            repository = ProjectRepository(root / "saved")
            saved = repository.save(project, asset_root=root)
            self.assertEqual(repository.load().project.get_page(page_id)["artwork"]["asset"], "missing.gif")
            manifest = json.loads((saved.asset_root / "resources.json").read_text())
            self.assertEqual(manifest["missing"], ["missing.gif"])
            self.assertTrue(any(issue.code == "missing_resource" for issue in saved.project.validate()))
            project.update_artwork(page_id, {"asset": "../escape.gif"})
            project.update_project_data("recovery", {"missing_resources": ["../escape.gif"]})
            with self.assertRaises(ProjectRepositoryError):
                repository.save(project, asset_root=root, expected_revision=saved.revision)
            self.assertEqual(repository.load().revision, saved.revision)

    def test_draft_service_save_load_and_failed_load_preserve_active_session(self):
        from project_save import ProjectSaveService
        from project_state import ProjectStateController
        from project_session import get_project_session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "work"
            service = ProjectSaveService(workspace, ProjectStateController(workspace))
            session = get_project_session(workspace)
            project = create_project(("cover", "letter", "illustration", "letter", "end_page"))
            ids = [page["id"] for page in project.pages]
            for page_id, letter in ((ids[1], "A"), (ids[3], "B")):
                document = project.get_page(page_id)["message"]
                document["html"] = f"<b>Message {letter}</b>"
                project.update_message(page_id, document)
                project.update_prompt(page_id, {"text": f"Prompt {letter}\n  exact"})
            session.replace(project, storage_revision=session.storage_revision)
            project = session.project
            project.reorder_pages([ids[0], ids[3], ids[2], ids[1], ids[4]])
            duplicate = project.duplicate_page(ids[1])
            document = project.get_page(duplicate)["message"]
            document["html"] = "<i>Duplicate only</i>"
            project.update_message(duplicate, document)
            stored = service.save_project(root / "draft")
            source_before = (stored.asset_root / "project.json").read_bytes()
            loaded = service.load_project(root / "draft")
            self.assertEqual(loaded.project.get_page(ids[3])["message"]["html"], "<b>Message B</b>")
            self.assertEqual(loaded.project.get_page(duplicate)["message"]["html"], "<i>Duplicate only</i>")
            self.assertEqual((stored.asset_root / "project.json").read_bytes(), source_before)
            before, epoch = session.project.to_dict(), session.session_id
            (root / "bad").mkdir()
            (root / "bad/current.json").write_text("malformed", encoding="utf-8")
            with self.assertRaises(ProjectRepositoryError):
                service.load_project(root / "bad")
            self.assertEqual(session.project.to_dict(), before)
            self.assertEqual(session.session_id, epoch)

    def test_legacy_optional_wall_load_preserves_source_and_commits_identity(self):
        from test_saved_letter_restore_sound import SavedLetterSoundRestoreTests
        from saved_letters import SavedLetterCatalog, SavedLetterRestorer
        from project_session import get_project_session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = SavedLetterSoundRestoreTests()._bundle(root)
            metadata_path = bundle / "lettersmith-metadata.json"
            metadata = json.loads(metadata_path.read_text())
            metadata["schema_version"] = 3
            metadata.pop("project_created_at", None)
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            (bundle / "prompt_writer_state.json").unlink()
            (bundle / "gallery/pages/wall.png").unlink()
            (bundle / "gallery/pages/lettersmith-images.json").unlink()
            before = {path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
            entries = SavedLetterCatalog(root).list_entries()
            self.assertEqual(len(entries), 1)
            SavedLetterRestorer(root).restore(entries[0])
            project = get_project_session(root).project
            self.assertEqual([page["type"] for page in project.pages], ["cover", "illustration", "end_page"])
            self.assertIn("unassigned_message", project.to_dict()["recovery"])
            self.assertEqual(before, {path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob("*") if path.is_file()})

    def test_selected_audio_is_portable_without_copying_reusable_library(self):
        from project_repository import read_legacy_project
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "gallery/user/sounds/appssong"
            (archive / "processed").mkdir(parents=True)
            (archive / "processed/chosen.mp3").write_bytes(b"chosen audio bytes")
            (archive / "processed/private-unused.mp3").write_bytes(b"not part of this project")
            (archive / "project_sound.json").write_text(json.dumps({"version": 2, "mode": "playlist", "playlist": ["chosen"], "selected_track_id": "chosen"}))
            library = {"version": 2, "tracks": {"chosen": {"processed_file": "chosen.mp3", "display_title": "Chosen"}, "unused": {"processed_file": "private-unused.mp3"}}}
            (archive / "library.json").write_text(json.dumps(library))
            migrated = read_legacy_project(root)
            stored = ProjectRepository(root / "portable").save(migrated.project, asset_root=root)
            sound = stored.project.to_dict()["sound"]
            self.assertEqual(sound["playlist"], ["chosen"])
            self.assertEqual((stored.asset_root / sound["tracks"][0]["asset"]).read_bytes(), b"chosen audio bytes")
            self.assertEqual(json.loads((archive / "library.json").read_text()), library)
            self.assertFalse(any("private-unused" in str(path) for path in stored.asset_root.rglob("*")))

    def test_service_switch_restores_settings_sound_and_rolls_back_failed_commit(self):
        from project_save import ProjectSaveService
        from project_state import ProjectStateController
        from project_session import get_project_session
        from settings_store import SettingsStore
        from sound_model import load_project_state, current_music_path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            service = ProjectSaveService(workspace, ProjectStateController(workspace))
            source = root / "source"
            source.mkdir()
            (source / "track.mp3").write_bytes(b"synthetic audio A")
            a = create_project(("cover", "end_page"), metadata={"recipient_name": "Alpha", "recipient_title": "First"})
            a.update_shared_settings({"starting_volume": 17})
            a.update_project_data("sound", {"mode": "playlist", "playlist": ["a"], "selected_track_id": "a", "tracks": [{"track_id": "a", "asset": "track.mp3", "display_title": "A"}]})
            ProjectRepository(root / "a").save(a, asset_root=source)
            b = create_project((), metadata={"recipient_name": "Beta", "recipient_title": "Second"})
            b.update_shared_settings({"starting_volume": 43})
            ProjectRepository(root / "b").save(b, asset_root=source)
            service.load_project(root / "a")
            self.assertEqual(SettingsStore(workspace).get("starting_volume"), 17)
            self.assertTrue(load_project_state(workspace).playlist)
            before = get_project_session(workspace).project.to_dict()
            from project_repository import atomic_write_json
            def fail_active(path, payload, **kwargs):
                if Path(path).name == "current.json" and ".lettersmith-project" in str(path):
                    raise PermissionError("Synthetic interrupted activation")
                return atomic_write_json(path, payload, **kwargs)
            with mock.patch("project_repository.atomic_write_json", side_effect=fail_active):
                with self.assertRaises(PermissionError):
                    service.load_project(root / "b")
            self.assertEqual(get_project_session(workspace).project.to_dict(), before)
            self.assertEqual(SettingsStore(workspace).get("starting_volume"), 17)
            self.assertTrue(load_project_state(workspace).playlist)
            service.load_project(root / "b")
            self.assertEqual(SettingsStore(workspace).get("recipient_name"), "Beta")
            self.assertEqual(SettingsStore(workspace).get("starting_volume"), 43)
            self.assertEqual(service.project_state.identity.project_id, b.project_id)
            self.assertEqual(load_project_state(workspace).playlist, [])
            self.assertFalse(current_music_path(workspace).exists())

    def test_interrupted_activation_recovers_committed_views_before_import(self):
        from project_repository import active_project_repository, begin_activation, activation_pending
        from project_session import get_project_session, release_project_session
        from project_save import ProjectSaveService
        from project_state import ProjectStateController
        from settings_store import SettingsStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            project = create_project(("cover", "letter", "end_page"), metadata={"recipient_name": "Committed", "recipient_title": "Original"})
            page_id = project.pages[1]["id"]
            message = project.get_page(page_id)["message"]
            message["html"] = "<b>Committed message</b>"
            project.update_message(page_id, message)
            project.update_shared_settings({"starting_volume": 19})
            ProjectRepository(root / "saved").save(project, asset_root=source)
            workspace = root / "work"
            service = ProjectSaveService(workspace, ProjectStateController(workspace))
            service.load_project(root / "saved")
            session = get_project_session(workspace)
            before = session.project.to_dict()
            storage_revision = session.storage_revision
            begin_activation(workspace)
            SettingsStore(workspace).update_fields({"recipient_name": "Partial Other Project", "starting_volume": 91})
            (workspace / "gallery/user/message/message.html").write_text("<p>Partial other message</p>", encoding="utf-8")
            with self.assertRaises(ProjectRepositoryError):
                session.save()
            with self.assertRaises(ProjectRepositoryError):
                session.synchronize_workspace()
            release_project_session(workspace)
            recovered = get_project_session(workspace)
            self.assertFalse(activation_pending(workspace))
            self.assertEqual(SettingsStore(workspace).get("recipient_name"), "Committed")
            self.assertEqual(SettingsStore(workspace).get("starting_volume"), 19)
            self.assertEqual((workspace / "gallery/user/message/message.html").read_text(encoding="utf-8"), "<b>Committed message</b>")
            self.assertEqual(recovered.project.to_dict(), before)
            self.assertEqual(recovered.storage_revision, storage_revision)
            self.assertEqual(active_project_repository(workspace).load().project.get_page(page_id)["message"]["html"], "<b>Committed message</b>")

    def test_recovery_projects_unmapped_api_pages_without_reimporting_duplicates(self):
        from project_repository import active_project_repository, begin_activation
        from project_session import get_project_session, release_project_session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = get_project_session(root)
            project = create_project(("letter", "illustration", "cover", "letter", "end_page"))
            ids = [page["id"] for page in project.pages]
            project.update_prompt(ids[3], {"text": "  Exact recovered prompt\n"})
            session.replace(project)
            committed = session.save(sync_workspace=False)
            begin_activation(root)
            (root / "gallery/user/message").mkdir(parents=True, exist_ok=True)
            (root / "gallery/user/message/message.html").write_text("partial")
            release_project_session(root)
            recovered = get_project_session(root)
            self.assertEqual(recovered.storage_revision, committed.revision)
            self.assertEqual(active_project_repository(root).load().project.to_dict(), committed.project.to_dict())
            self.assertEqual([page["id"] for page in recovered.project.pages], ids)
            self.assertEqual(recovered.project.get_page(ids[3])["prompt"]["text"], "  Exact recovered prompt\n")
            self.assertTrue(all(page["recovery"].get("legacy_key") for page in recovered.project.pages))
            recovered.save()
            release_project_session(root)
            reopened = get_project_session(root)
            reopened.save()
            self.assertEqual([page["id"] for page in reopened.project.pages], ids)
            self.assertEqual(reopened.project.get_page(ids[3])["prompt"]["text"], "  Exact recovered prompt\n")

    def test_corrupt_future_schema_and_changed_assets_fail_without_source_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "art.gif").write_bytes(b"GIF89a original")
            project = create_project(("cover",))
            project.update_artwork(project.pages[0]["id"], {"asset": "art.gif"})
            repository = ProjectRepository(root / "saved")
            stored = repository.save(project, asset_root=root)
            art = stored.asset_root / stored.project.pages[0]["artwork"]["asset"]
            art.write_bytes(b"changed")
            with self.assertRaises(ProjectRepositoryError):
                repository.load()
            art.write_bytes((root / "art.gif").read_bytes())
            document = stored.asset_root / "project.json"
            payload = json.loads(document.read_text())
            original_reference = payload["pages"][0]["artwork"]["asset"]
            payload["pages"][0]["artwork"]["asset"] = "unlisted.gif"
            document.write_text(json.dumps(payload))
            with self.assertRaises(ProjectRepositoryError):
                repository.load()
            payload["pages"][0]["artwork"]["asset"] = original_reference
            payload["schema_version"] = 999
            document.write_text(json.dumps(payload))
            before = hashlib.sha256(document.read_bytes()).hexdigest()
            with self.assertRaises(ProjectRepositoryError):
                repository.load()
            self.assertEqual(hashlib.sha256(document.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
