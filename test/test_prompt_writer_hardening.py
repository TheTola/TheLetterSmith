import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6 import QtWidgets

from PromptWriterPanel import (
    HIDDEN_FRAMING_DEFAULT,
    HIDDEN_STYLE_DEFAULT,
    PromptWriterPanel,
    reset_prompt_writer_state_file,
)
from saved_letters import (
    _ACTIVE_LETTER_LOAD_WORKSPACES,
    cleanup_stale_letter_load_workspaces,
)
from settings_store import SettingsStore, VISIONARY_URL_KEY
from transactional_io import safe_write_json


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
        if self.panel is not None:
            self.panel.deleteLater()
            self.app.processEvents()
        self.temp_dir.cleanup()

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
        saved = json.loads((self.project_root / "prompt_writer_state.json").read_text(encoding="utf-8"))
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
            mock.patch.object(QtWidgets.QMessageBox, "warning"),
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
            "PromptWriterPanel.QDesktopServices.openUrl",
            return_value=True,
        ) as open_url:
            self.panel.btn_visionary.click()

        self.assertEqual(open_url.call_args.args[0].toString(), url)

    def test_invalid_user_color_storage_does_not_fallback_to_legacy_file(self):
        modules = self.project_root / "Prompter" / "modules"
        (modules / "user_colors.json").write_text("not json", encoding="utf-8")
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.assertEqual(
            [entry.text for entry in self.panel._read_managed_entries("color") if entry.is_user],
            [],
        )

    def test_malformed_persisted_generated_output_is_invalidated(self):
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        self.panel.cmb_subject.setCurrentText("Subject")
        self.panel._on_generate()
        state_path = self.project_root / "prompt_writer_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["generated_prompts"]["cover"] = "{{unresolved}}"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.panel.deleteLater()
        self.app.processEvents()
        self.panel = PromptWriterPanel(project_root=str(self.project_root))

        self.assertFalse(self.panel._generated_output_valid)
        self.assertFalse(self.panel._generated_prompts)

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

        exact_cover = self.panel._generated_prompts["cover"] + "\r\nExact saved suffix."
        self.panel._generated_prompts["cover"] = exact_cover
        expected_prompts = dict(self.panel._generated_prompts)
        expected_resolved = {
            "role": resolved["role"],
            "subject_lead_in": resolved["order"],
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
        self.assertEqual(self.panel._generated_prompts["cover"], exact_cover)
        self.assertTrue(self.panel._generated_output_valid)

    def test_overlong_module_entries_are_rejected(self):
        modules = self.project_root / "Prompter" / "modules"
        (modules / "topic.txt").write_text("a" * 301 + "\nValid Subject\n", encoding="utf-8")
        self.panel = PromptWriterPanel(project_root=str(self.project_root))
        entries = self.panel._read_managed_entries("subject")
        self.assertEqual([entry.text for entry in entries], ["Valid Subject"])


if __name__ == "__main__":
    unittest.main()
