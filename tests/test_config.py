import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from my_dictation.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_loads_dotenv_from_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "GROQ_API_KEY=from-dotenv\n"
                "LLM_API_KEY=llm-from-dotenv\n"
                "LLM_MODEL=dotenv-model\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True), patch("my_dictation.config.Path.cwd", return_value=root):
                settings = load_settings(root / "missing.toml")
            self.assertEqual(settings.groq_api_key, "from-dotenv")
            self.assertEqual(settings.llm_api_key, "llm-from-dotenv")
            self.assertEqual(settings.llm_model, "dotenv-model")

    def test_exported_environment_wins_over_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("GROQ_API_KEY=from-dotenv\n", encoding="utf-8")
            with patch.dict(os.environ, {"GROQ_API_KEY": "from-shell"}, clear=True), patch(
                "my_dictation.config.Path.cwd", return_value=root
            ):
                settings = load_settings(root / "missing.toml")
            self.assertEqual(settings.groq_api_key, "from-shell")


if __name__ == "__main__":
    unittest.main()
