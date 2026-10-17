import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from PromptWriterPanel import (
    HIDDEN_FRAMING_DEFAULT,
    HIDDEN_STYLE_DEFAULT,
    ListManagerDialog,
    ManagedListEntry,
    PROMPT_COLORS,
    PromptWriterPanel,
    USER_ADDED_HEADER,
    reset_prompt_writer_state_file,
)
from saved_letters import (
    _ACTIVE_LETTER_LOAD_WORKSPACES,
    cleanup_stale_letter_load_workspaces,
)
from settings_store import SettingsStore, VISIONARY_URL_KEY
from transactional_io import safe_write_json, set_path_hidden
from ui_theme import ThemeService


class _FakeLanguageService:
    def __init__(self, corrections=None, *, error=None):
        self.corrections = dict(corrections or {})
        self.error = error
        self.calls = []

    def correct_text(self, text, *, context="prompt", protected_terms=()):
        self.calls.append((text, context, tuple(protected_terms)))
        if self.error is not None:
            raise self.error
        return self.corrections.get(text, text)


class PromptWriterHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        modules = self.project_root / "Prompter" / "modules"
        modules.mkdir(parents=True)
        (modules / "topic.txt").write_text("Subject\n", encoding="utf-8")
        (modules / "type.txt").write_text("Illustration\n", encoding="utf-8")
        (modules / "role.txt").write_text("Artist\n", encoding="utf-8")
        (modules / "order.txt").write_text("composition, lighting, mood\n", encoding="utf-8")
        (modules / "effort.txt").write_text("maximum fidelity\n", encoding="utf-8")
        (modules / "format.txt").write_text("Portrait image.\n", encoding="utf-8")
        (modules / "color.txt").write_text(
            "-Basic\nRuby\n\n-User Added\nLegacy User Color\n", encoding="utf-8"
        )
        self.panel = None

    def tearDown(self):
        self.doCleanups()
        if self.panel is not None:
            self.panel.shutdown()
            self.panel.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        from project_session import release_project_session
        release_project_session(self.project_root)
        self.temp_dir.cleanup()

    def _saved_project(self):
        from project_repository import active_project_repository
        return active_project_repository(self.project_root).load().project

    @staticmethod
    def _preview_colors(widget):
        return ' '.join(selection.format.foreground().color().name() for selection in widget.extraSelections())

    def test_hidden_defaults_are_generation_only_and_preview_matches(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.assertFalse(hasattr(self.panel, "cb_neutral_style"))
        self.assertFalse(hasattr(self.panel, "cb_unspecified_framing"))
        self.panel.cmb_subject.setCurrentText("Subject")
        self.panel._on_generate()

        self.assertTrue(self.panel._generated_output_valid)
        for page in self.panel._page_specs:
            plain = self.panel._generated_prompts[page.key]
            preview = page.preview_widget.toPlainText()
            self.assertIn(HIDDEN_STYLE_DEFAULT, plain)
            self.assertIn(HIDDEN_FRAMING_DEFAULT, plain)
            self.assertIn(HIDDEN_STYLE_DEFAULT, preview)
            self.assertIn(HIDDEN_FRAMING_DEFAULT, preview)

        self.panel.cb_real.setChecked(True)
        self.panel.cb_close_up_focus.setChecked(True)
        self.assertNotIn(HIDDEN_STYLE_DEFAULT, self.panel._collect_guidance())
        self.assertNotIn(HIDDEN_FRAMING_DEFAULT, self.panel._collect_guidance())

    def test_action_buttons_follow_their_own_prerequisites_and_busy_state(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.panel.cmb_subject.setCurrentIndex(-1)

        self.assertFalse(self.panel.btn_generate.isEnabled())
        self.assertFalse(self.panel.btn_copy.isEnabled())
        self.assertFalse(self.panel.btn_erase.isEnabled())

        self.panel.cmb_subject.setCurrentText("Subject")
        self.assertTrue(self.panel.btn_generate.isEnabled())
        self.assertTrue(self.panel.btn_erase.isEnabled())
        self.assertFalse(self.panel.btn_copy.isEnabled())

        self.panel._set_generation_busy(True)
        for button in (
            self.panel.btn_generate,
            self.panel.btn_copy,
            self.panel.btn_erase,
        ):
            self.assertFalse(button.isEnabled())
            effect = button.graphicsEffect()
            self.assertIsInstance(effect, QtWidgets.QGraphicsOpacityEffect)
            self.assertAlmostEqual(effect.opacity(), 0.52)

        self.panel._set_generation_busy(False)
        self.assertTrue(self.panel.btn_generate.isEnabled())
        self.assertTrue(self.panel.btn_erase.isEnabled())
        self.panel._on_generate()
        self.assertTrue(self.panel.btn_copy.isEnabled())
        self.assertTrue(
            all(
                page.copy_button is not None and page.copy_button.isEnabled()
                for page in self.panel._page_specs
            )
        )

        self.panel.cmb_subject.setCurrentIndex(-1)
        self.assertFalse(self.panel.btn_generate.isEnabled())
        self.assertTrue(self.panel.btn_copy.isEnabled())

    def test_titlebar_uses_minimize_artwork_as_its_only_close_control(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        title_bar = self.panel.title_bar

        self.assertTrue(title_bar.btn_minimize.isHidden())
        self.assertTrue(title_bar.btn_maximize.isHidden())
        self.assertFalse(title_bar.btn_close.isHidden())
        self.assertEqual(title_bar.btn_close.objectName(), "windowControlButton")
        self.assertEqual(title_bar.btn_close.property("fallbackSymbol"), "\u2212")
        self.assertEqual(title_bar._close_icon_asset, "titlebar/minimize.png")

        dismissed = QtTest.QSignalSpy(self.panel.dismissed)
        title_bar.btn_close.click()
        self.assertEqual(dismissed.count(), 1)

    def test_input_change_signals_dirty_before_debounced_persist(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        changed = QtTest.QSignalSpy(self.panel.project_changed)

        self.panel.txt_global.setPlainText("Unsaved prompt direction")

        self.assertEqual(changed.count(), 1)
        self.assertTrue(self.panel._persist_timer.isActive())

    def test_focus_request_preserves_current_prompt_writer_contents(self):
        service = _FakeLanguageService()
        self.panel = PromptWriterPanel(
            project_root=str(self.project_root), language_service=service,
        )
        self.panel.popup()
        self.panel.cmb_subject.setCurrentText("Subject")
        self.panel.txt_global.setPlainText("Keep this draft")
        self.panel._on_generate()
        QtTest.QTest.qWait(300)
        prompts = dict(self.panel._generated_prompts)
        self.assertTrue(self.panel._generated_output_valid)
        service.corrections["Keep this draft"] = "Changed draft."
        service.calls.clear()

        self.panel.popup()

        self.assertTrue(self.panel.is_open)
        self.assertEqual(self.panel.txt_global.toPlainText(), "Keep this draft")
        self.assertEqual(self.panel._generated_prompts, prompts)
        self.assertTrue(self.panel._generated_output_valid)
        self.assertEqual(service.calls, [])
        self.assertEqual(self.panel._fade_anim.state(), QtCore.QAbstractAnimation.Stopped)

    def test_prompt_writer_reopen_cancels_the_closing_animation(self):
        self.panel = PromptWriterPanel(
            project_root=str(self.project_root), language_service=_FakeLanguageService(),
        )
        self.panel.popup()
        QtTest.QTest.qWait(300)
        dismissed_states = []
        self.panel.dismissed.connect(lambda: dismissed_states.append(self.panel.is_open))
        self.panel.close_with_anim()
        self.assertFalse(self.panel.is_open)
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(dismissed_states, [False])

        self.panel.popup()
        QtTest.QTest.qWait(300)

        self.assertTrue(self.panel.is_open)
        self.assertFalse(self.panel._hide_timer.isActive())
        self.assertAlmostEqual(self.panel.windowOpacity(), 1.0, places=2)
        self.panel.close_with_anim()
        QtTest.QTest.qWait(200)
        self.assertLess(self.panel.windowOpacity(), 0.1)
        QtTest.QTest.qWait(80)
        self.assertFalse(self.panel.isVisible())

    def test_prompt_writer_native_close_keeps_the_cached_panel_reusable(self):
        self.panel = PromptWriterPanel(
            project_root=str(self.project_root), language_service=_FakeLanguageService(),
        )
        self.panel.popup()
        self.panel.txt_global.setPlainText("Keep this draft")

        self.panel.close()

        self.assertFalse(self.panel.is_open)
        self.assertFalse(self.panel.isVisible())
        self.assertFalse(self.panel._shutdown)
        self.assertFalse(self.panel._hide_timer.isActive())
        self.assertFalse(self.panel._visionary_timer.isActive())
        saved = self._saved_project().to_dict()["prompt_settings"]
        self.assertEqual(saved["global"], "Keep this draft")
        self.panel.popup()
        QtTest.QTest.qWait(300)
        self.assertTrue(self.panel.is_open)
        self.assertEqual(self.panel.txt_global.toPlainText(), "Keep this draft")

    def test_outside_click_and_deactivation_leave_prompt_writer_open(self):
        owner = QtWidgets.QWidget()
        self.addCleanup(owner.deleteLater)
        button = QtWidgets.QPushButton("Main application control", owner)
        clicked = QtTest.QSignalSpy(button.clicked)
        self.panel = PromptWriterPanel(
            owner, project_root=str(self.project_root), language_service=_FakeLanguageService(),
        )
        owner.show()
        self.panel.popup()
        self.panel.txt_global.setPlainText("Keep this draft")

        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        self.app.sendEvent(self.panel, QtCore.QEvent(QtCore.QEvent.WindowDeactivate))
        self.app.sendEvent(self.app, QtCore.QEvent(QtCore.QEvent.ApplicationDeactivate))
        QtTest.QTest.qWait(300)

        self.assertEqual(clicked.count(), 1)
        self.assertEqual(self.panel.windowModality(), QtCore.Qt.NonModal)
        self.assertIsNone(self.app.activeModalWidget())
        self.assertTrue(self.panel.is_open)
        self.assertEqual(self.panel.txt_global.toPlainText(), "Keep this draft")
        owner.close()

    def test_malformed_state_is_preserved_for_foundation_recovery(self):
        from project_repository import ProjectRepositoryError
        state_path = self.project_root / "prompt_writer_state.json"
        state_path.write_bytes(b"{not-json")
        with self.assertRaises(ProjectRepositoryError):
            PromptWriterPanel(project_root=str(self.project_root))
        self.assertEqual(state_path.read_bytes(), b"{not-json")

    def test_future_state_schema_is_not_overwritten(self):
        from project_model import UnsupportedProjectError
        state_path = self.project_root / "prompt_writer_state.json"
        original = json.dumps({"version": 99, "subject": "Future subject"}).encode()
        state_path.write_bytes(original)
        with self.assertRaises(UnsupportedProjectError):
            PromptWriterPanel(project_root=str(self.project_root))
        self.assertEqual(state_path.read_bytes(), original)

    def test_explicit_proofreading_updates_only_requested_direction(self):
        service = _FakeLanguageService({'shared dramatc': 'Shared dramatic.', 'page treee': 'Page tree.'})
        self.panel = PromptWriterPanel(project_root=str(self.project_root), language_service=service)
        self.panel.cmb_subject.setCurrentText('Subject')
        self.panel.txt_global.setPlainText('shared dramatc')
        self.panel._view_spec.detail_widget.setPlainText('page treee')
        self.panel.cb_paint.setChecked(True)
        self.panel.btn_proofread_page.click()
        self.assertEqual(self.panel.txt_global.toPlainText(), 'shared dramatc')
        self.assertEqual(self.panel._view_spec.detail_widget.toPlainText(), 'Page tree.')
        self.panel.btn_proofread_shared.click()
        self.panel._on_generate()
        self.assertTrue(self.panel._generated_output_valid)
        self.assertTrue(self.panel.cb_paint.isChecked())
        saved = self._saved_project()
        page_id = self.panel._view_page_id
        self.assertEqual(saved.to_dict()['prompt_settings']['global'], 'Shared dramatic.')
        self.assertEqual(saved.get_prompt(page_id)['directions'], 'Page tree.')
        self.assertIn('Page tree.', saved.get_prompt(page_id)['text'])
        self.assertEqual(len(service.calls), 2)

    def test_failed_explicit_proofreading_preserves_authored_text(self):
        service = _FakeLanguageService(error=RuntimeError('checker unavailable'))
        self.panel = PromptWriterPanel(project_root=str(self.project_root), language_service=service)
        self.panel.cmb_subject.setCurrentText('Subject')
        self.panel._view_spec.detail_widget.setPlainText('  page treee\n')
        with self.assertLogs('PromptWriterPanel', level='ERROR'):
            self.panel.btn_proofread_page.click()
        self.assertEqual(self.panel._view_spec.detail_widget.toPlainText(), '  page treee\n')
        self.panel._on_generate()
        self.assertTrue(self.panel._generated_output_valid)
        self.assertEqual(self._saved_project().get_prompt(self.panel._view_page_id)['directions'], '  page treee\n')
        self.assertEqual(len(service.calls), 1)

    def test_open_close_and_build_never_automatically_proofread(self):
        service = _FakeLanguageService({'draft': 'Unrequested correction.'})
        self.panel = PromptWriterPanel(project_root=str(self.project_root), language_service=service)
        self.panel.cmb_subject.setCurrentText('Subject')
        self.panel.txt_global.setPlainText('draft')
        self.panel._view_spec.detail_widget.setPlainText('draft')
        self.panel.popup()
        self.panel._on_generate()
        self.panel.close()
        self.panel.popup()
        self.assertEqual(service.calls, [])
        self.assertEqual(self.panel.txt_global.toPlainText(), 'draft')
        self.assertEqual(self._saved_project().get_prompt(self.panel._view_page_id)['directions'], 'draft')

    def test_source_labels_and_generated_segments_share_unique_colors(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.panel.ensurePolished()

        source_labels = {
            self.panel.lbl_type: PROMPT_COLORS["type"],
            self.panel.lbl_subject: PROMPT_COLORS["subject"],
            self.panel.lbl_color: PROMPT_COLORS["scheme"],
            self.panel.lbl_helpful: PROMPT_COLORS["helpful"],
            self.panel.lbl_global: PROMPT_COLORS["global"],
        }
        for page in self.panel._page_specs:
            source_labels[page.detail_label] = page.preview_color

        self.assertEqual(len(set(source_labels.values())), len(source_labels))
        for label, expected in source_labels.items():
            label.ensurePolished()
            self.assertEqual(
                label.palette().color(QtGui.QPalette.WindowText).name().casefold(),
                expected.casefold(),
            )

        self.panel.cmb_type.setCurrentText("Illustration")
        self.panel.cmb_subject.addItem("Excitement")
        self.panel.cmb_subject.setCurrentText("Excitement")
        self.panel.cmb_color.setCurrentText("Ruby")
        self.panel.txt_global.setPlainText("Shared violet sky")
        self.panel.cb_black.setChecked(True)
        for page in self.panel._page_specs:
            page.detail_widget.setPlainText(f"Exact {page.key} detail")

        self.panel._on_generate()

        for page in self.panel._page_specs:
            html = self._preview_colors(page.preview_widget)
            for expected in (
                PROMPT_COLORS["type"],
                PROMPT_COLORS["subject"],
                PROMPT_COLORS["scheme"],
                PROMPT_COLORS["helpful"],
                PROMPT_COLORS["global"],
                page.preview_color,
            ):
                self.assertIn(expected.casefold(), html)

        before = dict(self.panel._generated_prompts)
        original_colors = dict(PROMPT_COLORS)
        self.addCleanup(PROMPT_COLORS.update, original_colors)
        shared_consumers = {
            "type": self.panel.lbl_type,
            "subject": self.panel.lbl_subject,
            "scheme": self.panel.lbl_color,
            "helpful": self.panel.lbl_helpful,
            "global": self.panel.lbl_global,
        }
        replacement_colors = {
            "type": "#7A5CFF",
            "subject": "#FF315A",
            "scheme": "#20B86B",
            "helpful": "#20AFCF",
            "global": "#F4F5F6",
            "cover": "#F2BF20",
            "letter": "#3574E8",
            "wall": "#EC7025",
            "back": "#DA3EB5",
        }

        for semantic_key, label in shared_consumers.items():
            replacement = replacement_colors[semantic_key]
            self.panel.set_semantic_color(semantic_key, replacement)
            self.assertEqual(
                label.palette().color(QtGui.QPalette.WindowText).name(),
                replacement.casefold(),
            )
            for page in self.panel._page_specs:
                self.assertIn(
                    replacement.casefold(),
                    self._preview_colors(page.preview_widget),
                )

        for page in self.panel._page_specs:
            replacement = replacement_colors[page.color_key]
            self.panel.set_semantic_color(page.color_key, replacement)
            for label in (page.detail_label, page.preview_title):
                self.assertEqual(
                    label.palette().color(QtGui.QPalette.WindowText).name(),
                    replacement.casefold(),
                )
            self.assertIn(replacement.casefold(), page.preview_card.styleSheet().casefold())
            self.assertIn(replacement.casefold(), self._preview_colors(page.preview_widget))

        self.assertEqual(self.panel._generated_prompts, before)

    def test_semantic_palette_matches_requested_high_contrast_colors(self):
        self.assertEqual(
            PROMPT_COLORS,
            {
                "type": "#B084FF",
                "subject": "#FF5C7A",
                "scheme": "#35D07F",
                "helpful": "#32C7E8",
                "global": "#E5E7EB",
                "cover": "#FFD54A",
                "letter": "#4D8DFF",
                "wall": "#FF8A3D",
                "back": "#F25ACD",
            },
        )
        self.assertEqual(len(PROMPT_COLORS), len(set(PROMPT_COLORS.values())))

    def test_neutral_prompt_preview_text_follows_light_and_dark_polarity(self):
        service = ThemeService(self.project_root)
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.panel.cmb_subject.setCurrentText("Subject")
        self.panel.txt_global.setPlainText("Shared direction")

        service.set_theme("light", persist=False)
        self.panel.apply_theme_assets(service)
        self.panel._on_generate()
        light_text = service.tokens.text.casefold()
        self.assertEqual(
            self.panel.lbl_global.palette()
            .color(QtGui.QPalette.WindowText)
            .name()
            .casefold(),
            light_text,
        )
        for page in self.panel._page_specs:
            self.assertIn(light_text, self._preview_colors(page.preview_widget))

        service.set_theme("dark", persist=False)
        self.panel.apply_theme_assets(service)
        self.panel.lbl_global.ensurePolished()
        self.app.processEvents()
        dark_text = self.panel._display_prompt_color("global").casefold()
        self.assertGreater(QtGui.QColor(dark_text).lightness(), 160)
        self.assertEqual(
            self.panel.lbl_global.palette()
            .color(QtGui.QPalette.WindowText)
            .name()
            .casefold(),
            dark_text,
        )
        for page in self.panel._page_specs:
            self.assertIn(dark_text, self._preview_colors(page.preview_widget))

    def test_reset_clears_prompt_writer_but_preserves_user_options(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.panel.cmb_subject.setCurrentText("Subject")
        self.panel.txt_global.setPlainText("Prompt content")
        self.panel.cb_paint.setChecked(True)
        self.panel._on_generate()
        before = [entry.text for entry in self.panel._read_managed_entries("color") if entry.is_user]

        self.assertTrue(self.panel.reset_prompt_writer_state())
        self.assertFalse(self.panel.cb_paint.isChecked())
        self.assertFalse(self.panel.txt_global.toPlainText())
        self.assertFalse(self.panel._generated_prompts)
        self.assertFalse(self.panel._generated_output_valid)
        after = [entry.text for entry in self.panel._read_managed_entries("color") if entry.is_user]
        self.assertEqual(before, after)
        self.assertNotIn(
            "Legacy User Color",
            (self.project_root / "Prompter" / "modules" / "color.txt").read_text(encoding="utf-8"),
        )
        saved = self._saved_project().to_dict()["prompt_settings"]
        self.assertFalse(saved["checks"].get("real"))
        self.assertNotIn("neutral_style", saved["checks"])
        self.assertNotIn("unspecified_framing", saved["checks"])

    def test_old_checkbox_state_is_normalized_without_recreating_controls(self):
        (self.project_root / "prompt_writer_state.json").write_text(
            json.dumps({"checks": {"neutral_style": True, "unspecified_framing": True}}),
            encoding="utf-8",
        )
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        for name in (
            "cb_real", "cb_paint", "cb_minimal",
            "cb_close_up_focus", "cb_full_body_view", "cb_wide_scene",
        ):
            self.assertFalse(getattr(self.panel, name).isChecked())

    def test_safe_write_preserves_previous_file_on_validation_failure(self):
        target = self.project_root / "state.json"
        target.write_text('{"valid": true}\n', encoding="utf-8")

        def reject(_value):
            raise ValueError("bad")

        with self.assertRaises(ValueError):
            safe_write_json(target, {"valid": False}, validator=reject)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"valid": True})

    def test_load_workspace_cleanup_targets_exact_stale_prefix(self):
        output = self.project_root / "output"
        output.mkdir()
        stale = output / ".letter-load-stale"
        stale.mkdir()
        (stale / "asset.txt").write_text("temporary", encoding="utf-8")
        unrelated = output / ".other-temp"
        unrelated.mkdir()

        removed = cleanup_stale_letter_load_workspaces(self.project_root)

        self.assertIn(stale.resolve(), removed)
        self.assertFalse(stale.exists())
        self.assertTrue(unrelated.exists())

    def test_active_load_workspace_is_not_removed(self):
        output = self.project_root / "output"
        output.mkdir()
        active = (output / ".letter-load-active").resolve()
        active.mkdir()
        _ACTIVE_LETTER_LOAD_WORKSPACES.add(active)
        try:
            removed = cleanup_stale_letter_load_workspaces(self.project_root)
            self.assertNotIn(active, removed)
            self.assertTrue(active.exists())
        finally:
            _ACTIVE_LETTER_LOAD_WORKSPACES.discard(active)

    def test_copy_failure_does_not_report_success(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.panel._generated_prompts = {
            page.key: "generated prompt"
            for page in self.panel._page_specs
        }
        self.panel._generated_input_signature = self.panel._current_prompt_input_signature()
        self.panel._set_generated_output_valid(True)
        clipboard = mock.Mock()
        clipboard.setText.side_effect = RuntimeError("clipboard unavailable")
        with (
            mock.patch.object(QtWidgets.QApplication, "clipboard", return_value=clipboard),
            mock.patch("PromptWriterPanel.show_lettersmith_message"),
        ):
            self.assertEqual(self.panel._copy_all_prompts_text(), "")

    def test_reset_state_file_does_not_construct_panel(self):
        self.assertTrue(reset_prompt_writer_state_file(self.project_root))
        state = json.loads((self.project_root / "prompt_writer_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["generated_prompts"], {})
        self.assertNotIn("reference_images", state)

    def test_reference_image_workflow_is_removed_and_legacy_state_is_ignored(self):
        (self.project_root / "prompt_writer_state.json").write_text(
            json.dumps(
                {
                    "subject": "Subject",
                    "reference_images": [
                        {
                            "path": "legacy.png",
                            "filename": "legacy.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.panel = PromptWriterPanel(project_root=str(self.project_root))

        self.assertFalse(hasattr(self.panel, "btn_add_reference"))
        self.assertFalse(hasattr(self.panel, "reference_strip"))
        self.assertNotIn("reference_images", self.panel._capture_state())

    def test_visionary_uses_configured_location(self):
        url = "https://example.com/custom-visionary"
        SettingsStore(self.project_root).update_fields(**{VISIONARY_URL_KEY: url})
        self.panel = PromptWriterPanel(project_root=str(self.project_root))

        with mock.patch(
            "PromptWriterPanel.open_external_url",
            return_value=True,
        ) as open_url:
            self.panel.btn_visionary.click()

        self.assertEqual(open_url.call_args.args[0].toString(), url)

    def test_visionary_uses_macos_launchservices_fallback(self):
        url = "https://example.com/custom-visionary"
        SettingsStore(self.project_root).update_fields(**{VISIONARY_URL_KEY: url})
        self.panel = PromptWriterPanel(project_root=str(self.project_root))

        with (
            mock.patch("external_urls.sys.platform", "darwin"),
            mock.patch(
                "external_urls.QtGui.QDesktopServices.openUrl",
                return_value=False,
            ),
            mock.patch(
                "external_urls.QtCore.QProcess.startDetached",
                return_value=(True, 123),
            ) as start_detached,
        ):
            self.panel.btn_visionary.click()

        start_detached.assert_called_once_with("/usr/bin/open", [url])

    def test_invalid_user_color_storage_does_not_fallback_to_legacy_file(self):
        modules = self.project_root / "Prompter" / "modules"
        (modules / "user_colors.json").write_text("not json", encoding="utf-8")
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.assertEqual(
            [entry.text for entry in self.panel._read_managed_entries("color") if entry.is_user],
            [],
        )

    def test_duplicate_notice_is_click_through_and_normalizes_color_names(self):
        dialog = ListManagerDialog(
            title="Color Scheme",
            entries=[
                ManagedListEntry("Two-Color Combinations", True),
                ManagedListEntry("Blue and Lavender Purple"),
                ManagedListEntry(USER_ADDED_HEADER, True),
            ],
            allow_headers=True,
            auto_user_header=USER_ADDED_HEADER,
            user_owned_only=True,
        )
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()

        changes = []
        dialog.entries_changed.connect(changes.append)
        self.assertFalse(dialog.entry_edit.isReadOnly())
        self.assertFalse(dialog.btn_update.isEnabled())
        self.assertFalse(dialog.btn_remove.isEnabled())
        dialog.entry_edit.selectAll()
        QtTest.QTest.keyClicks(dialog.entry_edit, "  blue and lavender purple  ")
        self.assertEqual(dialog.entry_edit.text(), "  blue and lavender purple  ")
        QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)
        self.app.processEvents()

        self.assertTrue(dialog._duplicate_notice.isVisible())
        self.assertTrue(
            dialog._duplicate_notice.windowFlags() & QtCore.Qt.WindowTransparentForInput
        )
        self.assertTrue(
            dialog._duplicate_notice.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        )
        self.assertEqual(changes, [])
        self.assertEqual(
            [entry.text for entry in dialog._entries].count("Blue and Lavender Purple"),
            1,
        )

        dialog.entry_edit.selectAll()
        QtTest.QTest.keyClicks(dialog.entry_edit, "New Cyan Scheme")
        dialog._update_entry()
        dialog._remove_entry()
        self.assertEqual(dialog._entries[1].text, "Blue and Lavender Purple")
        QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)
        self.app.processEvents()

        self.assertFalse(dialog._duplicate_notice.isVisible())
        self.assertIn("New Cyan Scheme", [entry.text for entry in dialog._entries])
        self.assertEqual(dialog._selected_entry().text, "New Cyan Scheme")
        self.assertEqual(dialog.entry_edit.text(), "New Cyan Scheme")
        self.assertEqual(len(changes), 1)

    def test_added_color_is_selected_without_rebuilding_or_losing_pending_text(self):
        self.panel = PromptWriterPanel(
            project_root=str(self.project_root), language_service=_FakeLanguageService()
        )
        self.panel.popup()
        QtTest.QTest.qWait(300)
        self.panel.cmb_subject.setCurrentText("Subject")
        self.panel.cmb_color.setCurrentText("Ruby")
        self.panel.txt_global.setPlainText("  Shared café direction\n\n  ")
        self.panel.cb_paint.setChecked(True)
        for page in self.panel._page_specs:
            page.detail_widget.setPlainText(f"  {page.key} direction 雪\n\n")
        self.panel.session.project.update_project_data('prompt_settings', {'resolved_components': {'role': 'Chosen once'}})
        for page in self.panel._page_specs:
            self.panel.session.project.update_prompt(page.key, {'text': f"  Manual {page.key} café\r\n\n雪  ", 'stale': False})
        before = self.panel._capture_state()
        library_path = self.panel._resolve_managed_list_path("color")
        builtin_bytes = library_path.read_bytes()
        dismissed = QtTest.QSignalSpy(self.panel.dismissed)

        with (
            mock.patch.object(self.panel, "_on_generate", side_effect=AssertionError("rebuilt")),
            mock.patch.object(self.panel, "_roll_shared_prompt_data", side_effect=AssertionError("rerolled")),
        ):
            self.panel._open_list_manager("color")
            dialog = self.panel._list_manager_dialogs["color"]
            self.addCleanup(dialog.close)
            self.app.processEvents()
            dialog.entry_edit.selectAll()
            QtTest.QTest.keyClicks(dialog.entry_edit, "New Cyan Scheme")
            QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)
            self.app.processEvents()

            self.assertEqual(self.panel.cmb_color.currentText(), "New Cyan Scheme")
            self.assertEqual(dialog._selected_entry().text, "New Cyan Scheme")
            self.assertEqual(dialog.entry_edit.text(), "New Cyan Scheme")
            self.assertTrue(dialog.isVisible())
            self.assertTrue(self.panel.is_open)
            self.assertEqual(dismissed.count(), 0)
            self.assertFalse(self.panel._generated_output_valid)
            after = self.panel._capture_state()
            for key in (
                "subject", "type", "global", "checks", "resolved_instructions",
                "generated_prompts", *(page.key for page in self.panel._page_specs),
            ):
                self.assertEqual(after[key], before[key], key)
            self.assertEqual(library_path.read_bytes(), builtin_bytes)
            colors = json.loads(self.panel._user_colors_path().read_text(encoding="utf-8"))["colors"]
            self.assertIn("New Cyan Scheme", colors)
            self.assertIn("Legacy User Color", colors)
            self.assertTrue(self.panel.persist_project_state())
            self.assertTrue(self.panel.reload_project_state())
            self.assertEqual(self.panel.cmb_color.currentText(), "New Cyan Scheme")
            self.assertEqual(self.panel._generated_prompts, before["generated_prompts"])
            self.assertEqual(self.panel.txt_global.toPlainText(), before["global"])
            for page in self.panel._page_specs:
                self.assertEqual(page.detail_widget.toPlainText(), before[page.key])

    def test_failed_color_add_retains_draft_and_does_not_activate_unsaved_value(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.panel.cmb_color.setCurrentText("Ruby")
        self.panel._open_list_manager("color")
        dialog = self.panel._list_manager_dialogs["color"]
        self.addCleanup(dialog.close)
        self.app.processEvents()
        original_entries = list(dialog._entries)
        original_row = dialog._selected_row()
        library_bytes = self.panel._user_colors_path().read_bytes()
        changes = QtTest.QSignalSpy(dialog.entries_changed)
        draft = "  New Copper Scheme  "
        dialog.entry_edit.selectAll()
        QtTest.QTest.keyClicks(dialog.entry_edit, draft)

        with (
            mock.patch("PromptWriterPanel.safe_write_json", side_effect=OSError("test write failure")),
            mock.patch("PromptWriterPanel.show_lettersmith_message") as show_message,
        ):
            QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)

        self.assertEqual(show_message.call_args.args[1], "Save failed")
        self.assertEqual(changes.count(), 0)
        self.assertEqual(self.panel.cmb_color.currentText(), "Ruby")
        self.assertEqual(dialog._entries, original_entries)
        self.assertEqual(dialog._selected_row(), original_row)
        self.assertEqual(dialog.entry_edit.text(), draft)
        self.assertEqual(self.panel._user_colors_path().read_bytes(), library_bytes)
        self.assertTrue(dialog.isVisible())

        QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)
        self.assertEqual(changes.count(), 1)
        self.assertEqual(self.panel.cmb_color.currentText(), "New Copper Scheme")
        self.assertEqual(dialog._selected_entry().text, "New Copper Scheme")

    def test_color_reload_has_one_user_header_and_deduplicates_builtin_conflicts(self):
        modules = self.project_root / "Prompter" / "modules"
        (modules / "color.txt").write_text(
            "-Two-Color Combinations\nBlue and Lavender Purple\n\n"
            "-User Added\n  blue and lavender purple  \nCustom Cyan\n",
            encoding="utf-8",
        )
        self.panel = PromptWriterPanel(project_root=str(self.project_root))

        for _ in range(3):
            self.panel._reload_managed_list("color")
            entries = self.panel._read_managed_entries("color")
            self.assertEqual(
                [entry.text for entry in entries if entry.is_header].count(USER_ADDED_HEADER),
                1,
            )
            self.assertEqual(
                [entry.text.casefold() for entry in entries if not entry.is_header].count(
                    "blue and lavender purple"
                ),
                1,
            )
            self.assertIn("Custom Cyan", [entry.text for entry in entries if entry.is_user])

        self.assertEqual(
            [self.panel.cmb_color.itemText(index) for index in range(self.panel.cmb_color.count())].count(
                USER_ADDED_HEADER
            ),
            1,
        )

    def test_color_state_restore_reuses_normalized_builtin_entry(self):
        modules = self.project_root / "Prompter" / "modules"
        (modules / "color.txt").write_text(
            "-Two-Color Combinations\nBlue and Lavender Purple\n",
            encoding="utf-8",
        )
        (self.project_root / "prompt_writer_state.json").write_text(
            json.dumps({"color": "  blue and lavender purple  "}),
            encoding="utf-8",
        )

        self.panel = PromptWriterPanel(project_root=str(self.project_root))

        self.assertEqual(self.panel.cmb_color.currentText(), "Blue and Lavender Purple")
        self.assertEqual(
            [
                self.panel.cmb_color.itemText(index).casefold()
                for index in range(self.panel.cmb_color.count())
            ].count("blue and lavender purple"),
            1,
        )

    def test_bundled_color_schemes_include_each_intended_option_once(self):
        color_path = Path(__file__).resolve().parents[1] / "Prompter" / "modules" / "color.txt"
        choices = [
            line.strip()
            for line in color_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("-")
        ]
        for expected in (
            "Blue and Lavender Purple",
            "Turquoise Gold",
            "Deep Violet Kintsugi",
            "Turquoise Black",
            "Gold Kintsugi",
            "Natural Colors",
        ):
            self.assertEqual(choices.count(expected), 1)

    def test_helpful_guidance_stacks_groups_to_prevent_horizontal_overflow(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        layout = self.panel.gb_helpful.layout()
        positions = {}
        for object_name in (
            "helpful_decorative_frames",
            "helpful_style_bias",
            "helpful_image_safety",
            "helpful_camera_framing",
        ):
            group = self.panel.gb_helpful.findChild(QtWidgets.QGroupBox, object_name)
            self.assertIsNotNone(group)
            row, column, _row_span, _column_span = layout.getItemPosition(layout.indexOf(group))
            positions[object_name] = (row, column)

        self.assertEqual(
            [positions[name][0] for name in positions],
            [1, 2, 3, 4],
        )
        self.assertEqual({column for _row, column in positions.values()}, {0})

    def test_saved_manual_placeholder_text_is_preserved_without_validation(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        page_id = self.panel._view_page_id
        self.panel._view_spec.preview_widget.setPlainText('  {{unresolved}}\n')
        self.assertTrue(self.panel.persist_project_state())
        self.panel.reload_project_state()
        self.assertEqual(self.panel._view_spec.preview_widget.toPlainText(), '  {{unresolved}}\n')
        self.assertEqual(self._saved_project().get_prompt(page_id)['text'], '  {{unresolved}}\n')

    def test_stale_partial_prompt_snapshots_round_trip_without_regeneration(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        page_id = self.panel._view_page_id
        exact = "  Edited café \r\n\n prompt  "
        self.panel.session.project.update_project_data('prompt_settings', {'resolved_components': {'role': 'Chosen once'}})
        self.panel.session.project.update_prompt(page_id, {'text': exact})
        self.panel.txt_global.setPlainText('Changed shared direction')
        self.assertFalse(self.panel._generated_output_valid)
        self.assertTrue(self.panel.persist_project_state())
        with mock.patch.object(self.panel, '_roll_shared_prompt_data', side_effect=AssertionError('load rerolled')):
            self.assertTrue(self.panel.reload_project_state())
        self.assertEqual(self.panel._generated_prompts, {page_id: exact})
        self.assertEqual(self.panel._resolved_instructions, {'role': 'Chosen once'})
        self.assertFalse(self.panel._generated_output_valid)
        self.assertEqual(self._saved_project().get_prompt(page_id)['text'], exact)

    def test_complete_workspace_restores_historical_values_and_exact_prompts(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        for combo, value in (
            (self.panel.cmb_type, "Retired Etching"),
            (self.panel.cmb_subject, "GO and Genesis Prime"),
            (self.panel.cmb_color, "Archived Copper Palette"),
        ):
            combo.addItem(value)
            combo.setCurrentIndex(combo.count() - 1)
        self.panel.txt_global.setPlainText("Shared visual direction")
        for page in self.panel._page_specs:
            page.detail_widget.setPlainText(f"Exact {page.key} direction")
        self.panel.cb_black.setChecked(True)
        self.panel.cb_cinematic_framing.setChecked(True)
        resolved = {
            "role": "Historical master illustrator",
            "order": ["preserve the named subject", "then compose the scene"],
            "effort": "Historical exact effort instruction",
            "format": "Historical exact format instruction",
        }
        with mock.patch.object(
            self.panel,
            "_roll_shared_prompt_data",
            return_value=resolved,
        ):
            self.panel._on_generate()

        page_id = self.panel._view_page_id
        exact_cover = self.panel._generated_prompts[page_id] + "\r\nExact saved suffix."
        self.panel.session.project.update_prompt(page_id, {"text": exact_cover})
        expected_prompts = dict(self.panel._generated_prompts)
        expected_resolved = {
            "role": resolved["role"],
            "order": resolved["order"],
            "effort": resolved["effort"],
            "format": resolved["format"],
        }
        self.assertTrue(self.panel.persist_project_state())

        self.panel.deleteLater()
        self.app.processEvents()
        with mock.patch.object(
            PromptWriterPanel,
            "_roll_shared_prompt_data",
            side_effect=AssertionError("load must not generate prompts"),
        ):
            self.panel = PromptWriterPanel(project_root=str(self.project_root))

        self.assertEqual(self.panel.cmb_type.currentText(), "Retired Etching")
        self.assertEqual(self.panel.cmb_subject.currentText(), "GO and Genesis Prime")
        self.assertEqual(self.panel.cmb_color.currentText(), "Archived Copper Palette")
        self.assertEqual(self.panel.txt_global.toPlainText(), "Shared visual direction")
        self.assertTrue(self.panel.cb_black.isChecked())
        self.assertTrue(self.panel.cb_cinematic_framing.isChecked())
        self.assertEqual(self.panel._resolved_instructions, expected_resolved)
        self.assertEqual(self.panel._generated_prompts, expected_prompts)
        self.assertEqual(self.panel._generated_prompts[page_id], exact_cover)
        self.assertTrue(self.panel._generated_output_valid)
        cover_page = self.panel._view_spec
        self.assertEqual(
            cover_page.preview_widget.toPlainText(),
            exact_cover.replace("\r\n", "\n"),
        )
        restored_html = self._preview_colors(cover_page.preview_widget)
        for expected in (
            PROMPT_COLORS["type"],
            PROMPT_COLORS["subject"],
            PROMPT_COLORS["scheme"],
            PROMPT_COLORS["helpful"],
            PROMPT_COLORS["global"],
            PROMPT_COLORS["cover"],
        ):
            self.assertIn(expected.casefold(), restored_html)

    def test_overlong_module_entries_are_rejected(self):
        modules = self.project_root / "Prompter" / "modules"
        (modules / "topic.txt").write_text("a" * 301 + "\nValid Subject\n", encoding="utf-8")
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        entries = self.panel._read_managed_entries("subject")
        self.assertEqual([entry.text for entry in entries], ["Valid Subject"])


if __name__ == "__main__":
    unittest.main()
