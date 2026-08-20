import tempfile
import unittest
from pathlib import Path

from language_service import (
    LANGUAGE_CUSTOM_DICTIONARY_KEY,
    LanguageService,
    clear_language_service_cache,
    get_language_service,
)
from project_state import ProjectStateController
from saved_letters import RESTORABLE_SETTING_KEYS
from settings_store import SettingsStore


class LanguageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_language_service_cache()
        self.holder = tempfile.TemporaryDirectory()
        self.project_root = Path(self.holder.name)
        self.service = LanguageService(self.project_root)

    def tearDown(self) -> None:
        clear_language_service_cache()
        self.holder.cleanup()

    def test_high_confidence_mechanical_corrections(self) -> None:
        self.assertEqual(
            self.service.correct_text(
                "A beutiful medival castel",
                context="prompt",
            ),
            "A beautiful medieval castle.",
        )
        self.assertEqual(
            self.service.correct_text(
                "a woman stands in the rain. she looks upward.",
                context="prose",
            ),
            "A woman stands in the rain. She looks upward.",
        )
        self.assertEqual(
            self.service.correct_text(
                "make all image dark and dramatc",
                context="prompt",
            ),
            "Make all images dark and dramatic.",
        )
        self.assertEqual(
            self.service.correct_text(
                "a women standing under blue treee",
                context="prompt",
            ),
            "A woman standing under a blue tree.",
        )
        self.assertEqual(
            self.service.correct_text(
                "make all the image dark and dramatc, the charcters should "
                "have blue eye and there clothing should be medival",
                context="prompt",
            ),
            "Make all the images dark and dramatic. The characters should "
            "have blue eyes and their clothing should be medieval.",
        )

    def test_high_confidence_corrections_run_until_stable(self) -> None:
        corrected = self.service.correct_text(
            "a women hold a seperate sword",
            context="prompt",
        )
        self.assertEqual(corrected, "A woman holds a separate sword.")
        self.assertEqual(
            self.service.correct_text(corrected, context="prompt"),
            corrected,
        )

    def test_valid_american_and_british_variants_are_preserved(self) -> None:
        for text in (
            "A colorful theater scene uses gray armor at the center.",
            "A colourful theatre scene uses grey armour at the centre.",
            "We organize the favorite colors.",
            "We organise the favourite colours.",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    self.service.correct_text(text, context="prose"),
                    text,
                )
                self.assertFalse(
                    any(
                        issue.category == "spelling"
                        for issue in self.service.get_issues(text, context="prose")
                    )
                )

    def test_spacing_lists_and_prompt_structure_are_preserved(self) -> None:
        self.assertEqual(
            self.service.correct_text(
                "blue eyes,dark hair,and white robes",
                context="prompt",
            ),
            "blue eyes, dark hair, and white robes",
        )
        fragment = "portrait, blue lighting, close-up, NO TEXT / 16:9 #00D0FF UE5 RGB"
        corrected = self.service.correct_text(fragment, context="prompt")
        for protected in ("close-up", "NO TEXT", "16:9", "#00D0FF", "UE5", "RGB"):
            self.assertIn(protected, corrected)
        self.assertNotIn(".", corrected)
        self.assertEqual(
            self.service.correct_text("Note:this matters", context="prose"),
            "Note: this matters.",
        )
        self.assertEqual(
            self.service.correct_text("word.. next", context="prose"),
            "Word. Next",
        )
        self.assertEqual(
            self.service.correct_text("wait... then continue", context="prose"),
            "Wait... Then continue.",
        )

    def test_punctuation_cleanup_preserves_slash_prompt_fragments(self) -> None:
        self.assertEqual(
            self.service.correct_text(
                "portrait,blue lighting/close-up;NO TEXT",
                context="prompt",
            ),
            "portrait, blue lighting/close-up; NO TEXT",
        )
        self.assertEqual(
            self.service.correct_text(
                "portrait/blue lighting/close-up",
                context="prompt",
            ),
            "portrait/blue lighting/close-up",
        )
        self.assertEqual(
            self.service.correct_text(
                "woman face, close-up, blue lighting",
                context="prompt",
            ),
            "woman face, close-up, blue lighting",
        )
        self.assertEqual(
            self.service.correct_text(
                "use red/blue lighting",
                context="prompt",
            ),
            "Use red/blue lighting.",
        )

    def test_conjugation_is_correct_and_conservative(self) -> None:
        expected = {
            "The character carry a sword.": "The character carries a sword.",
            "She face the camera.": "She faces the camera.",
            "The women hold swords.": "The women hold swords.",
            "They carry swords.": "They carry swords.",
            "The character should carry a sword.": "The character should carry a sword.",
        }
        for source, corrected in expected.items():
            with self.subTest(source=source):
                self.assertEqual(
                    self.service.correct_text(source, context="prose"),
                    corrected,
                )

    def test_custom_dictionary_is_shared_persistent_and_project_safe(self) -> None:
        self.assertFalse(self.service.is_known_word("Karaethon"))
        self.assertTrue(self.service.add_to_dictionary("Karaethon"))
        self.assertTrue(self.service.is_known_word("Karaethon"))
        self.assertFalse(
            any(
                issue.text.casefold() == "karaethon"
                for issue in self.service.get_issues("karaethon", context="prose")
            )
        )

        reloaded = LanguageService(self.project_root)
        self.assertTrue(reloaded.is_known_word("Karaethon"))
        stored = SettingsStore(self.project_root).get(
            LANGUAGE_CUSTOM_DICTIONARY_KEY,
            [],
        )
        self.assertEqual(stored, ["Karaethon"])

        controller = ProjectStateController(self.project_root)
        controller.initialize()
        controller.begin_new_project()
        self.assertTrue(LanguageService(self.project_root).is_known_word("Karaethon"))
        self.assertNotIn(LANGUAGE_CUSTOM_DICTIONARY_KEY, RESTORABLE_SETTING_KEYS)

        self.assertTrue(self.service.add_to_dictionary("medival"))
        self.assertEqual(
            self.service.correct_text("medival castle portrait", context="prose"),
            "Medival castle portrait.",
        )
        self.assertTrue(self.service.ignore_word("beutiful"))
        self.assertEqual(
            self.service.correct_text("beutiful castle", context="prose"),
            "Beutiful castle",
        )

    def test_service_instance_and_analysis_are_authoritative(self) -> None:
        first = get_language_service(self.project_root)
        second = get_language_service(self.project_root)
        self.assertIs(first, second)
        text = "a beautful portrait"
        self.assertEqual(first.get_issues(text), second.check_text(text))
        self.assertEqual(first.correct_text(text), "A beautiful portrait.")

    def test_protected_values_and_quoted_text_are_not_changed(self) -> None:
        text = 'Use #00D0FF at 16:9 in UE5 RGB and keep "beutiful medival" exact.'
        corrected = self.service.correct_text(text, context="prompt")
        self.assertIn("#00D0FF", corrected)
        self.assertIn("16:9", corrected)
        self.assertIn("UE5", corrected)
        self.assertIn("RGB", corrected)
        self.assertIn('"beutiful medival"', corrected)

    def test_valid_phrases_and_identifiers_are_not_overcorrected(self) -> None:
        unchanged = (
            "The same women return every year.",
            "All image files are valid.",
            "She is walking in rain boots.",
            "Figures stand under blue wall panels.",
            "There clothing is sold.",
            "A ewe stands here.",
            "A utopia appears.",
            "An herb grows here.",
            "She had had enough.",
            "I am very very happy.",
            "Use cover.png and example.com as references.",
        )
        for text in unchanged:
            with self.subTest(text=text):
                self.assertEqual(
                    self.service.correct_text(text, context="prose"),
                    text,
                )
        self.assertEqual(
            self.service.correct_text(
                "The character turn sheet uses cover.png as reference",
                context="prose",
            ),
            "The character turn sheet uses cover.png as reference.",
        )
        self.assertEqual(
            self.service.correct_text("A apple grows here.", context="prose"),
            "An apple grows here.",
        )


if __name__ == "__main__":
    unittest.main()
