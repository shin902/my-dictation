from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = Path("data")
    asr_provider: str = "groq"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str | None = None
    groq_model: str = "whisper-large-v3-turbo"
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_api_key: str | None = None
    elevenlabs_model: str = "scribe_v1"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str | None = None
    timeout: float = 30.0
    temperature: float = 0.0
    itn_backend: str = "builtin"
    terminology_backend: str = "builtin"
    terminology_glossary: Path | None = None
    terminology: dict[str, list[str]] = field(default_factory=dict)
    vad_enabled: bool = True
    vad_backend: str = "webrtc"
    vad_padding_ms: int = 300
    vad_frame_ms: int = 30
    vad_min_speech_ms: int = 90
    vad_aggressiveness: int = 2


def _load_dotenv(path: Path) -> None:
    """Load the small KEY=VALUE subset needed by this CLI.

    Existing process variables always win. Quoted values and optional ``export``
    prefixes are accepted; malformed lines are ignored rather than becoming
    surprising environment entries.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key.replace("_", "a").isalnum() or key[0].isdigit():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _parse_bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean")


def load_settings(path: str | Path | None = None) -> Settings:
    # Load project-local secrets without replacing values explicitly exported by
    # the calling shell.
    _load_dotenv(Path.cwd() / ".env")
    raw: dict = {}
    selected = Path(path) if path else Path(os.getenv("MY_DICTATION_CONFIG", "config.toml"))
    if selected.exists():
        with selected.open("rb") as f:
            raw = tomllib.load(f)
    api = raw.get("api", {})
    vad = raw.get("vad", {})
    asr_provider = os.getenv("MY_DICTATION_ASR_PROVIDER", os.getenv("ASR_PROVIDER", api.get("asr_provider", api.get("provider", Settings.asr_provider))))
    cfg = Settings(
        data_dir=Path(os.getenv("MY_DICTATION_DATA_DIR", raw.get("data_dir", "data"))),
        asr_provider=str(asr_provider).lower(),
        groq_base_url=os.getenv("GROQ_BASE_URL", api.get("groq_base_url", Settings.groq_base_url)),
        groq_api_key=os.getenv("GROQ_API_KEY", api.get("groq_api_key")),
        groq_model=os.getenv("GROQ_MODEL", api.get("groq_model", Settings.groq_model)),
        elevenlabs_base_url=os.getenv("ELEVENLABS_BASE_URL", api.get("elevenlabs_base_url", Settings.elevenlabs_base_url)),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", api.get("elevenlabs_api_key")),
        elevenlabs_model=os.getenv("ELEVENLABS_MODEL", api.get("elevenlabs_model", Settings.elevenlabs_model)),
        llm_base_url=os.getenv("LLM_BASE_URL", api.get("llm_base_url", Settings.llm_base_url)),
        llm_api_key=os.getenv("LLM_API_KEY", api.get("llm_api_key")),
        llm_model=os.getenv("LLM_MODEL", api.get("llm_model")),
        timeout=float(os.getenv("MY_DICTATION_TIMEOUT", api.get("timeout", 30))),
        temperature=float(api.get("temperature", 0)),
        itn_backend=os.getenv("MY_DICTATION_ITN_BACKEND", raw.get("processors", {}).get("itn", "builtin")),
        terminology_backend=os.getenv("MY_DICTATION_TERMINOLOGY_BACKEND", raw.get("processors", {}).get("terminology", "builtin")),
        terminology_glossary=(Path(value) if (value := raw.get("processors", {}).get("terminology_glossary")) else None),
        terminology=raw.get("terminology", {}),
        vad_enabled=_parse_bool(os.getenv("MY_DICTATION_VAD_ENABLED", vad.get("enabled", True)), "MY_DICTATION_VAD_ENABLED"),
        vad_backend=str(os.getenv("MY_DICTATION_VAD_BACKEND", vad.get("backend", "webrtc"))).lower(),
        vad_padding_ms=int(os.getenv("MY_DICTATION_VAD_PADDING_MS", vad.get("padding_ms", 300))),
        vad_frame_ms=int(os.getenv("MY_DICTATION_VAD_FRAME_MS", vad.get("frame_ms", 30))),
        vad_min_speech_ms=int(os.getenv("MY_DICTATION_VAD_MIN_SPEECH_MS", vad.get("min_speech_ms", 90))),
        vad_aggressiveness=int(os.getenv("MY_DICTATION_VAD_AGGRESSIVENESS", vad.get("aggressiveness", 2))),
    )
    return cfg
