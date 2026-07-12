import asyncio
import base64
import copy
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from agy_provider import AgyError, AgyModelClient, strip_ansi


class StripAnsiTests(unittest.TestCase):
    def test_removes_terminal_sequences_and_normalizes_line_endings(self) -> None:
        value = (
            "\x1b]0;Antigravity\x07"
            "\x1b[32mпервая\x1b[0m\r\n"
            "\x1b[2Kвторая\r"
        )

        self.assertEqual(strip_ansi(value), "первая\nвторая")

    def test_empty_text_stays_empty(self) -> None:
        self.assertEqual(strip_ansi(""), "")


class AgyModelClientTests(unittest.TestCase):
    def test_defaults_match_gemini_flash_configuration(self) -> None:
        client = AgyModelClient()

        self.assertEqual(client.model, "gemini-3.5-flash")
        self.assertEqual(client.timeout_seconds, 300)
        self.assertEqual(client.executable, "agy")
        self.assertTrue(callable(client.responses.create))

    def test_rejects_blank_model_and_non_positive_timeout(self) -> None:
        with self.assertRaises(ValueError):
            AgyModelClient(model="   ")
        with self.assertRaises(ValueError):
            AgyModelClient(timeout_seconds=0)

    def test_error_details_hide_oauth_url_and_report_auth_failure(self) -> None:
        details = AgyModelClient._safe_error_details(
            "Authentication required. Visit https://accounts.google.com/secret\n"
            "Error: authentication timed out."
        )

        self.assertEqual(
            details,
            "Antigravity CLI не авторизован или срок авторизации истёк",
        )
        self.assertNotIn("https://", details)

    def test_missing_executable_is_reported_as_agy_error(self) -> None:
        client = AgyModelClient(executable="missing-agy")
        with (
            patch("agy_provider.platform.system", return_value="Linux"),
            patch.object(client, "_run_direct", side_effect=FileNotFoundError),
        ):
            with self.assertRaisesRegex(AgyError, "не найдена"):
                client._query({"input": []})

    def test_timeout_is_reported_as_agy_error(self) -> None:
        client = AgyModelClient(timeout_seconds=17)
        timeout = subprocess.TimeoutExpired(["agy"], 17)
        with (
            patch("agy_provider.platform.system", return_value="Linux"),
            patch.object(client, "_run_direct", side_effect=timeout),
        ):
            with self.assertRaisesRegex(AgyError, "17 секунд"):
                client._query({"input": []})

    def test_command_uses_selected_model_and_keeps_prompt_last(self) -> None:
        client = AgyModelClient(model="gemini-3.5-flash", timeout_seconds=42)
        workspace = Path("temporary-workspace")

        command = client._command("короткий prompt", workspace)

        self.assertEqual(command[:3], ["agy", "--model", "gemini-3.5-flash"])
        self.assertIn("--sandbox", command)
        self.assertIn("--dangerously-skip-permissions", command)
        self.assertEqual(command[-2:], ["-p", "короткий prompt"])
        self.assertIn("42s", command)

    def test_launcher_prompt_contains_absolute_request_path(self) -> None:
        client = AgyModelClient()
        with TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"

            prompt = client._launcher_prompt(request_path, structured=True)

        self.assertIn(f'"{request_path.resolve().as_posix()}"', prompt)
        self.assertIn("Return only one JSON object", prompt)

    def test_request_payload_materializes_image_data_url(self) -> None:
        client = AgyModelClient()
        image_bytes = b"\x89PNG\r\n\x1a\nimage"
        request = {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": (
                                "data:image/png;base64,"
                                + base64.b64encode(image_bytes).decode("ascii")
                            ),
                        }
                    ],
                }
            ]
        }

        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            payload = client._request_payload(request, workspace)
            image_item = payload["input"][0]["content"][0]

            self.assertNotIn("image_url", image_item)
            self.assertEqual(image_item["mime_type"], "image/png")
            local_path = Path(image_item["local_path"])
            self.assertTrue(local_path.is_absolute())
            self.assertEqual(local_path.read_bytes(), image_bytes)

    def test_request_payload_adds_diary_output_without_mutating_request(self) -> None:
        client = AgyModelClient()
        request = {
            "instructions": "Системная инструкция",
            "input": [{"role": "user", "content": "Запомни это"}],
            "tools": [
                {
                    "type": "function",
                    "name": "write_diary",
                    "description": "Добавить запись в дневник",
                    "parameters": {
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "telegram_reply",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "messages": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "reaction": {"type": ["string", "null"]},
                        },
                        "required": ["messages", "reaction"],
                        "additionalProperties": False,
                    },
                }
            },
        }
        original = copy.deepcopy(request)

        with TemporaryDirectory() as directory:
            payload = client._request_payload(request, Path(directory))

        self.assertEqual(request, original)
        response_schema = payload["response_format"]["schema"]
        self.assertEqual(
            response_schema["properties"]["diary_entries"],
            {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
            },
        )
        self.assertIn("diary_entries", response_schema["required"])
        self.assertIn("diary_entries", payload["instructions"])

    @staticmethod
    def _fake_winpty_module(process: SimpleNamespace) -> tuple[Any, MagicMock]:
        spawn = MagicMock(return_value=process)
        module = SimpleNamespace(PtyProcess=SimpleNamespace(spawn=spawn))
        return module, spawn

    @staticmethod
    def _alive_process() -> tuple[SimpleNamespace, dict[str, bool]]:
        state = {"alive": True}
        pty = SimpleNamespace(isalive=MagicMock(side_effect=lambda: state["alive"]))
        process = SimpleNamespace(
            pty=pty,
            fileobj=object(),
            exitstatus=0,
            read=MagicMock(),
            terminate=MagicMock(
                side_effect=lambda force=False: state.__setitem__("alive", False)
            ),
            close=MagicMock(),
        )
        return process, state

    def test_windows_pty_times_out_without_output_or_blocking_read(self) -> None:
        client = AgyModelClient(timeout_seconds=1)
        process, _ = self._alive_process()
        winpty_module, spawn = self._fake_winpty_module(process)

        with (
            TemporaryDirectory() as directory,
            patch.dict(sys.modules, {"winpty": winpty_module}),
            patch("agy_provider.select.select", return_value=([], [], [])) as select_call,
            patch("agy_provider.time.monotonic", side_effect=[0.0, 0.0, 12.0]),
        ):
            workspace = Path(directory)
            with self.assertRaises(subprocess.TimeoutExpired):
                client._run_windows(["agy"], workspace)

        spawn.assert_called_once_with(["agy"], cwd=str(workspace))
        select_call.assert_called_once()
        process.read.assert_not_called()
        process.terminate.assert_called_once_with(force=True)
        process.close.assert_called_once_with(force=True)

    def test_windows_pty_cancellation_terminates_process_without_reading(self) -> None:
        client = AgyModelClient(timeout_seconds=10)
        process, _ = self._alive_process()
        winpty_module, _ = self._fake_winpty_module(process)
        cancel_event = threading.Event()
        cancel_event.set()

        with (
            TemporaryDirectory() as directory,
            patch.dict(sys.modules, {"winpty": winpty_module}),
            patch("agy_provider.select.select") as select_call,
            patch("agy_provider.time.monotonic", side_effect=[0.0, 0.0]),
        ):
            with self.assertRaisesRegex(AgyError, "отменён"):
                client._run_windows(["agy"], Path(directory), cancel_event)

        select_call.assert_not_called()
        process.read.assert_not_called()
        process.terminate.assert_called_once_with(force=True)
        process.close.assert_called_once_with(force=True)

    def test_windows_pty_nonzero_exit_uses_agy_log_diagnostic(self) -> None:
        client = AgyModelClient(timeout_seconds=10)
        pty = SimpleNamespace(isalive=MagicMock(return_value=False))
        process = SimpleNamespace(
            pty=pty,
            fileobj=object(),
            exitstatus=7,
            read=MagicMock(),
            terminate=MagicMock(),
            close=MagicMock(),
        )
        winpty_module, _ = self._fake_winpty_module(process)

        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "agy.log").write_text(
                "E0000 log.go:398] model output error: request.json not found\n",
                encoding="utf-8",
            )
            with (
                patch.dict(sys.modules, {"winpty": winpty_module}),
                patch("agy_provider.select.select", return_value=([], [], [])),
                patch(
                    "agy_provider.time.monotonic",
                    side_effect=[0.0, 0.0, 0.31],
                ),
            ):
                with self.assertRaisesRegex(
                    AgyError, "кодом 7: model output error: request.json not found"
                ):
                    client._run_windows(["agy"], workspace)

        process.read.assert_not_called()
        process.terminate.assert_not_called()
        process.close.assert_called_once_with(force=True)


class AgyResponsesTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_delegates_blocking_query_to_thread(self) -> None:
        client = AgyModelClient()
        request = {"model": "ignored-by-adapter", "input": [{"role": "user"}]}
        client._query = MagicMock()  # type: ignore[method-assign]

        with patch(
            "agy_provider.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value="готовый ответ",
        ) as to_thread:
            response = await client.responses.create(**request)

        to_thread.assert_awaited_once()
        query, submitted_request, cancel_event = to_thread.await_args.args
        self.assertIs(query, client._query)
        self.assertEqual(submitted_request, request)
        self.assertIsInstance(cancel_event, threading.Event)
        self.assertFalse(cancel_event.is_set())
        client._query.assert_not_called()
        self.assertEqual(response.output_text, "готовый ответ")
        self.assertEqual(response.output, [])
        self.assertEqual(response.status, "completed")

    async def test_plain_request_preserves_fenced_json_as_plain_text(self) -> None:
        client = AgyModelClient()
        raw = '```json\n{"messages":["привет"],"reaction":null}\n```'
        client._query = MagicMock(return_value=raw)  # type: ignore[method-assign]

        response = await client.responses.create(input=[])

        self.assertEqual(response.output_text, raw)

    async def test_structured_request_normalizes_fenced_json(self) -> None:
        client = AgyModelClient()
        client._query = MagicMock(  # type: ignore[method-assign]
            return_value=(
                "```json\n"
                '{"messages":["привет","как дела?"],"reaction":"👍"}'
                "\n```"
            )
        )

        response = await client.responses.create(input=[], text={"format": {}})

        self.assertEqual(
            json.loads(response.output_text),
            {"messages": ["привет", "как дела?"], "reaction": "👍"},
        )

    async def test_structured_diary_entries_are_exposed_outside_output_text(self) -> None:
        client = AgyModelClient()
        client._query = MagicMock(  # type: ignore[method-assign]
            return_value=json.dumps(
                {
                    "messages": ["запомнила"],
                    "reaction": None,
                    "diary_entries": ["  Лена любит зелёный чай  ", "", "живёт в Перми"],
                },
                ensure_ascii=False,
            )
        )

        response = await client.responses.create(input=[], text={"format": {}})

        self.assertEqual(
            response.agy_diary_entries,
            ("Лена любит зелёный чай", "живёт в Перми"),
        )
        self.assertEqual(
            json.loads(response.output_text),
            {"messages": ["запомнила"], "reaction": None},
        )
        self.assertNotIn("diary_entries", response.output_text)

    async def test_structured_request_wraps_unstructured_answer(self) -> None:
        client = AgyModelClient()
        client._query = MagicMock(  # type: ignore[method-assign]
            return_value="обычный ответ без json"
        )

        response = await client.responses.create(input=[], text={"format": {}})

        self.assertEqual(
            json.loads(response.output_text),
            {"messages": ["обычный ответ без json"], "reaction": None},
        )

    async def test_structured_read_only_sentinel_becomes_empty_reply(self) -> None:
        client = AgyModelClient()
        client._query = MagicMock(  # type: ignore[method-assign]
            return_value="[[READ_ONLY]]"
        )

        response = await client.responses.create(input=[], text={"format": {}})

        self.assertEqual(
            json.loads(response.output_text),
            {"messages": [], "reaction": None},
        )

    async def test_empty_plain_answer_raises_agy_error(self) -> None:
        client = AgyModelClient()
        client._query = MagicMock(return_value=" \r\n ")  # type: ignore[method-assign]

        with self.assertRaisesRegex(AgyError, "пустой ответ"):
            await client.responses.create(input=[])

    async def test_event_loop_keeps_running_while_query_is_in_thread(self) -> None:
        import threading

        client = AgyModelClient()
        started = asyncio.Event()
        release = threading.Event()

        def blocking_query(request, cancel_event):
            del request
            del cancel_event
            loop.call_soon_threadsafe(started.set)
            release.wait(timeout=2)
            return "ответ"

        loop = asyncio.get_running_loop()
        client._query = blocking_query  # type: ignore[method-assign]
        task = asyncio.create_task(client.responses.create(input=[]))
        await asyncio.wait_for(started.wait(), timeout=1)

        # This coroutine can still make progress while the provider is blocked.
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        response = await asyncio.wait_for(task, timeout=1)
        self.assertEqual(response.output_text, "ответ")

    async def test_cancelling_create_signals_blocking_query(self) -> None:
        client = AgyModelClient()
        started = asyncio.Event()
        cancellation_seen = threading.Event()
        loop = asyncio.get_running_loop()

        def blocking_query(request, cancel_event):
            del request
            loop.call_soon_threadsafe(started.set)
            if not cancel_event.wait(timeout=2):
                raise AssertionError("cancel_event не был установлен")
            cancellation_seen.set()
            raise AgyError("Запрос Gemini отменён")

        client._query = blocking_query  # type: ignore[method-assign]
        task = asyncio.create_task(client.responses.create(input=[]))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)

        self.assertTrue(cancellation_seen.is_set())


if __name__ == "__main__":
    unittest.main()
