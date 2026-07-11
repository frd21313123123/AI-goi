import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from milana_memory import (
    MAX_DIARY_ENTRY_LENGTH,
    MAX_MESSAGE_LENGTH,
    MilanaMemoryStore,
)


class MilanaMemoryStoreTests(unittest.TestCase):
    def test_history_is_persistent_ordered_limited_and_isolated(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            store = MilanaMemoryStore(path)
            self.assertTrue(
                store.add_message(
                    100,
                    "user",
                    "Привет",
                    telegram_message_id=1,
                    sender_name="Анна",
                )
            )
            store.add_message(100, "assistant", "Привет!", telegram_message_id=1)
            store.add_message(100, "user", "Как дела?", telegram_message_id=2)
            store.add_message(200, "user", "Другой чат", telegram_message_id=1)
            self.assertFalse(
                store.add_message(100, "user", "Дубль", telegram_message_id=1)
            )
            store.close()

            reopened = MilanaMemoryStore(path)
            history = reopened.get_chat_history(100)
            self.assertEqual(
                [(item.role, item.content) for item in history],
                [
                    ("user", "Привет"),
                    ("assistant", "Привет!"),
                    ("user", "Как дела?"),
                ],
            )
            self.assertEqual(
                [item.content for item in reopened.get_chat_history(100, limit=2)],
                ["Привет!", "Как дела?"],
            )
            self.assertEqual(
                [item.content for item in reopened.get_chat_history(200)],
                ["Другой чат"],
            )
            reopened.close()

    def test_diary_is_global_persistent_and_deduplicated(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            store = MilanaMemoryStore(path)
            self.assertTrue(
                store.add_diary_entry(
                    "Анна любит чай",
                    source_chat_id=100,
                    source_message_id=5,
                )
            )
            self.assertFalse(store.add_diary_entry("  анна любит чай  "))
            store.close()

            reopened = MilanaMemoryStore(path)
            entries = reopened.get_diary()
            self.assertEqual([entry.content for entry in entries], ["Анна любит чай"])
            self.assertEqual(entries[0].source_chat_id, "100")
            self.assertIn("Анна любит чай", reopened.diary_instructions())
            reopened.close()

    def test_invalid_values_are_rejected(self) -> None:
        store = MilanaMemoryStore()
        with self.assertRaises(ValueError):
            store.add_message(1, "system", "нет")
        with self.assertRaises(TypeError):
            store.add_message(1, "user", None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            store.add_message(1, "user", "x" * (MAX_MESSAGE_LENGTH + 1))
        with self.assertRaises(ValueError):
            store.add_diary_entry("   ")
        with self.assertRaises(ValueError):
            store.add_diary_entry("x" * (MAX_DIARY_ENTRY_LENGTH + 1))
        store.close()

    def test_empty_store_and_non_positive_limits(self) -> None:
        store = MilanaMemoryStore()

        self.assertFalse(store.has_chat_history("missing"))
        self.assertIsNone(store.latest_telegram_message_id("missing"))
        self.assertEqual(store.get_chat_history("missing", limit=0), [])
        self.assertEqual(store.get_diary(limit=-1), [])
        self.assertIn("Дневник пока пуст", store.diary_instructions())

        store.close()

    def test_latest_message_id_ignores_local_turns_without_telegram_id(self) -> None:
        store = MilanaMemoryStore()
        store.add_message(1, "assistant", "Локальный ответ")
        store.add_message(1, "user", "Первое", telegram_message_id=7)
        store.add_message(1, "user", "Второе", telegram_message_id=11)

        self.assertTrue(store.has_chat_history(1))
        self.assertEqual(store.latest_telegram_message_id(1), 11)

        store.close()

    def test_sender_and_explicit_timestamps_are_normalized_and_preserved(self) -> None:
        store = MilanaMemoryStore()
        store.add_message(
            1,
            "user",
            "  Привет  ",
            sender_name="  Анна  ",
            created_at="2026-07-11T10:00:00+00:00",
        )

        message = store.get_chat_history(1)[0]
        self.assertEqual(message.content, "Привет")
        self.assertEqual(message.sender_name, "Анна")
        self.assertEqual(message.created_at, "2026-07-11T10:00:00+00:00")

        store.close()

    def test_response_input_uses_only_requested_chat(self) -> None:
        store = MilanaMemoryStore()
        store.add_message(1, "user", "Первый", sender_name="Ира")
        store.add_message(1, "assistant", "Ответ")
        store.add_message(2, "user", "Секрет другого чата")

        self.assertEqual(
            store.response_input(1),
            [
                {"role": "user", "content": "Ира: Первый"},
                {"role": "assistant", "content": "Ответ"},
            ],
        )
        store.close()


if __name__ == "__main__":
    unittest.main()
