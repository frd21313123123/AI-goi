import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram_client import load_ai_settings, split_telegram_text


class SplitTelegramTextTests(unittest.TestCase):
    def test_empty_text_returns_no_parts(self) -> None:
        self.assertEqual(split_telegram_text("   \n"), [])

    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(split_telegram_text("  Привет  "), ["Привет"])

    def test_long_unbroken_text_respects_limit(self) -> None:
        parts = split_telegram_text("a" * 8001)
        self.assertEqual([len(part) for part in parts], [4000, 4000, 1])

    def test_load_ai_settings_reads_json_object(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ai_config.json"
            path.write_text('{"model": "test-model", "temperature": 0.2}', encoding="utf-8")

            self.assertEqual(
                load_ai_settings(path),
                {"model": "test-model", "temperature": 0.2},
            )

    def test_load_ai_settings_rejects_non_object(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ai_config.json"
            path.write_text('[]', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON-объект"):
                load_ai_settings(path)


if __name__ == "__main__":
    unittest.main()
