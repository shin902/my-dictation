from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = Path("data")
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str | None = None
    groq_model: str = "whisper-large-v3-turbo"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str | None = None
    timeout: float = 30.0
    temperature: float = 0.0
    itn_backend: str = "builtin"
    terminology_backend: str = "builtin"
    terminology_glossary: Path | None = None
    terminology: dict[str, list[str]] = field(default_factory=dict)


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
    cfg = Settings(
        data_dir=Path(os.getenv("MY_DICTATION_DATA_DIR", raw.get("data_dir", "data"))),
        groq_base_url=os.getenv("GROQ_BASE_URL", api.get("groq_base_url", Settings.groq_base_url)),
        groq_api_key=os.getenv("GROQ_API_KEY", api.get("groq_api_key")),
        groq_model=os.getenv("GROQ_MODEL", api.get("groq_model", Settings.groq_model)),
        llm_base_url=os.getenv("LLM_BASE_URL", api.get("llm_base_url", Settings.llm_base_url)),
        llm_api_key=os.getenv("LLM_API_KEY", api.get("llm_api_key")),
        llm_model=os.getenv("LLM_MODEL", api.get("llm_model")),
        timeout=float(os.getenv("MY_DICTATION_TIMEOUT", api.get("timeout", 30))),
        temperature=float(api.get("temperature", 0)),
        itn_backend=os.getenv("MY_DICTATION_ITN_BACKEND", raw.get("processors", {}).get("itn", "builtin")),
        terminology_backend=os.getenv("MY_DICTATION_TERMINOLOGY_BACKEND", raw.get("processors", {}).get("terminology", "builtin")),
        terminology_glossary=(Path(value) if (value := raw.get("processors", {}).get("terminology_glossary")) else None),
        terminology=raw.get("terminology", {}),
    )
    return cfg
