import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from library import Library
from quality import Withheld, checked_translation, supported
from translate_screen import run_pipeline


SOURCE = "प्रभाते नदी शान्ता भवति।"
FABRICATION = ("Here is the translation of the given Tamil text to English: "
               "Souvevamrishi was born in the Sakajeva dynasty.")


class QualityTest(unittest.TestCase):
    def setUp(self):
        self.lib = Library(":memory:")
        self.addCleanup(self.lib.close)
        image = (Path(__file__).parent / "assets" / "river-at-dawn.png").read_bytes()
        self.lid = self.lib.add_leaf("Print", image, "print.png", language="Sanskrit", kind="Printed text")
        self.provider = Mock()
        self.provider.ocr.return_value = [{"text": SOURCE}]
        self.provider.supports_translation.return_value = True

    def run_print(self, reading):
        with patch("local_demo.available", return_value=True), patch("local_demo.read_image", return_value=reading):
            run_pipeline(self.lib, self.provider, self.lib.leaf(self.lid), lambda _: None)

    def test_disagreeing_ocr_and_wrong_script_stop_before_translation(self):
        for wrong in ("गजः", "தமிழ்", "[gap]"):
            with self.subTest(wrong=wrong), self.assertRaises(Withheld):
                self.run_print(wrong)
        self.provider.translate.assert_not_called()

    def test_bad_english_cannot_hide_valid_hindi(self):
        self.provider.translate.side_effect = [FABRICATION, "भोर में नदी शांत होती है।"]
        with self.assertRaises(Withheld):
            self.run_print(SOURCE)
        row, = self.lib.passages(self.lid)
        self.assertEqual(row["english"], "")
        self.assertTrue(row["hindi_visible"])
        self.assertFalse(row["hindi_approved"])
        self.assertEqual(self.provider.supports_translation.call_count, 1)
        self.lib.flag_leaf(self.lid, "Poor image")
        self.lib.flag_leaf(self.lid, "")
        self.assertFalse(self.lib.passages(self.lid)[0]["hindi_visible"])

    def test_uncertain_fluent_invention_withheld(self):
        self.provider.translate.return_value = "The sage was born in a royal family."
        self.provider.supports_translation.return_value = False
        with self.assertRaises(Withheld):
            checked_translation(self.provider, SOURCE, "Sanskrit", "English", "en-IN", "sa-IN")
        for value in ('{}', 'null', 'true', '{"supported":1}', '{"supported":"true"}', 'maybe'):
            self.assertFalse(supported(value))
        self.assertTrue(supported(json.dumps({"supported": True})))

    def test_legacy_drafts_and_stale_answers_are_hidden(self):
        pid = self.lib.add_passage(self.lid, SOURCE)
        self.lib.db.execute("UPDATE passages SET english=?, english_origin='legacy' WHERE id=?",
                            ("A plausible invention.", pid))
        self.lib.db.commit()
        row, = self.lib.passages(self.lid)
        self.assertFalse(row["english_visible"])
        self.assertEqual(row["english"], "")
        self.lib.save_translation(pid, "en", "The river is calm at dawn.", source_version=1, checked=True)
        self.lib.save_source(pid, "अहम्", None)
        with self.assertRaisesRegex(ValueError, "reading changed"):
            self.lib.save_translation(pid, "en", "A stale result.", source_version=1, checked=True)
        self.assertFalse(self.lib.passages(self.lid)[0]["english_visible"])
