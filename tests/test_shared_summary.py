from copy import deepcopy
from asyncio import CancelledError
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from lite_handoff_router.agent_history_block import pack_agent_history
from lite_handoff_router.context_budget import estimate_context_budget
from lite_handoff_router.context_policy import ContextPolicy
from lite_handoff_router.history import agent_projection, router_projection
from lite_handoff_router.observation import check_initial_stream, observe_response
from lite_handoff_router.prompt_usage import last_prompt_tokens, prompt_token_count, restore_prompt_usage
from lite_handoff_router.provider_errors import ProviderContextOverflow
from lite_handoff_router.shared_summary import SharedSummary
from lite_handoff_router.summary_response import SummaryGenerationError, summary_response_text
from tests.test_history import turn


class SharedSummaryTest(IsolatedAsyncioTestCase):
    async def test_summary_body_formats_preserve_utf8_and_keep_the_last_snapshot(self):
        events = [
            {"choices": [{"delta": {"content": "Дельта"}}]},
            {"choices": [{"message": {"content": "Старый снимок"}}]},
            {"choices": [{"message": {"content": "Итог 🧪"}, "finish_reason": "stop"}]},
        ]
        lines = [json.dumps(event, ensure_ascii=False).encode() for event in events]
        bodies = {
            "NDJSON": b"\n".join(lines),
            "JSON array": b"[" + b",\n".join(lines) + b"]",
            "SSE": b"".join(b"data: " + line + b"\n\n" for line in lines) + b"data: [DONE]\n\n",
        }
        self.assertEqual(await summary_response_text(events, convert_responses=None), "Итог 🧪")
        for name, body in bodies.items():
            with self.subTest(format=name):
                closed = []
                split = body.index("🧪".encode()) + 2

                async def chunks():
                    try:
                        yield body[:split]
                        yield body[split:]
                        if name == "SSE":
                            self.fail("Chunks after [DONE] must not be read")
                    finally:
                        closed.append(True)

                self.assertEqual(await summary_response_text(chunks(), convert_responses=None), "Итог 🧪")
                self.assertEqual(closed, [True])

    async def test_summary_unframed_events_preserve_provider_error_and_require_completion(self):
        body = b'{"error":{"code":"context_length_exceeded","message":"Context length exceeded"}}'
        with self.assertRaises(ProviderContextOverflow):
            await summary_response_text(body, convert_responses=None)
        first = b'{"choices":[{"delta":{"content":"One"}}]}'
        last = b'{"choices":[{"delta":{"content":" two"},"finish_reason":"stop"}]}'
        self.assertEqual(await summary_response_text(first + b"\n" + last, convert_responses=None), "One two")
        with self.assertRaisesRegex(SummaryGenerationError, "JSON-событий без SSE: 1"):
            await summary_response_text(b"[" + first + b"]", convert_responses=None)

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
        async def stream(report_usage, trailing_newline):
            yield b'data: {"choices":[{"finish_reason":"stop"}]}\n\n'
            if report_usage:
                yield b'data: {"choices":[],"usage":{"prompt_tokens":900,"completion_tokens":300}}' + trailing_newline
            if trailing_newline:
                yield b'data: [DONE]\n\n'

        state = {"historical_router_usage": {
            "llm": {"prompt_tokens": 1000, "summary_key": None},
            "legacy": {"prompt_tokens": 2000},
        }}
        self.assertEqual(last_prompt_tokens(state, "llm", agent_id=None, summary_key=None), 1000)
        self.assertIsNone(last_prompt_tokens(state, "legacy", agent_id=None, summary_key=None))
        for report_usage, ending, expected_chunks, expected in (
            (True, b"\n\n", 3, 900), (True, b"", 2, 900), (False, b"\n\n", 2, None),
        ):
            usage = AsyncMock()
            response = observe_response(SimpleNamespace(body_iterator=stream(report_usage, ending)),
                                        state, {"kind": "router"}, on_usage=usage)
            self.assertEqual(len([chunk async for chunk in response.body_iterator]), expected_chunks)
            tokens = prompt_token_count(usage.await_args.args[0])
            state["router_usage"] = {"llm": {"prompt_tokens": tokens, "summary_key": None}}
            self.assertEqual(last_prompt_tokens(state, "llm", agent_id=None, summary_key=None), expected)

    def test_saved_usage_ignores_malformed_entries_and_uses_current_agent_only_on_resume(self):
        state = {"chat_id": "chat", "message_id": "current"}
        earlier = {"agent_id": "a", "model": "base", "prompt_tokens": 200, "summary_key": None}
        later = {**earlier, "prompt_tokens": 300}
        branch = [
            {"id": "previous", "role": "assistant", "meta": {"lite_agent_usage": [None, earlier]}},
            {"id": "current", "role": "assistant", "meta": {
                "lite_agent_usage": [later],
                "lite_router_usage": {"model": "llm", "prompt_tokens": 400, "summary_key": None},
            }},
        ]
        for resume, tokens in ((None, 200), (("chat", "current"), 300)):
            with self.subTest(resume=resume):
                restore_prompt_usage(state, branch, resume=resume)
                self.assertEqual(last_prompt_tokens(state, "base", agent_id="a", summary_key=None), tokens)
                self.assertIsNone(last_prompt_tokens(state, "llm", agent_id=None, summary_key=None))

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
        self.assertTrue(summary_start.startswith("Сжимаю контекст… Порог. Модель summary. Ходов 1."))
        before = estimate_context_budget({"messages": original}).total
        after = estimate_context_budget(view).total
        self.assertEqual([event.args[0] for event in emitter.await_args_list], [
            {"type": "status", "data": {"action": "lite_shared_summary", "description": summary_start,
                                        "done": False, "error": False}},
            {"type": "status", "data": {"action": "lite_shared_summary",
                                        "description": ("Контекст сокращён. Ходов в общей сводке 1. "
                                                        f"Оценка входа: {before} → {after} токенов."),
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
        self.assertIn("Все доступные старые ходы уже входят в сводку", emitter.await_args.args[0]["data"]["description"])

    async def test_skip_status_explains_why_no_turn_is_eligible(self):
        current = {"role": "user", "content": "Current"}
        past = {"role": "user", "content": "Past"}
        for old, projected, expected in (
            ([], [current], "ещё нет старых ходов"),
            ([past], [{**past, "content": "Rewritten"}, current], "отличается от исходной"),
        ):
            with self.subTest(expected=expected):
                source = [*old, current]
                state = {"source": source, "user_boundary": len(old), "audit": [],
                         "context_policy": ContextPolicy(shared_summary_model_id="summary").model_dump()}
                adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                          generate_shared_summary=AsyncMock())
                emitter = AsyncMock()
                summary = SharedSummary(adapter, None, None, state, emitter=emitter)
                await summary.load()
                messages = source if projected is None else projected
                view = {"messages": deepcopy(messages)}
                self.assertFalse(await summary.extend(view, None, messages, trigger="Порог"))
                self.assertEqual(view["messages"], messages)
                self.assertIn(expected, emitter.await_args.args[0]["data"]["description"])
                adapter.generate_shared_summary.assert_not_awaited()

    async def test_budget_skip_distinguishes_protected_results_from_rewritten_history(self):
        history = turn("a", "PRIVATE_RESULT")
        current = {"role": "user", "content": "Current"}
        source = history + [current]
        for policy_state, projected, expected in (
            ({"shared_summary_protected": [history[4]]}, source, "Ход 1 содержит защищённый штатный контекст"),
            ({"shared_summary_unchanged_turns": 0}, source, "Фильтр агента изменил историю с хода 1"),
            ({}, history + [{**current, "content": "Rewritten"}], "Вопрос после хода 1 отличается"),
            ({}, history, "В запросе нет пользовательского сообщения после хода 1"),
        ):
            with self.subTest(expected=expected):
                state = {"source": source, "user_boundary": len(history), "audit": [],
                         "context_policy": ContextPolicy(shared_summary_model_id="summary").model_dump(),
                         **policy_state}
                adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                          generate_shared_summary=AsyncMock())
                emitter = AsyncMock()
                summary = SharedSummary(adapter, None, None, state, emitter=emitter)
                view = {"messages": deepcopy(projected)}
                await summary.load(view)
                reason = "Бюджет запроса, оценка 266103 токенов при лимите 197952"
                self.assertFalse(await summary.extend(view, None, projected, trigger=reason))
                status = emitter.await_args.args[0]["data"]["description"]
                self.assertIn(reason, status)
                self.assertIn(expected, status)
                self.assertNotIn("PRIVATE_RESULT", status)
                self.assertEqual(view["messages"], projected)
                adapter.generate_shared_summary.assert_not_awaited()

    async def test_first_turn_media_and_visualizer_compact_into_one_summary_for_router_and_agent(self):
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,OLD_IMAGE"}}
        old = turn("a", "PRIVATE_RAW_TOOL_RESULT")
        old[0]["content"] = [{"type": "text", "text": "Посмотри фото WH-8031"}, image]
        old[0]["files"] = [{"id": "OLD_USER_ATTACHMENT"}]
        old[-2]["files"] = [{"id": "OLD_TOOL_ATTACHMENT"}]
        old[-1]["content"] = (
            "Доступно 15 единиц.\n@@@VIZ-START\n<svg>PRIVATE_CHART_SOURCE</svg>\n@@@VIZ-END\n"
            "Следующий шаг: сверить склад."
        )
        old[-1]["embeds"] = ["PRIVATE_IFRAME"]
        old.insert(-1, {"role": "user", "content": [
            {"type": "text", "text": "Here are the images from the tool results above. Please analyze them."},
            image,
        ]})
        more = [{"role": "user", "content": "Запомни дату 18.11.2026"},
                {"role": "assistant", "content": "Дата подтверждена."}]
        current = {"role": "user", "content": [
            {"type": "text", "text": "Проверь новое изображение"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,CURRENT_IMAGE"}},
        ]}
        state = {"source": old + more + [current], "user_boundary": len(old + more), "audit": [],
                 "context_policy": ContextPolicy(shared_summary_model_id="summary",
                                                shared_summary_context_percent=100).model_dump()}
        original = deepcopy(state["source"])
        adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                  save_shared_summary=AsyncMock(),
                                  generate_shared_summary=AsyncMock(return_value="Сводка о складе"))
        router_messages = router_projection(state, set(), set())
        router = {"messages": deepcopy(router_messages)}
        summary = SharedSummary(adapter, None, None, state)
        await summary.load(router)
        self.assertTrue(await summary.extend(router, None, router_messages, trigger="Бюджет"))
        self.assertEqual(summary.applied_turns, 2)
        supplied = str(adapter.generate_shared_summary.await_args.args[3])
        for text in ("WH-8031", "Доступно 15", "Визуализация", "Следующий шаг",
                     "В исходном ходе были медиа или вложения", "Дата подтверждена"):
            self.assertIn(text, supplied)
        for raw in ("OLD_IMAGE", "OLD_USER_ATTACHMENT", "OLD_TOOL_ATTACHMENT", "PRIVATE_IFRAME",
                    "PRIVATE_CHART_SOURCE", "PRIVATE_RAW_TOOL_RESULT", "CURRENT_IMAGE"):
            self.assertNotIn(raw, supplied)
        self.assertEqual(router["messages"][-1], current)
        self.assertIn("[Shared conversation summary]", router["messages"][0]["content"])

        agent = {"messages": agent_projection(state, "a", {"lookup"}, set())}
        shared = SharedSummary(adapter, None, None, state)
        await shared.load(agent)
        self.assertTrue(shared.apply(agent, None))
        pack_agent_history(agent["messages"], state, current, covered_turns=shared.applied_turns)
        self.assertEqual(agent["messages"][-1], current)
        self.assertIn("[Conversation history]", agent["messages"][0]["content"])
        self.assertIn("Сводка о складе", agent["messages"][0]["content"])
        self.assertEqual(state["source"], original)
        adapter.generate_shared_summary.assert_awaited_once()

    async def test_summary_share_rounds_up_to_a_whole_turn(self):
        first = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b" * 12}]
        second = [{"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}]
        third = [{"role": "user", "content": "e"}, {"role": "assistant", "content": "f"}]
        current = {"role": "user", "content": "current"}
        past = first + second + third
        source = past + [current]
        for settings, covered in (
            ({}, first + second),
            ({"shared_summary_context_percent": 1}, first),
            ({"shared_summary_context_percent": 80}, past),
            ({"shared_summary_context_percent": 100}, past),
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
                self.assertIn("Убираемая история:", status)
                self.assertIn(f"({summary.context_percent}%).", status)

    async def test_large_tool_result_counts_in_working_history_without_capping_summary_input(self):
        first = [{"role": "user", "content": "First"}, {"role": "assistant", "content": "a" * 1200}]
        second = [
            {"role": "user", "content": "Run query"},
            {"role": "assistant", "tool_calls": [
                {"id": "t", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t", "content": "result" * 13000},
            {"role": "assistant", "content": "Query finished"},
        ]
        current = {"role": "user", "content": "New"}
        for percent, third_size in ((60, 50), (100, 65000)):
            with self.subTest(percent=percent):
                third = [{"role": "user", "content": "x" * third_size},
                         {"role": "assistant", "content": "Reply"}]
                source = first + second + third + [current]
                state = {"source": source, "user_boundary": len(source) - 1, "audit": [],
                         "context_policy": ContextPolicy(shared_summary_model_id="summary",
                                                         shared_summary_context_percent=percent).model_dump()}
                adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                          generate_shared_summary=AsyncMock(return_value="Summary"),
                                          save_shared_summary=AsyncMock())
                summary = SharedSummary(adapter, None, None, state)
                await summary.load()
                view = {"messages": deepcopy(source)}
                self.assertTrue(await summary.extend(view, None, source, trigger="Порог"))
                material = adapter.generate_shared_summary.await_args.args[3]
                self.assertIn("Query finished", str(material))
                self.assertNotIn(second[2]["content"], str(material))
                self.assertEqual(view["messages"][1:], third + [current] if percent == 60 else [current])
                if percent == 100:
                    self.assertIn(third[0], material)

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
        with self.assertRaises(ProviderContextOverflow):
            await check_initial_stream(SimpleNamespace(body_iterator=stream(role, overflow.rstrip())))
        response = await check_initial_stream(SimpleNamespace(body_iterator=stream(role, content, overflow)))
        self.assertEqual([chunk async for chunk in response.body_iterator], [role, content, overflow])

    async def test_shared_input_excludes_private_results_skills_native_context_and_group_packet(self):
        first = turn("a", "PRIVATE_A")
        second = turn("b", "PRIVATE_B")
        second.extend([
            {"role": "assistant", "tool_calls": [
                {"id": "s", "type": "function", "function": {"name": "view_skill", "arguments": "{}"}},
                {"id": "n", "type": "function", "function": {"name": "recall_memories", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "s", "content": "WORK_SKILL_LIMITED"},
            {"role": "tool", "tool_call_id": "n", "content": "NATIVE_PRIVATE"},
        ])
        past = first + second
        question = {"role": "user", "content": "Current question"}
        packet = {"role": "user", "content": "Confirmed parallel task results. GROUP_PRIVATE"}
        state = {"source": past + [question], "user_boundary": len(past), "audit": [],
                 "context_policy": ContextPolicy(shared_summary_model_id="summary",
                                                shared_summary_context_percent=100).model_dump()}
        adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                  save_shared_summary=AsyncMock(), generate_shared_summary=AsyncMock(return_value="Summary"))
        view = {"messages": deepcopy(past) + [packet, question]}
        original = deepcopy(view["messages"])
        summary = SharedSummary(adapter, None, None, state)
        await summary.load(view, packet)
        self.assertTrue(await summary.extend(view, packet, original, trigger="Бюджет"))
        prompt_history = str(adapter.generate_shared_summary.await_args.args[3])
        for visible in ("Visible answer a", "Visible answer b", "agent_id"):
            self.assertIn(visible, prompt_history)
        for private in ("PRIVATE_A", "PRIVATE_B", "WORK_SKILL_LIMITED", "NATIVE_PRIVATE", "GROUP_PRIVATE"):
            self.assertNotIn(private, prompt_history)
        self.assertEqual(view["messages"][1:], [packet, question])

    async def test_summary_includes_completed_calls_and_uses_the_result_window(self):
        history = turn("agent-a", "PRIVATE_AGENT_RESULT")
        history[1:1] = [
            {"role": "assistant", "tool_calls": [
                {"id": "lookup-r", "type": "function",
                 "function": {"name": "common_lookup", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "lookup-r", "content": "ROUTER_FACT " + "a" * 2000 + " ROUTER_TAIL"},
        ]
        current = {"role": "user", "content": "Continue"}
        source = [*history, current]
        for limit in (None, 0, 1):
            with self.subTest(limit=limit):
                state = {"source": source, "user_boundary": len(history), "audit": [],
                         "context_policy": ContextPolicy(shared_summary_model_id="summary",
                                                         tool_result_history_limit=limit).model_dump()}
                adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                          generate_shared_summary=AsyncMock(return_value="Summary"),
                                          save_shared_summary=AsyncMock())
                summary = SharedSummary(adapter, None, None, state)
                view = {"messages": deepcopy(source)}
                await summary.load(view)
                self.assertTrue(await summary.extend(view, None, source, trigger="Бюджет"))
                result = adapter.generate_shared_summary.await_args.args[3]
                activity = [json.loads(message["content"].removeprefix("[Tool activity] "))
                            for message in result if message["content"].startswith("[Tool activity] ")]
                router_call, agent_call = activity
                self.assertEqual((router_call["tool"], router_call["executor"], router_call["status"]),
                                 ("common_lookup", "router", "completed"))
                self.assertEqual((agent_call["tool"], agent_call["agent_id"]), ("lookup", "agent-a"))
                self.assertNotIn("PRIVATE_AGENT_RESULT", str(result))
                self.assertNotIn("ROUTER_TAIL", str(result))
                if limit is None:
                    self.assertIn("ROUTER_FACT", router_call["result"])
                    self.assertTrue(router_call["result_truncated"])
                else:
                    self.assertIn("hidden by history limit", router_call["result"])
                    self.assertNotIn("ROUTER_FACT", str(result))
                self.assertEqual(state["source"], source)
                self.assertEqual(view["messages"][-1], current)

    async def test_incompatible_prefix_uses_shorter_checkpoint_and_preserves_fresh_results(self):
        first = turn("a", "FIRST")
        second = turn("b", "PROTECTED_RESULT")
        current = {"role": "user", "content": "Current"}
        source = first + second + [current]
        state = {"source": source, "user_boundary": len(source) - 1, "audit": [],
                 "context_policy": ContextPolicy(shared_summary_model_id="summary").model_dump()}
        adapter = SimpleNamespace(shared_summary_cache=AsyncMock(), generate_shared_summary=AsyncMock(),
                                  save_shared_summary=AsyncMock())
        summary = SharedSummary(adapter, None, None, state)
        short, long = (turn["key"] for turn in summary.turns)
        adapter.shared_summary_cache.return_value = {short: "One", long: "Two"}
        state["shared_summary_protected"] = [m for m in second if m.get("content") == "PROTECTED_RESULT"]
        state["shared_summary_unchanged_turns"] = 2
        view = {"messages": deepcopy(source)}
        await summary.load(view)
        self.assertTrue(summary.apply(view, None))
        self.assertIn("One", view["messages"][0]["content"])
        self.assertFalse(await summary.extend(view, None, source, trigger="Бюджет"))
        self.assertEqual(view["messages"][1:], second + [current])
        state["shared_summary_unchanged_turns"] = 0
        summary = SharedSummary(adapter, None, None, state)
        filtered = {"messages": deepcopy(source)}
        await summary.load(filtered)
        self.assertFalse(summary.apply(filtered, None))
        adapter.generate_shared_summary.assert_not_awaited()

    async def test_summary_from_an_edited_prefix_is_not_published(self):
        history = turn("a", "FIRST") + [{"role": "user", "content": "Current"}]
        state = {"source": history, "user_boundary": len(history) - 1, "audit": [],
                 "context_policy": ContextPolicy(shared_summary_model_id="summary").model_dump()}
        adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                  shared_summary_source_version=AsyncMock(return_value="original"),
                                  save_shared_summary=AsyncMock(return_value=False),
                                  generate_shared_summary=AsyncMock(return_value="Stale"))
        view = {"messages": deepcopy(history)}
        summary = SharedSummary(adapter, None, None, state)
        await summary.load(view)
        self.assertFalse(await summary.extend(view, None, history, trigger="Бюджет"))
        self.assertEqual(view["messages"], history)
        self.assertFalse(state["shared_summary_cache"])
        self.assertIsNone(summary.applied_summary_key())

    async def test_summary_contains_verbatim_visible_references_and_excludes_private_values(self):
        history = turn("a", "SECRET-776655")
        history[0]["content"] = "Склад WH-8031 на 18.11.2026"
        history += [{"role": "user", "content": "Назови номер кейса"}]
        state = {"source": history, "user_boundary": len(history) - 1, "audit": [],
                 "context_policy": ContextPolicy(shared_summary_model_id="summary").model_dump()}
        adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                  save_shared_summary=AsyncMock(),
                                  generate_shared_summary=AsyncMock(return_value="Складской отчёт"))
        summary = SharedSummary(adapter, None, None, state)
        view = {"messages": deepcopy(history)}
        await summary.load(view)
        self.assertTrue(await summary.extend(view, None, history, trigger="Бюджет"))
        summary_text = view["messages"][0]["content"]
        self.assertIn("Turn 1: WH-8031, 18.11.2026", summary_text)
        self.assertIn("Складской отчёт", summary_text)
        self.assertNotIn("SECRET-776655", summary_text)
