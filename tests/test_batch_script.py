import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "transcribe-files.sh"


class BatchScriptTests(unittest.TestCase):
    def test_discards_cli_stdout_and_formats_saved_record(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            audio = temp / "voice sample.ogg"
            audio.write_bytes(b"audio")
            record = temp / "record.json"
            record.write_text(json.dumps({
                "output": "整形済みです。",
                "stages": [{
                    "name": "llm",
                    "accepted": True,
                    "model": "test-model",
                    "error": None,
                }],
            }, ensure_ascii=False), encoding="utf-8")
            cli = temp / "fake-cli"
            cli.write_text(
                "#!/bin/sh\n"
                "echo 'this generated text must be discarded'\n"
                f"echo 'record: {record}' >&2\n",
                encoding="utf-8",
            )
            cli.chmod(0o755)
            env = os.environ | {
                "MY_DICTATION_CLI": str(cli),
                "MY_DICTATION_PYTHON": os.sys.executable,
            }
            result = subprocess.run(
                [str(SCRIPT), str(audio)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload, [{
            "audio": str(audio.resolve()),
            "record": str(record),
            "output": "整形済みです。",
            "llm_accepted": True,
            "llm_model": "test-model",
            "llm_error": None,
        }])
        self.assertNotIn("generated text", result.stdout)
        self.assertIn("transcribing:", result.stderr)


if __name__ == "__main__":
    unittest.main()
