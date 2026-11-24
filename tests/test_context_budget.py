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


class PipeContextBudgetTest(IsolatedAsyncioTestCase):
    async def invoke(self, history, policy, *, recipient=None, adapter=None, state_updates=None):
        request = SimpleNamespace(scope={}, state=SimpleNamespace())
        metadata = {"chat_id": "local:budget", "message_id": "answer"}
        body = {"model": "router", "messages": deepcopy(history)}
        state = begin(request, body, metadata, "owner",
                      {"agent": {"agent_name": "Agent"}}, [], [], [], context_policy=policy)
        state.update(state_updates or {})
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
                 "tools": [{"type": "function", "function": tool["spec"]}]}
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
