from copy import deepcopy
from asyncio import CancelledError
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from lite_handoff_router.observation import observe_response
from lite_handoff_router.shared_summary import (ProviderContextOverflow, SharedSummary, check_initial_stream,
                                                last_router_prompt_tokens, prompt_token_count)
from tests.test_history import turn


class SharedSummaryTest(IsolatedAsyncioTestCase):
    async def test_router_uses_only_the_last_reported_input_usage(self):
        async def stream():
            yield b'data: {"choices":[{"finish_reason":"stop"}]}\n\n'
            yield b'data: {"choices":[],"usage":{"prompt_tokens":900,"completion_tokens":300}}\n\n'
            yield b'data: [DONE]\n\n'

        state = {"historical_router_usage": {"llm": {"prompt_tokens": 1000, "estimated_input": 3000}}}
        self.assertIsNone(last_router_prompt_tokens(state, "other"))
        self.assertEqual(last_router_prompt_tokens(state, "llm"), 1000)
        usage = AsyncMock()
        response = observe_response(SimpleNamespace(body_iterator=stream()), state, {"kind": "router"}, on_usage=usage)
        self.assertEqual(len([chunk async for chunk in response.body_iterator]), 3)
        tokens = prompt_token_count(usage.await_args.args[0])
        self.assertEqual(tokens, 900)
        state["router_usage"] = {"llm": {"prompt_tokens": tokens}}
        self.assertEqual(last_router_prompt_tokens(state, "llm"), 900)

    async def test_shared_checkpoint_preserves_current_tool_chain_and_parallel_packet(self):
        source = turn("a", "PRIVATE_RESULT")
        question = {"role": "user", "content": "New question"}
        packet = {"role": "user", "content": "Confirmed parallel task results. Worker data."}
        current_tool = {"role": "tool", "tool_call_id": "fresh", "content": "CURRENT_RESULT"}
        source += [question, current_tool]
        state = {"source": source, "user_boundary": 6, "audit": [],
                 "context_policy": {"shared_summary_model_id": "summary"}}
        adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                  generate_shared_summary=AsyncMock(return_value="Common summary"),
                                  save_shared_summary=AsyncMock())
        emitter = AsyncMock()
        summary = SharedSummary(adapter, None, None, state, emitter=emitter)
        await summary.load()
        view = {"messages": source[:6] + [packet, question, current_tool]}
        original = deepcopy(view["messages"])
        self.assertTrue(await summary.extend(view, packet, original))
        self.assertEqual(view["messages"][1:], [packet, question, current_tool])
        self.assertEqual(state["source"], source)
        summary_input = adapter.generate_shared_summary.await_args.args[3]
        self.assertIn("Visible answer a", str(summary_input))
        self.assertNotIn("PRIVATE_RESULT", str(summary_input))
        self.assertEqual([event.args[0] for event in emitter.await_args_list], [
            {"type": "status", "data": {"action": "lite_shared_summary", "description": "Сжимаю контекст…",
                                        "done": False, "error": False}},
            {"type": "status", "data": {"action": "lite_shared_summary", "description": "Контекст сокращён",
                                        "done": True, "error": False}},
        ])
        receiver = SharedSummary(adapter, None, None, state, emitter=emitter)
        await receiver.load()
        new_view = {"messages": deepcopy(original)}
        self.assertTrue(receiver.apply(new_view, packet))
        self.assertEqual(new_view["messages"], view["messages"])
        self.assertFalse(await receiver.extend(new_view, packet, original))
        self.assertEqual(emitter.await_count, 2)

    async def test_summary_status_closes_on_failure_and_cancellation(self):
        for failure, text, error in (
            (RuntimeError(), "Не удалось сократить контекст", True),
            (CancelledError(), "Сжатие контекста отменено", False),
        ):
            with self.subTest(failure=type(failure).__name__):
                messages = turn("a", "result") + [{"role": "user", "content": "New question"}]
                state = {"source": messages, "user_boundary": 6, "audit": [],
                         "context_policy": {"shared_summary_model_id": "summary"}}
                adapter = SimpleNamespace(shared_summary_cache=AsyncMock(return_value={}),
                                          generate_shared_summary=AsyncMock(side_effect=failure))
                emitter = AsyncMock()
                summary = SharedSummary(adapter, None, None, state, emitter=emitter)
                await summary.load()
                operation = summary.extend({"messages": deepcopy(messages)}, None, messages)
                if isinstance(failure, CancelledError):
                    with self.assertRaises(CancelledError):
                        await operation
                else:
                    self.assertFalse(await operation)
                self.assertEqual([event.args[0]["data"]["done"] for event in emitter.await_args_list], [False, True])
                self.assertEqual(emitter.await_args.args[0]["data"], {
                    "action": "lite_shared_summary", "description": text, "done": True, "error": error,
                })

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
