from copy import deepcopy
from asyncio import CancelledError
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from lite_handoff_router.context_policy import ContextPolicy
from lite_handoff_router.observation import observe_response
from lite_handoff_router.shared_summary import (ProviderContextOverflow, SharedSummary, check_initial_stream,
                                                last_prompt_tokens, prompt_token_count)
from tests.test_history import turn


class SharedSummaryTest(IsolatedAsyncioTestCase):
    def test_agent_token_count_uses_the_latest_sample_for_the_same_agent_and_model(self):
        state = {
            "historical_agent_usage": {
                ("a", "base"): {"prompt_tokens": 43000, "summary_key": None},
                ("b", "base"): {"prompt_tokens": 1200, "summary_key": None},
            },
            "agent_usage": {("a", "base"): {"prompt_tokens": None, "summary_key": "new-summary"}},
        }
        self.assertIsNone(last_prompt_tokens(state, "base", agent_id="a", summary_key="new-summary"))
        self.assertIsNone(last_prompt_tokens(state, "base", agent_id="a", summary_key=None))
        self.assertIsNone(last_prompt_tokens(state, "base", agent_id="b", summary_key="new-summary"))
        self.assertEqual(last_prompt_tokens(state, "base", agent_id="b", summary_key=None), 1200)
        self.assertIsNone(last_prompt_tokens(state, "other-base", agent_id="a", summary_key=None))

    async def test_router_uses_only_the_last_reported_input_usage(self):
        async def stream(report_usage):
            yield b'data: {"choices":[{"finish_reason":"stop"}]}\n\n'
            if report_usage:
                yield b'data: {"choices":[],"usage":{"prompt_tokens":900,"completion_tokens":300}}\n\n'
            yield b'data: [DONE]\n\n'

        state = {"historical_router_usage": {
            "llm": {"prompt_tokens": 1000, "summary_key": None},
            "legacy": {"prompt_tokens": 2000},
        }}
        self.assertEqual(last_prompt_tokens(state, "llm", agent_id=None, summary_key=None), 1000)
        self.assertIsNone(last_prompt_tokens(state, "legacy", agent_id=None, summary_key=None))
        for report_usage, expected in ((True, 900), (False, None)):
            usage = AsyncMock()
            response = observe_response(SimpleNamespace(body_iterator=stream(report_usage)),
                                        state, {"kind": "router"}, on_usage=usage)
            self.assertEqual(len([chunk async for chunk in response.body_iterator]), 3 if report_usage else 2)
            tokens = prompt_token_count(usage.await_args.args[0])
            state["router_usage"] = {"llm": {"prompt_tokens": tokens, "summary_key": None}}
            self.assertEqual(last_prompt_tokens(state, "llm", agent_id=None, summary_key=None), expected)

    async def test_shared_checkpoint_preserves_current_tool_chain_and_parallel_packet(self):
        source = turn("a", "PRIVATE_RESULT")
        question = {"role": "user", "content": "New question"}
        packet = {"role": "user", "content": "Confirmed parallel task results. Worker data."}
        current_tool = {"role": "tool", "tool_call_id": "fresh", "content": "CURRENT_RESULT"}
        source += [question, current_tool]
        state = {"source": source, "user_boundary": 6, "audit": [],
                 "context_policy": ContextPolicy(shared_summary_model_id="summary").model_dump()}
        adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                  generate_shared_summary=AsyncMock(return_value="Common summary"),
                                  save_shared_summary=AsyncMock())
        emitter = AsyncMock()
        summary = SharedSummary(adapter, None, None, state, emitter=emitter)
        await summary.load()
        view = {"messages": source[:6] + [packet, question, current_tool]}
        original = deepcopy(view["messages"])
        self.assertTrue(await summary.extend(view, packet, original, trigger="Порог"))
        self.assertEqual(view["messages"][1:], [packet, question, current_tool])
        self.assertEqual(state["source"], source)
        summary_input = adapter.generate_shared_summary.await_args.args[3]
        self.assertIn("Visible answer a", str(summary_input))
        self.assertNotIn("PRIVATE_RESULT", str(summary_input))
        summary_start = emitter.await_args_list[0].args[0]["data"]["description"]
        self.assertTrue(summary_start.startswith("Сжимаю контекст… Порог. Модель summary. Ходов 1, символов "))
        self.assertEqual([event.args[0] for event in emitter.await_args_list], [
            {"type": "status", "data": {"action": "lite_shared_summary", "description": summary_start,
                                        "done": False, "error": False}},
            {"type": "status", "data": {"action": "lite_shared_summary", "description": "Контекст сокращён. Ходов в общей сводке 1.",
                                        "done": True, "error": False}},
        ])
        receiver = SharedSummary(adapter, None, None, state, emitter=emitter)
        await receiver.load()
        new_view = {"messages": deepcopy(original)}
        self.assertTrue(receiver.apply(new_view, packet))
        self.assertEqual(new_view["messages"], view["messages"])
        self.assertTrue(summary.applied_summary_key())
        self.assertEqual(receiver.applied_summary_key(), summary.applied_summary_key())
        self.assertFalse(await receiver.extend(new_view, packet, original, trigger="Порог"))
        self.assertEqual(emitter.await_count, 3)
        self.assertIn("Сжатие пропущено", emitter.await_args.args[0]["data"]["description"])

    async def test_summary_share_uses_estimated_tokens_and_whole_turns(self):
        first = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b" * 12}]
        second = [{"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}]
        third = [{"role": "user", "content": "e"}, {"role": "assistant", "content": "f"}]
        current = {"role": "user", "content": "current"}
        past = first + second + third
        source = past + [current]
        for settings, covered, estimated, budget in (
            ({}, first, 12, 19),
            ({"shared_summary_context_percent": 1}, first, 12, 0),
            ({"shared_summary_context_percent": 80}, first + second, 22, 25),
            ({"shared_summary_context_percent": 100}, past, 32, 32),
        ):
            with self.subTest(settings=settings):
                state = {"source": source, "user_boundary": len(past), "audit": [],
                         "context_policy": ContextPolicy(shared_summary_model_id="summary", **settings).model_dump()}
                emitter = AsyncMock()
                adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                          generate_shared_summary=AsyncMock(return_value="Summary"),
                                          save_shared_summary=AsyncMock())
                summary = SharedSummary(adapter, None, None, state, emitter=emitter)
                await summary.load()
                view = {"messages": deepcopy(source)}
                self.assertTrue(await summary.extend(view, None, source, trigger="Порог"))
                adapter.generate_shared_summary.assert_awaited_once_with(
                    None, None, "summary", covered, None)
                self.assertEqual(view["messages"][1:], source[len(covered):])
                status = emitter.await_args_list[0].args[0]["data"]["description"]
                self.assertIn(f"Токенов по оценке Open WebUI {estimated} из 32.", status)
                self.assertIn(f"Целевой объём {budget}.", status)

    async def test_summary_status_closes_on_failure_and_cancellation(self):
        for failure, text, error in (
            (RuntimeError(), "Не удалось сократить контекст. Ошибка запроса к модели суммаризации (RuntimeError).", True),
            (TimeoutError(), "Не удалось сократить контекст. Модель суммаризации не ответила за 45 секунд.", True),
            (AttributeError("private provider detail", name="get", obj=[]),
             "Не удалось сократить контекст. Ошибка Open WebUI (AttributeError: list.get).", True),
            (RuntimeError('{"error":{"message":"Model missing at https://private.example/v1. '
                          'api_key=test-secret; request body: private chat"}}'),
             "Не удалось сократить контекст. Ошибка запроса к модели суммаризации (RuntimeError).", True),
            (CancelledError(), "Сжатие контекста отменено", False),
        ):
            with self.subTest(failure=type(failure).__name__):
                messages = turn("a", "result") + [{"role": "user", "content": "New question"}]
                state = {"source": messages, "user_boundary": 6, "audit": [],
                         "context_policy": ContextPolicy(shared_summary_model_id="summary").model_dump()}
                adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                          generate_shared_summary=AsyncMock(side_effect=failure))
                emitter = AsyncMock()
                summary = SharedSummary(adapter, None, None, state, emitter=emitter)
                await summary.load()
                operation = summary.extend({"messages": deepcopy(messages)}, None, messages, trigger="Порог")
                if isinstance(failure, CancelledError):
                    with self.assertRaises(CancelledError):
                        await operation
                else:
                    self.assertFalse(await operation)
                self.assertEqual([event.args[0]["data"]["done"] for event in emitter.await_args_list], [False, True])
                status = emitter.await_args.args[0]["data"]
                self.assertEqual((status["action"], status["done"], status["error"]),
                                 ("lite_shared_summary", True, error))
                self.assertTrue(status["description"].startswith(text))
                if not isinstance(failure, CancelledError):
                    self.assertIn("Подробности", status["description"])
                if isinstance(failure, AttributeError):
                    self.assertIn("private provider detail", status["description"])
                if str(failure).startswith("{"):
                    self.assertIn("Model missing", status["description"])
                    self.assertRegex(status["description"], r"\.py:\d+")
                    for hidden in ("private.example", "test-secret", "private chat"):
                        self.assertNotIn(hidden, status["description"])

    async def test_stream_overflow_is_recoverable_only_before_content(self):
        async def stream(*events):
            for event in events:
                yield event

        role = b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        content = b'data: {"choices":[{"delta":{"content":"Result"}}]}\n\n'
        overflow = (b'data: {"error":{"message":"The input (2049 tokens) is longer than '
                    b"the model's context length (2048 tokens)."+b'"}}\n\n')
        with self.assertRaises(ProviderContextOverflow):
            await check_initial_stream(SimpleNamespace(body_iterator=stream(role, overflow)))
        response = await check_initial_stream(SimpleNamespace(body_iterator=stream(role, content, overflow)))
        self.assertEqual([chunk async for chunk in response.body_iterator], [role, content, overflow])
