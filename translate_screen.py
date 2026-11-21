"""Main image-to-translation workspace."""
import base64
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep

from PIL import Image, ImageOps
import streamlit as st

import local_demo
from quality import LANGUAGES, Withheld, checked_translation, compact, uses_script
from samples import PRACTICE_TITLE
from sarvam_live import LiveError, PENDING

paste_image = st.components.v2.component(
    "clipboard_image",
    html='<div role="status" tabindex="0" style="font:16px system-ui;color:#45524c">Paste an image with ⌘V or Ctrl+V.</div>',
    js=Path(__file__).with_name("paste.js").read_text(),
)


def prepare(raw):
    if len(raw) > 30 * 1024 * 1024:
        raise ValueError("Choose an image smaller than 30 MB.")
    with Image.open(BytesIO(raw)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP", "BMP", "TIFF", "GIF"}:
            raise ValueError("Choose a supported image format.")
        if image.width * image.height > 40_000_000 or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Choose one image with at most 40 million pixels.")
        image.load()
        if image.format in {"PNG", "JPEG"} and image.getexif().get(274, 1) == 1:
            return raw
        out = BytesIO()
        ImageOps.exif_transpose(image).convert("RGB").save(out, format="PNG")
        return out.getvalue()


def read_blocks(live, leaf, raw):
    deadline = monotonic() + 75
    while True:
        try:
            return live.ocr(raw, leaf["filename"], LANGUAGES[leaf["language"]])
        except LiveError as error:
            if str(error) != PENDING or monotonic() >= deadline:
                raise
            sleep(3)


def check_unreviewed_reading(live, leaf, raw, rows, report):
    if leaf["kind"] != "Printed text":
        return
    if any(row["source_version"] != 1 or row["source"] != row["raw_ocr"] or row["normalized"]
           for row in rows):
        raise Withheld("An edited source needs approval in Review.")
    if not local_demo.available():
        if live:
            return
        raise Withheld("OCR is ready. Compare the source with the image in Review before translation.")
    report("Checking the printed text against the image…")
    reading = local_demo.read_image(raw, leaf["language"], printed=True, double_check=True)
    if (not uses_script(reading, leaf["language"]) or "[gap]" in reading
            or compact(reading) != compact(" ".join(row["source"] for row in rows))):
        raise Withheld(local_demo.READING_HELP)


def run_pipeline(lib, live, leaf, report):
    if leaf["flag"]:
        raise Withheld("A clearer image was requested. Resolve the image issue first.")
    language = leaf["language"]
    raw = prepare(leaf["original"])
    rows = lib.passages(leaf["id"])
    printed = leaf["kind"] == "Printed text"
    if not rows:
        report("Reading the image…")
        if live:
            blocks = read_blocks(live, leaf, raw)
        else:
            blocks = [{"text": local_demo.read_image(raw, language, printed=printed)}]
        lib.ingest(leaf["id"], blocks)
        rows = lib.passages(leaf["id"])
    pending = [row for row in rows if not all(row[k + "_visible"] for k in ("english", "hindi"))]
    if not pending:
        return
    source_issue = ""
    if any(not row["source_approved"] for row in pending):
        try:
            check_unreviewed_reading(live, leaf, raw, rows, report)
        except Withheld as error:
            source_issue = str(error)
    provider = live or local_demo
    withheld = False
    for row in pending:
        if source_issue and not row["source_approved"]:
            continue
        source = row["normalized"] or row["source"]
        for target, code in (("English", "en-IN"), ("Hindi", "hi-IN")):
            field = target.lower()
            if row[field + "_visible"]:
                continue
            report(f"Translating into {target}…")
            try:
                draft = checked_translation(provider, source, language, target, code, LANGUAGES[language])
            except Withheld:
                withheld = True
                continue
            lib.save_translation(row["id"], code, draft, source_version=row["source_version"], checked=True)
    if source_issue:
        raise Withheld(source_issue)
    if withheld:
        raise Withheld("A translation could not be completed.")


def translate_screen(lib, live, enabled):
    st.title("Translate an image")
    st.write("Paste a manuscript or document to read it in English and Hindi.")
    with st.container(border=True, key="import_surface"):
        input_lang = st.selectbox("Language in the image", list(LANGUAGES))
        kind = st.radio("Image type", ["Manuscript", "Printed text"], horizontal=True,
                        help="Manuscripts need a reviewed source reading before translation.")
        event = paste_image(key="clipboard", on_image_change=lambda: None)
        upload = st.file_uploader("Or drop a file here", type=["jpg","jpeg","png","webp","bmp","tif","tiff","gif"])
        candidate = None
        if event and event.image:
            try:
                candidate = (event.image["name"], base64.b64decode(event.image["data"], validate=True))
            except (KeyError, ValueError):
                st.error("Clipboard data could not be read.")
        elif upload and st.session_state.get("last_upload") != upload.file_id:
            candidate = (upload.name, upload.getvalue())
            st.session_state["last_upload"] = upload.file_id
        if candidate:
            name, original = candidate
            try:
                prepare(original)
                digest = (input_lang, kind, sha256(original).hexdigest())
                known = st.session_state.setdefault("import_ids", {})
                if digest not in known:
                    known[digest] = lib.add_leaf(Path(name).stem or "Pasted image", original,
                                                 name, language=input_lang, kind=kind)
                st.session_state["translation_leaf"] = known[digest]
                st.session_state["show_sample"] = False
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                st.session_state.pop("translation_leaf", None)
                st.error(f"Could not open this image. {error}")
    sample = next((row for row in lib.leaves() if row["title"] == PRACTICE_TITLE), None)
    if sample and st.button("Try the saved example"):
        st.session_state["translation_leaf"] = sample["id"]
        st.session_state["show_sample"] = True
    local_ready = not enabled and local_demo.available()
    if not enabled:
        st.caption("Local vision · Images stay on this Mac." if local_ready else
                   "Connect Sarvam to process new images. The saved example works offline.")
    leaf_id = st.session_state.get("translation_leaf")
    if not leaf_id:
        return
    leaf = lib.leaf(leaf_id)
    st.divider()
    left, right = st.columns([1,1.12], gap="large")
    with left:
        st.subheader(leaf["title"])
        st.caption(f"{leaf['kind']} · {leaf['language']}")
        st.image(prepare(leaf["original"]), width="stretch")
        if st.button("Open in Reader"):
            st.session_state.update(next_page="Reader", new_leaf=leaf_id)
            st.rerun()
    with right:
        st.subheader("Translations")
        sample_mode = st.session_state.get("show_sample", False)
        if sample_mode:
            st.caption("Saved example · Modern typeset Sanskrit")
        elif input_lang != leaf["language"] or kind != leaf["kind"]:
            st.info(f"This image is saved as {leaf['kind']} in {leaf['language']}. Paste the image again to change its settings.")
        elif leaf["flag"]:
            st.warning(f"A clearer image is needed. {leaf['flag']}")
        elif st.button("Translate into English and Hindi", type="primary", disabled=not (enabled or local_ready)):
            with st.status("Reading the image…", expanded=True) as progress:
                try:
                    run_pipeline(lib, live if enabled else None, leaf, lambda msg: progress.update(label=msg))
                    progress.update(label="Translation complete.", state="complete")
                except (LiveError, ValueError) as error:
                    progress.update(label="Processing stopped.", state="error")
                    st.warning(str(error))
        rows = lib.passages(leaf_id)
        if rows and not leaf["flag"]:
            for number, row in enumerate(rows, 1):
                if len(rows) > 1:
                    st.markdown(f"**Passage {number}**")
                if row["source"].strip():
                    st.caption(f"Recognized text · {leaf['language']}")
                    st.text(row["source"])
                for target in ("English", "Hindi"):
                    field = target.lower()
                    if row[field].strip():
                        st.caption(f"{target} translation")
                        st.text(row[field])
                    else:
                        st.caption(f"{target}: translation not available.")
