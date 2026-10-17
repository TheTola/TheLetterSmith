from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from project_model import ProjectModel, ProjectModelError, create_project
from project_session import ProjectSession


class ProjectSessionTests(unittest.TestCase):
    def test_prompt_batch_captures_once_and_reverted_order_invalidates(self):
        factory = mock.Mock(return_value={'role': 'Resolved once'})
        snapshot, resolved, targets = self.session.capture_prompt_batch(self.ids, factory)
        self.assertEqual(resolved, {'role': 'Resolved once'})
        factory.assert_called_once_with()
        self.session.select_page(self.ids[-1])
        self.assertTrue(all(self.session.target_is_current(target) for target in targets))
        self.session.project.reorder_pages(list(reversed(self.ids)))
        self.session.project.reorder_pages(self.ids)
        self.assertTrue(all(not self.session.target_is_current(target) for target in targets))
        self.assertEqual([page['id'] for page in snapshot.pages], self.ids)
        self.session.capture_prompt_batch(self.ids, factory)
        factory.assert_called_once_with()

    def test_presentation_changes_invalidate_prompt_work_even_when_reverted(self):
        session = self.session
        token = session.capture_target(self.ids[0], fields=("prompt",))
        session.project.update_project_data("presentation", {"artwork_output": {"width": 900, "height": 1600}})
        session.project.update_project_data("presentation", {"artwork_output": {}})
        self.assertFalse(session.target_is_current(token))
        self.assertTrue(session.project.get_prompt(self.ids[0])["stale"])

    def setUp(self):
        self.session = ProjectSession(create_project(('cover', 'letter', 'illustration', 'letter', 'end_page')))
        self.ids = [page['id'] for page in self.session.project.pages]

    def test_selection_is_not_an_edit_and_reorder_does_not_redirect_result(self):
        session = self.session
        revision = session.project.revision
        session.select_page(self.ids[1])
        self.assertEqual(session.project.revision, revision)
        session.project.update_prompt(self.ids[1], {'text': 'Previous prompt', 'stale': False})
        default_target = session.capture_target(self.ids[1])
        target = session.capture_target(self.ids[1], fields=('type', 'message'))
        session.select_page(self.ids[3])
        session.project.reorder_pages(list(reversed(self.ids)))
        session.project.rename_page(self.ids[1], 'Same name')
        self.assertTrue(session.target_is_current(default_target))
        self.assertTrue(session.apply_result(target, lambda model, page_id: model.update_prompt(page_id, {'text': 'Result A'})))
        self.assertEqual(session.project.get_prompt(self.ids[1])['text'], 'Result A')
        self.assertIsNone(session.project.get_prompt(self.ids[3])['text'])

    def test_relevant_change_even_when_reverted_invalidates_result(self):
        session = self.session
        page_id = self.ids[1]
        original = session.project.get_message(page_id)
        target = session.capture_target(page_id, fields=('message',))
        session.project.update_message(page_id, {**original, 'html': '<b>Changed</b>'})
        session.project.update_message(page_id, original)
        self.assertFalse(session.apply_result(target, lambda *_: self.fail('stale result applied')))

    def test_project_target_captures_and_applies_without_pages(self):
        session = ProjectSession(create_project([]))
        target = session.capture_project_target()
        self.assertTrue(session.project_target_is_current(target))
        self.assertTrue(session.apply_project_result(target, lambda model: model.update_project_data(
            'sound', {'playlist': ['track-a'], 'volume': 35, 'muted': False})))
        self.assertEqual(session.project.to_dict()['sound']['playlist'], ['track-a'])
        self.assertEqual(session.project.pages, [])
        self.assertFalse(session.project_target_is_current(target))
        with self.assertRaises(ProjectModelError):
            session.capture_project_target(fields=('pages',))

    def test_project_target_ignores_selection_pages_and_unrelated_project_fields(self):
        session = self.session
        target = session.capture_project_target()
        session.select_page(self.ids[1])
        session.project.reorder_pages(reversed(self.ids))
        session.project.update_prompt(self.ids[1], {'text': 'Independent text'})
        session.project.update_shared_settings({'font': 'Serif'})
        session.project.update_project_data('presentation', {'style': 'fade'})
        session.project.update_project_data('sound', {})
        session.adopt(ProjectModel.from_dict(session.project.to_dict()))
        self.assertTrue(session.project_target_is_current(target))
        combined = session.capture_project_target(fields=('sound', 'presentation'))
        session.project.update_project_data('presentation', {'style': 'slide'})
        self.assertFalse(session.project_target_is_current(combined))
        self.assertTrue(session.project_target_is_current(target))

    def test_project_edit_then_revert_invalidates_without_notifications(self):
        session = self.session
        session.project.update_project_data('sound', {'volume': 40})
        target = session.capture_project_target()
        session.project.remove_listener(session._on_model_change)
        try:
            session.project.update_project_data('sound', {'volume': 70})
            session.project.update_project_data('sound', {'volume': 40})
            self.assertFalse(session.apply_project_result(target, lambda *_: self.fail('Stale project work applied')))
        finally:
            session.project.add_listener(session._on_model_change)

    def test_project_snapshot_edit_then_revert_does_not_revive_target(self):
        session = self.session
        original = session.project.to_dict()
        target = session.capture_project_target()
        changed = ProjectModel.from_dict(original)
        changed.update_project_data('sound', {'volume': 70})
        session.adopt(changed)
        session.adopt(ProjectModel.from_dict(original))
        self.assertEqual(session.project.to_dict(), original)
        self.assertFalse(session.project_target_is_current(target))

    def test_project_target_rejected_during_and_after_unchanged_restoration(self):
        session = self.session
        target = session.capture_project_target()
        with session.restoring():
            with session.restoring():
                self.assertFalse(session.project_target_is_current(target))
                self.assertFalse(session.apply_project_result(target, lambda *_: self.fail('Restoration accepted work')))
                with self.assertRaises(RuntimeError):
                    session.capture_project_target()
        self.assertFalse(session.project_target_is_current(target))
        self.assertTrue(session.project_target_is_current(session.capture_project_target()))

    def test_project_target_rejected_after_replacement_and_close(self):
        session = self.session
        original = session.project
        target = session.capture_project_target()
        session.replace(create_project([]))
        session.replace(original)
        self.assertFalse(session.apply_project_result(target, lambda *_: self.fail('Replaced project accepted work')))
        target = session.capture_project_target()
        session.close()
        self.assertFalse(session.apply_project_result(target, lambda *_: self.fail('Closed project accepted work')))
        with self.assertRaises(RuntimeError):
            session.capture_project_target()

    def test_concurrent_project_edit_cannot_slip_between_guard_and_apply(self):
        session = self.session
        model = session.project
        target = session.capture_project_target()
        edited = threading.Event()
        workers = []
        model.remove_listener(session._on_model_change)
        model.add_listener(lambda event: edited.set())
        model.add_listener(session._on_model_change)
        original_signature = session._project_target_signature
        def signature_with_concurrent_edit(fields):
            signature = original_signature(fields)
            if not workers:
                worker = threading.Thread(target=lambda: model.update_project_data('sound', {'volume': 75}))
                workers.append(worker)
                worker.start()
                edited.wait(0.1)
            return signature
        try:
            with mock.patch.object(session, '_project_target_signature', side_effect=signature_with_concurrent_edit):
                session.apply_project_result(target, lambda current: current.update_project_data('sound', {'volume': 20}))
        finally:
            for worker in workers:
                worker.join(2)
        self.assertTrue(all(not worker.is_alive() for worker in workers), 'Project guard deadlocked')
        self.assertEqual(model.to_dict()['sound']['volume'], 75)

    def test_concurrent_relevant_edit_cannot_slip_between_guard_and_apply(self):
        session = self.session
        page_id = self.ids[1]
        model = session.project
        target = session.capture_target(page_id, fields=('message',), include_shared=False)
        original = model.get_message(page_id)
        edited = threading.Event()
        workers = []
        # Observe the completed model mutation before its session notification
        # waits for apply_result's session lock.
        model.remove_listener(session._on_model_change)
        model.add_listener(lambda event: edited.set() if event.kind == 'message' else None)
        model.add_listener(session._on_model_change)
        original_signature = session._target_signature
        def signature_with_concurrent_edit(*args, **kwargs):
            signature = original_signature(*args, **kwargs)
            if not workers:
                worker = threading.Thread(target=lambda: model.update_message(
                    page_id, {**original, 'html': '<b>New user edit</b>'}))
                workers.append(worker)
                worker.start()
                edited.wait(0.5)
            return signature
        try:
            with mock.patch.object(session, '_target_signature', side_effect=signature_with_concurrent_edit):
                applied = session.apply_result(target, lambda project, identity: project.update_message(
                    identity, {**original, 'html': '<b>Obsolete result</b>'}))
        finally:
            for worker in workers:
                worker.join(2)
        self.assertTrue(all(not worker.is_alive() for worker in workers), 'Guarded apply deadlocked')
        # Either the result was rejected after the edit, or an atomic guarded
        # apply finished first and the newer edit followed it. Neither ordering
        # is allowed to replace the newer user edit with obsolete content.
        self.assertEqual(model.get_message(page_id)['html'], '<b>New user edit</b>')

    def test_edit_then_revert_invalidates_before_notifications_are_delivered(self):
        session = self.session
        page_id = self.ids[1]
        target = session.capture_target(page_id, fields=('message',), include_shared=False)
        original = session.project.get_message(page_id)
        session.project.remove_listener(session._on_model_change)
        try:
            session.project.update_message(page_id, {**original, 'html': 'Changed while notification waits'})
            session.project.update_message(page_id, original)
            self.assertFalse(session.target_is_current(target))
        finally:
            session.project.add_listener(session._on_model_change)

    def test_old_model_notification_is_ignored_after_project_replacement(self):
        session = self.session
        previous = session.project
        queued = []
        previous.add_listener(queued.append)
        session.replace(previous)
        received = []
        session.add_listener(received.append)
        previous.rename_page(self.ids[1], 'A late old-session edit')
        session._on_model_change(queued[-1])
        self.assertEqual(received, [])
        self.assertEqual(session.project.get_page(self.ids[1])['name'], '')

    def test_save_adopts_portable_state_without_detaching_model_references(self):
        from project_session import get_project_session, release_project_session
        with tempfile.TemporaryDirectory() as directory:
            session = get_project_session(directory)
            original = session.project
            page_id = original.pages[0]['id']
            original.update_artwork(page_id, {'asset': 'missing.png', 'media_type': 'image/png'})
            session.save(sync_workspace=False)
            self.assertIs(session.project, original)
            original.rename_page(page_id, 'Still the active model')
            self.assertEqual(session.project.get_page(page_id)['name'], 'Still the active model')
            release_project_session(directory)

    def test_restore_scope_blocks_save_and_preserves_committed_generation(self):
        from project_repository import active_project_repository
        from project_session import get_project_session, release_project_session
        with tempfile.TemporaryDirectory() as directory:
            session = get_project_session(directory)
            revision = session.storage_revision
            with session.restoring():
                with self.assertRaises(RuntimeError):
                    session.save(sync_workspace=False)
            self.assertEqual(active_project_repository(directory).load().revision, revision)
            release_project_session(directory)

    def test_restoration_waits_for_already_running_save(self):
        from project_repository import ProjectRepository
        from project_session import get_project_session, release_project_session
        with tempfile.TemporaryDirectory() as directory:
            session = get_project_session(directory)
            save_entered = threading.Event()
            release_save = threading.Event()
            restore_attempted = threading.Event()
            restore_entered = threading.Event()
            failures = []
            original_save = ProjectRepository.save
            def slow_save(repository, *args, **kwargs):
                save_entered.set()
                if not release_save.wait(2):
                    raise RuntimeError('Synthetic save was not released')
                return original_save(repository, *args, **kwargs)
            def save():
                try:
                    session.save(sync_workspace=False)
                except Exception as error:
                    failures.append(error)
            def restore():
                try:
                    restore_attempted.set()
                    with session.restoring():
                        restore_entered.set()
                except Exception as error:
                    failures.append(error)
            with mock.patch.object(ProjectRepository, 'save', autospec=True, side_effect=slow_save):
                saving = threading.Thread(target=save)
                restoring = threading.Thread(target=restore)
                saving.start()
                try:
                    self.assertTrue(save_entered.wait(2))
                    restoring.start()
                    self.assertTrue(restore_attempted.wait(2))
                    overlapped = restore_entered.wait(0.1)
                finally:
                    release_save.set()
                    saving.join(2)
                    if restoring.ident is not None:
                        restoring.join(2)
            self.assertFalse(overlapped, 'Restoration entered while the previous save was writing')
            self.assertTrue(restore_entered.is_set())
            self.assertFalse(saving.is_alive() or restoring.is_alive(), 'Save/restore lock deadlocked')
            self.assertEqual(failures, [])
            release_project_session(directory)

    def test_restoration_rejects_reentrant_jobs_until_completed(self):
        session = self.session
        page_id = self.ids[1]
        target = session.capture_target(page_id)
        with session.restoring():
            with session.restoring():
                self.assertFalse(session.target_is_current(target))
                self.assertFalse(session.apply_result(target, lambda *_: self.fail('Intermediate restoration accepted work')))
                with self.assertRaises(RuntimeError):
                    session.capture_target(page_id)
            self.assertTrue(session.is_restoring)
        self.assertFalse(session.is_restoring)
        self.assertTrue(session.target_is_current(target))
        self.assertTrue(session.target_is_current(session.capture_target(page_id)))

    def test_fresh_session_uses_existing_settings_identity(self):
        import uuid
        from project_session import get_project_session, release_project_session
        from settings_store import SettingsStore
        with tempfile.TemporaryDirectory() as directory:
            identity = str(uuid.uuid4())
            SettingsStore(directory).update_fields({'project_id': identity})
            session = get_project_session(directory)
            self.assertEqual(session.project.project_id, identity)
            self.assertEqual(SettingsStore(directory).snapshot()['project_id'], identity)
            release_project_session(directory)

    def test_fresh_template_preserves_existing_audio_and_settings(self):
        from project_session import get_project_session, release_project_session
        from settings_store import SettingsStore
        from sound_model import project_sound_path
        from transactional_io import atomic_write_json
        for state in ({}, {'mode': 'single', 'single_track_id': 'missing-track'},
                      {'mode': 'playlist', 'playlist': ['missing-track'], 'selected_track_id': 'missing-track'}):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                SettingsStore(directory).update_fields({'music_volume': 37})
                atomic_write_json(project_sound_path(directory), state)
                session = get_project_session(directory)
                try:
                    payload = session.project.to_dict()
                    self.assertEqual(len(payload['pages']), 4)
                    self.assertEqual(payload['shared_settings']['music_volume'], 37)
                    for key, value in state.items():
                        self.assertEqual(payload['sound'][key], value)
                finally:
                    release_project_session(directory)
                reopened = get_project_session(directory)
                try:
                    self.assertEqual(reopened.project.to_dict(), payload)
                finally:
                    release_project_session(directory)

    def test_reset_pointer_failure_preserves_session_then_success_reopens_empty(self):
        from project_repository import active_project_repository
        from project_session import get_project_session, release_project_session, reset_project_session
        from settings_store import SettingsStore
        from transactional_io import atomic_write_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            session = get_project_session(root)
            page = next(page for page in session.project.pages if page['message'] is not None)
            session.project.update_message(page['id'], {**page['message'], 'html': 'Retain on failed reset'})
            session.project.update_prompt(page['id'], {'text': 'Retain exact old prompt'})
            session.save(sync_workspace=False)
            before = session.project.to_dict()
            revision = session.storage_revision
            session_id = session.session_id
            SettingsStore(root).update_fields({'project_id': '', 'recipient_name': '', 'recipient_title': '', 'message_overlay_opacity': 0.42})
            def fail_pointer(path, payload, **kwargs):
                if Path(path).name == 'current.json':
                    raise OSError('Synthetic reset pointer failure')
                return atomic_write_json(path, payload, **kwargs)
            with mock.patch('project_repository.atomic_write_json', side_effect=fail_pointer):
                with self.assertRaises(OSError):
                    reset_project_session(root)
            self.assertEqual(session.project.to_dict(), before)
            self.assertEqual(session.storage_revision, revision)
            self.assertEqual(session.session_id, session_id)
            self.assertEqual(active_project_repository(root).load().project.to_dict(), before)
            reset_project_session(root)
            reset = session.project.to_dict()
            self.assertNotEqual(reset['project_id'], before['project_id'])
            self.assertEqual(reset['sound'], {})
            self.assertEqual(reset['publication'], {})
            self.assertEqual(reset['shared_settings']['message_overlay_opacity'], 0.42)
            self.assertEqual(SettingsStore(root).snapshot()['project_id'], '')
            self.assertTrue(all(page['prompt']['text'] is None for page in reset['pages']))
            self.assertTrue(all(not page['message'] or page['message']['html'] == '' for page in reset['pages']))
            release_project_session(root)
            reopened = get_project_session(root)
            self.assertEqual(reopened.project.to_dict(), reset)
            release_project_session(root)

    def test_unrelated_page_edits_do_not_invalidate_result(self):
        target = self.session.capture_target(self.ids[1])
        self.session.project.update_prompt(self.ids[3], {'text': 'Another page'})
        self.assertTrue(self.session.target_is_current(target))

    def test_delete_restore_invalidates_result_and_selection_has_valid_fallback(self):
        session = self.session
        page_id = self.ids[1]
        session.select_page(page_id)
        target = session.capture_target(page_id)
        record = session.project.delete_page(page_id)
        self.assertEqual(session.selected_page_id, self.ids[0])
        session.project.restore_page(record)
        self.assertFalse(session.target_is_current(target))

    def test_a_b_a_replacement_rejects_original_a_work(self):
        session = self.session
        project_a = session.project
        target = session.capture_target(self.ids[1])
        session.replace(create_project())
        session.replace(project_a)
        self.assertFalse(session.target_is_current(target))

    def test_shared_change_invalidates_without_rewriting_authored_content(self):
        session = self.session
        page_id = self.ids[1]
        session.project.update_prompt(page_id, {'text': 'Exact\n  Ω  '})
        target = session.capture_target(page_id)
        session.project.update_shared_settings({'palette': ''})
        self.assertFalse(session.target_is_current(target))
        self.assertEqual(session.project.get_prompt(page_id)['text'], 'Exact\n  Ω  ')

    def test_snapshot_is_deeply_read_only(self):
        snapshot = self.session.snapshot()
        with self.assertRaises(TypeError):
            snapshot['pages'][0]['name'] = 'bad'
        self.assertEqual(self.session.project.get_page(self.ids[0])['name'], '')

    def test_restoration_emits_completed_state_once(self):
        events = []
        self.session.add_listener(events.append)
        with self.session.restoring():
            self.session.replace(create_project())
            self.session.select_page(None)
            self.assertFalse(events)
        self.assertEqual([event.kind for event in events], ['project_replaced'])

    def test_closed_session_cannot_apply_work_or_save(self):
        from project_session import get_project_session, release_project_session
        with tempfile.TemporaryDirectory() as directory:
            session = get_project_session(directory)
            target = session.capture_target(session.project.pages[0]['id'])
            release_project_session(directory)
            replacement = get_project_session(directory)
            self.assertFalse(session.target_is_current(target))
            self.assertFalse(replacement.target_is_current(target))
            with self.assertRaises(RuntimeError):
                session.save()
            release_project_session(directory)

    def test_replacing_content_keeps_expected_disk_revision(self):
        from project_session import get_project_session, release_project_session
        with tempfile.TemporaryDirectory() as directory:
            session = get_project_session(directory)
            revision = session.storage_revision
            session.replace(create_project(['illustration', 'end_page']))
            self.assertEqual(session.storage_revision, revision)
            saved = session.save(sync_workspace=False)
            self.assertEqual([page['type'] for page in saved.project.pages], ['illustration', 'end_page'])
            release_project_session(directory)

    def test_new_project_clears_canonical_content_and_retains_libraries(self):
        from command import reset_everything
        from project_session import get_project_session, release_project_session
        from project_state import ProjectStateController
        from settings_store import SettingsStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = ProjectStateController(root)
            state.initialize()
            session = get_project_session(root)
            page = next(page for page in session.project.pages if page['message'] is not None)
            session.project.update_message(page['id'], {**page['message'], 'html': 'Old text'})
            session.project.update_prompt(page['id'], {'text': 'Old prompt'})
            session.project.update_project_data('sound', {'playlist': ['Old track']})
            session.save(sync_workspace=False)
            target = session.capture_target(page['id'])
            palette = root / 'Prompter/modules/user_colors.json'
            palette.parent.mkdir(parents=True)
            palette.write_text('{"kept":"palette"}')
            saved = root / 'output/Play/kept.txt'
            saved.parent.mkdir(parents=True)
            saved.write_text('saved project')
            SettingsStore(root).update_fields({'ui_sound_enabled': False})
            reset_everything(project_root=root, project_state=state)
            self.assertFalse(session.target_is_current(target))
            self.assertEqual(session.project.to_dict()['sound'], {})
            self.assertTrue(all(page['prompt']['text'] is None for page in session.project.pages))
            self.assertTrue(all(not page['message'] or page['message']['html'] == '' for page in session.project.pages))
            self.assertEqual(palette.read_text(), '{"kept":"palette"}')
            self.assertEqual(saved.read_text(), 'saved project')
            self.assertFalse(SettingsStore(root).snapshot()['ui_sound_enabled'])
            session.save(sync_workspace=False)
            release_project_session(root)


