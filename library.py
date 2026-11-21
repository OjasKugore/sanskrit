"""Local manuscript images, source revisions, editorial decisions, and HTML editions."""

import base64
import hashlib
import io
import json
import math
import sqlite3
from contextlib import contextmanager
from html import escape
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from PIL import Image

from quality import LANGUAGES as _LANGUAGE_CODES, plausible


SCHEMA = """
CREATE TABLE IF NOT EXISTS leaves (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    original BLOB NOT NULL,
    filename TEXT NOT NULL,
    catalogue TEXT NOT NULL,
    language TEXT NOT NULL,
    script TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_url TEXT NOT NULL,
    flag TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY,
    leaf_id INTEGER NOT NULL REFERENCES leaves(id),
    position INTEGER NOT NULL,
    source TEXT NOT NULL,
    normalized TEXT NOT NULL,
    raw_ocr TEXT NOT NULL,
    bbox TEXT,
    note TEXT NOT NULL DEFAULT '',
    source_version INTEGER NOT NULL DEFAULT 1,
    source_reviewer TEXT NOT NULL DEFAULT '',
    english TEXT NOT NULL,
    english_reviewer TEXT NOT NULL DEFAULT '',
    hindi TEXT NOT NULL,
    hindi_reviewer TEXT NOT NULL DEFAULT '',
    import_key TEXT,
    UNIQUE(leaf_id, import_key)
);
CREATE INDEX IF NOT EXISTS passage_order ON passages(leaf_id, position);
"""
# Maps normalised lang tags to the internal field name used in the passages table.
_LANG_FIELD = {
    "en": "english", "en-in": "english", "english": "english",
    "hi": "hindi", "hi-in": "hindi", "hindi": "hindi",
}


def _language(lang):
    try:
        return _LANG_FIELD[lang.lower()]
    except (KeyError, AttributeError):
        raise ValueError("Choose English or Hindi.") from None


def _box(bbox):
    if bbox is None:
        return None
    try:
        left, top, right, bottom = (float(value) for value in bbox)
    except (ValueError, TypeError):
        raise ValueError("Use four coordinates for the image region.") from None
    coords = [left, top, right, bottom]
    if (not all(math.isfinite(value) and 0 <= value <= 1 for value in coords)
            or left >= right or top >= bottom):
        raise ValueError("Choose a region inside the image with a positive width and height.")
    return json.dumps(coords, separators=(",", ":"))


def _require_text(value, message):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    return value.strip()


def _record(row):
    record = dict(row)
    record["bbox"] = json.loads(record["bbox"]) if record["bbox"] else None
    for field in ("source", "english", "hindi"):
        record[field + "_approved"] = bool(record[field + "_reviewer"])
    for field in ("english", "hindi"):
        text = record[field]
        approved = record["source_approved"] and record[field + "_approved"]
        origin = record[field + "_origin"]
        usable = plausible(record["normalized"] or record["source"], text, field.title())
        record[field + "_approved"] = bool(approved and usable)
        record[field + "_visible"] = usable and (approved or origin in {"checked", "reference"})
        if not usable or (origin == "legacy" and not approved):
            record[field] = ""
    return record


