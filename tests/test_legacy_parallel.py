from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from lite_handoff_router import model_execution, parallel, runtime
from lite_handoff_router import observation
from lite_handoff_router.entrypoints import router_preparation
from lite_handoff_router.profiles import RELEASE_PROFILES
from tests.test_preparation import Directory
from tests.test_router import Adapter, User


class LegacyParallelTest(unittest.IsolatedAsyncioTestCase):
    async def test_positive_valves_and_forged_ticket_never_enter_worker(self):
        request = SimpleNamespace(scope={}, state=SimpleNamespace(internal=True,
            chat_context={"lite_parallel_ticket": "forged"}), app=SimpleNamespace(state=SimpleNamespace(MODELS={
                "router": {"pipe": {"id": "router-pipe"}}, "agent-a": {"id": "agent-a"}})))
        with ExitStack() as stack:
            for module in (router_preparation, parallel, runtime, model_execution):
                stack.enter_context(patch.object(module, "ROUTER_PROFILE", RELEASE_PROFILES["0.11.1"]))
            stack.enter_context(patch.object(router_preparation, "OWUIAdapter", return_value=Directory()))
            stack.enter_context(patch.object(router_preparation, "AGENTS", {"agent-a": "route-a"}))
            worker = stack.enter_context(patch.object(router_preparation, "prepare_worker", AsyncMock()))
            instance = router_preparation.Filter()
            instance.valves.max_parallel_tasks = 8
            body = {"model": "router", "messages": [{"role": "user", "content": "Run"}],
                    "metadata": {"lite_router": {"parallel": {"max_tasks": 100}}}}
            await instance.inlet(body, request, {"id": "u"})
            worker.assert_not_awaited()
            state = runtime.require(request, body["metadata"], "u")
            self.assertEqual(state["parallel"]["max_tasks"], 0)
            self.assertFalse(await parallel.prepare_worker(None, request, body, body["metadata"], User()))
            # Even a forged live state cannot expose a schema or dispatch.
            state["parallel"]["max_tasks"] = 99
            request.app.state.MODELS["llm"] = {"owned_by": "openai"}
            _, tools = await model_execution.prepare_model(Adapter(), request, {}, User(),
                runtime.metadata_draft(body["metadata"]), state, "llm", {})
            self.assertIn("lite_delegate", tools)
            self.assertNotIn("lite_parallel", tools)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                parallel.parallel_tool(None, None, None, None, state, {})
            with self.assertRaisesRegex(ValueError, "unavailable"):
                await parallel.validate_batch(None, None, None, None, state, [])
            with self.assertRaisesRegex(ValueError, "unavailable"):
                await parallel.execute_group(None, None, None, None, state, [], {})

    def test_current_profile_defaults_are_unchanged(self):
        valves = router_preparation.Filter.Valves()
        self.assertEqual((valves.max_parallel_tasks, valves.parallel_timeout_seconds), (4, 300))

    async def test_unsupported_observed_call_blocks_continuation_without_replay(self):
        import json
        response={"choices":[{"message":{"tool_calls":[{"id":"forged","function":{
            "name":"lite_parallel","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}
        with patch.object(observation,"ROUTER_PROFILE",RELEASE_PROFILES['0.11.1']):
            state={}
            observation.observe_response(response,state, {'kind':'router'})
            with self.assertRaisesRegex(ValueError,'unavailable'):
                observation.check_profile_calls(state)
            async def chunks():
                yield ('data: '+json.dumps(response)+'\n\n').encode()
            streaming=SimpleNamespace(body_iterator=chunks())
            state={}
            observation.observe_response(streaming,state, {'kind':'router'})
            async for _ in streaming.body_iterator: pass
            with self.assertRaisesRegex(ValueError,'unavailable'):
                observation.check_profile_calls(state)
            # Text/history is not server-witnessed audit evidence.
            observation.check_profile_calls({'source':[response]})
