# Palmleaf

Palmleaf is a local web app for reading and translating Sanskrit manuscripts. It uses Streamlit and SQLite.

## Start the reader

Run this command from the project folder with Python 3.11 or later.

```sh
./run.sh
```

Open [the local reader](http://127.0.0.1:8501). The first run installs free Python packages. The app saves its data in `.reader/library.sqlite3`.

## Try the included examples

1. Open **The river at dawn** in **Review**. This modern practice sheet has two passages and draft English and Hindi translations.
2. Select **Passage detail** to enlarge the selected text.
3. Enter a reviewer name. Approve the Sanskrit, English, and Hindi in that order.
4. Open **Reader**. Export the approved text as HTML. Open the file in a browser to print it.
5. Open **Manuscript bundle photograph** to see the supplied image and its quality request.

For another manuscript, import a JPEG or PNG of one side of a leaf. Record its language, script, and catalogue reference. Select **Add passage** for manual transcription. In the source form, set the image coordinates as percentages of the full image. Save a Devanagari reading for translation when the source uses a different script.

Use `[gap]` for text you cannot read. Include the same number of gap markers in the source and each translation. An edit to a source or its image region removes all approvals for that passage.

## Enable Sarvam with free credits

The app starts in local mode. Open **Connect Sarvam** in the sidebar to enter a session-only key and confirm billing. Confirm in Sarvam's billing settings that the organisation holds only free credits and that auto-recharge is off. Sarvam shares one credit balance across the organisation. The app uses these settings as your confirmation. [Billing guide](https://docs.sarvam.ai/api/platform/billing)

On macOS with zsh, enter the key at a prompt. The key stays out of the command history.

```zsh
read -rs 'SARVAM_API_KEY?Sarvam API key: '
export SARVAM_API_KEY
SARVAM_FREE_CREDITS_ONLY=1 SARVAM_AUTO_RECHARGE_OFF=1 ./run.sh
```

You can also set `SARVAM_LOCAL_LIMIT_INR` to a lower amount than the default ₹100. Use a limit within the confirmed remaining free credits. A Sarvam quota response stops further live requests. The saved reader and manual review remain available.

- **Run or check OCR** submits the image once. Select it again to check a pending job. The app saves results for reuse. [Sarvam Parse](https://docs.sarvam.ai/api/api-guides-tutorials/document-intelligence/how-to/parse)
- **Generate translation drafts** sends the approved reading for each language that still needs review. Check both drafts against the text on the leaf. [Sarvam Translate](https://docs.sarvam.ai/api-reference/text/translate-text)
- **Reviewer notes and Sarvam tools** offers note dictation and a draft comparison. The reader offers audio and Hindi in Roman letters.

For a historical test, choose one permitted leaf image from the British Library example. The catalogue identifies the language as Sanskrit and the script as Grantha. [Grantha research sample](https://searcharchives.bl.uk/catalog/040-004311498)

## Run tests

```sh
.venv/bin/python -m unittest discover -v
```

## Image translation demo

Open **Translate**. Choose the language, then paste, drop, or upload a single image.
Use **Manuscript** for a handwritten leaf. Select **Translate into English and Hindi** to read the image.
Open **Review passages** and compare the transcription with the leaf. Mark unreadable words with `[gap]`.
Approve the source before generating manuscript translations.

Use **Printed text** for clear typeset material. Automatic translation requires two matching readings of the source.
If the app cannot check the reading, it asks for review. The local model can make both readings.

The app checks each proposed translation against the source in a separate model request.
Sarvam mode uses Sarvam-105B for this check. Live checks use credits under the local spending limit.
The app withholds a translation when a check fails. English and Hindi have separate results.
A machine-checked draft can still contain errors. Reader and export require human approval.
Earlier machine drafts without a recorded check stay hidden until a new check succeeds.
For a session-only Sarvam connection, use **Connect Sarvam** after confirming free credits and disabled auto-recharge.
When Sarvam is disconnected, the app can use an installed Ollama model named `qwen2.5vl:7b` on this Mac.
If available, the app uses the local text model `qwen2.5:7b` for translation.
The app checks the local Ollama server on port 11434. The saved example also works without a model.
