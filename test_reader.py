"""Verify the local Streamlit review path without calling Sarvam."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from library import Library
from samples import PHOTO_TITLE, PRACTICE_TITLE


ROOT = Path(__file__).resolve().parent


class ReaderTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "test.sqlite3"
        settings = patch.dict(os.environ, {
            "PALMLEAF_DB": str(self.db_path),
            "SARVAM_API_KEY": "",
            "SARVAM_FREE_CREDITS_ONLY": "",
            "SARVAM_AUTO_RECHARGE_OFF": "",
        })
        settings.start()
        self.addCleanup(settings.stop)
        self.app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15)
        self.app.run()
        self.assert_healthy()
        self.library = Library(self.db_path)
        self.addCleanup(self.library.close)

    def assert_healthy(self):
        self.assertFalse(self.app.exception, [item.message for item in self.app.exception])

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def choose_page(self, page):
        self.app.radio(key="page").set_value(page).run()
        self.assert_healthy()

    def test_review_and_export_then_edit_sanskrit(self):
        self.choose_page("Review")
        leaf = next(row for row in self.library.leaves() if row["title"] == PRACTICE_TITLE)
        leaf_id = leaf["id"]
        self.assertEqual(self.app.title[0].value, PRACTICE_TITLE)
        self.assertTrue(self.button("Run or check OCR").disabled)
        self.app.text_input(key="reviewer").set_value("Local test reviewer").run()
        for label in ("Approve Sanskrit", "Approve English", "Approve Hindi"):
            self.button(label).click().run()
            self.assert_healthy()

        self.choose_page("Reader")
        self.assertTrue(any(button.key == f"export_{leaf_id}" for button in self.app.get("download_button")))
        self.assertIn("The river is calm at dawn.", self.library.export_html(leaf_id))

        self.choose_page("Review")
        transcription = next(field for field in self.app.text_area if field.label == "Transcription")
        transcription.set_value("प्रभाते नदी शान्ता अस्ति।")
        self.button("Save Sanskrit").click().run()
        self.assert_healthy()
        passage = self.library.passages(leaf_id)[0]
        self.assertFalse(passage["source_approved"])
        self.assertFalse(passage["english_approved"])
        self.assertFalse(passage["hindi_approved"])
        self.choose_page("Reader")
        self.assertFalse(any(button.key == f"export_{leaf_id}" for button in self.app.get("download_button")))
        with self.assertRaises(ValueError):
            self.library.export_html(leaf_id)

    def test_supplied_photo_remains_a_quality_example(self):
        self.choose_page("Review")
        photo = next(row for row in self.library.leaves() if row["title"] == PHOTO_TITLE)
        self.app.radio(key="leaf").set_value(photo["id"]).run()
        self.assert_healthy()
        self.assertEqual(self.app.title[0].value, PHOTO_TITLE)
        self.assertTrue(any("Clearer image requested" in alert.value for alert in self.app.warning))
        self.assertTrue(self.button("Run or check OCR").disabled)
        self.assertEqual([], self.library.passages(photo["id"]))

    def test_manuscript_ocr_needs_approval_before_either_translation(self):
        from translate_screen import run_pipeline
        original = (ROOT / "assets" / "river-at-dawn.png").read_bytes()
        lid = self.library.add_leaf("New image", original, "source.png", language="Sanskrit")
        provider = Mock()
        provider.ocr.return_value = [{"text": "बालकः पुस्तकं पठति।", "bbox": None}]
        provider.translate.side_effect = ["The boy reads a book.", "लड़का एक पुस्तक पढ़ता है।"]
        with self.assertRaisesRegex(ValueError, "Check the manuscript"):
            run_pipeline(self.library, provider, self.library.leaf(lid), lambda _: None)
        provider.translate.assert_not_called()
        row, = self.library.passages(lid)
        self.assertFalse(row["english_visible"] or row["hindi_visible"])
        self.library.approve_source(row["id"], "Reviewer")
        provider.supports_translation.return_value = True
        run_pipeline(self.library, provider, self.library.leaf(lid), lambda _: None)
        run_pipeline(self.library, provider, self.library.leaf(lid), lambda _: None)
        self.assertEqual(2, provider.translate.call_count)
        row, = self.library.passages(lid)
        self.assertEqual("The boy reads a book.", row["english"])
        self.assertTrue(row["english_visible"] and row["hindi_visible"])
        self.assertFalse(row["english_approved"])

    def test_old_unverified_translations_hidden_in_all_workspaces(self):
        original = (ROOT / "assets" / "manuscript-bundle.jpeg").read_bytes()
        self.app.file_uploader[0].upload("old-leaf.jpeg", original, "image/jpeg")
        self.app.run()
        lid = self.library.leaves()[0]["id"]
        pid = self.library.add_passage(lid, "श्री")
        guess = "The sage was born in a royal dynasty."
        with self.library.db:
            self.library.db.execute("UPDATE passages SET english=?, english_origin='legacy' WHERE id=?", (guess, pid))
        self.app.run()
        self.assertTrue(any("English withheld" in elt.value for elt in self.app.caption))
        self.assertNotIn(guess, [elt.value for elt in self.app.text])
        self.button("Review passages").click().run()
        self.assert_healthy()
        self.assertNotIn(guess, [elt.value for elt in self.app.text_area])
        self.choose_page("Reader")
        self.assertFalse(any(guess in elt.value for elt in self.app.markdown))
        with self.assertRaises(ValueError):
            self.library.export_html(lid)

    def test_import_and_flag_original_photo(self):
        original = (ROOT / "assets" / "manuscript-bundle.jpeg").read_bytes()
        self.app.file_uploader[0].upload("bundle-test.jpeg", original, "image/jpeg")
        self.app.run()
        self.button("Review passages").click().run()
        self.assert_healthy()
        uploaded = self.library.leaves()[0]
        self.assertEqual(uploaded["title"], self.app.title[0].value)
        self.assertEqual(original, self.library.leaf(uploaded["id"])["original"])
        issue = next(field for field in self.app.text_input if field.label == "Image issue")
        issue.set_value("Several leaves overlap.")
        self.button("Request clearer image").click().run()
        self.assert_healthy()
        self.assertEqual("Several leaves overlap.", self.library.leaf(uploaded["id"])["flag"])


if __name__ == "__main__":
    unittest.main()
