import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from my_dictation.cli import main


class CliTests(unittest.TestCase):
    def test_process_text_stdout_is_only_text(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(f'data_dir = "{directory}/data"\n')
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["--config", str(config), "process-text", "三個です"])
            self.assertEqual(code, 0); self.assertEqual(stdout.getvalue(), "3個です\n")
            self.assertIn("record:", stderr.getvalue())

    def test_empty_process_text_fails_without_output_or_record(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(f'data_dir = "{directory}/data"\n')
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["--config", str(config), "process-text", " \t"])
            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("transcription is empty", stderr.getvalue())
            self.assertFalse((Path(directory) / "data" / "records").exists())


if __name__ == "__main__": unittest.main()
