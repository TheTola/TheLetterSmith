"""Delayed Forge results must retain their original canonical owner."""
from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from Forge_Tab import (
    ForgeTab, _ForgeOperationError, _apply_forge_context,
    _forge_context_is_current, _forge_project_context, _forge_source_fingerprint,
    _publication_fields_from_result, _record_verified_publication,
)
from project_model import create_project
from project_session import get_project_session, release_project_session
from publishing import PublishResult
from settings_store import SettingsStore, PUBLISHED_SOURCE_FINGERPRINT_KEY


class ForgeSessionOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.session = get_project_session(self.root)
        self.session.replace(create_project(["cover", "letter", "letter", "end_page"]), asset_root=self.root)
        self.context = _forge_project_context(self.root)

    def tearDown(self):
        release_project_session(self.root)
        self.temporary.cleanup()

    def test_selection_and_edits_keep_owner_but_replace_even_with_same_id_rejects(self):
        self.session.select_page(self.session.project.pages[2]["id"])
        self.session.project.rename_page(self.session.project.pages[1]["id"], "Renamed")
        self.assertTrue(_forge_context_is_current(self.root, self.context))
        self.session.replace(self.session.project, asset_root=self.root)
        self.assertFalse(_forge_context_is_current(self.root, self.context))
        operation = mock.Mock()
        with self.assertRaises(_ForgeOperationError):
            _apply_forge_context(self.root, self.context, operation)
        operation.assert_not_called()

    def test_preview_replacement_rejects_before_active_path_or_metadata_changes(self):
        self.session.replace(create_project(), asset_root=self.root)
        forge = SimpleNamespace(project_root=self.root, _record_active_play_dir=mock.Mock())
        with self.assertRaises(_ForgeOperationError):
            ForgeTab._finish_preview(forge, (self.root / "old", True, None, 1, "old", "old", self.context), record_activity=False)
        forge._record_active_play_dir.assert_not_called()

    def test_queued_metadata_never_follows_project_replacement(self):
        self.session.replace(create_project(), asset_root=self.root)
        forge = SimpleNamespace(
            project_root=self.root,
            _pending_metadata_update=(self.root / "old", None, False, self.context),
            _update_metadata_silently=mock.Mock(),
        )
        ForgeTab._run_pending_metadata_update(forge)
        forge._update_metadata_silently.assert_not_called()

    def test_edit_after_worker_result_is_still_stale_at_ui_completion(self):
        fingerprint = _forge_source_fingerprint(self.root)
        self.session.project.rename_page(self.session.project.pages[1]["id"], "Edited after worker")
        forge = SimpleNamespace(
            project_root=self.root, _source_revision=1,
            _record_active_play_dir=mock.Mock(), request_preview=mock.Mock(),
            is_protected_project=lambda: False, _metadata_timer=mock.Mock(),
        )
        ForgeTab._finish_preview(forge, (self.root / "old", True, None, 1, fingerprint, fingerprint, self.context), record_activity=False)
        self.assertTrue(forge._preview_refresh_pending)
        self.assertTrue(forge._preview_refresh_requested)
        forge.request_preview.assert_not_called()

    def test_pending_authentication_keeps_preview_refresh_deferred(self):
        forge = SimpleNamespace(
            _shutdown=False, _worker_thread=None, _worker=None,
            _set_busy=mock.Mock(), _finish_restore_activity=mock.Mock(),
            _preview_refresh_requested=True, _tab_active=True,
            _publication_operation="publish", ensure_preview_current=mock.Mock(),
        )
        with mock.patch("Forge_Tab.QtCore.QTimer.singleShot") as schedule:
            ForgeTab._operation_finished(forge)
        schedule.assert_not_called()
        self.assertTrue(forge._preview_refresh_requested)

    def test_late_edit_does_not_open_or_report_stale_preview_as_updated(self):
        forge = SimpleNamespace(
            _preview_refresh_pending=True, _set_status=mock.Mock(),
            _finish_preview=mock.Mock(return_value=(self.root, self.root / "index.html")),
        )
        with mock.patch("Forge_Tab.QtGui.QDesktopServices.openUrl") as open_url:
            ForgeTab._preview_completed(forge, ())
            ForgeTab._embedded_preview_completed(forge, ())
        open_url.assert_not_called()
        self.assertTrue(all("draft changed" in call.args[0] for call in forge._set_status.call_args_list))

    def _forge(self):
        return SimpleNamespace(
            project_root=self.root, settings=SettingsStore(self.root),
            _source_revision=8, _pending_publish_context=object(),
            _record_active_play_dir=mock.Mock(), request_preview=mock.Mock(),
            _refresh_catalog_entry=mock.Mock(), published_url_changed=mock.Mock(),
            refresh_project_state=mock.Mock(), _sync_publishing_controls=mock.Mock(),
            _set_status=mock.Mock(), _finish_publication_activity=mock.Mock(),
            _queue_github_operation_retry=mock.Mock(return_value=False),
            _show_publish_failure=mock.Mock(),
        )

    def _result(self, *, success=True):
        result = PublishResult(
            success, url="https://synthetic.github.io/display/", public_path="display",
            provider="github_pages", published_at="2026-10-13T12:00:00+00:00",
            source_fingerprint="older-output", owner="synthetic",
            repository="synthetic.github.io", verified=success,
        )
        return (self.root / "display", None, {}, result, 3, "older-output", "newer-draft", None, False, self.context)

    def test_publish_success_for_previous_project_cannot_write_current_settings(self):
        forge = self._forge()
        before = forge.settings.snapshot()
        self.session.replace(create_project(), asset_root=self.root)
        ForgeTab._publish_completed(forge, self._result())
        self.assertEqual(forge.settings.snapshot(), before)
        self.assertEqual(self.session.project.to_dict()["publication"], {})
        forge._record_active_play_dir.assert_not_called()
        forge.published_url_changed.emit.assert_not_called()

    def test_older_success_records_output_revision_without_marking_new_draft_current(self):
        forge = self._forge()
        with mock.patch("Forge_Tab.update_saved_publication_metadata"), mock.patch("Forge_Tab.play_ui_sound"):
            ForgeTab._publish_completed(forge, self._result())
        self.assertEqual(forge.settings.get(PUBLISHED_SOURCE_FINGERPRINT_KEY), "older-output")
        self.assertEqual(self.session.project.to_dict()["publication"][PUBLISHED_SOURCE_FINGERPRINT_KEY], "older-output")
        self.assertTrue(forge._preview_refresh_pending)
        forge.request_preview.assert_not_called()

    def test_failed_transport_preserves_previous_publication(self):
        forge = self._forge()
        before = forge.settings.snapshot()
        ForgeTab._publish_completed(forge, self._result(success=False))
        self.assertEqual(forge.settings.snapshot(), before)
        self.assertEqual(self.session.project.to_dict()["publication"], {})

    def test_verified_transport_persists_before_any_ui_callback(self):
        from project_repository import active_project_repository
        publication = _publication_fields_from_result(self._result()[3])
        _record_verified_publication(self.root, self.context, publication)
        self.assertEqual(SettingsStore(self.root).get(PUBLISHED_SOURCE_FINGERPRINT_KEY), "older-output")
        stored = active_project_repository(self.root).load()
        self.assertEqual(stored.project.to_dict()["publication"][PUBLISHED_SOURCE_FINGERPRINT_KEY], "older-output")
        self.session.replace(create_project(), asset_root=self.root)
        with self.assertRaises(_ForgeOperationError):
            _record_verified_publication(self.root, self.context, publication)
        self.assertEqual(self.session.project.to_dict()["publication"], {})

    def test_readiness_routes_original_page_id_through_existing_shell_hook(self):
        page = self.session.project.pages[2]
        self.session.project.update_page_recovery(page["id"], {"legacy_key": "page_2"})
        forge = SimpleNamespace(project_root=self.root, dismiss_readiness=mock.Mock(), correction_requested=mock.Mock())
        selected = self.session.selected_page_id
        ForgeTab._handle_readiness_correction(forge, "images", page["id"])
        self.assertEqual(self.session.selected_page_id, selected)
        forge.correction_requested.emit.assert_called_once_with("images", page["id"])

    def test_output_preparation_uses_all_editors_and_stops_on_flush_failure(self):
        shell = SimpleNamespace(flush_project_edits=mock.Mock(return_value=False),
                                flush_prompt_writer_state=mock.Mock(return_value=True))
        forge = SimpleNamespace(window=lambda: shell, _set_status=mock.Mock())
        self.assertFalse(ForgeTab._flush_prompt_writer_state(forge))
        shell.flush_project_edits.assert_called_once_with()
        shell.flush_prompt_writer_state.assert_not_called()
        forge._set_status.assert_called_once()

    def test_preview_release_failure_is_reported_and_can_retry(self):
        forge = SimpleNamespace(_project_release_error="", _set_status=mock.Mock(),
                                preview_files_release_requested=mock.Mock())
        forge.preview_files_release_requested.emit.side_effect = lambda: ForgeTab.report_project_file_release_failure(forge, "Preview still releasing")
        self.assertFalse(ForgeTab._release_preview_files(forge))
        forge._set_status.assert_called_once_with("Preview still releasing", error=True, timeout_ms=0)
        forge.preview_files_release_requested.emit.side_effect = None
        self.assertTrue(ForgeTab._release_preview_files(forge))
