"""Main image-to-translation workspace."""
import base64
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
import textwrap

from PIL import Image, ImageOps
import streamlit as st

import local_demo
from samples import PRACTICE_TITLE
from sarvam_live import LiveError, PENDING

paste_image = st.components.v2.component(
    "clipboard_image",
    html='<div role="status" tabindex="0" style="font:16px system-ui;color:#45524c">Paste an image with ⌘V or Ctrl+V.</div>',
    js=Path(__file__).with_name("paste.js").read_text(),
)


LANGUAGES = {"Sanskrit": "sa-IN", "Hindi": "hi-IN", "English": "en-IN", "Tamil": "ta-IN",
             "Telugu": "te-IN", "Kannada": "kn-IN", "Malayalam": "ml-IN", "Bengali": "bn-IN",
             "Marathi": "mr-IN", "Gujarati": "gu-IN", "Punjabi": "pa-IN"}


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


def run_pipeline(lib, live, leaf, report):
    raw = prepare(leaf["original"])
    report("Reading the image with Sarvam Vision…")
    source_code = LANGUAGES[leaf["language"]]
    deadline = monotonic() + 75
    while True:
        try:
            blocks = live.ocr(raw, leaf["filename"], source_code)
            break
        except LiveError as error:
            if str(error) != PENDING or monotonic() >= deadline:
                raise
            sleep(3)
    lib.ingest(leaf["id"], blocks)
    for row in lib.passages(leaf["id"]):
        source = row["normalized"] or row["source"]
        if not source.strip():
            continue
        for name, code in (("English", "en-IN"), ("Hindi", "hi-IN")):
            if row[name.lower()].strip():
                continue
            report(f"Translating into {name}…")
            chunks = textwrap.wrap(source, width=1990, replace_whitespace=False)
            draft = source if code == source_code else " ".join(
                live.translate(chunk, code, source_code) for chunk in chunks
            )
            lib.save_translation(row["id"], code, draft, source_version=row["source_version"])


def translate_screen(lib, live, enabled):
    st.title("Translate an image")
    st.write("Paste a manuscript or document to read it in English and Hindi.")
    with st.container(border=True, key="import_surface"):
        input_lang = st.selectbox("Language in the image", list(LANGUAGES))
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
                digest = (input_lang, sha256(original).hexdigest())
                known = st.session_state.setdefault("import_ids", {})
                if digest not in known:
                    known[digest] = lib.add_leaf(Path(name).stem or "Pasted image", original,
                                                 name, language=input_lang)
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
        st.image(prepare(leaf["original"]), width="stretch")
        if leaf["language"] == "Sanskrit" and st.button("Review passages"):
            st.session_state.update(next_page="Review", new_leaf=leaf_id)
            st.rerun()
    with right:
        st.subheader("Translation drafts")
        sample_mode = st.session_state.get("show_sample", False)
        if sample_mode:
            st.caption("Saved example · Modern typeset Sanskrit")
        elif input_lang != leaf["language"] and lib.passages(leaf_id):
            st.info(f"This image was processed as {leaf['language']}. To use another language, paste the image again.")
        elif st.button("Translate into English and Hindi", type="primary", disabled=not (enabled or local_ready)):
            with st.status("Reading the image…", expanded=True) as progress:
                try:
                    if leaf["language"] != input_lang:
                        lib.update_leaf(leaf_id, leaf["title"], leaf["catalogue"], input_lang, leaf["script"], leaf["source_url"])
                        leaf = lib.leaf(leaf_id)
                    if enabled:
                        run_pipeline(lib, live, leaf, lambda message: progress.update(label=message))
                    elif not lib.passages(leaf_id):
                        progress.update(label="Reading and translating on this Mac…")
                        lib.save_local_draft(leaf_id, *local_demo.read_image(prepare(leaf["original"]), input_lang))
                    elif not all(row["english"] and row["hindi"] for row in lib.passages(leaf_id)):
                        raise ValueError("This image already has passages under review. Resume in Review.")
                    progress.update(label="Drafts ready for review.", state="complete")
                except (LiveError, ValueError) as error:
                    progress.update(label="Processing stopped.", state="error")
                    st.warning(str(error))
        rows = lib.passages(leaf_id)
        if rows:
            if any(row.get("import_key") == "local-vision" for row in rows):
                st.caption("Machine draft · Qwen2.5-VL on this Mac")
            for name in ("English", "Hindi"):
                st.markdown(f"**{name}**")
                st.text("\n\n".join(row[name.lower()] or "Awaiting translation." for row in rows))
            with st.expander(f"Recognized text · {leaf['language']}"):
                st.text("\n".join(row["source"] for row in rows))
