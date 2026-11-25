from copy import deepcopy
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from lite_handoff_router.context_budget import ContextBudgetExceeded, estimate_context_budget, input_budget
from lite_handoff_router.context_policy import ContextPolicy
from lite_handoff_router.entrypoints.lite_handoff_router import Pipe
from lite_handoff_router.entrypoints.router_preparation import Filter
from lite_handoff_router.runtime import begin


class ContextBudgetTest(TestCase):
    def test_shared_window_defaults_and_ignores_retired_settings(self):
        policy = Filter.Valves(context_window_tokens="250000", router_summary_trigger_tokens=1,
                               context_input_budget_tokens=1, context_model_input_budgets={"agent": 1},
                               context_model_window_tokens={"agent": 1}, context_safety_margin_tokens=0)
        self.assertEqual(policy.shared_summary_trigger_percent, 90)
        self.assertEqual(input_budget(policy.model_dump()), 247952)
        self.assertIsNone(input_budget(ContextPolicy().model_dump()))
        for settings in ({"context_window_tokens": True}, {"context_window_tokens": 2048},
                         {"shared_summary_trigger_percent": True}, {"shared_summary_trigger_percent": 0},
                         {"shared_summary_trigger_percent": 101}):
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                Filter.Valves(**settings)

    def test_counts_model_facing_data_but_does_not_count_media_bytes_or_metadata(self):
        draft = {"messages": [
            {"role": "system", "content": "instructions"},
            {"role": "user", "content": '[Conversation history]\n{"turns":[],"summary":"A summary"}'},
            {"role": "assistant", "tool_calls": [{"function": {"name": "delegate"}}]},
            {"role": "tool", "content": '{"tool_results":[{"result":"Nested handoff result"}]}'},
            {"role": "user", "content": [{"type": "text", "text": "Image question"},
                                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,a"}}]},
        ], "tools": [{"function": {"parameters": {"type": "object"}}}]}
        measure = estimate_context_budget(draft)
        self.assertEqual(measure.mode, "estimated")
        self.assertTrue(all(measure.breakdown.values()), measure)
        changed = deepcopy(draft)
        changed["messages"][-1]["content"][1]["image_url"]["url"] += "a" * 20000
        changed["metadata"] = {"cache": "discard" * 30000}
        changed["messages"][0]["origin"] = "ignored" * 30000
        self.assertEqual(estimate_context_budget(changed), measure)
        count = lambda messages, tools: 101
        self.assertEqual(estimate_context_budget(changed, count_serialized_request=count).mode, "estimated")
        changed["messages"].pop()
        result = estimate_context_budget(changed, count_serialized_request=count)
        self.assertEqual((result.mode, result.total), ("exact", 101))

    def test_total_window_reserves_model_output_and_safety_margin(self):
        policy = Filter.Valves(context_window_tokens="15000").model_dump()
        self.assertEqual(input_budget(policy), 12952)
        self.assertEqual(input_budget(policy, {"max_tokens": 500, "max_completion_tokens": 4000}), 9976)
        self.assertEqual(input_budget(policy, {"options": {"num_predict": 3500}}), 10476)
        self.assertEqual(input_budget(policy, {"max_output_tokens": 16000}), 0)


class PipeContextBudgetTest(IsolatedAsyncioTestCase):
    async def invoke(self, history, policy, *, recipient=None, adapter=None, state_updates=None, model_options=None,
                     emitter=None):
        request = SimpleNamespace(scope={}, state=SimpleNamespace())
        metadata = {"chat_id": "local:budget", "message_id": "answer"}
        body = {"model": "router", "messages": deepcopy(history)}
        state = begin(request, body, metadata, "owner",
                      {"agent": {"agent_name": "Agent"}}, [], [], [], context_policy=policy)
        state.update(deepcopy(state_updates or {}))
        state["agent_history_question"] = history[state["user_boundary"]]
        if adapter is None:
            adapter = SimpleNamespace(
                user=AsyncMock(return_value=SimpleNamespace(id="owner")),
                complete=AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]}),
                save_prompt_usage=AsyncMock(),
            )
        tool = {"spec": {"name": "fixture", "description": "Tool"}}
        messages = deepcopy(history)
        draft = {"model": recipient or "base", "metadata": dict(metadata), "messages": messages,
                 "tools": [{"type": "function", "function": tool["spec"]}], **(model_options or {})}
        pipe = Pipe()
        with (
            patch("lite_handoff_router.entrypoints.lite_handoff_router.OWUIAdapter", return_value=adapter),
            patch("lite_handoff_router.entrypoints.lite_handoff_router.confirmed_target", return_value=recipient),
            patch("lite_handoff_router.entrypoints.lite_handoff_router.prepare_model",
                  AsyncMock(return_value=(draft, {"fixture": tool}))),
        ):
            result = await pipe.pipe(body, request, {"id": "owner"}, metadata, __event_emitter__=emitter)
        return result, state, draft, adapter

    async def test_shared_budget_rejects_large_current_turn_before_provider(self):
        adapter = SimpleNamespace(user=AsyncMock(return_value=SimpleNamespace(id="owner")), complete=AsyncMock())
        history = [{"role": "user", "content": "x" * 900}]
        for recipient in (None, "agent"):
            with self.subTest(recipient=recipient), self.assertRaisesRegex(ContextBudgetExceeded, "при лимите 200"):
                await self.invoke(history, {"context_window_tokens": 2248}, recipient=recipient, adapter=adapter)
        adapter.complete.assert_not_awaited()

    async def test_budget_compacts_then_rechecks_and_reuses_the_same_summary(self):
        adapter = SimpleNamespace(
            user=AsyncMock(return_value=SimpleNamespace(id="owner")),
            complete=AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]}),
            historical_handoff_statuses=AsyncMock(return_value={}),
            generate_shared_summary=AsyncMock(return_value="Shared recap"),
            save_prompt_usage=AsyncMock(),
        )
        cache = {}
        adapter.shared_summary_cache = AsyncMock(side_effect=lambda *args: dict(cache))

        async def save(state, user, model, key, summary):
            cache[key] = summary
            return summary

        adapter.save_shared_summary = AsyncMock(side_effect=save)
        history = [{"role": "user", "content": "old question " + "q" * 700},
                   {"role": "assistant", "content": "old answer " + "a" * 800},
                   {"role": "user", "content": "current question"},
                   {"role": "assistant", "tool_calls": [
                       {"id": "t", "type": "function", "function": {"name": "fixture", "arguments": "{}"}}]},
                   {"role": "tool", "tool_call_id": "t", "content": "CURRENT_RESULT"}]
        policy = {"shared_summary_model_id": "summary",
                  "context_window_tokens": 2368}
        _, _, router_draft, _ = await self.invoke(history, policy, adapter=adapter)
        _, _, agent_draft, _ = await self.invoke(history, policy, recipient="agent", adapter=adapter)
        adapter.generate_shared_summary.assert_awaited_once()
        self.assertIn("Shared recap", str(router_draft["messages"]))
        self.assertIn('"summary":"Shared recap"', str(agent_draft["messages"]))
        self.assertEqual(agent_draft["messages"][-3:], history[-3:])
        self.assertIs(adapter.complete.await_args.args[1], agent_draft)

    async def test_overflow_rebuilds_router_and_agent_context_before_retry(self):
        history = [{"role": "user", "content": "Original question"},
                   {"role": "assistant", "content": "Original response"},
                   {"role": "user", "content": "Current question"}]
        policy = {"shared_summary_model_id": "summary"}

        async def overflow():
            yield b'data: {"error":{"code":"context_length_exceeded"}}'

        for recipient, heading in ((None, "[Shared conversation summary]"), ("agent", "[Conversation history]")):
            with self.subTest(recipient=recipient):
                sent = []

                async def complete(request, draft, user):
                    sent.append(deepcopy(draft["messages"]))
                    if len(sent) == 1:
                        return SimpleNamespace(body_iterator=overflow())
                    return {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 42}}

                adapter = SimpleNamespace(
                    user=AsyncMock(return_value=SimpleNamespace(id="owner")),
                    complete=AsyncMock(side_effect=complete),
                    historical_handoff_statuses=AsyncMock(return_value={}),
                    shared_summary_cache=AsyncMock(return_value={}),
                    generate_shared_summary=AsyncMock(return_value="Shared recap"),
                    save_shared_summary=AsyncMock(return_value="Shared recap"),
                    save_prompt_usage=AsyncMock(),
                )
                result, _, _, _ = await self.invoke(history, policy, recipient=recipient, adapter=adapter)
                self.assertEqual(result["choices"][0]["message"]["content"], "ok")
                self.assertEqual(len(sent), 2)
                self.assertIn("Original response", str(sent[0]))
                self.assertNotIn("Original response", str(sent[1]))
                self.assertIn(heading, sent[1][0]["content"])
                self.assertIn("Shared recap", sent[1][0]["content"])
                self.assertEqual(sent[1][-1], history[-1])
                adapter.generate_shared_summary.assert_awaited_once()
                adapter.save_prompt_usage.assert_awaited_once()
                self.assertEqual(adapter.save_prompt_usage.await_args.args[-1], 42)

    async def test_current_tool_result_and_previous_usage_each_trigger_early_compaction(self):
        previous_turn = [{"role": "user", "content": "Old question"}, {"role": "assistant", "content": "Old reply"}]
        current_turn = [{"role": "user", "content": "Current question"}]
        for recipient, usage, result in ((None, 40, "new " * 220), ("agent", 300, "small")):
            with self.subTest(recipient=recipient):
                tools = [{"role": "assistant", "tool_calls": [
                    {"id": "t", "type": "function", "function": {"name": "fixture", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "t", "content": result}]
                policy = {"shared_summary_model_id": "summary", "shared_summary_trigger_percent": 10,
                          "context_window_tokens": 3000}
                sample = {"prompt_tokens": usage, "summary_key": None}
                previous = ({"agent_usage": {("agent", "agent"): sample}} if recipient
                            else {"router_usage": {"base": sample}})
                adapter = SimpleNamespace(
                    user=AsyncMock(return_value=SimpleNamespace(id="owner")),
                    complete=AsyncMock(return_value={"choices": []}),
                    historical_handoff_statuses=AsyncMock(return_value={}),
                    shared_summary_cache=AsyncMock(return_value={}),
                    generate_shared_summary=AsyncMock(return_value="Shared recap"),
                    save_shared_summary=AsyncMock(return_value="Shared recap"),
                    save_prompt_usage=AsyncMock(),
                )
                if recipient is None:
                    await self.invoke(previous_turn + current_turn, policy, adapter=adapter, state_updates=previous)
                    adapter.generate_shared_summary.assert_not_awaited()
                    adapter.complete.reset_mock()
                _, _, draft, _ = await self.invoke(previous_turn + current_turn + tools, policy, adapter=adapter,
                                                   recipient=recipient, state_updates=previous)
                adapter.generate_shared_summary.assert_awaited_once()
                adapter.complete.assert_awaited_once()
                self.assertIn("Shared recap", draft["messages"][0]["content"])
                self.assertEqual(draft["messages"][-3:], current_turn + tools)

    async def test_first_turn_skips_proactive_compaction_even_if_threshold_is_reached(self):
        emitter = AsyncMock()
        adapter = SimpleNamespace(
            user=AsyncMock(return_value=SimpleNamespace(id="owner")),
            complete=AsyncMock(return_value={"choices": [], "usage": {"prompt_tokens": 110}}),
            generate_shared_summary=AsyncMock(),
            save_prompt_usage=AsyncMock(),
        )
        await self.invoke([{"role": "user", "content": "x" * 300}],
                          {"shared_summary_model_id": "summary", "context_window_tokens": 3000,
                           "shared_summary_trigger_percent": 1}, adapter=adapter, emitter=emitter)
        adapter.complete.assert_awaited_once()
        adapter.generate_shared_summary.assert_not_awaited()
        emitter.assert_not_awaited()

    async def test_model_output_reservation_blocks_a_request_before_provider(self):
        adapter = SimpleNamespace(user=AsyncMock(return_value=SimpleNamespace(id="owner")), complete=AsyncMock())
        with self.assertRaisesRegex(ContextBudgetExceeded, "при лимите 200"):
            await self.invoke([{"role": "user", "content": "x" * 900}],
                              {"context_window_tokens": 3024},
                              adapter=adapter, model_options={"max_completion_tokens": 1800})
        adapter.complete.assert_not_awaited()

    async def test_current_oversized_tool_result_is_shortened_on_budget_or_provider_overflow(self):
        call = {"id": "lookup", "type": "function", "function": {"name": "fixture", "arguments": "{}"}}
        history = [{"role": "user", "content": "Read the result"},
                   {"role": "assistant", "tool_calls": [call]},
                   {"role": "tool", "tool_call_id": "lookup", "content": "BEGIN " + "x" * 9000 + " TAIL"}]
        for window in (3000, None):
            with self.subTest(window=window):
                sent = []

                async def send(request, draft, user):
                    sent.append(deepcopy(draft["messages"]))
                    if window is None and len(sent) == 1:
                        async def overflow():
                            yield b'data: {"error":{"code":"context_length_exceeded"}}'

                        return SimpleNamespace(body_iterator=overflow())
                    return {"choices": [{"message": {"content": "Answer"}}]}

                emitter = AsyncMock()
                adapter = SimpleNamespace(user=AsyncMock(return_value=SimpleNamespace(id="owner")),
                                          complete=AsyncMock(side_effect=send))
                response, state, _, _ = await self.invoke(history, {"context_window_tokens": window}, adapter=adapter,
                                                           emitter=emitter)
                self.assertEqual(response["choices"][0]["message"]["content"], "Answer")
                self.assertEqual(len(sent), 2 if window is None else 1)
                self.assertEqual(sent[-1][-2], history[-2])
                self.assertEqual(sent[-1][-1]["tool_call_id"], "lookup")
                self.assertIn("Tool result shortened", sent[-1][-1]["content"])
                self.assertIn("BEGIN", sent[-1][-1]["content"])
                self.assertNotIn("TAIL", sent[-1][-1]["content"])
                self.assertEqual(state["source"], history)
                self.assertEqual(emitter.await_args.args[0]["data"]["action"], "lite_tool_result_budget")
