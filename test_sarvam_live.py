import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

from sarvam_live import LiveError, LiveService

ENV = {"SARVAM_API_KEY": "offline-test", "SARVAM_FREE_CREDITS_ONLY": "1", "SARVAM_AUTO_RECHARGE_OFF": "1"}
PNG = b"\x89PNG\r\n\x1a\n" + b"one-page-fixture"


def wav_clip(seconds=1):
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * (8000 * seconds))
    return out.getvalue()


class ProviderError(Exception):
    def __init__(self, code, reason=""):
        self.status_code = code
        self.body = {"error": {"code": reason}}


class LiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "edition.db"
        context = patch.dict("os.environ", ENV, clear=True)
        context.start()
        self.addCleanup(context.stop)
        self.live = LiveService(self.path)
        self.live._rest = Mock(side_effect=AssertionError("Unexpected API call"))
        self.live._sdk_translate = Mock(side_effect=AssertionError("Unexpected translation call"))

    def test_requires_all_server_settings(self):
        for name in ENV:
            with self.subTest(name=name), patch.dict("os.environ", {name: ""}):
                self.assertFalse(self.live.status()[0])
                with self.assertRaises(LiveError):
                    self.live.translate("एकम्", "en-IN")
        self.live._sdk_translate.assert_not_called()

    def test_gap_and_fragment_cache_survive_restarts(self):
        self.live._sdk_translate.side_effect = ["one", "two"]
        source = " एकम् [gap] [gap] द्वे "
        self.assertEqual(self.live.translate(source, "en-IN"), " one [gap] [gap] two ")
        self.assertEqual(self.live._sdk_translate.call_count, 2)
        new = LiveService(self.path)
        with patch.object(new, "_sdk_translate", side_effect=AssertionError("cache miss")):
            self.assertEqual(new.translate(source, "en-IN"), " one [gap] [gap] two ")

    @unittest.skipUnless(importlib.util.find_spec("sarvamai"), "The official SDK is not installed in this interpreter.")
    def test_official_sdk_translation_through_mock_transport(self):
        import httpx
        def handle(transport, request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/translate")
            self.assertEqual(request.headers["api-subscription-key"], "offline-test")
            payload = json.loads(request.content)
            self.assertEqual(payload["source_language_code"], "sa-IN")
            self.assertEqual(payload["target_language_code"], "hi-IN")
            self.assertEqual(payload["model"], "sarvam-translate:v1")
            return httpx.Response(200, json={"request_id": "example", "translated_text": "एक", "source_language_code": "sa-IN"})
        with patch.object(httpx.HTTPTransport, "handle_request", handle):
            self.assertEqual(LiveService(self.path).translate("एकम्", "hi-IN"), "एक")

    @unittest.skipUnless(importlib.util.find_spec("sarvamai"), "The official SDK is not installed in this interpreter.")
    def test_official_sdk_does_not_repeat_rate_limited_request(self):
        import httpx
        limited = Mock(return_value=httpx.Response(429, json={"error": {"code": "rate_limit_exceeded_error"}}))
        with patch.object(httpx.HTTPTransport, "handle_request", limited):
            with self.assertRaisesRegex(LiveError, "rate limit"):
                LiveService(self.path).translate("एकम्", "hi-IN")
        self.assertEqual(limited.call_count, 1)

    def test_duplicate_while_request_in_flight(self):
        sent, finish = threading.Event(), threading.Event()
        def slow(*args):
            sent.set()
            finish.wait(4)
            return "one"
        self.live._sdk_translate.side_effect = slow
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.live.translate, "एकम्", "en-IN")
            self.assertTrue(sent.wait(2))
            try:
                with self.assertRaisesRegex(LiveError, "duplicate"):
                    LiveService(self.path).translate("एकम्", "en-IN")
            finally:
                finish.set()
            self.assertEqual(first.result(2), "one")
        self.assertEqual(self.live._sdk_translate.call_count, 1)

    def test_timeout_keeps_reservation_even_after_restart(self):
        self.live._sdk_translate.side_effect = TimeoutError()
        with self.assertRaisesRegex(LiveError, "duplicate"):
            self.live.translate("एकम्", "hi-IN")
        with self.assertRaisesRegex(LiveError, "duplicate"):
            LiveService(self.path).translate("एकम्", "hi-IN")

    def test_rate_limit_allows_manual_retry(self):
        self.live._sdk_translate.side_effect = [ProviderError(429, "rate_limit_exceeded_error"), "one"]
        with self.assertRaisesRegex(LiveError, "rate limit"):
            self.live.translate("एकम्", "en-IN")
        self.assertTrue(self.live.status()[0])
        self.assertEqual(self.live.translate("एकम्", "en-IN"), "one")

    def test_quota_latches_across_operations_and_restarts(self):
        self.live._sdk_translate.side_effect = ProviderError(402)
        with self.assertRaisesRegex(LiveError, "locked"):
            self.live.translate("एकम्", "en-IN")
        new = LiveService(self.path)
        self.assertFalse(new.status()[0])
        with self.assertRaisesRegex(LiveError, "locked"):
            new.speak("one", "en-IN")

    def test_seat_spend_limit_also_latches(self):
        self.live._sdk_translate.side_effect = ProviderError(429, "seat_budget_exhausted_error")
        with self.assertRaisesRegex(LiveError, "locked"):
            self.live.translate("एकम्", "en-IN")
        self.assertFalse(LiveService(self.path).status()[0])

    def test_invalid_or_low_local_limit_prevents_calls(self):
        for value in ["NaN", "1000", "0", "0.01"]:
            with self.subTest(limit=value), patch.dict("os.environ", {"SARVAM_LOCAL_LIMIT_INR": value}):
                with self.assertRaises(LiveError):
                    self.live.ocr(PNG, "leaf.png")
        self.live._rest.assert_not_called()

    def test_saved_ocr_job_resumes_with_same_input_under_different_name(self):
        self.live._rest.side_effect = [{"job_id": "job-1"}, {"status": "running"}]
        with self.assertRaisesRegex(LiveError, "Check OCR"):
            self.live.ocr(PNG, "leaf.png")
        submit = self.live._rest.call_args_list[0]
        self.assertEqual(submit.args, ("POST", "/doc-ai/v1/job/parse"))
        self.assertEqual(submit.kwargs["data"]["content_type"], "mixed")
        self.assertEqual(submit.kwargs["data"]["language"], "sa-IN")

        new = LiveService(self.path)
        page = {"width": 400, "height": 100, "blocks": [{"text": "श्री", "bbox": [40, 10, 280, 50]}]}
        result = {"documents": [{"pages": [{"page_number": 1, "content": json.dumps(page)}]}]}
        new._rest = Mock(side_effect=[{"status": "partially_completed"}, result])
        expected = [{"text": "श्री", "bbox": [0.1, 0.1, 0.7, 0.5]}, {"text": "[gap]", "bbox": None}]
        self.assertEqual(new.ocr(PNG, "renamed.png"), expected)
        self.assertEqual(new.ocr(PNG, "renamed.png"), expected)
        self.assertEqual([c.args[0] for c in new._rest.call_args_list], ["GET", "GET"])

    def test_official_html_result_and_recoverable_poll_error(self):
        self.live._rest.side_effect = [{"job_id": "J_2"}, TimeoutError(), {"status": "completed"},
            {"documents": [{"pages": [{"page_number": 1, "content": "<h1>श्री</h1><p>गुरुः</p>"}]}]}]
        with self.assertRaisesRegex(LiveError, "resume"):
            self.live.ocr(PNG, "a.png")
        self.assertEqual(self.live.ocr(PNG, "a.png"), [{"text": "श्री", "bbox": None}, {"text": "गुरुः", "bbox": None}])
        self.assertEqual(sum(c.args[0] == "POST" for c in self.live._rest.call_args_list), 1)

    def test_final_reservation_allows_ocr_job_to_finish(self):
        with patch.dict("os.environ", {"SARVAM_LOCAL_LIMIT_INR": "0.60"}):
            self.live._rest.side_effect = [
                {"job_id": "final"}, {"status": "pending"}, {"status": "completed"},
                {"documents": [{"pages": [{"content": "श्री"}]}]}]
            with self.assertRaisesRegex(LiveError, "Check OCR"):
                self.live.ocr(PNG, "last.png")
            self.assertTrue(self.live.status()[0])
            with self.assertRaisesRegex(LiveError, "limit"):
                self.live.translate("एकम्", "en-IN")
            self.assertEqual(self.live.ocr(PNG, "last.png")[0]["text"], "श्री")
            self.assertFalse(self.live.status()[0])

    def test_empty_ocr_and_failed_jobs_return_manual_message(self):
        self.live._rest.side_effect = [{"job_id": "J_1"}, {"status": "failed"}]
        for _ in range(2):
            with self.assertRaisesRegex(LiveError, "manually"):
                self.live.ocr(PNG, "a.png")
        self.assertEqual(self.live._rest.call_count, 2)

    def test_audio_romanization_and_advisory_use_expected_payloads(self):
        clip = wav_clip()
        self.live._rest.side_effect = [
            {"transcript": "note"}, {"audios": [base64.b64encode(clip).decode()]},
            {"transliterated_text": "shri"}, {"choices": [{"message": {"content": "Check the name."}}]}]
        self.assertEqual(self.live.dictate(clip, "note.wav"), "note")
        self.assertEqual(self.live.speak("an approved passage", "en-IN"), clip)
        self.assertEqual(self.live.romanize("श्री"), "shri")
        self.assertEqual(self.live.check("A name", "एक नाम"), "Check the name.")
        calls = self.live._rest.call_args_list
        self.assertEqual(calls[0].kwargs["data"]["model"], "saaras:v4")
        self.assertEqual(calls[1].kwargs["json"]["language_code"], "en-IN")
        self.assertEqual(calls[2].kwargs["json"]["source_language_code"], "hi-IN")
        self.assertEqual(calls[3].kwargs["json"]["model"], "sarvam-105b")
        self.assertEqual(self.live.speak("an approved passage", "en-IN"), clip)
        self.assertEqual(self.live._rest.call_count, 4)

    def test_verifier_rejects_malformed_or_incomplete_decisions_and_caches_them(self):
        self.live._rest.side_effect = None
        for i, (reason, content, expected) in enumerate([
            ("stop", '{"supported":true}', True),
            ("length", '{"supported":true}', False),
            ("stop", 'possibly', False),
            ("stop", '{"supported":false}', False),
        ]):
            self.live._rest.return_value = {"choices": [{"finish_reason": reason, "message": {"content": content}}]}
            args = ("गुरुः", "Sanskrit", f"Teacher {i}", "English")
            self.assertIs(self.live.supports_translation(*args), expected)
            self.assertIs(self.live.supports_translation(*args), expected)
        self.assertEqual(self.live._rest.call_count, 4)

    def test_validation_runs_before_submission(self):
        for action in [
            lambda: self.live.translate("ए" * 2001, "en-IN"),
            lambda: self.live.translate("ए", "sa-IN"),
            lambda: self.live.dictate(wav_clip(31), "note.wav"),
            lambda: self.live.ocr(b"bad", "leaf.jpg"),
            lambda: self.live.romanize("अ" * 1001),
        ]:
            with self.assertRaises(LiveError):
                action()
        self.live._rest.assert_not_called()
        self.live._sdk_translate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
