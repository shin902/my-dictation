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
    terminology: dict[str, list[str]] = field(default_factory=dict)


def load_settings(path: str | Path | None = None) -> Settings:
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
        terminology=raw.get("terminology", {}),
    )
    return cfg
