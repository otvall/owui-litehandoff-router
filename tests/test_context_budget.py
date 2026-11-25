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
    def test_budget_resolves_overrides_and_validates_valves(self):
        policy = Filter.Valves(context_input_budget_tokens="2000", context_safety_margin_tokens="100",
                               context_model_input_budgets={"agent": "900"}).model_dump()
        self.assertEqual(input_budget(policy, "router"), 1900)
        self.assertEqual(input_budget(policy, "agent"), 800)
        self.assertIsNone(input_budget(ContextPolicy().model_dump(), "router"))
        self.assertEqual(input_budget(ContextPolicy(context_input_budget_tokens=230000).model_dump(),
                                      "router"), 228976)
        for mapping in ({"context_input_budget_tokens": 1024},
                        {"context_model_input_budgets": {"agent": -1}},
                        {"context_model_input_budgets": {"agent": True}},
                        {"context_safety_margin_tokens": True}):
            with self.subTest(mapping=mapping), self.assertRaises(ValidationError):
                Filter.Valves(**mapping)

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

    def test_total_window_reserves_model_output_without_double_counting_input_cap(self):
        policy = Filter.Valves(context_window_tokens="15000", context_response_reserve_tokens="1800",
                               context_safety_margin_tokens="200",
                               context_model_input_budgets={"agent": 10000}).model_dump()
        self.assertEqual(input_budget(policy, "router"), 13000)
        self.assertEqual(input_budget(policy, "router", {"max_tokens": 500, "max_completion_tokens": 4000}), 10800)
        self.assertEqual(input_budget(policy, "router", {"options": {"num_predict": 3500}}), 11300)
        self.assertEqual(input_budget(policy, "agent", {"max_output_tokens": 4000}), 9800)
        self.assertEqual(input_budget(policy, "agent", {"max_output_tokens": 16000}), 0)
        for settings in ({"context_window_tokens": True}, {"context_response_reserve_tokens": True},
                         {"context_window_tokens": 2048}):
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                Filter.Valves(**settings)


class PipeContextBudgetTest(IsolatedAsyncioTestCase):
    async def invoke(self, history, policy, *, recipient=None, adapter=None, state_updates=None, model_options=None):
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
            result = await pipe.pipe(body, request, {"id": "owner"}, metadata)
        return result, state, draft, adapter

    async def test_recipient_override_rejects_large_current_turn_before_provider(self):
        adapter = SimpleNamespace(user=AsyncMock(), complete=AsyncMock())
        adapter.user.return_value = SimpleNamespace(id="owner")
        history = [{"role": "user", "content": "x" * 900}]
        policy = {"context_input_budget_tokens": 1000, "context_safety_margin_tokens": 0,
                  "context_model_input_budgets": {"agent": 200}}
        with self.assertRaisesRegex(ContextBudgetExceeded, "при лимите 200"):
            await self.invoke(history, policy, recipient="agent", adapter=adapter)
        adapter.complete.assert_not_awaited()
        await self.invoke(history, policy)

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
                  "context_input_budget_tokens": 320, "context_safety_margin_tokens": 0,
                  "context_model_input_budgets": {"agent": 250}}
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
        for budget, usage, result in ((None, 40, "new " * 180), (1000, 350, "small")):
            with self.subTest(budget=budget):
                tools = [{"role": "assistant", "tool_calls": [
                    {"id": "t", "type": "function", "function": {"name": "fixture", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "t", "content": result}]
                policy = {"shared_summary_model_id": "summary", "router_summary_trigger_tokens": 300,
                          "context_input_budget_tokens": budget, "context_safety_margin_tokens": 0}
                previous = {"router_usage": {"base": {"prompt_tokens": usage, "summary_key": None}}}
                adapter = SimpleNamespace(
                    user=AsyncMock(return_value=SimpleNamespace(id="owner")),
                    complete=AsyncMock(return_value={"choices": []}),
                    shared_summary_cache=AsyncMock(return_value={}),
                    generate_shared_summary=AsyncMock(return_value="Shared recap"),
                    save_shared_summary=AsyncMock(return_value="Shared recap"),
                    save_prompt_usage=AsyncMock(),
                )
                if budget is None:
                    await self.invoke(previous_turn + current_turn, policy, adapter=adapter, state_updates=previous)
                    adapter.generate_shared_summary.assert_not_awaited()
                    adapter.complete.reset_mock()
                _, _, draft, _ = await self.invoke(previous_turn + current_turn + tools, policy, adapter=adapter,
                                                   state_updates=previous)
                adapter.generate_shared_summary.assert_awaited_once()
                adapter.complete.assert_awaited_once()
                self.assertIn("Shared recap", draft["messages"][0]["content"])
                self.assertEqual(draft["messages"][-3:], current_turn + tools)

    async def test_model_output_reservation_blocks_a_request_before_provider(self):
        adapter = SimpleNamespace(user=AsyncMock(return_value=SimpleNamespace(id="owner")), complete=AsyncMock())
        with self.assertRaisesRegex(ContextBudgetExceeded, "при лимите 200"):
            await self.invoke([{"role": "user", "content": "x" * 900}],
                              {"context_window_tokens": 1000, "context_response_reserve_tokens": 100,
                               "context_safety_margin_tokens": 0},
                              adapter=adapter, model_options={"max_completion_tokens": 800})
        adapter.complete.assert_not_awaited()
