import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from my_dictation.asr import GroqAsr
from my_dictation.processors import OpenAIProofreader


class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return json.dumps(self.payload).encode()


class ExternalApiContractTests(unittest.TestCase):
    def test_groq_multipart_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "voice.wav"; audio.write_bytes(b"RIFFaudio")
            with patch("urllib.request.urlopen", return_value=Response({"text": "成功"})) as call:
                result = GroqAsr("https://api.groq.com/openai/v1/", "secret", "whisper", 7).transcribe(audio)
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/audio/transcriptions")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertIn("multipart/form-data; boundary=", request.headers["Content-type"])
        self.assertIn(b'name="model"\r\n\r\nwhisper', request.data)
        self.assertIn(b'name="file"; filename="voice.wav"', request.data)
        self.assertIn(b"RIFFaudio", request.data)
        self.assertEqual(call.call_args.kwargs["timeout"], 7)
        self.assertEqual((result.text, result.model), ("成功", "whisper"))

    def test_groq_requires_auth_and_wraps_http_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "a.wav"; audio.write_bytes(b"x")
            with self.assertRaisesRegex(RuntimeError, "GROQ_API_KEY"):
                GroqAsr("http://mock", None, "model").transcribe(audio)
            with patch("urllib.request.urlopen", side_effect=OSError("503")):
                with self.assertRaisesRegex(RuntimeError, "Groq ASR failed: 503"):
                    GroqAsr("http://mock", "key", "model").transcribe(audio)

    @patch("my_dictation.processors.json_request")
    def test_llm_structured_response_contract(self, request):
        request.return_value = {"choices": [{"message": {"content": json.dumps({"text": "修正。", "changes": [{"before": "修正", "after": "修正。"}]})}}]}
        result = OpenAIProofreader("http://mock/v1/", "key", "model", 3, 0).process("修正", [])
        self.assertTrue(result.accepted); self.assertEqual(result.output, "修正。")
        self.assertEqual(request.call_args.args[0], "http://mock/v1/chat/completions")
        self.assertEqual(request.call_args.args[1]["model"], "model")

    @patch("my_dictation.processors.json_request")
    def test_llm_malformed_json_and_empty_choices_fall_back(self, request):
        for response in ({"choices": [{"message": {"content": "{"}}]}, {"choices": []}):
            request.return_value = response
            result = OpenAIProofreader("http://mock", "key", "model", 1, 0).process("安全", [])
            self.assertFalse(result.accepted); self.assertEqual(result.output, "安全")
            self.assertEqual(result.candidate_output, ""); self.assertTrue(result.rejection_reason)


if __name__ == "__main__": unittest.main()
