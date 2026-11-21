"""Local workspace for manuscript transcription, translation, and review."""

from html import escape
from io import BytesIO
from math import ceil
import os
from pathlib import Path

from PIL import Image, ImageDraw, UnidentifiedImageError
import streamlit as st

import local_demo
from quality import LANGUAGES, Withheld, checked_translation
from library import Library
from samples import GRANTHA_URL, seed
from sarvam_live import LiveService


ROOT = Path(__file__).resolve().parent
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
TARGETS = {"English": "en-IN", "Hindi": "hi-IN"}


def flash(message, kind="success"):
    st.session_state["flash"] = (kind, message)
    st.rerun()


def validate_image(original):
    if len(original) > MAX_IMAGE_BYTES:
        raise ValueError("Choose an image smaller than 30 MB.")
    try:
        with Image.open(BytesIO(original)) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError("Choose a JPEG or PNG image.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Choose a copy with a maximum of 40 million pixels.")
            image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError("The file does not contain a readable JPEG or PNG image.") from error


def label(name):
    st.markdown(f'<div class="field-label">{escape(name)}</div>', unsafe_allow_html=True)


def badge(text, tone="neutral"):
    st.markdown(
        f'<span class="badge {tone}"><span></span>{escape(text)}</span>',
        unsafe_allow_html=True,
    )


def show_text(text, lang):
    st.markdown(
        f'<div class="reading-text" lang="{lang}">{escape(text or "—")}</div>',
        unsafe_allow_html=True,
    )


def sidebar(library, is_live, reason):
    with st.sidebar:
        st.markdown(
            '<div class="brand"><span class="brand-seal" aria-hidden="true">अ</span>'
            '<div><strong>Palmleaf</strong><small>Manuscript reader</small></div></div>',
            unsafe_allow_html=True,
        )
        if "next_page" in st.session_state:
            st.session_state["page"] = st.session_state.pop("next_page")
        page = st.radio("Workspace", ["Translate", "Reader"], key="page")
        leaves = library.leaves()
        leaf_titles = {row["id"]: row["title"] for row in leaves}
        if "new_leaf" in st.session_state:
            st.session_state["leaf"] = st.session_state.pop("new_leaf")
        leaf_id = None
        if page != "Translate":
            leaf_id = st.radio("Library", list(leaf_titles), format_func=leaf_titles.get, key="leaf")
        st.divider()
        st.caption("Sarvam connected" if is_live else "Local mode")
        with st.popover("Connect Sarvam"):
            st.caption(reason)
            st.markdown("[Open Sarvam billing](https://indus.sarvam.ai/)")
            with st.form("connect", clear_on_submit=True):
                key = st.text_input("Sarvam API key", type="password")
                balance = st.number_input("Free credits confirmed in Billing (₹)", 0.0, 100.0,
                                          step=0.5, help="Palmleaf uses this amount as its local spending limit.")
                free = st.checkbox("My balance contains only free credits.")
                recharge = st.checkbox("Auto-recharge is off.")
                if st.form_submit_button("Connect"):
                    if key.strip() and free and recharge and balance > 0:
                        st.session_state["connection"] = {
                            "SARVAM_API_KEY": key.strip(), "SARVAM_FREE_CREDITS_ONLY": "1",
                            "SARVAM_AUTO_RECHARGE_OFF": "1",
                            "SARVAM_LOCAL_LIMIT_INR": str(balance),
                        }
                        flash("Connection settings saved for this session.")
                    st.error("Enter the key and available free credits. Confirm both billing settings.")
            if "connection" in st.session_state and st.button("Disconnect"):
                del st.session_state["connection"]
                flash("Session key removed.")
        return page, leaf_id


