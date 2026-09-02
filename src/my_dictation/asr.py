from __future__ import annotations

import json
import mimetypes
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AsrResult:
    text: str
    provider: str
    model: str


class GroqAsr:
    def __init__(self, base_url: str, api_key: str | None, model: str, timeout: float = 30):
        self.base_url, self.api_key, self.model, self.timeout = base_url, api_key, model, timeout

    def transcribe(self, audio: Path) -> AsrResult:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is required")
        if not audio.is_file():
            raise RuntimeError(f"Groq ASR input is not a regular file: {audio}")
        if audio.stat().st_size == 0:
            raise RuntimeError(f"Groq ASR input is empty: {audio}")
        boundary = f"----my-dictation-{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
        parts = []
        def field(name: str, value: str) -> None:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        field("model", self.model); field("response_format", "json")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{audio.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        parts.extend([audio.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
        request = urllib.request.Request(self.base_url.rstrip("/") + "/audio/transcriptions", data=b"".join(parts), headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "my-dictation/0.1.0",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response: payload = json.load(response)
            text = payload["text"]
        except Exception as exc:
            raise RuntimeError(f"Groq ASR failed: {exc}") from exc
        if not isinstance(text, str): raise RuntimeError("Groq ASR returned no text")
        if not text.strip(): raise RuntimeError("Groq ASR returned empty text")
        return AsrResult(text, "groq", self.model)


class ElevenLabsAsr:
    """ElevenLabs Scribe speech-to-text adapter.

    ``base_url`` is the API root (``https://api.elevenlabs.io``) or an API
    version root (for example a test server ending in ``/v1``).  The adapter
    deliberately only maps the response's top-level ``text`` field into the
    provider-neutral AsrResult.
    """

    def __init__(self, base_url: str, api_key: str | None, model: str = "scribe_v1", timeout: float = 30):
        self.base_url, self.api_key, self.model, self.timeout = base_url, api_key, model, timeout

    def transcribe(self, audio: Path) -> AsrResult:
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is required")
        if not audio.is_file():
            raise RuntimeError(f"ElevenLabs ASR input is not a regular file: {audio}")
        if audio.stat().st_size == 0:
            raise RuntimeError(f"ElevenLabs ASR input is empty: {audio}")

        boundary = f"----my-dictation-{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="model_id"\r\n\r\n'
            f'{self.model}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{audio.name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode() + audio.read_bytes() + b"\r\n" + f"--{boundary}--\r\n".encode()
        base = self.base_url.rstrip("/")
        endpoint = base + "/speech-to-text" if base.endswith("/v1") else base + "/v1/speech-to-text"
        request = urllib.request.Request(endpoint, data=body, headers={
            "xi-api-key": self.api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "my-dictation/0.1.0",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
            text = payload["text"]
        except Exception as exc:
            raise RuntimeError(f"ElevenLabs ASR failed: {exc}") from exc
        if not isinstance(text, str):
            raise RuntimeError("ElevenLabs ASR returned no text")
        if not text.strip():
            raise RuntimeError("ElevenLabs ASR returned empty text")
        return AsrResult(text, "elevenlabs", self.model)
