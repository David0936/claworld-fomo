import io
import tempfile
import unittest
from pathlib import Path

import update_checker


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class UpdateCheckerTests(unittest.TestCase):
    def test_detects_newer_version(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "VERSION").write_text("0.1.0")
            result = update_checker.check(
                directory, opener=lambda *_args, **_kwargs: Response(b"0.2.0\n"), now=lambda: 12
            )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["checked_at"], 12)

    def test_rejects_untrusted_version_text(self):
        with self.assertRaises(ValueError):
            update_checker.parse_version("1.2.3-beta")