class Library:
    def __init__(self, db_path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = RLock()
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

        with self._write():
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(passages)")}
            for field in ("english", "hindi"):
                if field + "_origin" not in columns:
                    self.db.execute(f"ALTER TABLE passages ADD COLUMN {field}_origin TEXT NOT NULL DEFAULT 'legacy'")

    def close(self):
        with self.lock:
            self.db.close()

    @contextmanager
    def _write(self):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            yield

    def add_leaf(self, title, original: bytes, filename, catalogue='', language='Unknown',
                 script='Unknown', kind='Manuscript', source_url=''):
        title = _require_text(title, "Enter a leaf title.")
        if not original:
            raise ValueError("Select a leaf image.")
        with self._write():
            cursor = self.db.execute(
                """INSERT INTO leaves(title,original,filename,catalogue,language,script,kind,source_url)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (title, original, filename, catalogue, language, script, kind, source_url))
            return cursor.lastrowid

    def leaves(self):
        with self.lock:
            rows = self.db.execute(
                """SELECT id,title,filename,catalogue,language,script,kind,source_url,flag
                   FROM leaves ORDER BY id DESC""")
            return [dict(row) for row in rows]

    def leaf(self, leaf_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM leaves WHERE id=?", (leaf_id,)).fetchone()
            if row is None:
                raise ValueError("Leaf not found.")
            return dict(row)

    def update_leaf(self, leaf_id, title, catalogue, language, script, source_url):
        title = _require_text(title, "Enter a leaf title.")
        with self._write():
            old = self.db.execute("SELECT language,script FROM leaves WHERE id=?", (leaf_id,)).fetchone()
            if old is None:
                raise ValueError("Leaf not found.")
            self.db.execute(
                "UPDATE leaves SET title=?,catalogue=?,language=?,script=?,source_url=? WHERE id=?",
                (title, catalogue, language, script, source_url, leaf_id))
            if old["language"] != language or old["script"] != script:
                self.db.execute(
                    """UPDATE passages SET source_version=source_version+1,source_reviewer='',
                       english_reviewer='',hindi_reviewer='',english_origin='legacy',hindi_origin='legacy' WHERE leaf_id=?""", (leaf_id,))

    def flag_leaf(self, leaf_id, note):
        with self._write():
            changed = self.db.execute("UPDATE leaves SET flag=? WHERE id=?", (note.strip(), leaf_id))
            if not changed.rowcount:
                raise ValueError("Leaf not found.")
            if note.strip():
                self.db.execute(
                    """UPDATE passages SET source_version=source_version+1,source_reviewer='',english_reviewer='',hindi_reviewer='',
                       english_origin='legacy', hindi_origin='legacy'
                       WHERE leaf_id=?""", (leaf_id,))

    def passages(self, leaf_id):
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM passages WHERE leaf_id=? ORDER BY position, id", (leaf_id,))
            return [_record(row) for row in rows]

    def _passage(self, passage_id):
        row = self.db.execute(
            """SELECT passages.*, leaves.flag FROM passages
               JOIN leaves ON leaves.id=passages.leaf_id WHERE passages.id=?""", (passage_id,)).fetchone()
        if row is None:
            raise ValueError("Passage not found.")
        return row

    def _insert(self, leaf_id, source, bbox, english, hindi, raw_ocr, normalized, import_key=None):
        return self.db.execute(
            """INSERT INTO passages(leaf_id,position,source,bbox,english,hindi,raw_ocr,normalized,import_key,english_origin,hindi_origin)
               VALUES (?,(SELECT COALESCE(MAX(position)+1,0) FROM passages WHERE leaf_id=?),?,?,?,?,?,?,?,'manual','manual')""",
            (leaf_id, leaf_id, source, _box(bbox), english, hindi, raw_ocr, normalized, import_key)).lastrowid

    def add_passage(self, leaf_id, source='', bbox=None, english='', hindi='', raw_ocr='', normalized=''):
        with self._write():
            return self._insert(leaf_id, source, bbox, english, hindi, raw_ocr, normalized)

    def mark_reference(self, leaf_id, source, english, hindi):
        with self._write():
            self.db.execute(
                """UPDATE passages SET english_origin='reference', hindi_origin='reference'
                   WHERE leaf_id=? AND source_version=1 AND source=? AND normalized=?
                   AND english=? AND hindi=?""", (leaf_id, source, source, english, hindi))

    def ingest(self, leaf_id, blocks):
        """Return the number of new OCR blocks. Replaying a result preserves human edits."""
        added = 0
        occurrences = {}
        with self._write():
            for block in blocks:
                raw = block.get("text") or block.get("raw_ocr") or block.get("source") or ""
                if not raw.strip():
                    continue
                bbox = block.get("bbox")
                digest = hashlib.sha256(json.dumps([raw, _box(bbox)]).encode()).hexdigest()
                index = occurrences.get(digest, 0)
                occurrences[digest] = index + 1
                key = f"{digest}:{index}"
                exists = self.db.execute(
                    "SELECT 1 FROM passages WHERE leaf_id=? AND import_key=?", (leaf_id, key)).fetchone()
                if exists is None:
                    self._insert(leaf_id, raw, bbox, "", "", raw, "", key)
                    added += 1
        return added

    def save_source(self, passage_id, source, bbox, normalized=''):
        box = _box(bbox)
        with self._write():
            old = self._passage(passage_id)
            if (old["source"], old["normalized"], old["bbox"]) == (source, normalized, box):
                return
            self.db.execute(
                """UPDATE passages SET source=?,normalized=?,bbox=?,source_version=source_version+1,
                   source_reviewer='',english_reviewer='',hindi_reviewer='',english_origin='legacy',hindi_origin='legacy' WHERE id=?""",
                (source, normalized, box, passage_id))

    def approve_source(self, passage_id, reviewer):
        reviewer = _require_text(reviewer, "Enter the reviewer's name.")
        with self._write():
            row = self._passage(passage_id)
            if row["flag"]:
                raise ValueError("Resolve the request for a clearer image first.")
            _require_text(row["source"], "Enter the source text before approval.")
            if row["normalized"] and row["source"].count("[gap]") != row["normalized"].count("[gap]"):
                raise ValueError("Keep each [gap] marker in the normalized Sanskrit.")
            self.db.execute("UPDATE passages SET source_reviewer=? WHERE id=?", (reviewer, passage_id))

    def save_translation(self, passage_id, lang, text, *, source_version=None, checked=False):
        field = _language(lang)
        with self._write():
            old = self._passage(passage_id)
            if source_version is not None and old["source_version"] != source_version:
                raise ValueError("The reading changed during translation. Review the current Sanskrit.")
            if checked and (old["flag"] or source_version is None or _record(old)[field + "_approved"]):
                raise ValueError("Review the current passage before replacing its translation.")
            origin = "checked" if checked else "manual"
            if old[field] != text or old[field + "_origin"] == "legacy" or (checked and old[field + "_origin"] != origin):
                self.db.execute(
                    f"UPDATE passages SET {field}=?,{field}_reviewer='',{field}_origin=? WHERE id=?", (text, origin, passage_id))

    def approve_translation(self, passage_id, lang, reviewer):
        field = _language(lang)
        reviewer = _require_text(reviewer, "Enter the reviewer's name.")
        with self._write():
            row = self._passage(passage_id)
            if row["flag"] or not row["source_reviewer"]:
                raise ValueError("Approve the source before approving a translation.")
            _require_text(row[field], "Enter the translation before approval.")
            if row[field].count("[gap]") != row["source"].count("[gap]"):
                raise ValueError("Keep each [gap] marker in the translation.")
            if not _record(row)[field]:
                raise ValueError("Enter a translation in the target language and check it against the source.")
            self.db.execute(f"UPDATE passages SET {field}_reviewer=? WHERE id=?", (reviewer, passage_id))

    def move_passage(self, passage_id, direction):
        if direction not in (-1, 1):
            raise ValueError("Choose one position up or down.")
        with self._write():
            row = self._passage(passage_id)
            neighbor = self.db.execute(
                "SELECT id, position FROM passages WHERE leaf_id=? AND position=?",
                (row["leaf_id"], row["position"] + direction)).fetchone()
            if neighbor:
                self.db.executemany("UPDATE passages SET position=? WHERE id=?",
                                    [(neighbor["position"], passage_id), (row["position"], neighbor["id"])])

    def save_note(self, passage_id, text):
        with self._write():
            self._passage(passage_id)
            self.db.execute("UPDATE passages SET note=? WHERE id=?", (text, passage_id))

    def export_html(self, leaf_id):
        # Read the source and all review decisions in one database snapshot.
        with self.lock, self.db:
            self.db.execute("BEGIN")
            leaf = self.leaf(leaf_id)
            approved = [row for row in self.passages(leaf_id)
                        if not leaf["flag"] and all(row[field + "_approved"]
                        for field in ("source", "english", "hindi"))]
        if not approved:
            raise ValueError("Approve a source passage and both translations before export.")
        return _edition(leaf, approved)


def _edition(leaf, passages):
    original = leaf["original"]
    with Image.open(io.BytesIO(original)) as image:
        width, height = image.size
        mime = Image.MIME.get(image.format)
        if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            converted = io.BytesIO()
            image.convert("RGB").save(converted, format="PNG")
            original, mime = converted.getvalue(), "image/png"
    encoded = base64.b64encode(original).decode("ascii")
    title = escape(leaf["title"])
    metadata = escape(" · ".join(filter(None, (leaf["catalogue"], leaf["language"], leaf["script"]))))
    link = leaf["source_url"]
    try:
        scheme = urlsplit(link).scheme
    except ValueError:
        scheme = ""
    if scheme in ("http", "https"):
        metadata += f' · <a href="{escape(link, quote=True)}">Collection record</a>'
    source_label = escape(leaf["language"])
    source_code = _LANGUAGE_CODES.get(leaf["language"], "und").split("-")[0]
    blocks = []
    for row in passages:
        pid = int(row["id"])
        left, top, right, bottom = row["bbox"] or (0, 0, 1, 1)
        view = f"{left*width:g} {top*height:g} {(right-left)*width:g} {(bottom-top)*height:g}"
        normalized = ""
        if row["normalized"] and row["normalized"] != row["source"]:
            normalized = f'<h3>Normalized {source_label}</h3><p lang="{source_code}">{escape(row["normalized"])}</p>'
        blocks.append(f'''<section id="passage-{pid}"><h2>Passage {len(blocks) + 1}</h2>
        <figure id="region-{pid}"><svg viewBox="{view}" role="img" aria-label="Source image region">
        <use href="#leaf-image"/></svg></figure>
        <h3>{source_label}</h3><p lang="{source_code}">{escape(row["source"])}</p>{normalized}
        <h3>English</h3><p lang="en">{escape(row["english"])}</p>
        <h3>Hindi</h3><p lang="hi">{escape(row["hindi"])}</p>
        <a href="#region-{pid}">View source region</a>
        <p class="credits">{source_label} review · {escape(row["source_reviewer"])}<br>
        English review · {escape(row["english_reviewer"])}<br>Hindi review · {escape(row["hindi_reviewer"])}</p>
        </section>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
    <style>body{{font:18px/1.5 Georgia,serif;color:#242017;max-width:54rem;margin:3rem auto;padding:0 1.25rem}}
    p{{white-space:pre-wrap;overflow-wrap:anywhere}}figure{{margin:1rem 0}}svg{{display:block;width:100%;height:auto}}
    section{{border-top:1px solid #a99c83;padding-top:1rem}}h3{{margin-bottom:.3rem}}h3+p{{margin-top:0}}
    .credits{{font-size:.82rem;color:#595246}}@media print{{body{{margin:0}}h2,h3,figure{{break-after:avoid}}}}</style>
    </head><body><h1>{title}</h1><p>{metadata}</p>
    <svg viewBox="0 0 {width} {height}" role="img" aria-label="Original leaf">
    <defs><image id="leaf-image" width="{width}" height="{height}" href="data:{mime};base64,{encoded}"/></defs>
    <use href="#leaf-image"/></svg>{''.join(blocks)}</body></html>'''
