import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from library import Library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name, "library.sqlite3")
        self.lib = Library(self.path)
        self.addCleanup(self.lib.close)
        image = io.BytesIO()
        Image.new("RGB", (40, 20), "tan").save(image, "PNG")
        self.leaf = self.lib.add_leaf("Leaf <one>", image.getvalue(), "test.png",
                                     language="Sanskrit", script="Grantha")
        self.pid = self.lib.add_passage(self.leaf, source="देवः", normalized="देवः",
                                        bbox=[0, .25, .5, 1], english="The god.", hindi="देवता।")

    def approve(self):
        self.lib.approve_source(self.pid, "Scholar")
        self.lib.approve_translation(self.pid, "en-IN", "English reader")
        self.lib.approve_translation(self.pid, "hi-IN", "Hindi reader")

    def passage(self):
        return next(row for row in self.lib.passages(self.leaf) if row["id"] == self.pid)

    def test_source_approval_and_target_revisions(self):
        with self.assertRaises(ValueError):
            self.lib.approve_translation(self.pid, "hi", "Reader")
        self.approve()
        self.lib.save_translation(self.pid, "en", "A god.")
        self.assertTrue(self.passage()["hindi_approved"])
        self.assertFalse(self.passage()["english_approved"])
        self.lib.approve_translation(self.pid, "english", "Editor")
        self.lib.save_source(self.pid, "देवाः", self.passage()["bbox"], "देवाः")
        row = self.passage()
        self.assertEqual(row["source_version"], 2)
        self.assertFalse(row["source_approved"] or row["english_approved"] or row["hindi_approved"])
        with self.assertRaises(ValueError):
            self.lib.export_html(self.leaf)

    def test_export_only_approved_and_embeds_image_once(self):
        self.approve()
        self.lib.add_passage(self.leaf, source="PRIVATE DRAFT")
        self.lib.save_note(self.pid, "PRIVATE NOTE")
        html = self.lib.export_html(self.leaf)
        for value in ("Leaf &lt;one&gt;", "The god.", "देवता।", 'href="#region-', "0 5 20 15"):
            self.assertIn(value, html)
        self.assertEqual(html.count("data:image/png;base64,"), 1)
        self.assertNotIn("PRIVATE", html)
        self.assertNotIn("Leaf <one>", html)

    def test_flag_and_classification(self):
        self.approve()
        self.lib.update_leaf(self.leaf, "New title", "Catalogue", "Sanskrit", "Grantha", "")
        self.assertTrue(self.passage()["source_approved"])
        self.lib.update_leaf(self.leaf, "New title", "Catalogue", "Sanskrit", "Ranjana", "")
        self.assertFalse(self.passage()["source_approved"])
        self.assertEqual(self.passage()["english"], "")
        self.lib.save_translation(self.pid, "en", "The god.")
        self.lib.save_translation(self.pid, "hi", "देवता।")
        self.approve()
        self.lib.flag_leaf(self.leaf, "Text behind cord")
        with self.assertRaises(ValueError):
            self.lib.approve_source(self.pid, "Editor")
        self.lib.flag_leaf(self.leaf, "")
        self.assertFalse(self.passage()["hindi_approved"])

    def test_empty_fields_and_missing_gap(self):
        self.lib.save_source(self.pid, "  ", None)
        with self.assertRaises(ValueError):
            self.lib.approve_source(self.pid, "Scholar")
        self.lib.save_source(self.pid, "अ [gap] क", None, "अ क")
        with self.assertRaises(ValueError):
            self.lib.approve_source(self.pid, "Scholar")
        self.lib.save_source(self.pid, "अ [gap] क", None)
        self.lib.approve_source(self.pid, "Scholar")
        for value in ("", "missing marker"):
            self.lib.save_translation(self.pid, "en", value)
            with self.assertRaises(ValueError):
                self.lib.approve_translation(self.pid, "en", "Editor")
        self.lib.save_translation(self.pid, "en", "a [gap] ka")
        self.lib.approve_translation(self.pid, "en", "Editor")
        with self.assertRaises(ValueError):
            self.lib.approve_translation(self.pid, "en", " ")

    def test_ingestion_replay_and_move_persist(self):
        result = [{"text": "Line", "bbox": [0, 0, 1, 1]}, {"text": "Line", "bbox": [0, 0, 1, 1]}]
        self.assertEqual(self.lib.ingest(self.leaf, result), 2)
        _, first, last = self.lib.passages(self.leaf)
        self.lib.save_source(first["id"], "Corrected", first["bbox"])
        self.lib.move_passage(first["id"], -1)
        self.lib.move_passage(first["id"], -1)
        self.lib.move_passage(last["id"], 1)
        self.assertEqual(self.lib.ingest(self.leaf, result), 0)
        other = Library(self.path)
        self.addCleanup(other.close)
        rows = other.passages(self.leaf)
        self.assertEqual([row["id"] for row in rows], [first["id"], self.pid, last["id"]])
        self.assertEqual(rows[0]["raw_ocr"], "Line")
        self.assertEqual(rows[0]["source"], "Corrected")
        self.assertNotIn("original", other.leaves()[0])
        self.assertTrue(other.leaf(self.leaf)["original"])

    def test_nonfinite_and_reversed_coordinates(self):
        for bbox in ([0, 0, float("nan"), 1], [1, 1, 0, 0], [0, 0, 1.1, 1]):
            with self.assertRaises(ValueError):
                self.lib.save_source(self.pid, "x", bbox)


if __name__ == "__main__":
    unittest.main()