def image_panel(leaf, region):
    with st.container(key="image_panel"):
        head, controls = st.columns([1.0, 1.12], vertical_alignment="bottom")
        with head:
            label("Source image")
            st.markdown("### Manuscript view")
        with controls:
            view = st.segmented_control(
                "Image view",
                ["Full leaf", "Passage detail"],
                default="Full leaf",
                key=f"view_{leaf['id']}",
                label_visibility="collapsed",
                disabled=not bool(region),
            )
        with st.container(key="image_stage"):
            with Image.open(BytesIO(leaf["original"])) as original:
                image = original.convert("RGB")
            if region:
                x0, y0, x1, y1 = region
                margins = (image.width * .018, image.height * .04)
                box = (
                    int(x0 * image.width), int(y0 * image.height),
                    ceil(x1 * image.width), ceil(y1 * image.height),
                )
                if view == "Passage detail":
                    left, top, right, bottom = box
                    image = image.crop((
                        max(0, int(left - margins[0])),
                        max(0, int(top - margins[1])),
                        min(image.width, int(right + margins[0])),
                        min(image.height, int(bottom + margins[1])),
                    ))
                else:
                    draw = ImageDraw.Draw(image)
                    draw.rectangle(box, outline="#ffe0a1", width=max(3, image.width // 350))
            st.image(image, width="stretch")
        first, last = st.columns([2, 1], vertical_alignment="center")
        with first:
            st.caption(f"{leaf['catalogue'] or leaf['filename']}  ·  {leaf['script']}")
        with last:
            with st.popover("Original", width="stretch"):
                st.download_button(
                    "Download image",
                    leaf["original"],
                    file_name=Path(leaf["filename"]).name,
                    key=f"original_{leaf['id']}",
                )


def leaf_tools(library, live, leaf, is_live):
    leaf_id = leaf["id"]
    with st.container(key="leaf_tools"):
        label("Leaf controls")
        st.write("**Image and recognition**")
        if leaf["flag"]:
            st.warning(f"Clearer image requested. {leaf['flag']}")
        with st.expander("Image quality and details"):
            with st.form(f"quality_{leaf_id}"):
                flag = st.text_input("Image issue", value=leaf["flag"] or "",
                                     placeholder="Several leaves overlap or a cord covers the text.")
                request = st.form_submit_button("Request clearer image")
                clear = st.form_submit_button("Clear image request")
            if request or clear:
                if request and not flag.strip():
                    st.error("Describe the image issue.")
                else:
                    library.flag_leaf(leaf_id, flag.strip() if request else "")
                    flash("Image quality status saved.")
            with st.form(f"metadata_{leaf_id}"):
                title = st.text_input("Title", value=leaf["title"])
                catalogue = st.text_input("Catalogue reference", value=leaf["catalogue"])
                language_options = ["Unknown", "Sanskrit", "Other"]
                current_language = leaf["language"]
                if current_language not in language_options:
                    language_options.append(current_language)
                language = st.selectbox("Manuscript language", language_options,
                                        index=language_options.index(current_language))
                script = st.text_input("Script", value=leaf["script"])
                source_url = st.text_input("Source URL", value=leaf["source_url"])
                save_metadata = st.form_submit_button("Save details")
            if save_metadata:
                try:
                    library.update_leaf(
                        leaf_id, title, catalogue, language, script, source_url,
                    )
                    flash("Leaf details saved.")
                except ValueError as error:
                    st.error(str(error))
        eligible = (
            leaf["language"] == "Sanskrit"
            and leaf["script"].strip().casefold() not in {"", "unknown"}
            and not leaf["flag"]
        )
        actions = st.columns(2)
        if actions[0].button("Run or check OCR", key=f"ocr_{leaf_id}",
                             disabled=not (is_live and eligible), width="stretch"):
            try:
                with st.spinner("Checking Sarvam Vision…"):
                    blocks = live.ocr(leaf["original"], leaf["filename"], "sa-IN")
                    library.ingest(leaf_id, blocks)
                flash("OCR results are available in the passage list.")
            except Exception as error:
                flash(str(error), "warning")
        if actions[1].button("Add passage", key=f"add_{leaf_id}", width="stretch"):
            passage_id = library.add_passage(leaf_id)
            st.session_state["new_passage"] = passage_id
            flash("New passage added.")
        if not eligible:
            st.caption("Confirm Sanskrit and its script before OCR. Clear any image quality request first.")


def selected_passage(passages, leaf_id):
    if not passages:
        return None
    ids = [row["id"] for row in passages]
    with st.container(key="passage_selector"):
        label("Passage")
        descriptions = {
            p["id"]: f"{position}. {p['source'][:52] or 'Untitled passage'}"
            for position, p in enumerate(passages, start=1)
        }
        key = f"passage_{leaf_id}"
        if "new_passage" in st.session_state:
            requested_id = st.session_state.pop("new_passage")
            if requested_id in ids:
                st.session_state[key] = requested_id
        selected_id = st.selectbox(
            "Passage", ids, format_func=descriptions.get, key=key, label_visibility="collapsed"
        )
    return next(row for row in passages if row["id"] == selected_id)


def source_editor(library, passage, leaf, reviewer):
    passage_id = passage["id"]
    source_label = leaf["language"] if leaf["language"] != "Unknown" else "source"
    with st.container(key="source_editor"):
        top, status = st.columns([1, 1], vertical_alignment="center")
        with top:
            st.markdown(f"### {leaf['language']}")
        with status:
            if passage["source_approved"] and not leaf["flag"]:
                badge("Approved", "good")
            else:
                badge("Needs review")
        with st.form(f"source_{passage_id}"):
            source = st.text_area("Transcription", value=passage["source"], height=112)
            with st.expander("Reading for translation and image location"):
                normalized = st.text_area("Devanagari reading for translation" if source_label == "Sanskrit" else "Reading for translation",
                                          value=passage["normalized"], height=96)
                st.caption("Enter [gap] for unreadable text. Use the same gaps in the Devanagari reading.")
                has_region = st.checkbox("Use an image region", value=bool(passage["bbox"]))
                position = passage["bbox"] or [0.0, 0.0, 1.0, 1.0]
                shown = [round(coord * 100, 4) for coord in position]
                box = []
                for column, name, percent in zip(st.columns(4), ("Left", "Top", "Right", "Bottom"), shown):
                    box.append(column.number_input(
                        f"{name} (%)", min_value=0.0, max_value=100.0,
                        value=percent, step=1.0, format="%.4f",
                    ))
                if passage["raw_ocr"]:
                    st.caption("Original OCR")
                    show_text(passage["raw_ocr"], "sa")
            save, approve = st.columns(2)
            saved = save.form_submit_button(f"Save {source_label}", width="stretch")
            approved = approve.form_submit_button(
                f"Approve {source_label}", type="primary", width="stretch",
                disabled=not reviewer.strip() or bool(leaf["flag"]),
            )
        if saved or approved:
            try:
                if source != passage["source"] and normalized == passage["normalized"]:
                    normalized = ""
                region = position if box == shown else [percent / 100 for percent in box]
                library.save_source(passage_id, source, region if has_region else None, normalized)
                if approved:
                    library.approve_source(passage_id, reviewer)
                flash(f"{source_label} approved." if approved else f"{source_label} saved.")
            except ValueError as error:
                flash(str(error), "warning")


def translation_editors(library, live, passage, reviewer, is_live, leaf):
    passage_id = passage["id"]
    flag = leaf["flag"]
    language = leaf["language"]
    needs_devanagari = language == "Sanskrit" and leaf["script"].strip().casefold() not in {"unknown", "devanagari"} and not passage["normalized"].strip()
    can_generate = is_live or local_demo.available()
    if st.button(
        "Generate translation drafts",
        key=f"translate_{passage_id}", width="stretch",
        disabled=not (can_generate and language in LANGUAGES and passage["source_approved"]
                      and not flag and not needs_devanagari)
        or bool(passage["english_visible"] and passage["hindi_visible"]),
        help="Check each draft against the approved reading. Live translation and verification use Sarvam credits.",
    ):
        try:
            withheld = []
            with st.spinner("Checking translation drafts…"):
                for lang, code in TARGETS.items():
                    field = lang.lower()
                    if not passage[f"{field}_visible"]:
                        try:
                            draft = checked_translation(live if is_live else local_demo,
                                                        passage["normalized"] or passage["source"],
                                                        language, lang, code, LANGUAGES[language])
                        except Withheld:
                            withheld.append(lang)
                            continue
                        library.save_translation(
                            passage_id, code, draft, source_version=passage["source_version"], checked=True,
                        )

            if withheld:
                flash(f"Could not verify {' and '.join(withheld)}. Check the source or enter a translation.", "warning")
            flash("Checked translations are ready for review.")
        except Exception as error:
            flash(str(error), "warning")
    if needs_devanagari:
        st.caption("Save a reviewed Devanagari reading before translation.")

    for lang, code in TARGETS.items():
        field = lang.lower()
        with st.container(key=f"target_{field}"):
            title_column, status = st.columns([1, 1], vertical_alignment="center")
            with title_column:
                st.markdown(f"### {lang}")
            with status:
                approved = passage[f"{field}_approved"] and not flag
                state = "Approved" if approved else ("Draft" if passage[field] else "Withheld")
                badge(state, "good" if approved else "neutral")
            with st.form(f"translation_{passage_id}_{code}"):
                edit_key = f"draft_{passage_id}_{code}_{passage['source_version']}_{passage[field + '_origin']}"
                draft = st.text_area(f"{lang} translation", value=passage[field], height=106,
                                     label_visibility="collapsed", key=edit_key)
                save, approve = st.columns(2)
                saved = save.form_submit_button(f"Save {lang}", width="stretch")
                signed = approve.form_submit_button(
                    f"Approve {lang}", width="stretch",
                    disabled=not reviewer.strip() or not passage["source_approved"] or bool(flag),
                )
            if saved or signed:
                try:
                    library.save_translation(passage_id, code, draft, source_version=passage["source_version"])
                    if signed:
                        library.approve_translation(passage_id, code, reviewer)
                    flash(f"{lang} approved." if signed else f"{lang} saved.")
                except ValueError as error:
                    flash(str(error), "warning")


def review_notes(library, live, passage, is_live):
    passage_id = passage["id"]
    with st.expander("Reviewer notes and Sarvam tools"):
        with st.form(f"notes_{passage_id}"):
            note = st.text_area("Reviewer notes", value=passage["note"])
            if st.form_submit_button("Save note"):
                library.save_note(passage_id, note)
                flash("Reviewer note saved.")
        recording = st.audio_input("Dictate a note")
        if st.button("Transcribe note", key=f"dictate_{passage_id}", disabled=not (recording and is_live)):
            try:
                result = live.dictate(recording.getvalue(), recording.name)
                st.session_state[f"dictated_{passage_id}"] = result
            except Exception as error:
                st.warning(str(error))
        aloud = st.session_state.get(f"dictated_{passage_id}")
        if aloud:
            st.write(aloud)
            if st.button("Append dictated note", key=f"append_{passage_id}"):
                library.save_note(passage_id, "\n".join(filter(None, [passage["note"], aloud])))
                st.session_state.pop(f"dictated_{passage_id}")
                flash("Dictated note saved.")
        has_drafts = bool(passage["english"].strip() and passage["hindi"].strip())
        check_key = f"check_result_{passage_id}"
        draft_pair = (passage["english"], passage["hindi"])
        if st.button("Check names and numbers", key=f"check_{passage_id}",
                     disabled=not (is_live and has_drafts)):
            try:
                st.session_state[check_key] = (draft_pair, live.check(*draft_pair))
            except Exception as error:
                st.warning(str(error))
        check = st.session_state.get(check_key)
        if has_drafts and check and check[0] == draft_pair:
            show_text(check[1], "en")


def reader(library, live, leaf, passage, passages, is_live):
    if leaf["flag"]:
        st.warning(f"Clearer image requested. {leaf['flag']}")
        return
    if not passage:
        st.info("No passages found for this leaf.")
        return
    with st.container(key="reader_panel"):
        label("Text & translations")
        if passage["source"]:
            st.markdown(f"### {leaf['language']}")
            show_text(passage["source"], "")
        for lang, code in TARGETS.items():
            field = lang.lower()
            if passage[field]:
                st.markdown(f"### {lang}")
                show_text(passage[field], code.split("-")[0])
                audio_key = f"audio_{passage['id']}_{code}"
                text_version = passage[field]
                if st.button(f"Listen in {lang}", key=f"play_{audio_key}", disabled=not is_live):
                    try:
                        st.session_state[audio_key] = (text_version, live.speak(text_version, code))
                    except Exception as error:
                        st.warning(str(error))
                audio = st.session_state.get(audio_key)
                if audio and audio[0] == text_version:
                    st.audio(audio[1], format="audio/wav")
                if field == "hindi":
                    roman_key = f"roman_{passage['id']}"
                    if st.button("Show Hindi in Roman letters", key=f"button_{roman_key}", disabled=not is_live):
                        try:
                            st.session_state[roman_key] = (text_version, live.romanize(text_version))
                        except Exception as error:
                            st.warning(str(error))
                    roman = st.session_state.get(roman_key)
                    if roman and roman[0] == text_version:
                        show_text(roman[1], "hi-Latn")
            else:
                st.caption(f"{lang} translation not available.")
        ready_count = sum(
            bool(row["source"] and (row["english"] or row["hindi"]))
            for row in passages
        )
        st.divider()
        if ready_count:
            st.download_button(
                f"Export edition ({ready_count})",
                library.export_html(leaf["id"]),
                "palmleaf-edition.html",
                mime="text/html",
                type="primary",
                key=f"export_{leaf['id']}",
            )
        else:
            st.caption("Translate passages to export a reading edition.")


@st.cache_resource
def open_library(db_path):
    library = Library(db_path)
    seed(library)
    return library


def main():
    st.set_page_config(page_title="Palmleaf · Manuscript Reader", page_icon="📜", layout="wide")
    st.html(ROOT / "style.css")
    db_path = Path(os.getenv("PALMLEAF_DB", ROOT / ".reader" / "library.sqlite3"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    library = open_library(db_path)
    live = LiveService(db_path, st.session_state.get("connection"))
    is_live, reason = live.status()
    page, leaf_id = sidebar(library, is_live, reason)

    if "flash" in st.session_state:
        tone, message = st.session_state.pop("flash")
        getattr(st, tone)(message)
    if page == "Translate":
        from translate_screen import translate_screen
        translate_screen(library, live, is_live)
        return
    if leaf_id is None:
        st.title("Manuscript workspace")
        st.info("Import a leaf image to begin.")
        return

    leaf = library.leaf(leaf_id)
    passages = library.passages(leaf_id)
    with st.container(key="title_band"):
        heading, state = st.columns([5, 1.55], vertical_alignment="center")
        with heading:
            label("Reading edition")
            st.title(leaf["title"])
            st.caption(f"{leaf['kind']}  /  {leaf['language']}  /  {leaf['script']}")
        with state:
            done = sum(
                bool(p["source"] and (p["english"] or p["hindi"]))
                for p in passages
            )
            if leaf["flag"]:
                badge("Clearer image requested")
            elif done:
                badge(f"{done} translated", "good")
            else:
                badge(f"{len(passages)} {'passage' if len(passages) == 1 else 'passages'}")

    left, right = st.columns([1.22, 1], gap="large")
    with right:
        passage = selected_passage(passages, leaf_id)
        reader(library, live, leaf, passage, passages, is_live)
    with left:
        image_panel(leaf, passage["bbox"] if passage else None)


if __name__ == "__main__":
    main()
