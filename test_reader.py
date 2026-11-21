"""Verify the Streamlit reading and translation path without the review requirement."""

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

    def test_reader_displays_and_exports_translation(self):
        self.choose_page("Reader")
        leaf = next(row for row in self.library.leaves() if row["title"] == PRACTICE_TITLE)
        leaf_id = leaf["id"]
        self.assertEqual(self.app.title[0].value, PRACTICE_TITLE)
        self.assertTrue(any(button.key == f"export_{leaf_id}" for button in self.app.get("download_button")))
        html = self.library.export_html(leaf_id)
        self.assertIn("The river is calm at dawn.", html)

    def test_supplied_photo_remains_a_quality_example(self):
        self.choose_page("Reader")
        photo = next(row for row in self.library.leaves() if row["title"] == PHOTO_TITLE)
        self.app.radio(key="leaf").set_value(photo["id"]).run()
        self.assert_healthy()
        self.assertEqual(self.app.title[0].value, PHOTO_TITLE)
        self.assertTrue(any("Clearer image requested" in alert.value for alert in self.app.warning))

    def test_manuscript_ocr_translates_directly_without_prior_approval(self):
        from translate_screen import run_pipeline
        original = (ROOT / "assets" / "river-at-dawn.png").read_bytes()
        lid = self.library.add_leaf("New image", original, "source.png", language="Sanskrit")
        provider = Mock()
        provider.ocr.return_value = [{"text": "बालकः पुस्तकं पठति।", "bbox": None}]
        provider.translate.side_effect = ["The boy reads a book.", "लड़का एक पुस्तक पढ़ता है।"]
        provider.supports_translation.return_value = True

        # Pipeline translates directly without requiring a manual review step
        run_pipeline(self.library, provider, self.library.leaf(lid), lambda _: None)
        self.assertEqual(2, provider.translate.call_count)
        row, = self.library.passages(lid)
        self.assertEqual("The boy reads a book.", row["english"])
        self.assertTrue(row["english_visible"] and row["hindi_visible"])

    def test_import_photo_on_translate_screen(self):
        original = (ROOT / "assets" / "manuscript-bundle.jpeg").read_bytes()
        self.app.file_uploader[0].upload("bundle-test.jpeg", original, "image/jpeg")
        self.app.run()
        self.assert_healthy()
        uploaded = self.library.leaves()[0]
        self.assertEqual(uploaded["title"], "bundle-test")
        self.assertEqual(original, self.library.leaf(uploaded["id"])["original"])


if __name__ == "__main__":
    unittest.main()
