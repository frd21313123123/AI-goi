import unittest

from telegram_client import split_telegram_text


class SplitTelegramTextTests(unittest.TestCase):
    def test_empty_text_returns_no_parts(self) -> None:
        self.assertEqual(split_telegram_text("   \n"), [])

    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(split_telegram_text("  Привет  "), ["Привет"])

    def test_long_unbroken_text_respects_limit(self) -> None:
        parts = split_telegram_text("a" * 8001)
        self.assertEqual([len(part) for part in parts], [4000, 4000, 1])


if __name__ == "__main__":
    unittest.main()
