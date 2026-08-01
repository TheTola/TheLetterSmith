import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PromptWriterLegacyRemovalTests(unittest.TestCase):
    def test_over_nexus_contains_only_message_tab_overlay_wiring(self):
        source = (ROOT / "Over_Nexus.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }

        self.assertEqual(
            definitions,
            {"OverNexusController", "install_over_nexus"},
        )
        self.assertNotIn("PromptWriterPanel", source)
        self.assertNotIn("subprocess", source)

    def test_nexus_uses_the_canonical_overlay_entry_point(self):
        source = (ROOT / "Nexus.py").read_text(encoding="utf-8")

        self.assertIn("from Over_Nexus import install_over_nexus", source)
        self.assertNotIn("OverConfig", source)
        self.assertNotIn("show_prompter_launcher", source)


if __name__ == "__main__":
    unittest.main()