class RuntimeProfileTests(unittest.TestCase):
    def test_profiles_isolate_every_writable_path_without_importing_private_data(self):
        from project_paths import ApplicationPaths
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            resources = root / 'resources'
            resources.mkdir()
            (resources / 'settings.json').write_text('{"recipient_name":"Private"}')
            (resources / 'prompt_writer_state.json').write_text('{"subject":"Private"}')
            controls = resources / 'gallery/app/controls'
            controls.mkdir(parents=True)
            (controls / 'control.txt').write_text('bundled control')
            profiles = []
            for name in ('images', 'messages'):
                profile = root / name
                paths = ApplicationPaths.for_runtime(resources, environ={
                    'LETTER_SMITH_PROFILE_ROOT': str(profile),
                    'LETTER_SMITH_TEMP_ROOT': str(root / 'shared-temp'),
                    'LETTER_SMITH_DOCUMENTS_ROOT': str(root / 'shared-documents'),
                })
                paths.initialize(resources)
                for field in ('workspace_root', 'settings_file', 'custom_palette_root', 'music_archive_root',
                              'logs_root', 'cache_root', 'recovery_root', 'saved_letters_root',
                              'publication_root', 'autosave_root', 'temporary_root'):
                    self.assertTrue(getattr(paths, field).is_relative_to(profile))
                self.assertEqual(paths.resource_root, resources)
                self.assertFalse(paths.settings_file.exists())
                self.assertFalse((paths.workspace_root / 'prompt_writer_state.json').exists())
                self.assertEqual((paths.workspace_root / 'gallery/user/card/controls/control.txt').read_text(), 'bundled control')
                profiles.append(paths)
            self.assertNotEqual(profiles[0].workspace_root, profiles[1].workspace_root)


if __name__ == '__main__':
    unittest.main()
