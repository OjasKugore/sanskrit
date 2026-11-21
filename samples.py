"""Local practice material for the manuscript review workbench."""

from pathlib import Path


ASSETS = Path(__file__).resolve().parent / "assets"

# British Library EAP1294/1/73. Its catalogue identifies Sanskrit and Grantha.
# The image conditions specify research use and CC BY-NC. This is a reference link.
GRANTHA_URL = "https://searcharchives.bl.uk/catalog/040-004311498"

PRACTICE_TITLE = "The river at dawn"
PHOTO_TITLE = "Manuscript bundle photograph"


def seed(library):
    """Add the local examples when the library is empty. Leave all readings in draft."""
    if library.leaves():
        return

    # Read both assets first so a missing file cannot leave a partial set of examples.
    specimen = ASSETS / "river-at-dawn.png"
    photo = ASSETS / "manuscript-bundle.jpeg"
    specimen_bytes = specimen.read_bytes()
    photo_bytes = photo.read_bytes()

    photo_id = library.add_leaf(
        title=PHOTO_TITLE,
        original=photo_bytes,
        filename=photo.name,
        catalogue="User-supplied photograph",
        kind="Bundle photograph",
    )
    library.flag_leaf(
        photo_id,
        "The leaves overlap, and a cord covers part of the writing. "
        "Obtain a clear photograph of each leaf side. Confirm the language and script.",
    )

    specimen_id = library.add_leaf(
        title=PRACTICE_TITLE,
        original=specimen_bytes,
        filename=specimen.name,
        catalogue="Modern typeset example",
        language="Sanskrit",
        script="Devanagari",
        kind="Practice sheet",
    )
    # Reading boxes contain the printed line and a small margin on each side.
    # Coordinates are fractions of the full image in [left, top, right, bottom] order.
    practice_lines = (
        (
            "प्रभाते नदी शान्ता भवति।",
            [0.08889, 0.24516, 0.64861, 0.43387],
            "The river is calm at dawn.",
            "भोर में नदी शांत होती है।",
        ),
        (
            "तीरे पक्षिणो गायन्ति।",
            [0.08889, 0.50484, 0.56875, 0.70000],
            "Birds sing on the riverbank.",
            "नदी के किनारे पक्षी गाते हैं।",
        ),
    )
    for source, bbox, english, hindi in practice_lines:
        library.add_passage(
            specimen_id,
            source=source,
            normalized=source,
            bbox=bbox,
            english=english,
            hindi=hindi,
        )
